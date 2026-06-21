# 🎭 Character Examples

Ready-to-use character templates. Copy any of these into your `characters/` folder and fill in the `.env` to get started immediately.

---

## Available Templates

| File | Character | Personality | Best for |
|---|---|---|---|
| `minimal.json` | Blank template | — | Building from scratch |
| `the_mentor.json` | Jordan | Wise, calm, perceptive | Advice, deep talks |
| `the_skeptic.json` | Riley | Dry, sharp, questions everything | Debate, contrast |
| `the_hype_friend.json` | Max | Enthusiastic, supportive, high energy | Motivation, positivity |

---

## How to use a template

```bash
# 1. Create a character folder
mkdir characters/jordan

# 2. Copy the template in
cp examples/the_mentor.json characters/jordan/character.json

# 3. Copy the env template
cp .env.example characters/jordan/.env

# 4. Fill in your bot token
#    Open characters/jordan/.env and add:
#    DISCORD_BOT_TOKEN=your_token_here
#    OPENROUTER_API_KEY=your_key_here

# 5. Add to launch_all.sh
#    CHARACTERS=("characters/alex" "characters/jordan")

# 6. Launch
bash launch_all.sh
```

---

## Tips for writing great system prompts

The `system_prompt` field is the most important part of any character. Here's what makes one work:

**✅ Do:**
- Describe *how* they speak, not just *who* they are
- Give them specific interests, opinions, and quirks
- Include how they deflect "are you an AI?" questions
- Use concrete examples: *"You call people 'nerd' affectionately"*

**❌ Avoid:**
- Vague traits: *"You are friendly and helpful"*
- Contradictions: *"You are shy but also very talkative"*
- Breaking the fourth wall: never mention AI, bots, or models in the prompt

**📐 Template structure:**
```
You are [Name] — [1-2 sentences on core personality].
[1-2 sentences on speech style and quirks].
[1 sentence on specific interests or opinions].
You NEVER reveal that you're an AI or a bot.
If asked, [how they deflect in character].
```
