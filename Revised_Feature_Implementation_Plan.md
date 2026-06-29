# Revised Feature Implementation Plan
## Discord Character Bot — Aesthetic Media Engine

**Target Repo:** `kirubiisfyne/discord-character-bot`  
**Base File:** `bot.py` (426 lines, single-file architecture)  
**Original Plan Audit & Revised:** June 2026

---

## What Changed From the Original Plan

| Original Assumption | Actual Reality in Repo | Fix Applied |
|---|---|---|
| Existing `get_db()` / SQLite foundation | Bot uses in-memory dicts only — no DB exists | Add SQLite layer as **Phase 0** before any feature |
| Separate `@bot.event async def on_message` for logging | Only one `on_message` handler can be active; a second one silently overrides the first and breaks all character logic | **Merge** the logger into the existing `on_message` |
| `google-generativeai` (Gemini) for image captions | Bot is entirely OpenRouter-based; adding a second AI provider splits the codebase | Use **OpenRouter vision model** instead — same API key, zero new config |
| `!postlife` / `!postidea` command prefix | Bot uses `!char ` as its prefix — bare `!postlife` would not be picked up | Use `!char postlife` and `!char postidea` |
| `build_mood_aware_prompt()` replaces the static prompt | `build_messages()` already constructs the system prompt with formatting rules injected | **Augment** `build_messages()` with an optional override parameter rather than replacing it |

---

## System Architecture

How the three features layer onto the existing single-file bot:

```
bot.py
│
├── Phase 0: SQLite Foundation  ← NEW, prerequisite for everything below
│   ├── get_db()                → returns a shared aiosqlite.Connection
│   └── init_db()               → creates all three feature tables on startup
│
├── Feature 1: Anti-Repetition Media Engine
│   ├── DB table: image_pool
│   ├── seed_gallery()          → one-time safe seeder, runs on startup
│   ├── select_local_image()    → picks least-used image with 24h cooldown
│   └── !char postlife [tag]    → sends image, updates DB record
│
├── Feature 3: LLM Mood & Context Alignment
│   ├── DB table: chat_logs
│   ├── on_message (MERGED)     → logs every human message early in the handler
│   ├── get_channel_context()   → pulls last N raw messages from DB
│   ├── build_mood_aware_prompt()→ wraps base system_prompt with mood context
│   └── build_messages() (MODIFIED) → accepts optional system_prompt_override
│
└── Feature 2: Pinterest Pipeline
    ├── DB table: procedural_pins
    ├── fetch_pins_serpapi()    → live pin fetching via SerpApi
    ├── sync_pins_to_db()       → called on startup, inserts new pins
    ├── select_procedural_pin() → picks least-used pin with 24h cooldown
    ├── download_image_bytes()  → async download via aiohttp (already installed)
    ├── generate_caption_via_openrouter() → vision model caption, NO Gemini key needed
    └── !char postidea          → full pipeline: fetch → caption → send via webhook
```

### Full Data Flow Diagram

```
Discord Message arrives
        │
        ▼
on_message() ──► [EARLY] Log to chat_logs DB        ← Feature 3 addition
        │
        ├── message.author.bot?
        │       └── handle bot-to-bot logic (unchanged)
        │
        ├── await bot.process_commands(message)
        │         │
        │    ┌────┴────────────────────────┐
        │    │                             │
        │  !char postlife [tag]        !char postidea
        │    │                             │
        │  select_local_image()     select_procedural_pin()
        │    │                             │
        │  send discord.File()      download_image_bytes()
        │                                  │
        │                    generate_caption_via_openrouter()
        │                         (OpenRouter vision model)
        │                                  │
        │                      send caption + image via webhook
        │
        └── ctx.valid? → skip (was a command)
                │
                └── Is human chat?
                        │
                    build_mood_aware_prompt()       ← Feature 3
                        │
                    get_channel_context() from DB
                        │
                    build_messages() with enriched prompt
                        │
                    query_openrouter()  (unchanged)
                        │
                    strip_action_text() (unchanged)
                        │
                    send_as_character() via webhook (unchanged)
```

---

## Phase 0: SQLite Foundation

This phase is a hard prerequisite. Features 1, 2, and 3 all require it. Do this first and verify the bot still starts before touching anything else.

