import asyncio
import aiohttp
import urllib.request
import subprocess
import time
from config import DEEPSEEK_API_KEY, OPENROUTER_API_KEY, LLM_API_URL, log

def get_llm_endpoint(character: dict) -> str:
    """Returns the local Ollama URL, DeepSeek API URL, or the configured URL."""
    if character.get("run_local", False):
        return "http://localhost:11434/v1/chat/completions"
    if character.get("run_deepseek", False):
        return "https://api.deepseek.com/chat/completions"
    return LLM_API_URL

def ensure_ollama_running():
    """Checks if Ollama is running, and starts it in the background if not."""
    try:
        urllib.request.urlopen("http://localhost:11434/", timeout=1)
    except Exception:
        log.info("Ollama is not running. Starting 'ollama serve' in the background...")
        try:
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2)  # Give it a moment to bind to the port
        except FileNotFoundError:
            log.error("Failed to start Ollama. Is it installed and in your PATH?")

async def query_openrouter(messages: list[dict], character: dict, _retries: int = 2, _is_fallback: bool = False) -> tuple[str | None, int]:
    """
    Send a message list to OpenRouter and return the text reply.

    Automatically retries on temporary 429 rate limits using the Retry-After hint.
    Does NOT retry on daily quota exhaustion (free-models-per-day).
    Returns None on unrecoverable error.
    """

    if character.get("run_deepseek", False):
        api_key = DEEPSEEK_API_KEY
        default_model = "deepseek-reasoner"
    else:
        api_key = OPENROUTER_API_KEY
        default_model = "meta-llama/llama-3.1-8b-instruct:free"

    if character.get("run_local"):
        default_model = "llama3"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    if not character.get("run_local") and not character.get("run_deepseek"):
        headers["HTTP-Referer"] = "https://github.com/your-username/discord-character-bot"
        headers["X-Title"] = f"{character['name']} Discord Bot"
        
    model_to_use = character.get("fallback_model") if _is_fallback else character.get("model", default_model)
        
    payload = {
        "model":             model_to_use,
        "messages":          messages,
        "temperature":       character.get("temperature", 0.85),
        "frequency_penalty": character.get("frequency_penalty", 0.5),
        "presence_penalty":  character.get("presence_penalty", 0.5),
    }

    # Local Ollama handles its own context allocation natively. Forcing a massive max_tokens
    # causes it to pre-allocate huge KV cache blocks, leading to severe swap/paging.
    # However, DeepSeek API (reasoner) strictly requires a large max_tokens to accommodate
    # the <think> blocks before hitting limit cutoffs.
    if not character.get("run_local", False):
        if character.get("run_deepseek", False) or "deepseek" in payload["model"].lower() or "use_cot" in character:
            payload["max_tokens"] = 4000
        else:
            payload["max_tokens"] = character.get("max_sentences", 3) * 60

    endpoint = get_llm_endpoint(character)
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                endpoint, headers=headers, json=payload,
                timeout=aiohttp.ClientTimeout(total=300),
            ) as resp:

                if resp.status == 429 and _retries > 0:
                    try:
                        data = await resp.json()
                        error_meta = data.get("error", {}).get("metadata", {})
                        error_msg  = data.get("error", {}).get("message", "")

                        # Daily quota exhausted — retrying won't help, give up immediately
                        if "per-day" in error_msg or "per_day" in error_msg:
                            log.error("Daily free model quota exhausted. Add credits at openrouter.ai/credits")
                            if not _is_fallback and character.get("fallback_model"):
                                log.warning(f"Falling back to {character.get('fallback_model')}")
                                return await query_openrouter(messages, character, _retries=2, _is_fallback=True)
                            return None, 0

                        wait = float(error_meta.get("retry_after_seconds", 10))
                    except Exception:
                        wait = 10

                    wait = min(wait, 30)  # cap at 30s
                    log.warning(f"Rate limited — retrying in {wait:.1f}s ({_retries} retries left)")
                    await asyncio.sleep(wait)
                    return await query_openrouter(messages, character, _retries=_retries - 1, _is_fallback=_is_fallback)

                if resp.status != 200:
                    body = await resp.text()
                    log.error(f"LLM API {resp.status}: {body}")
                    if not _is_fallback and character.get("fallback_model"):
                        log.warning(f"Falling back to {character.get('fallback_model')}")
                        return await query_openrouter(messages, character, _retries=2, _is_fallback=True)
                    return None, 0

                data = await resp.json()
                usage = data.get("usage")
                total_tokens = usage.get("total_tokens", 0) if usage else 0
                    
                return data["choices"][0]["message"]["content"].strip(), total_tokens

    except asyncio.TimeoutError:
        log.error("LLM API request timed out.")
        if not _is_fallback and character.get("fallback_model"):
            log.warning(f"Falling back to {character.get('fallback_model')}")
            return await query_openrouter(messages, character, _retries=2, _is_fallback=True)
        return None, 0
    except Exception as e:
        log.error(f"LLM API exception: {e}")
        if not _is_fallback and character.get("fallback_model"):
            log.warning(f"Falling back to {character.get('fallback_model')}")
            return await query_openrouter(messages, character, _retries=2, _is_fallback=True)
        return None, 0
