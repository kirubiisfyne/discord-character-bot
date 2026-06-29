import re
import json
import datetime
from config import CHARACTER_FILE, args, log
from database import get_db
from state import channel_histories

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


# ── Helper Functions ───────────────────────────────────────────────────────────

def load_character() -> dict:
    """Load and return the character config from character.json (hot-reloadable)."""
    with open(CHARACTER_FILE, "r", encoding="utf-8") as f:
        char = json.load(f)
        
    # Override via CLI flag
    if getattr(args, "local", False):
        char["run_local"] = True
        
    return char


def trim_to_length(text: str, max_chars: int) -> str:
    """Trim text to complete sentences, staying roughly under `max_chars`."""
    if max_chars <= 0:
        return text
    sentences = re.split(r'(?<=[.!?])(?:\s+|$)', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    
    kept_sentences = []
    current_length = 0
    for s in sentences:
        if not kept_sentences:
            # Always keep at least one sentence even if it's over the limit
            kept_sentences.append(s)
            current_length += len(s)
        elif current_length + len(s) + 1 <= max_chars:
            kept_sentences.append(s)
            current_length += len(s) + 1
        else:
            break
            
    return " ".join(kept_sentences)


def strip_action_text(text: str) -> str:
    """
    Remove roleplay-style action text from LLM responses.
    Cleans: *smirks*, **bold actions**, _looks up_, (short parentheticals)
    """
    if '<think>' in text:
        if '</think>' in text:
            text = text.split('</think>', 1)[-1]
        else:
            text = "" # Unclosed think block (cut off by token limit)
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL) # Remove deepseek reasoning blocks
    text = re.sub(r'\*{1,2}[^*]{1,80}\*{1,2}', '', text)   # *action* / **action**
    text = re.sub(r'_[^_]{1,80}_', '', text)                 # _action_
    text = re.sub(r'\([^)]{1,60}\)', '', text)               # (action) — short only
    text = re.sub(r'^[\s,;:\-]+', '', text)                  # leading punctuation artifacts
    text = re.sub(r'\s{2,}', ' ', text).strip()              # collapse whitespace
    return text



async def build_messages(
    channel_id: int,
    user_display: str,
    user_text: str,
    character: dict,
    system_prompt_override: str = None,   # Feature 3: pass mood-enriched prompt here
) -> list[dict]:
    """
    Build the full message list for the OpenRouter API call:
      [system prompt + formatting rules] + [channel history] + [new user message]

    If system_prompt_override is provided it replaces character["system_prompt"] as the
    base, allowing Feature 3's mood-aware prompt to be injected without modifying the
    character config.

    Side-effect: appends the new user message to channel_histories.
    """
    max_history = character.get("max_history", 20)

    if channel_id not in channel_histories:
        channel_histories[channel_id] = []

    history = channel_histories[channel_id]
    history.append({"role": "user", "content": f"{user_display}: {user_text}"})

    # Keep history within configured limit AND a token-saving character budget (approx 600 words)
    history = history[-max_history:]
    
    # Cost optimization: enforce a strict character budget for history (~3000 chars / ~750 tokens)
    # We walk backwards from the newest messages, keeping them until we hit the budget.
    char_budget = 3000
    current_chars = 0
    budgeted_history = []
    
    for msg in reversed(history):
        msg_len = len(msg.get("content", ""))
        if current_chars + msg_len > char_budget and budgeted_history:
            break
        budgeted_history.insert(0, msg)
        current_chars += msg_len
        
    channel_histories[channel_id] = budgeted_history

    # Use override if provided, otherwise fall back to character's own system_prompt
    base = system_prompt_override if system_prompt_override else character["system_prompt"].rstrip()

    # 1. KEEP BASE COMPLETELY STATIC FOR CACHING
    base += "\n\nRULES: No action text, roleplay emotes (*smirks*), asterisks, or narration. Respond with natural spoken plain text only."
    base += "\nIf someone asks for a selfie, photo, or if it naturally fits the conversation, you can attach a picture from your gallery by including the exact text <send_photo> anywhere in your reply. You can optionally request a specific type of photo using a tag like <send_photo:selfie> or <send_photo:gym>."
    
    cot_setting = character.get("use_cot", False)
    if cot_setting:
        if isinstance(cot_setting, str):
            base += f" {cot_setting}"
        else:
            base += " Before responding, use a <think> block to briefly analyze the user's message, recall your character's persona, and plan a natural, in-character response."

    # 2. ISOLATE DYNAMIC CONTEXT
    dynamic_context = ""
    try:
        db = await get_db()
        import re
        # Token cost optimization: Extract >4 char words to use as search keywords
        words = [w for w in re.findall(r'\b\w+\b', user_text.lower()) if len(w) > 4]
        
        memories = []
        if words:
            conditions = " OR ".join(["LOWER(memory) LIKE ?"] * len(words))
            params = [channel_id, character["name"]] + [f"%{w}%" for w in words]
            query = f"SELECT memory FROM core_memories WHERE channel_id=? AND character=? AND ({conditions}) ORDER BY id DESC LIMIT 3"
            async with db.execute(query, params) as cursor:
                memories = await cursor.fetchall()
                
        # If no keywords matched, just pull the single most recent memory (instead of all 5)
        if not memories:
            async with db.execute("SELECT memory FROM core_memories WHERE channel_id=? AND character=? ORDER BY id DESC LIMIT 1", (channel_id, character["name"])) as cursor:
                memories = await cursor.fetchall()
                
        if memories:
            memory_text = "\n".join([row["memory"] for row in memories])
            dynamic_context += f"\n[CORE MEMORIES]\n{memory_text}\n"
    except Exception as e:
        log.warning(f"Failed to load core memories: {e}")

    current_time = datetime.datetime.now().strftime("%A, %I:%M %p")
    dynamic_context += f"\n[TIME]\n{current_time}. Keep context, but don't awkwardly announce it.\n"

    # 3. CONSTRUCT API MESSAGES ARRAY (Static System + Static Old History + Dynamic Last Message)
    final_messages = [{"role": "system", "content": base}]
    final_messages.extend(budgeted_history[:-1])
    
    # Inject dynamic context strictly into the final user message to preserve the cache prefix!
    last_user_msg = budgeted_history[-1]["content"]
    final_messages.append({"role": "user", "content": f"{dynamic_context.strip()}\n\n{last_user_msg}"})
    
    return final_messages
