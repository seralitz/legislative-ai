from __future__ import annotations

import json
import logging
import re
import uuid
from hashlib import sha256
from typing import Optional

from backend import claude_client, nia_client
from backend.config import AUDIT_BATCH_SIZE, DOMAIN_QUERIES
from backend.models import (
    AuditStatus,
    NiaSearchResult,
    Problem,
    ProblemType,
    Severity,
    SEVERITY_ORDER,
)
from backend.prompts import AUDIT_SYSTEM, AUDIT_USER_TEMPLATE

logger = logging.getLogger(__name__)

# In-memory store keyed by domain
_audit_results: dict[str, list[Problem]] = {}
_audit_status: dict[str, AuditStatus] = {}


def get_status(domain: str) -> Optional[AuditStatus]:
    return _audit_status.get(domain)


def get_results(domain: str) -> list[Problem]:
    return _audit_results.get(domain, [])


def _dedup_fragments(fragments: list[NiaSearchResult]) -> list[NiaSearchResult]:
    seen: set[str] = set()
    unique: list[NiaSearchResult] = []
    for frag in fragments:
        key = sha256(frag.content.strip().encode()).hexdigest()
        if key not in seen:
            seen.add(key)
            unique.append(frag)
    return unique


def _batch(items: list, size: int) -> list[list]:
    return [items[i : i + size] for i in range(0, len(items), size)]


def _parse_problems_json(raw: str) -> list[dict]:
    """Extract JSON array from Claude's response, tolerating markdown fences."""
    cleaned = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*(\[[\s\S]*?])\s*```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        arr_match = re.search(r"\[[\s\S]*]", cleaned)
        if arr_match:
            cleaned = arr_match.group(0)
    return json.loads(cleaned)


def _validate_problem(raw: dict, domain: str, law_text: str = "") -> Optional[Problem]:
    try:
        return Problem(
            id=str(uuid.uuid4())[:8],
            law_title=raw.get("law_title", "Неизвестный закон"),
            article=raw.get("article", "—"),
            problem_type=ProblemType(raw.get("problem_type", "outdated")),
            severity=Severity(raw.get("severity", "medium")),
            description=raw.get("description", ""),
            affected_articles=raw.get("affected_articles", []),
            law_text=law_text,
            domain=domain,
        )
    except (ValueError, KeyError) as exc:
        logger.warning("Skipping malformed problem: %s — %s", raw, exc)
        return None


async def run_audit(domain: str) -> list[Problem]:
    """
    Full audit pipeline:
    1. Query Nia with domain-specific queries
    2. Deduplicate results
    3. Batch into Claude-sized chunks
    4. Claude audits each batch
    5. Collect + sort problems by severity
    """
    queries = DOMAIN_QUERIES.get(domain)
    if not queries:
        raise ValueError(f"Unknown domain: {domain}")

    _audit_status[domain] = AuditStatus(
        status="running", domain=domain, total_batches=0, completed_batches=0
    )

    # Step 1: gather law fragments from Nia
    all_fragments: list[NiaSearchResult] = []
    for query in queries:
        try:
            results = await nia_client.search(query)
            all_fragments.extend(results)
            logger.info("Nia query '%s': %d results", query, len(results))
        except Exception as exc:
            logger.error("Nia search failed for '%s': %s", query, exc)

    if not all_fragments:
        _audit_status[domain] = AuditStatus(
            status="error",
            domain=domain,
            error="Nia returned no results. Ensure the data source is indexed.",
        )
        return []

    # Step 2: deduplicate
    unique = _dedup_fragments(all_fragments)
    logger.info("Fragments: %d total, %d unique", len(all_fragments), len(unique))

    # Step 3: batch
    batches = _batch(unique, AUDIT_BATCH_SIZE)
    _audit_status[domain].total_batches = len(batches)

    # Step 4: Claude audit per batch
    all_problems: list[Problem] = []
    for i, batch in enumerate(batches):
        fragments_text = "\n\n---\n\n".join(
            f"[Источник: {f.url or 'N/A'}]\n{f.content}" for f in batch
        )
        user_msg = AUDIT_USER_TEMPLATE.format(domain=domain, law_fragments=fragments_text)

        try:
            raw_response = claude_client.complete(AUDIT_SYSTEM, user_msg)
            logger.info("Batch %d Claude raw response: %s", i, raw_response[:300])
            parsed = _parse_problems_json(raw_response)
            for raw_problem in parsed:
                combined_text = "\n---\n".join(f.content[:500] for f in batch)
                problem = _validate_problem(raw_problem, domain, combined_text)
                if problem:
                    all_problems.append(problem)
        except (json.JSONDecodeError, Exception) as exc:
            logger.error("Batch %d audit failed: %s", i, exc)

        _audit_status[domain].completed_batches = i + 1
        _audit_status[domain].problems_found = len(all_problems)

    # Step 5: sort by severity
    all_problems.sort(key=lambda p: SEVERITY_ORDER.get(p.severity, 99))

    _audit_results[domain] = all_problems
    _audit_status[domain] = AuditStatus(
        status="completed",
        domain=domain,
        total_batches=len(batches),
        completed_batches=len(batches),
        problems_found=len(all_problems),
    )

    logger.info("Audit complete for '%s': %d problems found", domain, len(all_problems))
    return all_problems