### Step 0.1 — Update requirements.txt

```
# Existing (do not remove)
discord.py>=2.0
aiohttp
python-dotenv

# Add for all three features
aiosqlite

# Add for Feature 2 — SerpApi uses aiohttp (already present), no new package needed
# If you choose Playwright instead (advanced, may violate Pinterest ToS):
# playwright
```

Install:

```bash
pip install aiosqlite
```

### Step 0.2 — Add get_db() and init_db() to bot.py

Add these imports at the very top of `bot.py` alongside the existing ones:

```python
import aiosqlite
import time
import base64
from io import BytesIO
```

Then add the following block right after the in-memory state dictionaries (around line 70, after `channel_bot_chains`):

```python
# ── Database ───────────────────────────────────────────────────────────────────

DB_PATH = "bot_data.db"
_db_connection: aiosqlite.Connection | None = None

async def get_db() -> aiosqlite.Connection:
    """Return a shared, persistent aiosqlite connection (created once per process)."""
    global _db_connection
    if _db_connection is None:
        _db_connection = await aiosqlite.connect(DB_PATH)
        _db_connection.row_factory = aiosqlite.Row  # enables dict-like row["column"] access
    return _db_connection


async def init_db():
    """Create all feature tables if they don't already exist. Safe to call on every startup."""
    db = await get_db()
    await db.executescript("""
        -- Feature 1: local image pool with repeat-prevention
        CREATE TABLE IF NOT EXISTS image_pool (
            filename     TEXT PRIMARY KEY,
            description  TEXT,
            tags         TEXT,
            last_sent_at INTEGER DEFAULT 0,
            use_count    INTEGER DEFAULT 0
        );

        -- Feature 3: full chat message log for mood analysis
        CREATE TABLE IF NOT EXISTS chat_logs (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id      INTEGER,
            author_id       INTEGER,
            username        TEXT,
            message_content TEXT,
            timestamp       INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_chat_channel
            ON chat_logs(channel_id, id DESC);

        -- Feature 2: Pinterest pin pool with repeat-prevention
        CREATE TABLE IF NOT EXISTS procedural_pins (
            pin_id       TEXT PRIMARY KEY,
            image_url    TEXT,
            last_sent_at INTEGER DEFAULT 0,
            use_count    INTEGER DEFAULT 0
        );
    """)
    await db.commit()
    log.info("✅ Database initialized — all tables ready.")
```

### Step 0.3 — Call init_db() in the existing on_ready()

Find the existing `on_ready` event and add two lines:

```python
# BEFORE:
@bot.event
async def on_ready():
    log.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    log.info("─" * 40)

# AFTER:
@bot.event
async def on_ready():
    await init_db()          # ← Phase 0
    await seed_gallery()     # ← Feature 1 (safe to add now; function defined later)
    board = os.getenv("PINTEREST_BOARD")
    if board:
        await sync_pins_to_db(board)   # ← Feature 2 (safe to add now; function defined later)
    log.info(f"✅ Logged in as {bot.user} (ID: {bot.user.id})")
    log.info("─" * 40)
```

### Phase 0 verification

Start the bot. You should see `✅ Database initialized — all tables ready.` in the logs and a `bot_data.db` file should appear in the project root. The bot should otherwise behave identically to before.

---

## Feature 1: Anti-Repetition Media Engine

**Goal:** Post curated local images without repeating within 24 hours. Cooldown survives bot restarts because state lives in SQLite, not in-memory.

**Command:** `!char postlife [tag]`

### Step 1.1 — Create the gallery folder

```bash
mkdir gallery
# Add 5–10 images: .png, .jpg, or .jpeg
# Name them descriptively — the seeder auto-tags from the filename
# Example: morning_coffee.jpg, forest_path.png, city_night.jpg
```

### Step 1.2 — Add seed_gallery()

Add this function in the database section you created in Phase 0:

