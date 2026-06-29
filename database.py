import os
import re
import time
import aiohttp
import aiosqlite
from config import DB_PATH, log

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

        -- Feature 4: Episodic memory extraction
        CREATE TABLE IF NOT EXISTS core_memories (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            channel_id  INTEGER,
            character   TEXT,
            memory      TEXT,
            timestamp   INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_memories_channel
            ON core_memories(channel_id, id DESC);
    """)
    await db.commit()
    log.info("✅ Database initialized — all tables ready.")

async def extract_memories_bg(channel_id: int, character: dict, history: list[dict]):
    """Background task to extract core memories from recent chat."""
    try:
        transcript = ""
        for msg in history[-10:]:
            role = "AI" if msg["role"] == "assistant" else "USER"
            content = msg.get("content", "")
            transcript += f"{role}: {content}\n"
        
        prompt = (
            "Analyze the following conversation transcript.\n"
            "Identify if the USER or the AI revealed any new, important personal facts, preferences, or experienced a strong emotional moment.\n"
            "If yes, extract them as a brief, bulleted list of facts (e.g. '- USER loves coffee', '- AI is afraid of spiders').\n"
            "If nothing significant was revealed, output exactly 'NO_NEW_MEMORIES'.\n\n"
            "Transcript:\n" + transcript
        )
        
        payload = {
            "model": character.get("model", "llama3"),
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.3,
        }
        
        headers = {}
        if not character.get("run_local"):
            headers["Authorization"] = f"Bearer {os.getenv('OPENROUTER_API_KEY')}"
            endpoint = "https://openrouter.ai/api/v1/chat/completions"
        else:
            endpoint = "http://localhost:11434/v1/chat/completions"
            
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, headers=headers, json=payload, timeout=aiohttp.ClientTimeout(total=600)) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    result = data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
                    
                    if result and "NO_NEW_MEMORIES" not in result:
                        if '</think>' in result:
                            result = result.split('</think>', 1)[-1].strip()
                        result = re.sub(r'<think>.*?</think>', '', result, flags=re.DOTALL)
                        
                        db = await get_db()
                        await db.execute(
                            "INSERT INTO core_memories (channel_id, character, memory, timestamp) VALUES (?, ?, ?, ?)",
                            (channel_id, character["name"], result, int(time.time()))
                        )
                        await db.commit()
                        log.info(f"[🧠] Extracted new memory for #{channel_id}: {result[:50]}...")
    except Exception as e:
        log.exception(f"Memory extraction failed: {e}")
