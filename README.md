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
- 🧠 **Per-channel memory** — each channel has its own conversation history
- 🤖 **Multi-character support** — run multiple characters simultaneously, each as their own bot
- 💬 **Character-to-character interaction** — characters can reply to each other with loop prevention
- 🔄 **Hot-reload** — update personality without restarting the bot
- 📋 **Channel whitelist** — restrict characters to specific channels
- ⚡ **Smart rate limiting** — auto-retries on temporary limits, skips on daily quota exhaustion
- 🧹 **Action text filtering** — strips `*smirks*`, `*looks up*` and other roleplay artifacts automatically

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
  "reply_delay_seconds": 2,
  "temperature": 0.9,
  "model": "meta-llama/llama-3.1-8b-instruct:free",
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
| `reply_delay_seconds` | int | `0` | Pause before responding to humans (feels natural) |
| `temperature` | float | `0.85` | Creativity: `0.7` = consistent, `0.95` = unpredictable |
| `model` | string | `llama-3.1-8b:free` | Any [OpenRouter model](https://openrouter.ai/models) |
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
| `!char forget` | Manage Messages | Wipe this channel's conversation history |
| `!char reload` | Manage Messages | Hot-reload `character.json` without restarting |

---

## 👥 Running Multiple Characters

Each character is its own Discord bot application with its own token.

```
discord-character-bot/
└── characters/
    ├── alex/
    │   ├── character.json    ← Alex's personality
    │   └── .env              ← Alex's bot token
    └── sarah/
        ├── character.json    ← Sarah's personality
        └── .env              ← Sarah's bot token
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

---

## 📄 License

[MIT](LICENSE) — free to use, modify, and distribute.