```python
async def seed_gallery():
    """
    Scan ./gallery/ and insert any new images into image_pool.
    Uses INSERT OR IGNORE so it is safe to call on every startup — no duplicates.
    Auto-tags images based on keywords found in the filename.
    """
    if not os.path.isdir("./gallery"):
        log.warning("./gallery/ folder not found — skipping seed.")
        return

    db = await get_db()
    tag_keywords = ["coffee", "morning", "nature", "calm", "night", "city", "food", "travel", "rain", "books"]
    files = [f for f in os.listdir("./gallery") if f.lower().endswith((".png", ".jpg", ".jpeg"))]

    for f in files:
        detected_tags = [t for t in tag_keywords if t in f.lower()]
        tags = ",".join(detected_tags) if detected_tags else "general"
        await db.execute(
            "INSERT OR IGNORE INTO image_pool (filename, description, tags) VALUES (?, ?, ?)",
            (f, f"Gallery image: {f}", tags)
        )

    await db.commit()
    log.info(f"🖼️  Gallery seeded — {len(files)} image(s) registered.")
```

### Step 1.3 — Add select_local_image()

```python
async def select_local_image(tag: str = None) -> str | None:
    """
    Return a filename not sent in the last 24 hours, prioritising the least-used image.
    If a tag is given, only images whose tags column contains that tag are considered.
    Returns None when the pool is exhausted (all images are in cooldown).
    """
    db = await get_db()
    cutoff = int(time.time()) - 86400  # 24 hours ago

    if tag:
        cursor = await db.execute(
            """SELECT filename FROM image_pool
               WHERE last_sent_at < ? AND tags LIKE ?
               ORDER BY use_count ASC LIMIT 1""",
            (cutoff, f"%{tag}%")
        )
    else:
        cursor = await db.execute(
            """SELECT filename FROM image_pool
               WHERE last_sent_at < ?
               ORDER BY use_count ASC LIMIT 1""",
            (cutoff,)
        )

    row = await cursor.fetchone()
    return row["filename"] if row else None
```

### Step 1.4 — Add the !char postlife command

Add this block after your existing `@bot.command(name="status")` command:

```python
@bot.command(name="postlife")
async def post_life(ctx: commands.Context, tag: str = None):
    """Post a non-repeating local gallery image. Usage: !char postlife [tag]"""
    filename = await select_local_image(tag)

    if not filename:
        await ctx.send("No fresh images available right now. 🌱")
        return

    file_path = f"./gallery/{filename}"
    if not os.path.exists(file_path):
        await ctx.send(f"Image `{filename}` is registered but missing from disk.")
        log.warning(f"postlife: file not found on disk: {file_path}")
        return

    try:
        character = load_character()
        webhook = await get_or_create_webhook(ctx.channel, character["name"])

        if webhook:
            await webhook.send(
                file=discord.File(file_path),
                username=character["name"],
                avatar_url=character.get("avatar_url")
            )
        else:
            await ctx.send(file=discord.File(file_path))

        db = await get_db()
        await db.execute(
            "UPDATE image_pool SET last_sent_at = ?, use_count = use_count + 1 WHERE filename = ?",
            (int(time.time()), filename)
        )
        await db.commit()
        log.info(f"🖼️  Posted gallery image: {filename}")

    except Exception as e:
        await ctx.send("Couldn't send the image right now. 🌱")
        log.error(f"postlife error: {e}")
```

### Step 1.5 — Testing checklist

- `!char postlife` several times → each call should post a different image.
- `!char postlife coffee` → should only return images tagged `coffee`.
- Restart the bot, run `!char postlife` → 24h cooldown should still be active (persisted in DB).
- When all images are in cooldown: should receive the "No fresh images" message.
- Verify in DB: `sqlite3 bot_data.db "SELECT filename, use_count, last_sent_at FROM image_pool;"`

---

## Feature 3: LLM Mood & Context Alignment

**Goal:** Log every human message to the database. When the character generates a reply, analyse the last 10 messages to detect the room's current mood, and inject that context into the system prompt so the character's tone adapts naturally.

> ⚠️ **Critical:** You must **edit** the existing `on_message` function. Do **not** write a second `@bot.event async def on_message` — Discord.py will silently use only the last one defined, breaking all character response logic.

### Step 3.1 — Add get_channel_context()

Add this to the database section:

