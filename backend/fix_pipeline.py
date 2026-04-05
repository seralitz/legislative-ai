from __future__ import annotations

import json
import logging
import re

from backend import claude_client, nia_client
from backend.models import FixResponse, Problem
from backend.prompts import FIX_SYSTEM, FIX_USER_TEMPLATE

logger = logging.getLogger(__name__)


def _parse_fix_json(raw: str) -> dict:
    cleaned = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        obj_match = re.search(r"\{[\s\S]*\}", cleaned)
        if obj_match:
            cleaned = obj_match.group(0)
    return json.loads(cleaned)


async def generate_fix(problem: Problem, law_text: str = "") -> FixResponse:
    """
    1. If no law_text provided, search Nia for additional context
    2. Send problem + text to Claude
    3. Parse structured fix response
    """
    context = law_text or problem.law_text

    if not context:
        try:
            search_query = f"{problem.law_title} {problem.article}"
            nia_results = await nia_client.search(search_query, limit=5)
            context = "\n\n---\n\n".join(r.content for r in nia_results)
        except Exception as exc:
            logger.error("Nia context search failed: %s", exc)
            context = "(Текст закона недоступен)"

    user_msg = FIX_USER_TEMPLATE.format(
        law_title=problem.law_title,
        article=problem.article,
        problem_type=problem.problem_type.value,
        severity=problem.severity.value,
        description=problem.description,
        legal_reasoning=problem.legal_reasoning or "(не указано)",
        law_text=context,
    )

    import asyncio
    raw_response = await asyncio.to_thread(claude_client.complete, FIX_SYSTEM, user_msg)
    parsed = _parse_fix_json(raw_response)

    return FixResponse(
        problem_id=problem.id,
        preamble=parsed.get("preamble", ""),
        amendment_text=parsed.get("amendment_text", ""),
        justification=parsed.get("justification", ""),
        affected_articles=parsed.get("affected_articles", []),
    )
