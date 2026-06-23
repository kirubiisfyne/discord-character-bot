<div align="center">

# 🎭 Discord Character Bot

**Run fully configurable AI personas in Discord.**  
Each character posts with a custom name and avatar via Discord Webhooks — no BOT badge, looks like a real person.

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![discord.py](https://img.shields.io/badge/discord.py-2.x-5865F2?logo=discord&logoColor=white)](https://discordpy.readthedocs.io/)
[![OpenRouter](https://img.shields.io/badge/Powered%20by-OpenRouter-ff6b35)](https://openrouter.ai/)
[![License: MIT](https://img.shields.io/badge/License-MIT-green)](LICENSE)

</div>

---

## ✨ Features

- 🪝 **Webhook-powered** — custom name + avatar per character, no BOT badge in chat
- 🎭 **Fully configurable personas** — define any character via a single `character.json`
- 🧠 **Episodic memory** — extracts personal facts and bonds from conversation into long-term SQLite storage
- 💬 **Rapid-fire texting** — dynamically chops long responses into natural, short message chains with human typing delays
- 🤔 **Reasoning model support (CoT)** — cleanly strips `<think>` blocks and optimizes lazy-prompting for deep-thinking models
- 🤖 **Multi-character support** — run multiple characters simultaneously, each as their own bot
- 🔄 **Hot-reload** — update personality without restarting the bot
- 🛡️ **Anti-repetition engine** — applies intelligent presence/frequency penalties to prevent AI looping
- 📋 **Channel whitelist** — restrict characters to specific channels
- ⚡ **Smart rate limiting** — auto-retries on temporary limits, skips on daily quota exhaustion
- 🧹 **Action text filtering** — strips `*smirks*`, `*looks up*` and other roleplay artifacts automatically
- 🗄️ **SQLite persistence** — cooldowns and logs survive bot restarts
- 🖼️ **Anti-repetition media engine** — post local images without repeating within 24 hours
- 🎨 **Mood & context alignment** — character tone adapts to the room's current energy
- 📌 **Pinterest pipeline** — pull live pins, generate in-character captions, post without repeats

---

## 🚀 Quick Start

```bash
# 1. Clone the repo
git clone https://github.com/your-username/discord-character-bot.git
cd discord-character-bot

# 2. Create a virtual environment and install dependencies
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 3. Set up your first character
mkdir characters/mycharacter
cp examples/minimal.json characters/mycharacter/character.json
cp .env.example characters/mycharacter/.env

# 4. Edit the character config
#    → characters/mycharacter/character.json  (name, personality, model)
#    → characters/mycharacter/.env            (Discord token, OpenRouter key)

# 5. Run
python bot.py --character characters/mycharacter
```

---

## 📋 Prerequisites

- **Python 3.10+**
- A **Discord bot application** ([Developer Portal](https://discord.com/developers/applications))
- An **OpenRouter API key** ([openrouter.ai/keys](https://openrouter.ai/keys))
- *(Optional)* A **SerpApi key** ([serpapi.com](https://serpapi.com)) — only needed for the Pinterest pipeline

---

## 🔧 Setup Guide

### 1. Create a Discord Bot

1. Go to [discord.com/developers/applications](https://discord.com/developers/applications)
2. **New Application** → name it anything
3. Go to **Bot** → enable ✅ **Message Content Intent**
4. Copy the **Bot Token**

**Required bot permissions:**
- Read Messages / View Channels
- Send Messages
- **Manage Webhooks** ← needed for the human-like name/avatar
- Manage Messages *(for mod commands)*

**Invite URL** *(replace `YOUR_CLIENT_ID`)*:
```
https://discord.com/api/oauth2/authorize?client_id=YOUR_CLIENT_ID&permissions=536945664&scope=bot
```

### 2. Configure secrets

```bash
cp .env.example characters/mycharacter/.env
```

```env
DISCORD_BOT_TOKEN=your_discord_bot_token_here
OPENROUTER_API_KEY=your_openrouter_api_key_here

# Optional — only needed for !char postidea (Pinterest pipeline)
SERPAPI_KEY=your_serpapi_key_here
PINTEREST_BOARD=aesthetic-morning-mood
```

### 3. Configure your character

Edit `characters/mycharacter/character.json`:

```json
{
  "name": "Alex",
  "avatar_url": "https://i.pravatar.cc/300?img=12",
  "system_prompt": "You are Alex — witty, sarcastic, and brutally honest...",
  "listen_channels": [],
  "max_history": 20,
  "max_sentences": 3,
  "temperature": 0.9,
  "model": "meta-llama/llama-3.1-8b-instruct:free",
  "mood_context": true,
  "vision_model": "google/gemini-2.0-flash-exp:free",
  "bot_interaction": {
    "enabled": false,
    "reply_chance": 0.4,
    "max_bot_chain": 3,
    "reply_delay_seconds": 4
  }
}
```

### Full `character.json` reference

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | string | required | Display name shown in Discord |
| `avatar_url` | string | `null` | Profile picture URL for the webhook |
| `system_prompt` | string | required | The character's full personality — most important field |
| `listen_channels` | array | `[]` | Channel IDs to respond in. Empty = all channels |
| `max_history` | int | `20` | Messages to remember per channel |
| `max_sentences` | int | `3` | Maximum sentences per reply |
| `temperature` | float | `0.85` | Creativity: `0.7` = consistent, `0.95` = unpredictable |
| `model` | string | `llama-3.1-8b:free` | Any [OpenRouter model](https://openrouter.ai/models) |
| `mood_context` | bool | `true` | Inject recent chat history into system prompt for tone adaptation |
| `vision_model` | string | `gemini-2.0-flash-exp:free` | OpenRouter vision model used for Pinterest pin captions |
| `bot_interaction.enabled` | bool | `false` | Allow this character to reply to other characters |
| `bot_interaction.reply_chance` | float | `0.4` | Probability of replying to another character (0–1) |
| `bot_interaction.max_bot_chain` | int | `3` | Max consecutive bot-to-bot replies before going quiet |
| `bot_interaction.reply_delay_seconds` | int | `4` | Delay before replying to another character |

---

## 🛠️ Commands

All commands use the `!char ` prefix:

| Command | Permission | Description |
|---|---|---|
| `!char status` | Everyone | Show character name, memory, and model |
| `!char postlife` | Everyone | Post a non-repeating local gallery image |
| `!char postlife <tag>` | Everyone | Post a gallery image matching a tag (e.g. `coffee`, `nature`) |
| `!char postidea` | Everyone | Post a Pinterest pin with a mood-aware AI caption |
| `!char forget` | Manage Messages | Wipe this channel's conversation history |
| `!char reload` | Manage Messages | Hot-reload `character.json` without restarting |

---

## 🖼️ Anti-Repetition Media Engine (`!char postlife`)

Drop images into a `gallery/` folder at the project root. On startup the bot scans the folder and registers every image in SQLite. When `!char postlife` is called, it picks the **least-used image that hasn't been posted in the last 24 hours** and sends it as the character via webhook.

```bash
mkdir gallery
# Add images — name them descriptively for auto-tagging
# e.g. morning_coffee.jpg  city_night.png  forest_nature.jpg
```

**Auto-tagging** — the seeder detects keywords in filenames:
`coffee`, `morning`, `nature`, `calm`, `night`, `city`, `food`, `travel`, `rain`, `books`, `aesthetic`, `cozy`, `autumn`, `summer`, `winter`, `spring`, `sunset`, `ocean`

**Tag filtering:**
```
!char postlife          ← any available image
!char postlife coffee   ← only images tagged "coffee"
```

**Cooldown reset** (if needed for testing):
```bash
sqlite3 bot_data.db "UPDATE image_pool SET last_sent_at=0;"
```

---

## 🎨 Mood & Context Alignment

Every human message is silently logged to SQLite. When the character is about to reply, it pulls the **last 10 messages** from that channel and injects them into the system prompt with a mood-reading instruction:

> *"Read the messages above and identify the current mood — energetic, stressed, joking around, quiet and reflective? Match their cadence and energy naturally."*

The character never references the instruction directly. Tone adaptation is invisible to users.

**Toggle per character in `character.json`:**
```json
{ "mood_context": true }
```

Set to `false` to always use the static system prompt regardless of room energy.

---

## 📌 Pinterest Pipeline (`!char postidea`)

Pulls live images from a Pinterest board via **SerpApi**, generates a short **mood-aware, in-character caption** using an OpenRouter vision model, and posts the image + caption via webhook — without repeating any pin within 24 hours.

### Setup

Add to the character's `.env`:
```env
SERPAPI_KEY=your_serpapi_key
PINTEREST_BOARD=aesthetic-morning-mood
```

Add to `character.json`:
```json
{
  "vision_model": "google/gemini-2.0-flash-exp:free"
}
```

On every startup the bot syncs new pins from the board into SQLite (`INSERT OR IGNORE` — existing pins are never overwritten). If `PINTEREST_BOARD` is not set, startup proceeds normally and the feature is simply skipped.

**Caption generation** uses the same `OPENROUTER_API_KEY` already configured — no second API key needed.

**Check the pin pool:**
```bash
sqlite3 bot_data.db "SELECT pin_id, use_count, last_sent_at FROM procedural_pins;"
```

---

## 👥 Running Multiple Characters

Each character is its own Discord bot application with its own token.

```
discord-character-bot/
├── gallery/                  ← shared image pool for !char postlife
└── characters/
    ├── alex/
    │   ├── character.json    ← Alex's personality
    │   └── .env              ← Alex's bot token + API keys
    └── sarah/
        ├── character.json    ← Sarah's personality
        └── .env              ← Sarah's bot token + API keys
```

**Add a new character:**

```bash
mkdir characters/sarah
cp examples/the_mentor.json characters/sarah/character.json
cp .env.example characters/sarah/.env
# fill in sarah's .env with her own bot token
```

**Update `launch_all.sh`:**

```bash
CHARACTERS=("characters/alex" "characters/sarah")
```

**Launch all at once:**

```bash
bash launch_all.sh
```

Or individually:

```bash
source venv/bin/activate && python bot.py --character characters/alex
source venv/bin/activate && python bot.py --character characters/sarah
```

---

## 🤖 Character-to-Character Interaction

When multiple characters are running, they can reply to each other — with built-in loop prevention:

```json
"bot_interaction": {
  "enabled": true,
  "reply_chance": 0.4,
  "max_bot_chain": 3,
  "reply_delay_seconds": 4
}
```

- **`reply_chance`** — roll of the dice each time another character speaks
- **`max_bot_chain`** — after this many consecutive bot-to-bot messages, they go quiet until a human speaks
- Characters reset their chain counter automatically when a human sends a message

---

## 🎭 Character Templates

The `examples/` folder has ready-to-use characters:

| Template | Persona | Best for |
|---|---|---|
| `minimal.json` | Blank slate | Building from scratch |
| `the_mentor.json` | Jordan — wise, calm | Advice and deep talks |
| `the_skeptic.json` | Riley — dry, sharp | Debate and contrast |
| `the_hype_friend.json` | Max — enthusiastic | Motivation and energy |

See [`examples/README.md`](examples/README.md) for tips on writing great system prompts.

---

## 🆓 Free Models on OpenRouter

| Model | ID | Notes |
|---|---|---|
| Llama 3.1 8B | `meta-llama/llama-3.1-8b-instruct:free` | Recommended default |
| Gemma 3 4B | `google/gemma-3-4b-it:free` | Fast and lightweight |
| Mistral 7B | `mistralai/mistral-7b-instruct:free` | Good general purpose |
| Gemini 2.0 Flash | `google/gemini-2.0-flash-exp:free` | Recommended for vision/captions |

Free tier: **50 requests/day**. Add $10 credits for 1,000/day → [openrouter.ai/credits](https://openrouter.ai/credits)

---

## 🐛 Troubleshooting

| Problem | Fix |
|---|---|
| Bot doesn't respond at all | Enable **Message Content Intent** in the Discord Developer Portal |
| Messages show BOT badge / no custom avatar | Grant **Manage Webhooks** permission to the bot |
| `PrivilegedIntentsRequired` error | Enable Message Content Intent in Dev Portal |
| OpenRouter 429 — temporary | Bot will auto-retry. Wait a few seconds |
| OpenRouter 429 — daily quota | Add credits at [openrouter.ai/credits](https://openrouter.ai/credits) or wait for midnight UTC reset |
| Character uses `*action text*` | Already filtered automatically. Tighten `system_prompt` if it persists |
| `character.json not found` | Check the `--character` path and that the file exists |
| `aiosqlite` ImportError | Run `pip install -r requirements.txt` in your virtual environment |
| `bot_data.db` not created | Ensure `on_ready` calls `init_db()` — should be automatic |
| `!char postlife` sends nothing | Ensure `./gallery/` folder exists and contains `.png`/`.jpg` images |
| `!char postlife` says "No fresh images" | All images in 24h cooldown — reset with `sqlite3 bot_data.db "UPDATE image_pool SET last_sent_at=0;"` |
| `!char postidea` says "No fresh pins" | Check `SERPAPI_KEY` and `PINTEREST_BOARD` in `.env`; check startup logs for SerpApi errors |
| Caption always falls back to ✨ | Vision model unavailable on free tier — set `vision_model` to `google/gemini-2.0-flash-exp:free` |
| Mood context not affecting replies | Set `"mood_context": true` in `character.json`, send a few messages, then trigger a reply |
| Pins not syncing on startup | `SERPAPI_KEY` or `PINTEREST_BOARD` missing from `.env` |

---

## 📄 License

[MIT](LICENSE) — free to use, modify, and distribute.
