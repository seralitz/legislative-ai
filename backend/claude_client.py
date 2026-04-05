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


def complete(
    system: str,
    user: str,
    max_tokens: int = 8192,
    use_thinking: bool = False,
    thinking_budget: int = 8000,
) -> str:
    """Synchronous completion. When use_thinking=True, attempts extended thinking
    first and falls back to standard on failure."""
    client = _get_client()

    if use_thinking:
        logger.info(
            "Claude thinking request: model=%s, budget_tokens=%d",
            CLAUDE_MODEL, thinking_budget,
        )
        try:
            message = client.messages.create(
                model=CLAUDE_MODEL,
                max_tokens=max_tokens + thinking_budget,
                thinking={"type": "enabled", "budget_tokens": thinking_budget},
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            for block in message.content:
                if block.type == "text":
                    logger.info("Claude thinking response: %d chars", len(block.text))
                    return block.text
        except Exception as exc:
            logger.warning("Extended thinking failed, falling back to standard: %s", exc)

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