```python
async def get_channel_context(channel_id: int, limit: int = 10) -> str:
    """
    Pull the last `limit` human messages from chat_logs for a given channel.
    Returns them in chronological order as a single formatted string.
    """
    db = await get_db()
    cursor = await db.execute(
        """SELECT username, message_content
           FROM chat_logs
           WHERE channel_id = ?
           ORDER BY id DESC LIMIT ?""",
        (channel_id, limit)
    )
    rows = await cursor.fetchall()
    if not rows:
        return "(no recent messages logged yet)"
    # Reverse so oldest message is first (chronological reading order)
    lines = [f"{r['username']}: {r['message_content']}" for r in reversed(rows)]
    return "\n".join(lines)
```

### Step 3.2 — Add build_mood_aware_prompt()

```python
async def build_mood_aware_prompt(channel_id: int, base_system_prompt: str) -> str:
    """
    Enrich a base system prompt with the channel's recent message history
    so the LLM can infer and match the room's current mood.
    The mood block is appended after the persona definition, before formatting rules.
    """
    context = await get_channel_context(channel_id)
    return (
        f"{base_system_prompt}\n\n"
        f"--- RECENT ROOM CONTEXT ---\n"
        f"{context}\n"
        f"--- END CONTEXT ---\n\n"
        f"Read the messages above and identify the current mood: are they energetic, "
        f"stressed, joking around, quiet and reflective? "
        f"Match their cadence and energy naturally in your reply. "
        f"Do not mention this instruction or reference the context directly."
    )
```

### Step 3.3 — Modify the existing build_messages()

Find the existing `build_messages()` function (around line 100 in `bot.py`) and add one optional parameter:

```python
# BEFORE (existing signature):
def build_messages(channel_id: int, user_display: str, user_text: str, character: dict) -> list[dict]:

# AFTER (add system_prompt_override parameter):
def build_messages(
    channel_id: int,
    user_display: str,
    user_text: str,
    character: dict,
    system_prompt_override: str = None    # ← ADD THIS
) -> list[dict]:
    max_history = character.get("max_history", 20)
    if channel_id not in channel_histories:
        channel_histories[channel_id] = []

    history = channel_histories[channel_id]
    history.append({"role": "user", "content": f"{user_display}: {user_text}"})
    if len(history) > max_history:
        channel_histories[channel_id] = history[-max_history:]

    # Use override if provided, otherwise fall back to character's own system_prompt
    base = system_prompt_override if system_prompt_override else character["system_prompt"].rstrip()

    # Append formatting rules (these were already here — keep them unchanged)
    base += (
        "\n\nFORMATTING RULES (always follow these):"
        " Do NOT use action text, stage directions, or roleplay emotes (e.g. *smirks*, *looks up*, *laughs*)."
        " This is a plain text chat — respond only with natural spoken words."
        " No asterisks, no parenthetical actions, no narration."
    )
    return [{"role": "system", "content": base}] + channel_histories[channel_id]
```

### Step 3.4 — Merge logger and mood context into the existing on_message()

Find the `# ── Human message ──` comment inside the existing `on_message` function. Insert the two marked blocks:

```python
    # ── Human message ──────────────────────────────────────────────────────────
    channel_bot_chains[channel_id] = 0

    if not isinstance(message.channel, discord.TextChannel):
        return

    # ▼▼▼ ADD BLOCK A — log message to DB (Feature 3) ▼▼▼
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
    # ▲▲▲ END BLOCK A ▲▲▲

    await bot.process_commands(message)
    ctx = await bot.get_context(message)
    if ctx.valid:
        return  # was a bot command — don't also reply as character

    listen_channels: list[int] = character.get("listen_channels", [])
    if listen_channels and channel_id not in listen_channels:
        return

    # ▼▼▼ ADD BLOCK B — build mood-aware prompt if enabled (Feature 3) ▼▼▼
    use_mood_context = character.get("mood_context", True)
    if use_mood_context:
        enriched_prompt = await build_mood_aware_prompt(
            channel_id, character["system_prompt"]
        )
        msgs = build_messages(
            channel_id,
            message.author.display_name,
            message.content,
            character,
            system_prompt_override=enriched_prompt   # ← uses new parameter
        )
    else:
        msgs = build_messages(
            channel_id, message.author.display_name, message.content, character
        )
    # ▲▲▲ END BLOCK B — replaces the original single build_messages() call ▲▲▲

    # Everything below this line stays unchanged
    log.info(f"[#{message.channel.name}] {message.author.display_name}: {message.content[:80]}")
    # ... (reply_delay, typing indicator, query_openrouter, etc.)
```

