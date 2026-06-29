import os
import argparse
import logging
from dotenv import load_dotenv

VERSION = "1.2.0"

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
parser.add_argument(
    "--local",
    action="store_true",
    help="Run LLM requests locally via Ollama (spawns 'ollama serve' if not running)",
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
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "dummy-local-key")
DEEPSEEK_API_KEY   = os.getenv("DEEPSEEK_API_KEY", "")
LLM_API_URL        = os.getenv("LLM_API_URL", "https://openrouter.ai/api/v1/chat/completions")
ELEVENLABS_AGENT_ID = os.getenv("ELEVENLABS_AGENT_ID", "")

DB_PATH = "bot_data.db"
