"""
Discord Character Bot  v1.0.0
──────────────────────────────
Run fully configurable AI personas in Discord using OpenRouter LLMs.
Each character posts via webhook — custom name and avatar, no BOT badge.

Usage:
  python bot.py                                # loads ./character.json + ./.env
  python bot.py --character characters/alex    # loads from characters/alex/
  python bot.py --character characters/sarah   # loads from characters/sarah/

GitHub: https://github.com/your-username/discord-character-bot
"""

import os
import re
import json
import logging
import asyncio
import argparse
import random

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

VERSION = "1.0.0"

# ── CLI Args ───────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser(
    description="Discord Character Bot — run an AI persona in Discord via OpenRouter."
)
parser.add_argument(
    "--character",
    default=".",
    metavar="PATH",
    help="Path to character folder containing character.json and .env (default: current dir)",
)
args = parser.parse_args()

CHARACTER_DIR  = os.path.abspath(args.character)
CHARACTER_FILE = os.path.join(CHARACTER_DIR, "character.json")
ENV_FILE       = os.path.join(CHARACTER_DIR, ".env")

# ── Logging ────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("CharacterBot")

# ── Environment ────────────────────────────────────────────────────────────────

load_dotenv(ENV_FILE)

DISCORD_BOT_TOKEN  = os.getenv("DISCORD_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"

# ── In-memory State ────────────────────────────────────────────────────────────

# Per-channel conversation history  {channel_id: [{"role": ..., "content": ...}]}
channel_histories: dict[int, list[dict]] = {}

# Per-channel webhook cache  {channel_id: discord.Webhook}
channel_webhooks: dict[int, discord.Webhook] = {}

# Per-channel consecutive bot-to-bot message counter  {channel_id: int}
channel_bot_chains: dict[int, int] = {}

# Webhook IDs owned by this bot instance — prevents the bot from replying to itself
own_webhook_ids: set[int] = set()

# Per-channel active response task — enables message interruption  {channel_id: asyncio.Task}
channel_tasks: dict[int, asyncio.Task] = {}

# ── Helper Functions ───────────────────────────────────────────────────────────

def load_character() -> dict:
    """Load and return the character config from character.json (hot-reloadable)."""
    with open(CHARACTER_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def trim_to_sentences(text: str, max_sentences: int) -> str:
    """Trim text to at most `max_sentences` complete sentences."""
    if max_sentences <= 0:
        return text
    sentences = re.split(r'(?<=[.!?])(?:\s+|$)', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    return " ".join(sentences[:max_sentences])


def strip_action_text(text: str) -> str:
    """
    Remove roleplay-style action text from LLM responses.
    Cleans: *smirks*, **bold actions**, _looks up_, (short parentheticals)
    """
    text = re.sub(r'\*{1,2}[^*]{1,80}\*{1,2}', '', text)   # *action* / **action**
    text = re.sub(r'_[^_]{1,80}_', '', text)                 # _action_
    text = re.sub(r'\([^)]{1,60}\)', '', text)               # (action) — short only
    text = re.sub(r'^[\s,;:\-]+', '', text)                  # leading punctuation artifacts
    text = re.sub(r'\s{2,}', ' ', text).strip()              # collapse whitespace
    return text


# ── Behavioral Timing Engine ───────────────────────────────────────────────────

class BehavioralTimingEngine:
    """
    Simulates human-like reading and typing cadence.

    Reading delay  — short silent pause that scales with incoming word count,
                     mimicking the time a person takes to scan a message before
                     they start typing back.
    Typing duration — time spent inside Discord's typing indicator, scaled to
                      the length of the outgoing response so longer replies feel
                      like they actually took effort to write.
    """

    def __init__(self, base_cps: float = 14.0, variance: float = 3.0) -> None:
        """
        Args:
            base_cps:  Baseline characters-per-second typing speed.
            variance:  Maximum random +/- deviation from base_cps per message.
        """
        self.base_cps = base_cps
        self.variance = variance

    def calculate_reading_delay(self, incoming_text: str) -> float:
        """
        Simulate the time taken to scan an incoming message before responding.

        Formula: (word_count * 0.04) + uniform(0.4, 1.2), capped at 3.5 s.

        Args:
            incoming_text: The raw message content received from the user.

        Returns:
            A float representing seconds to sleep silently (no typing indicator).
        """
        word_count  = len(incoming_text.split())
        scan_time   = word_count * 0.04
        jitter      = random.uniform(0.4, 1.2)
        delay       = scan_time + jitter
        return min(delay, 3.5)

    def calculate_typing_duration(self, response_text: str) -> float:
        """
        Simulate the time taken to type out a response.

        Formula: char_count / (base_cps ± variance), clamped to [1.2, 5.5] s.

        Args:
            response_text: The AI-generated reply that will be sent.

        Returns:
            A float representing seconds to hold the typing indicator open.
        """
        effective_cps = self.base_cps + random.uniform(-self.variance, self.variance)
        effective_cps = max(effective_cps, 1.0)  # guard against near-zero division
        duration      = len(response_text) / effective_cps
        return max(1.2, min(duration, 5.5))


# Module-level singleton — shared across all channels/events
timing_engine = BehavioralTimingEngine()


def build_messages(channel_id: int, user_display: str, user_text: str, character: dict) -> list[dict]:
    """
    Build the full message list for the OpenRouter API call:
      [system prompt + formatting rules] + [channel history] + [new user message]

    Side-effect: appends the new user message to channel_histories.
    """
    max_history = character.get("max_history", 20)

    if channel_id not in channel_histories:
        channel_histories[channel_id] = []

    history = channel_histories[channel_id]
    history.append({"role": "user", "content": f"{user_display}: {user_text}"})

    # Keep history within configured limit
    if len(history) > max_history:
        channel_histories[channel_id] = history[-max_history:]

    # Inject formatting rules at runtime — no need to add to every character.json
    system_prompt = character["system_prompt"].rstrip()
    system_prompt += (
        "\n\nFORMATTING RULES (always follow these):"
        " Do NOT use action text, stage directions, or roleplay emotes (e.g. *smirks*, *looks up*, *laughs*)."
        " This is a plain text chat — respond only with natural spoken words."
        " No asterisks, no parenthetical actions, no narration."
    )

    return [{"role": "system", "content": system_prompt}] + channel_histories[channel_id]


async def get_or_create_webhook(channel: discord.TextChannel, char_name: str) -> discord.Webhook | None:
    """
    Return a cached webhook for the channel, creating one if needed.
    Returns None if the bot lacks Manage Webhooks permission (falls back to normal send).
    """
    if channel.id in channel_webhooks:
        return channel_webhooks[channel.id]

    try:
        existing = await channel.webhooks()
        for wh in existing:
            if wh.user and wh.user.id == channel.guild.me.id:
                channel_webhooks[channel.id] = wh
                own_webhook_ids.add(wh.id)   # register so we never reply to ourselves
                log.info(f"Reusing existing webhook in #{channel.name}")
                return wh

        wh = await channel.create_webhook(name=char_name)
        channel_webhooks[channel.id] = wh
        own_webhook_ids.add(wh.id)   # register so we never reply to ourselves
        log.info(f"Created new webhook in #{channel.name}")
        return wh

    except discord.Forbidden:
        log.warning(f"Missing Manage Webhooks in #{channel.name} — falling back to normal reply.")
        return None
    except discord.HTTPException as e:
        log.error(f"Webhook error: {e}")
        return None


async def query_openrouter(messages: list[dict], character: dict, _retries: int = 2) -> str | None:
    """
    Send a message list to OpenRouter and return the text reply.

    Automatically retries on temporary 429 rate limits using the Retry-After hint.
    Does NOT retry on daily quota exhaustion (free-models-per-day).
    Returns None on unrecoverable error.
    """
    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type":  "application/json",
        "HTTP-Referer":  "https://github.com/your-username/discord-character-bot",
        "X-Title":       f"{character['name']} Discord Bot",
    }

    payload = {
        "model":       character.get("model", "meta-llama/llama-3.1-8b-instruct:free"),
        "messages":    messages,
        "temperature": character.get("temperature", 0.85),
        "max_tokens":  character.get("max_sentences", 3) * 60,  # ~60 tokens per sentence
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                OPENROUTER_URL, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp:

                if resp.status == 429 and _retries > 0:
                    try:
                        data = await resp.json()
                        error_meta = data.get("error", {}).get("metadata", {})
                        error_msg  = data.get("error", {}).get("message", "")

                        # Daily quota exhausted — retrying won't help, give up immediately
                        if "per-day" in error_msg or "per_day" in error_msg:
                            log.error("Daily free model quota exhausted. Add credits at openrouter.ai/credits")
                            return None

                        wait = float(error_meta.get("retry_after_seconds", 10))
                    except Exception:
                        wait = 10

                    wait = min(wait, 30)  # cap at 30s
                    log.warning(f"Rate limited — retrying in {wait:.1f}s ({_retries} retries left)")
                    await asyncio.sleep(wait)
                    return await query_openrouter(messages, character, _retries=_retries - 1)

                if resp.status != 200:
                    body = await resp.text()
                    log.error(f"OpenRouter {resp.status}: {body}")
                    return None

                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()

    except asyncio.TimeoutError:
        log.error("OpenRouter request timed out.")
        return None
    except Exception as e:
        log.error(f"OpenRouter exception: {e}")
        return None


async def send_as_character(
    channel:   discord.TextChannel,
    text:      str,
    character: dict,
    fallback:  discord.Message,
) -> None:
    """
    Post a reply via webhook (character name + avatar, no BOT badge).
    Falls back to a normal channel send if webhook creation fails.
    Long messages are automatically split at Discord's 2000-char limit.
    """
    webhook = await get_or_create_webhook(channel, character["name"])
    chunks  = [text[i:i + 2000] for i in range(0, len(text), 2000)]

    if webhook:
        for chunk in chunks:
            await webhook.send(
                content=chunk,
                username=character["name"],
                avatar_url=character.get("avatar_url"),
            )
    else:
        for chunk in chunks:
            await fallback.channel.send(chunk)


async def maybe_send_followup(
    channel:    discord.TextChannel,
    channel_id: int,
    character:  dict,
    fallback:   discord.Message,
) -> None:
    """
    Optionally send a short follow-up after the main reply.

    Simulates the human habit of adding an afterthought or continuation
    shortly after sending a message. Fully opt-in via character.json:

        "followup": {
            "chance":    0.3,   // probability 0.0–1.0 (default 0 = disabled)
            "min_delay": 1.5,   // seconds before the follow-up appears
            "max_delay": 4.0
        }

    The LLM is re-called with the existing history plus a continuation
    directive so the follow-up reads as a natural extension of the main reply.
    """
    followup_cfg = character.get("followup", {})
    chance       = followup_cfg.get("chance", 0.0)

    if chance <= 0.0 or random.random() > chance:
        return

    if channel_id not in channel_histories or not channel_histories[channel_id]:
        return

    # ── Silent afterthought pause (bot appears to have stopped, then reconsiders)
    delay = random.uniform(
        followup_cfg.get("min_delay", 1.5),
        followup_cfg.get("max_delay", 4.0),
    )
    # Snapshot history length before sleeping — if new messages arrive during the
    # delay the conversation has moved on and the follow-up no longer makes sense.
    snapshot_len = len(channel_histories[channel_id])
    log.debug(f"[💭] Follow-up triggered — waiting {delay:.2f}s")
    await asyncio.sleep(delay)

    if len(channel_histories.get(channel_id, [])) != snapshot_len:
        log.debug("[💭] Follow-up aborted — conversation moved on.")
        return

    # ── Build a continuation-directive prompt ─────────────────────────────────
    system_prompt = character["system_prompt"].rstrip()
    system_prompt += (
        "\n\nFORMATTING RULES (always follow these):"
        " Do NOT use action text, stage directions, or roleplay emotes."
        " This is a plain text chat — respond only with natural spoken words."
        " No asterisks, no parenthetical actions, no narration."
        "\n\nCONTINUATION DIRECTIVE: You just sent a message and are now adding a"
        " brief follow-up thought. Write 1–2 sentences only. Do NOT repeat or"
        " summarise what you already said — add something new, like an afterthought,"
        " a clarification, or a natural continuation of your previous point."
    )
    messages = [{"role": "system", "content": system_prompt}] + channel_histories[channel_id]

    # ── Fetch the follow-up from the LLM ─────────────────────────────────────
    # Cap token budget to 2 sentences — we hard-trim anyway, no need to pay for more.
    followup_character = {**character, "max_sentences": 2}
    followup_reply = await query_openrouter(messages, followup_character)
    if not followup_reply:
        return

    followup_reply = strip_action_text(followup_reply)
    followup_reply = trim_to_sentences(followup_reply, 2)   # hard cap at 2 sentences

    # ── Typing indicator proportional to follow-up length ────────────────────
    typing_duration = timing_engine.calculate_typing_duration(followup_reply)
    log.debug(f"[⌨️] Follow-up typing duration: {typing_duration:.2f}s")
    async with channel.typing():
        await asyncio.sleep(typing_duration)

    # ── Deliver and persist ──────────────────────────────────────────────────
    channel_histories[channel_id].append({"role": "assistant", "content": followup_reply})
    log.info(f"[#{channel.name}] ↩ Follow-up: {followup_reply[:80]}")
    await send_as_character(channel, followup_reply, character, fallback)


async def _process_human_message(
    message:   discord.Message,
    character: dict,
    msgs:      list[dict],
) -> None:
    """
    Full response pipeline for a single human message.

    Runs as a cancellable asyncio.Task — when the same user sends another message
    while this is in-flight, the active task is cancelled and a fresh one starts,
    ensuring the bot only ever replies to the latest context in the channel.

    Cancellation is safe at every await point:
      ① asyncio.sleep (read_delay)    — free, zero API cost
      ② query_openrouter / aiohttp    — aborts the HTTP request mid-flight
      ③ asyncio.sleep (typing pad)    — LLM already responded (one call cost sunk)
    """
    channel_id = message.channel.id

    # ── Phase 1: Silent reading delay (cancellation point ①) ──────────────────
    read_delay = timing_engine.calculate_reading_delay(message.content)
    log.debug(f"[⏱] Reading delay: {read_delay:.2f}s")
    await asyncio.sleep(read_delay)

    # ── Phase 2+3: LLM under typing indicator; pad remaining to typing_duration ──
    # The indicator opens before the LLM call and stays open until the
    # response-proportional duration is fully consumed, so users always see
    # feedback during the slow generation step.
    llm_start = asyncio.get_event_loop().time()
    async with message.channel.typing():
        reply = await query_openrouter(msgs, character)   # cancellation point ②

        if not reply:
            log.warning("No reply received from OpenRouter.")
            return

        reply = strip_action_text(reply)
        reply = trim_to_sentences(reply, character.get("max_sentences", 3))

        typing_duration = timing_engine.calculate_typing_duration(reply)
        elapsed  = asyncio.get_event_loop().time() - llm_start
        leftover = typing_duration - elapsed
        log.debug(
            f"[⌨️] Typing: {typing_duration:.2f}s target "
            f"(LLM {elapsed:.2f}s, padding {max(0.0, leftover):.2f}s)"
        )
        if leftover > 0:
            await asyncio.sleep(leftover)   # cancellation point ③

    # ── Phase 4: Deliver ────────────────────────────────────────────────────
    channel_histories[channel_id].append({"role": "assistant", "content": reply})
    log.info(f"[#{message.channel.name}] {character['name']}: {reply[:80]}")

    await send_as_character(
        channel=message.channel,
        text=reply,
        character=character,
        fallback=message,
    )

    # ── Phase 5: Optional follow-up ─────────────────────────────────────────
    await maybe_send_followup(
        channel=message.channel,
        channel_id=channel_id,
        character=character,
        fallback=message,
    )


# ── Bot Initialisation ─────────────────────────────────────────────────────────

intents                 = discord.Intents.default()
intents.message_content = True   # Privileged intent — must be enabled in the Dev Portal

bot = commands.Bot(command_prefix="!char ", intents=intents)


# ── Events ─────────────────────────────────────────────────────────────────────

@bot.event
async def on_ready():
    log.info(f"✅  Logged in as {bot.user} (ID: {bot.user.id})")
    log.info("─" * 40)


@bot.event
async def on_message(message: discord.Message):
    character  = load_character()
    channel_id = message.channel.id
    bot_cfg    = character.get("bot_interaction", {})
    is_webhook = bool(message.webhook_id)

    # ── Other character (webhook) message ──────────────────────────────────────
    if message.author.bot:
        if not is_webhook:
            return  # ignore regular bots (moderation bots, etc.)
        if message.webhook_id in own_webhook_ids:
            return  # this is our own webhook message — never reply to ourselves
        if not bot_cfg.get("enabled", False):
            return  # bot-to-bot interaction disabled in config
        if not isinstance(message.channel, discord.TextChannel):
            return

        chain     = channel_bot_chains.get(channel_id, 0)
        max_chain = bot_cfg.get("max_bot_chain", 3)

        if chain >= max_chain:
            log.info(f"[⛔] Chain limit ({max_chain}) hit in #{message.channel.name} — staying quiet.")
            return

        if random.random() > bot_cfg.get("reply_chance", 0.4):
            log.info("[🎲] Skipped bot-to-bot reply (chance roll).")
            return

        await asyncio.sleep(bot_cfg.get("reply_delay_seconds", 4))
        channel_bot_chains[channel_id] = chain + 1
        log.info(f"[🤖↔️🤖] Bot reply in #{message.channel.name} (chain {chain + 1}/{max_chain})")

        msgs = build_messages(channel_id, message.author.display_name, message.content, character)
        async with message.channel.typing():
            reply = await query_openrouter(msgs, character)
        if not reply:
            return

        reply = strip_action_text(reply)
        reply = trim_to_sentences(reply, character.get("max_sentences", 3))
        channel_histories[channel_id].append({"role": "assistant", "content": reply})
        await send_as_character(message.channel, reply, character, message)
        return

    # ── Human message ──────────────────────────────────────────────────────────

    channel_bot_chains[channel_id] = 0  # reset chain on human activity

    if not isinstance(message.channel, discord.TextChannel):
        return

    await bot.process_commands(message)

    ctx = await bot.get_context(message)
    if ctx.valid:
        return  # was a bot command — don't also reply as character

    listen_channels: list[int] = character.get("listen_channels", [])
    if listen_channels and channel_id not in listen_channels:
        return

    msgs = build_messages(channel_id, message.author.display_name, message.content, character)
    log.info(f"[#{message.channel.name}] {message.author.display_name}: {message.content[:80]}")
    # Note: build_messages appends the user message to channel_histories here,
    # before any task is created, so context is preserved even on cancellation.

    # ── Cancel any in-flight response for this channel ─────────────────────────
    prior = channel_tasks.get(channel_id)
    if prior and not prior.done():
        prior.cancel()
        log.info(f"[⚡] Interrupted prior response in #{message.channel.name}")

    # ── Launch new cancellable response task ───────────────────────────────────
    channel_tasks[channel_id] = asyncio.create_task(
        _process_human_message(message, character, msgs)
    )


# ── Commands ───────────────────────────────────────────────────────────────────

@bot.command(name="forget")
@commands.has_permissions(manage_messages=True)
async def forget(ctx: commands.Context):
    """Wipe conversation history for this channel. Requires Manage Messages."""
    channel_histories.pop(ctx.channel.id, None)
    wh = channel_webhooks.pop(ctx.channel.id, None)
    if wh:
        own_webhook_ids.discard(wh.id)   # prune stale webhook ID from guard set
    task = channel_tasks.pop(ctx.channel.id, None)
    if task and not task.done():
        task.cancel()                     # abort any in-flight response cleanly
    await ctx.message.delete(delay=0)
    await ctx.send("🧹 Memory wiped. Fresh start.", delete_after=5)


@bot.command(name="reload")
@commands.has_permissions(manage_messages=True)
async def reload_char(ctx: commands.Context):
    """Reload character.json without restarting the bot. Requires Manage Messages."""
    try:
        char = load_character()
        await ctx.message.delete(delay=0)
        await ctx.send(f"✅ Character reloaded → **{char['name']}**", delete_after=5)
    except Exception as e:
        await ctx.send(f"❌ Reload failed: `{e}`", delete_after=8)


@bot.command(name="status")
async def status(ctx: commands.Context):
    """Show active character, memory usage, and model for this channel."""
    char    = load_character()
    history = channel_histories.get(ctx.channel.id, [])
    await ctx.message.delete(delay=0)
    await ctx.send(
        f"🤖 **{char['name']}** is active.\n"
        f"📝 History: `{len(history)}` / `{char.get('max_history', 20)}` messages\n"
        f"🧠 Model: `{char.get('model', 'N/A')}`\n"
        f"⚙️  v{VERSION}",
        delete_after=10,
    )


@forget.error
@reload_char.error
async def permission_error(ctx: commands.Context, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("🚫 You need **Manage Messages** to use that command.", delete_after=5)


# ── Entry Point ────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    if not os.path.exists(CHARACTER_FILE):
        log.critical(f"character.json not found at: {CHARACTER_FILE}")
        raise SystemExit(1)
    if not DISCORD_BOT_TOKEN:
        log.critical(f"DISCORD_BOT_TOKEN not set in: {ENV_FILE}")
        raise SystemExit(1)
    if not OPENROUTER_API_KEY:
        log.critical(f"OPENROUTER_API_KEY not set in: {ENV_FILE}")
        raise SystemExit(1)

    char = load_character()
    log.info(f"📂 Folder    : {CHARACTER_DIR}")
    log.info(f"🎭 Character : {char['name']}")
    log.info(f"🧠 Model     : {char.get('model', 'N/A')}")
    log.info(f"⚙️  Version   : v{VERSION}")

    bot.run(DISCORD_BOT_TOKEN, log_handler=None)