### Step 3.5 — Optional: add mood_context toggle to character.json

This lets you enable or disable mood injection per character without touching code:

```json
{
  "name": "Alex",
  "mood_context": true
}
```

Set to `false` to have a character always use its static system prompt regardless of room energy.

### Step 3.6 — Testing checklist

- Chat several messages in different tones (excited, calm, stressed), then trigger a reply → character tone should shift.
- Verify logging: `sqlite3 bot_data.db "SELECT username, message_content FROM chat_logs LIMIT 10;"`
- Set `"mood_context": false`, run `!char reload`, observe that the character's tone stops adapting.
- Confirm that `!char forget` still clears in-memory history as before (DB logs are separate and intentionally persist).

---

## Feature 2: Procedural Pinterest Pipeline

**Goal:** Pull live images from a Pinterest board via SerpApi, generate mood-aware AI captions using the existing OpenRouter account (no Gemini key needed), and post them without repeats.

**Command:** `!char postidea`

> ⚠️ **Original plan used `google-generativeai` (Gemini). This is replaced by an OpenRouter vision model so the entire bot uses one API key and one API pattern.**

### Step 2.1 — Add vision_model to character.json

```json
{
  "name": "Alex",
  "vision_model": "google/gemini-2.0-flash-exp:free",
  "mood_context": true
}
```

If `vision_model` is not set, the caption generator defaults to `google/gemini-2.0-flash-exp:free`. Other OpenRouter vision options include `meta-llama/llama-3.2-90b-vision-instruct` (paid).

### Step 2.2 — Add SERPAPI_KEY and PINTEREST_BOARD to .env

Edit `characters/yourcharacter/.env`:

```env
DISCORD_BOT_TOKEN=your_discord_token
OPENROUTER_API_KEY=your_openrouter_key
SERPAPI_KEY=your_serpapi_key
PINTEREST_BOARD=aesthetic-morning-mood
```

Then load the new variables near the top of `bot.py`, alongside the existing `os.getenv()` calls:

```python
SERPAPI_KEY = os.getenv("SERPAPI_KEY")
PINTEREST_BOARD = os.getenv("PINTEREST_BOARD")
```

### Step 2.3 — Add fetch_pins_serpapi()

```python
async def fetch_pins_serpapi(board_name: str, count: int = 10) -> list[dict]:
    """
    Fetch pins from a Pinterest board via SerpApi.
    Returns a list of {pin_id, image_url} dicts.
    Requires SERPAPI_KEY in environment.
    """
    if not SERPAPI_KEY:
        log.error("fetch_pins_serpapi: SERPAPI_KEY is not set.")
        return []

    params = {
        "engine": "pinterest",
        "pinterest_board": board_name,
        "api_key": SERPAPI_KEY,
        "num": count
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                "https://serpapi.com/search.json",
                params=params,
                timeout=aiohttp.ClientTimeout(total=30)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error(f"SerpApi error {resp.status}: {body[:200]}")
                    return []

                data = await resp.json()
                pins = []
                for result in data.get("pins", []):
                    pin_id = result.get("id")
                    img = result.get("images", {}).get("orig", {}).get("url")
                    if pin_id and img:
                        pins.append({"pin_id": str(pin_id), "image_url": img})
                log.info(f"📌 SerpApi returned {len(pins)} pins for board '{board_name}'.")
                return pins

    except Exception as e:
        log.error(f"fetch_pins_serpapi exception: {e}")
        return []
```

### Step 2.4 — Add sync_pins_to_db()

```python
async def sync_pins_to_db(board_identifier: str):
    """
    Fetch fresh pins from SerpApi and insert any new ones into procedural_pins.
    Uses INSERT OR IGNORE so existing pins are never overwritten.
    Called from on_ready() — runs once per startup.
    """
    pins = await fetch_pins_serpapi(board_identifier)
    if not pins:
        log.warning("sync_pins_to_db: no pins returned — check SERPAPI_KEY and PINTEREST_BOARD.")
        return

    db = await get_db()
    for pin in pins:
        await db.execute(
            "INSERT OR IGNORE INTO procedural_pins (pin_id, image_url) VALUES (?, ?)",
            (pin["pin_id"], pin["image_url"])
        )
    await db.commit()
    log.info(f"📌 Pin sync complete — {len(pins)} pins processed.")
```

