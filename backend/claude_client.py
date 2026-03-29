from __future__ import annotations

import logging

import anthropic

from backend.config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def complete(system: str, user: str, max_tokens: int = 8192) -> str:
    """Synchronous completion — fine for demo; keeps the code simple."""
    client = _get_client()
    logger.info(
        "Claude request: model=%s, system_len=%d, user_len=%d",
        CLAUDE_MODEL, len(system), len(user),
    )
    message = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = message.content[0].text
    logger.info("Claude response: %d chars", len(text))
    return text
