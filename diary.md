# Diary / Changelog

## 2026-06-29
- **Implemented Fallback Model Feature**: Added support for configuring a `fallback_model` in `character.json`. When the primary LLM model fails to respond after exhausting its retries (e.g., due to API timeouts, 5xx errors, or daily quota exhaustion), the bot now automatically intercepts the failure and recursively queries OpenRouter using the assigned `fallback_model`. Once the message is successfully sent, the bot will seamlessly switch back to the main model for subsequent messages. This significantly improves bot reliability during API outages or when free quotas run out.
