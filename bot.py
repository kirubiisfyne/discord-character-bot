"""
Discord Character Bot  v1.2.0
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
import sys
import re
import json
import logging
import asyncio
import argparse
import random
import subprocess
import urllib.request
import time
import datetime

import aiohttp
import discord
from discord.ext import commands
from dotenv import load_dotenv

import aiosqlite

from config import (
    VERSION, CHARACTER_DIR, CHARACTER_FILE,
    DISCORD_BOT_TOKEN, OPENROUTER_API_KEY, DEEPSEEK_API_KEY, LLM_API_URL,
    DB_PATH, log, args, ENV_FILE
)

# ── In-memory State ────────────────────────────────────────────────────────────

from llm import get_llm_endpoint, ensure_ollama_running, query_openrouter

from state import (
    channel_histories,
    channel_webhooks,
    channel_last_activity,
    channel_has_unanswered_proactive,
    channel_idle_target,
    channel_bot_chains,
    channel_last_thoughts,
    channel_msg_counts,
    own_webhook_ids,
    channel_tasks,
)
from database import get_db, init_db, extract_memories_bg


from gallery import seed_gallery, select_local_image
from chat_utils import (
    get_channel_context,
    build_mood_aware_prompt,
    load_character,
    trim_to_length,
    strip_action_text,
    build_messages,
)

def _deduplicate_reply(text: str) -> str:
    """If the LLM repeated the same chunk multiple times, keep only one copy."""
    text = text.strip()
    if not text:
        return text
    length = len(text)
    # Check if the text is made up of exact repeats of a substring
    for repeats in (2, 3, 4):
        if length % repeats == 0:
            chunk_len = length // repeats
            chunk = text[:chunk_len]
            if chunk * repeats == text:
                return chunk.strip()
    # Approximate matching: check if the front chunk reappears right after itself
    for repeats in (2, 3, 4):
        chunk_len = length // repeats
        if chunk_len < 10:
            break
        chunk = text[:chunk_len].strip()
        remaining = text[chunk_len:].strip()
        if remaining.startswith(chunk[:min(len(chunk), 20)]):
            return chunk
    return text

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





async def send_as_character(
    channel:   discord.TextChannel,
    text:      str,
    character: dict,
    fallback:  discord.Message,
    file_path: str | None = None,
) -> None:
    """
    Post a reply via webhook (character name + avatar, no BOT badge).
    Falls back to a normal channel send if webhook creation fails.
    Long messages are automatically split at Discord's 2000-char limit.
    """
    webhook = await get_or_create_webhook(channel, character["name"])
    
    # Split by explicit newlines first
    raw_lines = [line.strip() for line in text.split('\n') if line.strip()]
    
    # Further split into individual sentences
    chunks = []
    for line in raw_lines:
        sentences = re.split(r'(?<=[.!?])(?:\s+|$)', line)
        sentences = [s.strip() for s in sentences if s.strip()]
        chunks.extend(sentences)

    for i, chunk in enumerate(chunks):
        # Add a short, human-like typing delay between consecutive messages
        if i > 0:
            delay = min(max(len(chunk) / 15.0, 0.8), 2.5)
            async with channel.typing():
                await asyncio.sleep(delay)
                
        # Safe fallback split for insanely long sentences over 2000 chars
        sub_chunks = [chunk[j:j + 2000] for j in range(0, len(chunk), 2000)]
        for j, sc in enumerate(sub_chunks):
            is_last = (i == len(chunks) - 1 and j == len(sub_chunks) - 1)
            kwargs = {}
            if is_last and file_path and os.path.exists(file_path):
                kwargs['file'] = discord.File(file_path)
                
            if webhook:
                await webhook.send(
                    content=sc,
                    username=character["name"],
                    avatar_url=character.get("avatar_url"),
                    **kwargs
                )
            else:
                if fallback:
                    await fallback.channel.send(sc, **kwargs)
                else:
                    await channel.send(sc, **kwargs)



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
    read_delay = random.uniform(1.0, 2.5)
    await asyncio.sleep(read_delay)

    # ── Phase 2+3: LLM under typing indicator ──
    async with message.channel.typing():
        reply, total_tokens = await query_openrouter(msgs, character)   # cancellation point ②

        if not reply:
            log.warning("No reply received from the LLM.")
            return

        # Extract the thought before stripping it
        if '</think>' in reply:
            channel_last_thoughts[channel_id] = reply.split('</think>', 1)[0].replace('<think>', '').strip()

        reply = strip_action_text(reply)
        
        img_file_path = None
        img_data = None
        
        # Remove any hallucinated [Sent a photo...] blocks that the LLM tries to mimic from its own history
        reply = re.sub(r'\[Sent a photo of:.*?\]', '', reply).strip()
        
        photo_match = re.search(r'<send_photo(?:[:=]([^>]+))?>', reply)
        if photo_match:
            requested_tag = photo_match.group(1).strip() if photo_match.group(1) else None
            # Strip ALL <send_photo...> tags (LLM sometimes emits multiples)
            reply = re.sub(r'<send_photo(?:[:=][^>]+)?>', '', reply).strip()
            # Deduplicate if the LLM repeated itself around the tags
            reply = _deduplicate_reply(reply)
            
            img_data = await select_local_image(requested_tag)
            # If the specific tag fails, gracefully fallback to ANY available image
            if not img_data and requested_tag:
                img_data = await select_local_image(None)
                
            if img_data:
                file_path = f"./gallery/{img_data['filename']}"
                if os.path.exists(file_path):
                    img_file_path = file_path
                    # Update the database to prevent sending this image again soon
                    db = await get_db()
                    await db.execute(
                        "UPDATE image_pool SET last_sent_at = ?, use_count = use_count + 1 WHERE filename = ?",
                        (int(time.time()), img_data['filename'])
                    )
                    await db.commit()
                    
        reply = trim_to_length(reply, character.get("max_chars", 150))
        
        if not reply and not img_file_path:
            log.warning("Reply was entirely contained within <think> block or stripped. Dropping message.")
            return

        typing_duration = random.uniform(1.0, 3.5)
        await asyncio.sleep(typing_duration)

    # ── Phase 4: Deliver ────────────────────────────────────────────────────
    history_reply = reply
    if img_file_path and img_data:
        history_reply = f"[Sent a photo of: {img_data['description']}] {reply}".strip()
        
    channel_histories[channel_id].append({"role": "assistant", "content": history_reply})
    log.info(f"[#{message.channel.name}] {character['name']}: {reply[:80]} (Total Tokens: {total_tokens})")

    await send_as_character(
        channel=message.channel,
        text=reply if reply else "✨",
        character=character,
        fallback=message,
        file_path=img_file_path,
    )




# ── Bot Initialisation ─────────────────────────────────────────────────────────

intents                 = discord.Intents.default()
intents.message_content = True   # Privileged intent — must be enabled in the Dev Portal

bot = commands.Bot(command_prefix="!char ", intents=intents)

async def terminal_listener():
    loop = asyncio.get_running_loop()
    print("\n[Terminal] Listening for commands. Type 'update' to send a photo to the last active channel.")
    while True:
        try:
            line = await loop.run_in_executor(None, sys.stdin.readline)
            if not line:
                break
            line = line.strip().lower()
            if line == "forget":
                if not channel_last_activity:
                    print("[Terminal] No active channels found.")
                    continue
                channel_id = max(channel_last_activity, key=channel_last_activity.get)
                channel_histories.pop(channel_id, None)
                channel = bot.get_channel(channel_id)
                print(f"[Terminal] 🧠 Wiped memory for #{channel.name if channel else channel_id}.")
                
            elif line == "reload":
                try:
                    character = load_character()
                    print(f"[Terminal] 🔄 Reloaded configuration for {character['name']}.")
                except Exception as e:
                    print(f"[Terminal] ❌ Failed to reload: {e}")
                    
            elif line == "status":
                character = load_character()
                print(f"[Terminal] 📊 Status: Running as {character['name']} | Model: {character['model']} | Local: {character.get('run_local', False)}")
                
            elif line == "thought":
                if not channel_last_activity:
                    print("[Terminal] No active channels found.")
                    continue
                channel_id = max(channel_last_activity, key=channel_last_activity.get)
                thought = channel_last_thoughts.get(channel_id, "No recent thoughts recorded.")
                channel = bot.get_channel(channel_id)
                print(f"[Terminal] 💭 Last thought for #{channel.name if channel else channel_id}:")
                print(thought)
                
            elif line.startswith("checkmeta"):
                parts = line.split()
                if len(parts) < 2:
                    print("[Terminal] Usage: checkmeta <filename>")
                    continue
                filename = parts[1]
                db = await get_db()
                async with db.execute("SELECT use_count, description, tags, last_sent_at FROM image_pool WHERE filename = ?", (filename,)) as cursor:
                    row = await cursor.fetchone()
                
                if not row:
                    print(f"[Terminal] ❌ Image '{filename}' not found in database.")
                else:
                    use_count, desc, tags, last_sent = row
                    sent_str = "Never" if last_sent == 0 else time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last_sent))
                    print(f"[Terminal] 🖼️ Metadata for {filename}:")
                    print(f"Tags: {tags}")
                    print(f"Uses: {use_count}")
                    print(f"Sent: {sent_str}")
                    print(f"Desc: {desc}")
                    
            elif line == "update":
                if not channel_last_activity:
                    print("[Terminal] No active channels found.")
                    continue
                channel_id = max(channel_last_activity, key=channel_last_activity.get)
                channel = bot.get_channel(channel_id)
                if not channel:
                    print(f"[Terminal] ❌ Could not find channel {channel_id}.")
                    continue
                
                img_data = await select_local_image(None)
                if not img_data:
                    print("[Terminal] ❌ No fresh images available.")
                    continue
                
                filename = img_data["filename"]
                description = img_data["description"]
                tags = img_data["tags"]
                file_path = f"./gallery/{filename}"
                
                if not os.path.exists(file_path):
                    db = await get_db()
                    await db.execute("DELETE FROM image_pool WHERE filename = ?", (filename,))
                    await db.commit()
                    print(f"[Terminal] ❌ Ghost file '{filename}' removed from DB. Try again.")
                    continue
                
                character = load_character()
                mood_prompt = await build_mood_aware_prompt(channel_id, character["system_prompt"])
                msgs = [
                    {"role": "system", "content": mood_prompt},
                    {
                        "role": "user",
                        "content": f"You are about to share a photo in the chat. It is described as: '{description}'. The image tags are: {tags}. Write a short, natural first-person message (1-2 sentences) explaining why you are sharing this picture right now."
                    }
                ]
                
                caption, _ = await query_openrouter(msgs, character)
                if not caption:
                    caption = "Thought I'd share this. ✨"
                caption = strip_action_text(caption)
                caption = trim_to_length(caption, 150)
                
                try:
                    webhook = await get_or_create_webhook(channel, character["name"])
                    with open(file_path, "rb") as f:
                        file = discord.File(f, filename=filename)
                        if webhook:
                            await webhook.send(
                                content=caption,
                                file=file,
                                username=character["name"],
                                avatar_url=character.get("avatar_url")
                            )
                        else:
                            await channel.send(content=caption, file=file)
                    
                    db = await get_db()
                    await db.execute(
                        "UPDATE image_pool SET last_sent_at = ?, use_count = use_count + 1 WHERE filename = ?",
                        (int(time.time()), filename)
                    )
                    await db.commit()
                    channel_histories.setdefault(channel_id, []).append({"role": "assistant", "content": f"[Sent a photo of: {description}] {caption}"})
                    print(f"[Terminal] ✅ Sent '{filename}' to #{channel.name} with caption: {caption[:60]}...")
                except Exception as e:
                    print(f"[Terminal] ❌ Failed to send update: {e}")
                    
        except Exception as e:
            print(f"[Terminal] Listener error: {e}")


async def setup_hook():
    bot.loop.create_task(terminal_listener())
    await bot.tree.sync()
    log.info("Slash commands synced globally.")

bot.setup_hook = setup_hook



# ── Events ─────────────────────────────────────────────────────────────────────

async def proactive_messaging_loop():
    """Periodically check channels for inactivity and send spontaneous check-ins."""
    await bot.wait_until_ready()
    
    # Pre-populate tracker if we know which channels to listen to
    character = load_character()
    listen_channels = character.get("listen_channels", [])
    proactive_cfg = character.get("proactive_messaging", {})
    for cid in listen_channels:
        if cid not in channel_last_activity:
            channel_last_activity[cid] = time.time()
            channel_idle_target[cid] = random.uniform(proactive_cfg.get("idle_hours_min", 2), proactive_cfg.get("idle_hours_max", 8))

    while not bot.is_closed():
        try:
            character = load_character()
            proactive_cfg = character.get("proactive_messaging", {})
            
            if proactive_cfg.get("enabled", False):
                idle_min = proactive_cfg.get("idle_hours_min", 2)
                idle_max = proactive_cfg.get("idle_hours_max", 8)
                photo_chance = proactive_cfg.get("photo_chance", 0.3)
                now = time.time()
                
                for channel_id, last_active in list(channel_last_activity.items()):
                    if channel_id not in channel_idle_target:
                        channel_idle_target[channel_id] = random.uniform(idle_min, idle_max)
                    target_hours = channel_idle_target[channel_id]
                    
                    if (now - last_active) >= target_hours * 3600:
                        if not channel_has_unanswered_proactive.get(channel_id, False):
                            channel = bot.get_channel(channel_id)
                            if not channel:
                                continue
                                
                            # Mark as triggered to avoid spamming
                            channel_has_unanswered_proactive[channel_id] = True
                            channel_last_activity[channel_id] = now
                            
                            log.info(f"[PROACTIVE] Triggering for #{channel.name}")
                            
                            # Photo Check-in
                            if random.random() < photo_chance:
                                img_data = await select_local_image(None)
                                if img_data:
                                    filename, desc, tags = img_data["filename"], img_data["description"], img_data["tags"]
                                    file_path = f"./gallery/{filename}"
                                    if os.path.exists(file_path):
                                        prompt = f"The chat has been quiet for a while. You are reaching out randomly. You are sharing a photo described as: '{desc}'. Tags: {tags}. Write a short, natural 1-sentence caption."
                                        msgs = await build_messages(channel_id, "System", prompt, character)
                                        async with channel.typing():
                                            caption, _ = await query_openrouter(msgs, character)
                                        if not caption: caption = "Thought of you."
                                        caption = trim_to_length(strip_action_text(caption), 150)
                                        
                                        webhook = await get_or_create_webhook(channel, character["name"])
                                        if webhook:
                                            await webhook.send(content=caption, file=discord.File(file_path), username=character["name"], avatar_url=character.get("avatar_url"))
                                        else:
                                            await channel.send(content=caption, file=discord.File(file_path))
                                            
                                        db = await get_db()
                                        await db.execute("UPDATE image_pool SET last_sent_at = ?, use_count = use_count + 1 WHERE filename = ?", (int(time.time()), filename))
                                        await db.commit()
                                        channel_histories.setdefault(channel_id, []).append({"role": "assistant", "content": f"[Sent a photo of: {desc}] {caption}"})
                                        continue
                                    else:
                                        db = await get_db()
                                        await db.execute("DELETE FROM image_pool WHERE filename = ?", (filename,))
                                        await db.commit()
                                        log.warning(f"[PROACTIVE] Ghost file '{filename}' removed from DB.")
                                        continue
                                        
                            # Text Check-in (fallback or chosen)
                            prompt = "The chat has been quiet for a while. Reach out to the user with a single, casual, low-effort sentence to check in. Do not say you are an AI."
                            msgs = await build_messages(channel_id, "System", prompt, character)
                            async with channel.typing():
                                reply, _ = await query_openrouter(msgs, character)
                            if reply:
                                if '</think>' in reply:
                                    channel_last_thoughts[channel_id] = reply.split('</think>', 1)[0].replace('<think>', '').strip()
                                reply = trim_to_length(strip_action_text(reply), 150)
                                if not reply:
                                    log.warning("Proactive text was fully enclosed in <think> block. Dropping.")
                                    continue
                                channel_histories.setdefault(channel_id, []).append({"role": "assistant", "content": reply})
                                await send_as_character(channel, reply, character, None)
                                
        except Exception as e:
            log.exception(f"Proactive loop error:")
            
        await asyncio.sleep(30)  # Check every 30 seconds for responsiveness


@bot.event
async def on_ready():
    char = load_character()
    if char.get("run_local", False):
        ensure_ollama_running()

    await init_db()                           # Phase 0 — SQLite foundation
    await seed_gallery()                       # Phase 1 — Anti-Repetition Media Engine
    bot.loop.create_task(proactive_messaging_loop()) # Phase 2 — Proactive background task
    log.info(f"✅  Logged in as {bot.user} (ID: {bot.user.id})")
    log.info("─" * 40)


@bot.event
async def on_message(message: discord.Message):

    character  = load_character()
    channel_id = message.channel.id

    # ── Ignore all bots and webhooks ───────────────────────────────────────────
    if message.author.bot:
        return

    # ── Tracking for Proactive Messaging ─────────────────────────────────────────
    channel_last_activity[channel_id] = time.time()
    proactive_cfg = character.get("proactive_messaging", {})
    channel_idle_target[channel_id] = random.uniform(proactive_cfg.get("idle_hours_min", 2), proactive_cfg.get("idle_hours_max", 8))
    
    if message.author.id != bot.user.id:
        channel_has_unanswered_proactive[channel_id] = False

    if not isinstance(message.channel, discord.TextChannel):
        return

    # ── Block A: Log human message to DB (Feature 3) ───────────────────────────
    try:
        db = await get_db()
        await db.execute(
            """INSERT INTO chat_logs
               (channel_id, author_id, username, message_content, timestamp)
               VALUES (?, ?, ?, ?, ?)""",
            (
                channel_id,
                message.author.id,
                str(message.author.display_name),
                message.content,
                int(time.time())
            )
        )
        await db.commit()
    except Exception as e:
        log.warning(f"Chat log write failed (non-fatal): {e}")
    # ── End Block A ────────────────────────────────────────────────────────────

    await bot.process_commands(message)

    ctx = await bot.get_context(message)
    if ctx.valid:
        return  # was a bot command — don't also reply as character

    listen_channels: list[int] = character.get("listen_channels", [])
    if listen_channels and channel_id not in listen_channels:
        return

    # ── Block B: Build mood-aware prompt if enabled (Feature 3) ────────────────
    use_mood_context = character.get("mood_context", True)
    if use_mood_context:
        enriched_prompt = await build_mood_aware_prompt(
            channel_id, character["system_prompt"]
        )
        msgs = await build_messages(
            channel_id,
            message.author.display_name,
            message.content,
            character,
            system_prompt_override=enriched_prompt,
        )
    else:
        msgs = await build_messages(
            channel_id, message.author.display_name, message.content, character
        )
    # ── End Block B ────────────────────────────────────────────────────────────

    # ── Block C: Trigger Background Memory Extraction ──────────────────────────
    if character.get("memory_extraction", True):
        channel_msg_counts[channel_id] = channel_msg_counts.get(channel_id, 0) + 1
        if channel_msg_counts[channel_id] % 5 == 0:
            history = channel_histories.get(channel_id, [])
            if history:
                asyncio.create_task(extract_memories_bg(channel_id, character, history))

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


@bot.command(name="thought")
async def show_thought(ctx: commands.Context):
    """Peek into the LLM's internal reasoning for its last message."""
    thought = channel_last_thoughts.get(ctx.channel.id)
    if not thought:
        await ctx.send("I don't have any recent thoughts saved for this channel. 💭")
        return
        
    # Discord has a 2000 char limit; truncate if necessary
    if len(thought) > 1900:
        thought = thought[:1900] + "... (truncated)"
        
    await ctx.send(f"**🧠 Last internal thought:**\n```text\n{thought}\n```")


