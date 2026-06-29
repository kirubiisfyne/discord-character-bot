import asyncio
import discord

# Per-channel conversation history  {channel_id: [{"role": ..., "content": ...}]}
channel_histories: dict[int, list[dict]] = {}

# Per-channel webhook cache  {channel_id: discord.Webhook}
channel_webhooks: dict[int, discord.Webhook] = {}

# Proactive Messaging Tracking
channel_last_activity: dict[int, float] = {}
channel_has_unanswered_proactive: dict[int, bool] = {}
channel_idle_target: dict[int, float] = {}

# Per-channel consecutive bot-to-bot message counter  {channel_id: int}
channel_bot_chains: dict[int, int] = {}

# Per-channel last internal thought  {channel_id: str}
channel_last_thoughts: dict[int, str] = {}

# Message counter for memory extraction {channel_id: int}
channel_msg_counts: dict[int, int] = {}

# Webhook IDs owned by this bot instance — prevents the bot from replying to itself
own_webhook_ids: set[int] = set()

# Per-channel active response task — enables message interruption  {channel_id: asyncio.Task}
channel_tasks: dict[int, asyncio.Task] = {}