### Step 2.5 — Add select_procedural_pin()

```python
async def select_procedural_pin():
    """
    Return the least-used pin not sent in the last 24 hours.
    Returns an aiosqlite.Row with pin_id and image_url, or None if pool is dry.
    """
    db = await get_db()
    cutoff = int(time.time()) - 86400
    cursor = await db.execute(
        """SELECT pin_id, image_url FROM procedural_pins
           WHERE last_sent_at < ?
           ORDER BY use_count ASC LIMIT 1""",
        (cutoff,)
    )
    return await cursor.fetchone()
```

### Step 2.6 — Add download_image_bytes()

```python
async def download_image_bytes(url: str) -> bytes | None:
    """Download an image from a URL and return its raw bytes. Returns None on failure."""
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as resp:
                if resp.status == 200:
                    return await resp.read()
                log.error(f"Image download failed: HTTP {resp.status} for {url}")
                return None
    except Exception as e:
        log.error(f"download_image_bytes exception: {e}")
        return None
```

### Step 2.7 — Add generate_caption_via_openrouter()

This replaces the original Gemini-based caption function. It uses the same `OPENROUTER_API_KEY` and `OPENROUTER_URL` already defined in `bot.py`.

```python
async def generate_caption_via_openrouter(
    image_bytes: bytes,
    channel_id: int,
    character: dict
) -> str | None:
    """
    Generate a mood-aware, in-character caption for an image.
    Uses an OpenRouter vision model — no separate Gemini API key needed.
    Integrates Feature 3's mood context automatically.
    """
    # Build mood-enriched system prompt (Feature 3 integration)
    mood_prompt = await build_mood_aware_prompt(
        channel_id, character["system_prompt"]
    )
    caption_instruction = (
        "\n\nYou are looking at an image. Write a short first-person reaction "
        "(2–3 sentences) as your character. Be natural, match the chat energy, "
        "no asterisk action text."
    )

    image_b64 = base64.b64encode(image_bytes).decode("utf-8")
    vision_model = character.get("vision_model", "google/gemini-2.0-flash-exp:free")

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
        "HTTP-Referer": "https://github.com/your-username/discord-character-bot",
        "X-Title": f"{character['name']} Discord Bot",
    }
    payload = {
        "model": vision_model,
        "max_tokens": 200,
        "temperature": character.get("temperature", 0.85),
        "messages": [
            {
                "role": "system",
                "content": mood_prompt + caption_instruction
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}
                    },
                    {
                        "type": "text",
                        "text": "React to this image in character."
                    }
                ]
            }
        ]
    }

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                OPENROUTER_URL,
                headers=headers,
                json=payload,
                timeout=aiohttp.ClientTimeout(total=60)
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    log.error(f"Vision model {resp.status}: {body[:200]}")
                    return None
                data = await resp.json()
                return data["choices"][0]["message"]["content"].strip()
    except Exception as e:
        log.error(f"generate_caption_via_openrouter exception: {e}")
        return None
```

### Step 2.8 — Add the !char postidea command

Add this block after `!char postlife`:

```python
@bot.command(name="postidea")
async def post_idea(ctx: commands.Context):
    """Post a non-repeating Pinterest pin with a mood-aware AI caption. Usage: !char postidea"""
    character = load_character()

    pin = await select_procedural_pin()
    if not pin:
        await ctx.send("No fresh pins available right now. 📌 Try again tomorrow.")
        return

    async with ctx.channel.typing():
        image_bytes = await download_image_bytes(pin["image_url"])
        if not image_bytes:
            await ctx.send("Couldn't fetch that image. Try again in a moment.")
            return

        caption = await generate_caption_via_openrouter(image_bytes, ctx.channel.id, character)

        # Fallback caption if vision model fails
        if not caption:
            caption = "✨"
            log.warning("postidea: caption generation failed — using fallback.")

        caption = strip_action_text(caption)
        caption = trim_to_sentences(caption, character.get("max_sentences", 3))

    try:
        file = discord.File(BytesIO(image_bytes), filename="pin.jpg")
        webhook = await get_or_create_webhook(ctx.channel, character["name"])

        if webhook:
            await webhook.send(
                content=caption,
                file=file,
                username=character["name"],
                avatar_url=character.get("avatar_url")
            )
        else:
            await ctx.send(content=caption, file=file)

        db = await get_db()
        await db.execute(
            """UPDATE procedural_pins
               SET last_sent_at = ?, use_count = use_count + 1
               WHERE pin_id = ?""",
            (int(time.time()), pin["pin_id"])
        )
        await db.commit()
        log.info(f"📌 Posted pin {pin['pin_id']} with generated caption.")

    except Exception as e:
        await ctx.send("Something went wrong posting the idea. 🌿")
        log.error(f"postidea send error: {e}")
```

