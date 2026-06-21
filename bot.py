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
                log.info(f"Reusing existing webhook in #{channel.name}")
                return wh

        wh = await channel.create_webhook(name=char_name)
        channel_webhooks[channel.id] = wh
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

    # Brief pause before typing indicator — feels like reading first
    read_delay = character.get("reply_delay_seconds", 0)
    if read_delay > 0:
        await asyncio.sleep(read_delay)

    async with message.channel.typing():
        reply = await query_openrouter(msgs, character)

    if not reply:
        log.warning("No reply received from OpenRouter.")
        return

    reply = strip_action_text(reply)
    reply = trim_to_sentences(reply, character.get("max_sentences", 3))

    channel_histories[channel_id].append({"role": "assistant", "content": reply})
    log.info(f"[#{message.channel.name}] {character['name']}: {reply[:80]}")

    await send_as_character(
        channel=message.channel,
        text=reply,
        character=character,
        fallback=message,
    )


# ── Commands ───────────────────────────────────────────────────────────────────

@bot.command(name="forget")
@commands.has_permissions(manage_messages=True)
async def forget(ctx: commands.Context):
    """Wipe conversation history for this channel. Requires Manage Messages."""
    channel_histories.pop(ctx.channel.id, None)
    channel_webhooks.pop(ctx.channel.id, None)
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