@bot.command(name="checkmeta")
async def check_metadata(ctx: commands.Context, filename: str = None):
    """Check the stored metadata for an image to verify JSON updates."""
    db = await get_db()
    if filename:
        cursor = await db.execute("SELECT filename, description, tags, use_count FROM image_pool WHERE filename LIKE ?", (f"%{filename}%",))
    else:
        cursor = await db.execute("SELECT filename, description, tags, use_count FROM image_pool ORDER BY RANDOM() LIMIT 1")
        
    row = await cursor.fetchone()

    if not row:
        await ctx.send(f"Could not find any metadata for `{filename}`. 📂" if filename else "No images found in the database. 📂")
        return

    fname, desc, tags, count = row
    
    # Truncate description to fit within Discord limits if somehow massive
    if len(desc) > 1500:
        desc = desc[:1500] + "..."
        
    response = (
        f"**File:** `{fname}`\n"
        f"**Tags:** `{tags}`\n"
        f"**Send Count:** `{count}`\n"
        f"**Description:**\n> {desc}"
    )
    await ctx.send(response)


@bot.command(name="postlife")
async def post_life(ctx: commands.Context, tag: str = None):
    """Post a non-repeating local gallery image with a dynamically generated reason."""
    img_data = await select_local_image(tag)

    if not img_data:
        msg = f"No fresh images tagged `{tag}` right now. 🌱" if tag else "No fresh images available right now. 🌱"
        await ctx.send(msg)
        return

    filename, description, tags = img_data["filename"], img_data["description"], img_data["tags"]
    file_path = f"./gallery/{filename}"
    
    if not os.path.exists(file_path):
        db = await get_db()
        await db.execute("DELETE FROM image_pool WHERE filename = ?", (filename,))
        await db.commit()
        await ctx.send(f"Found a ghost image `{filename}` that was deleted from disk. Cleaned it from the database! Please run the command again.")
        log.warning(f"postlife: file not found on disk, removed from DB: {file_path}")
        return

    character = load_character()

    # Generate the human-like reason for posting using the text LLM
    async with ctx.channel.typing():
        mood_prompt = await build_mood_aware_prompt(ctx.channel.id, character["system_prompt"])
        msgs = [
            {"role": "system", "content": mood_prompt},
            {
                "role": "user", 
                "content": f"You are about to share a photo in the chat. It is described as: '{description}'. The image tags are: {tags}. Write a short, natural first-person message (1-2 sentences) explaining why you are sharing this picture right now."
            }
        ]
        
        caption, _ = await query_openrouter(msgs, character)
        if not caption:
            caption = "Thought I'd share this. ✨"
            
        caption = strip_action_text(caption)
        caption = trim_to_length(caption, 150)

    try:
        webhook = await get_or_create_webhook(ctx.channel, character["name"])
        if webhook:
            await webhook.send(
                content=caption,
                file=discord.File(file_path),
                username=character["name"],
                avatar_url=character.get("avatar_url"),
            )
        else:
            await ctx.send(content=caption, file=discord.File(file_path))

        db = await get_db()
        await db.execute(
            "UPDATE image_pool SET last_sent_at = ?, use_count = use_count + 1 WHERE filename = ?",
            (int(time.time()), filename)
        )
        await db.commit()
        channel_histories.setdefault(ctx.channel.id, []).append({"role": "assistant", "content": f"[Sent a photo of: {description}] {caption}"})
        log.info(f"🖼️  Posted gallery image: {filename}")

    except Exception as e:
        await ctx.send("Couldn't send the image right now. 🌱")
        log.error(f"postlife error: {e}")