### Step 2.9 — Testing checklist

- `!char postidea` → should post an image with a character-voiced caption matching the current chat tone.
- Run it multiple times → no pin should repeat within 24h.
- Restart the bot → cooldown persists (SQLite, not memory).
- Caption should shift in energy between a jokey chat and a quiet one (Feature 3 integration).
- Check pins in DB: `sqlite3 bot_data.db "SELECT pin_id, use_count FROM procedural_pins;"`
- If captions fall back to `✨`, check that your `vision_model` is available on your OpenRouter plan.

---

## Final Integration Checklist

### Imports to add at top of bot.py

```python
import aiosqlite    # pip install aiosqlite
import time
import base64
from io import BytesIO
```

### New .env variables (per character folder)

```env
SERPAPI_KEY=your_serpapi_key
PINTEREST_BOARD=your-pinterest-board-name
```

### New character.json fields

```json
{
  "vision_model": "google/gemini-2.0-flash-exp:free",
  "mood_context": true
}
```

### New commands summary

| Command | Who Can Use | Description |
|---|---|---|
| `!char postlife` | Everyone | Post a non-repeating local gallery image |
| `!char postlife coffee` | Everyone | Post a gallery image tagged `coffee` |
| `!char postidea` | Everyone | Post a Pinterest pin with AI caption |

### Correct order of implementation

1. **Phase 0** — Imports, `get_db()`, `init_db()`, add to `on_ready()`. Start bot. Confirm DB file created.
2. **Feature 1** — `seed_gallery()`, `select_local_image()`, `!char postlife`. Test cooldown persists on restart.
3. **Feature 3** — `get_channel_context()`, `build_mood_aware_prompt()`, modify `build_messages()`, merge logger into `on_message`. Test tone adaptation.
4. **Feature 2** — SerpApi fetcher, `sync_pins_to_db()`, `select_procedural_pin()`, `download_image_bytes()`, `generate_caption_via_openrouter()`, `!char postidea`. Test full pipeline.

---

## Troubleshooting Reference

| Symptom | Likely Cause | Fix |
|---|---|---|
| Bot completely stops responding to messages | Duplicate `on_message` event defined | Remove the duplicate; only one `@bot.event async def on_message` should exist |
| `!char postlife` sends nothing | `./gallery/` folder empty or path wrong | Confirm folder exists relative to where you run `python bot.py` |
| `!char postlife` returns "No fresh images" immediately | Images in cooldown from a previous run | Manually reset: `sqlite3 bot_data.db "UPDATE image_pool SET last_sent_at=0;"` |
| Caption always falls back to ✨ | Vision model unavailable on free tier | Change `vision_model` to `google/gemini-2.0-flash-exp:free` in character.json |
| Pins not syncing on startup | `SERPAPI_KEY` or `PINTEREST_BOARD` missing from `.env` | Add both variables to the character's `.env` file |
| `!char postidea` says "No fresh pins" | Sync didn't run or board returned 0 pins | Check log for SerpApi errors; verify `PINTEREST_BOARD` value |
| Mood context not affecting replies | `mood_context: false` or `chat_logs` is empty | Set `"mood_context": true`, send a few messages, try again |
| `aiosqlite` ImportError | Package not installed | Run `pip install aiosqlite` in your virtual environment |
| `bot_data.db` not created | `init_db()` not called in `on_ready` | Verify Phase 0 Step 0.3 was applied correctly |

---

*Implementation order: Phase 0 → Feature 1 → Feature 3 → Feature 2*