@bot.tree.command(name="update", description="Post a photo with a dynamically generated reason (imitating a life update).")
async def slash_update(interaction: discord.Interaction, tag: str = None):
    await interaction.response.defer()
    
    img_data = await select_local_image(tag)
    if not img_data:
        msg = f"No fresh images tagged `{tag}` right now. 🌱" if tag else "No fresh images available right now. 🌱"
        await interaction.followup.send(msg)
        return

    filename, description, tags = img_data["filename"], img_data["description"], img_data["tags"]
    file_path = f"./gallery/{filename}"
    
    if not os.path.exists(file_path):
        db = await get_db()
        await db.execute("DELETE FROM image_pool WHERE filename = ?", (filename,))
        await db.commit()
        await interaction.followup.send(f"Found a ghost image `{filename}` that was deleted from disk. Cleaned it from the database! Please run the command again.")
        log.warning(f"slash_update: file not found on disk, removed from DB: {file_path}")
        return

    character = load_character()

    # Generate the human-like reason for posting using the text LLM
    mood_prompt = await build_mood_aware_prompt(interaction.channel_id, character["system_prompt"])
    msgs = [
        {"role": "system", "content": mood_prompt},
        {
            "role": "user", 
            "content": f"You are about to share a photo in the chat. It is described as: '{description}'. The image tags are: {tags}. Write a short, natural first-person message (1-2 sentences) explaining why you are sharing this picture right now."
        }
    ]
    
    caption, _ = await query_openrouter(msgs, character)
    if not caption:
        caption = "Thought I'd share this. ✨"
        
    caption = strip_action_text(caption)
    caption = trim_to_length(caption, 150)

    try:
        webhook = await get_or_create_webhook(interaction.channel, character["name"])
        with open(file_path, "rb") as f:
            file = discord.File(f, filename=filename)
            if webhook:
                await webhook.send(
                    content=caption,
                    file=file,
                    username=character["name"],
                    avatar_url=character.get("avatar_url")
                )
            else:
                await interaction.channel.send(content=caption, file=file)
        
        db = await get_db()
        await db.execute(
            "UPDATE image_pool SET last_sent_at = ?, use_count = use_count + 1 WHERE filename = ?",
            (int(time.time()), filename)
        )
        await db.commit()
        channel_histories.setdefault(interaction.channel_id, []).append({"role": "assistant", "content": f"[Sent a photo of: {description}] {caption}"})
        log.info(f"🖼️  Posted gallery image via slash command: {filename}")
        
        await interaction.followup.send("Update posted!", ephemeral=True)
    except Exception as e:
        log.error(f"Failed to post image via slash command: {e}")
        await interaction.followup.send("Couldn't send the image right now. 🌱")


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
    char = load_character()
    if OPENROUTER_API_KEY == "dummy-local-key" and "openrouter.ai" in LLM_API_URL and not char.get("run_local"):
        log.critical(f"OPENROUTER_API_KEY not set in: {ENV_FILE}. (You can omit this if using a local LLM via LLM_API_URL or --local)")
        raise SystemExit(1)
    log.info(f"📂 Folder    : {CHARACTER_DIR}")
    log.info(f"🎭 Character : {char['name']}")
    log.info(f"🧠 Model     : {char.get('model', 'N/A')}")
    log.info(f"⚙️  Version   : v{VERSION}")

    bot.run(DISCORD_BOT_TOKEN, log_handler=None)
