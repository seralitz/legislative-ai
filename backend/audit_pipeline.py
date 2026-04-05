from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from hashlib import sha256
from pathlib import Path
from typing import Optional

from backend import claude_client, nia_client
from backend.config import DOMAIN_QUERIES
from backend.models import (
    AuditStatus,
    NiaSearchResult,
    Problem,
    ProblemType,
    Severity,
    SEVERITY_ORDER,
)
from backend.prompts import (
    AUDIT_SYSTEM,
    AUDIT_USER_TEMPLATE,
    CROSS_CHECK_SYSTEM,
    CROSS_CHECK_USER_TEMPLATE,
    PLAN_SYSTEM,
    PLAN_USER_TEMPLATE,
)

logger = logging.getLogger(__name__)

_FALLBACK_PATH = Path(__file__).resolve().parent / "fallback_laws.json"
_MAX_CHARS_PER_BATCH = 80_000  # ~20k tokens per batch

# In-memory store keyed by domain
_audit_results: dict[str, list[Problem]] = {}
_audit_status: dict[str, AuditStatus] = {}
_plan_queries: dict[str, list[str]] = {}


def get_status(domain: str) -> Optional[AuditStatus]:
    return _audit_status.get(domain)


def get_results(domain: str) -> list[Problem]:
    return _audit_results.get(domain, [])


def store_plan_queries(domain: str, queries: list[str]) -> None:
    _plan_queries[domain] = queries


def _load_fallback(domain: str) -> list[NiaSearchResult]:
    """Load static law fragments when Nia is unavailable."""
    try:
        with _FALLBACK_PATH.open(encoding="utf-8") as f:
            data = json.load(f)
        entries = data.get(domain, [])
        logger.info("Fallback: loaded %d entries for domain '%s'", len(entries), domain)
        return [NiaSearchResult(**e) for e in entries]
    except Exception as exc:
        logger.error("Failed to load fallback laws: %s", exc)
        return []


def _token_cap_batches(
    fragments: list[NiaSearchResult], max_chars: int = _MAX_CHARS_PER_BATCH
) -> list[list[NiaSearchResult]]:
    """Split fragments into batches capped by total character count."""
    batches: list[list[NiaSearchResult]] = []
    current: list[NiaSearchResult] = []
    current_chars = 0
    for frag in fragments:
        frag_chars = len(frag.content)
        if current and current_chars + frag_chars > max_chars:
            batches.append(current)
            current = [frag]
            current_chars = frag_chars
        else:
            current.append(frag)
            current_chars += frag_chars
    if current:
        batches.append(current)
    return batches


async def _audit_batch_async(
    batch: list[NiaSearchResult], domain: str, batch_idx: int
) -> list[Problem]:
    """Run a single Claude audit batch asynchronously."""
    fragments_text = "\n\n---\n\n".join(
        f"[Источник: {f.url or 'N/A'}]\n{f.content}" for f in batch
    )
    user_msg = AUDIT_USER_TEMPLATE.format(domain=domain, law_fragments=fragments_text)
    try:
        raw_response = await asyncio.to_thread(claude_client.complete, AUDIT_SYSTEM, user_msg)
        logger.info("Batch %d Claude raw response: %s", batch_idx, raw_response[:300])
        parsed = _parse_problems_json(raw_response)
        combined_text = "\n---\n".join(f.content[:500] for f in batch)
        problems = []
        for raw_problem in parsed:
            problem = _validate_problem(raw_problem, domain, combined_text)
            if problem:
                problems.append(problem)
        return problems
    except Exception as exc:
        logger.error("Batch %d audit failed: %s", batch_idx, exc)
        return []


async def plan_audit(domain: str) -> list[str]:
    """Use Claude to generate 10 targeted search queries for the domain."""
    user_msg = PLAN_USER_TEMPLATE.format(domain=domain)
    raw = await asyncio.to_thread(claude_client.complete, PLAN_SYSTEM, user_msg)
    cleaned = raw.strip()
    queries: list[str] = []
    try:
        # Try parsing the whole response as JSON first
        parsed = json.loads(re.search(r"[\[{][\s\S]*[\]}]", cleaned).group(0))
        if isinstance(parsed, list):
            queries = [q for q in parsed if isinstance(q, str)]
        elif isinstance(parsed, dict):
            # Claude returned a richer object — extract whichever key holds the query list
            for key in ("targeted_queries", "queries", "запросы"):
                if isinstance(parsed.get(key), list):
                    queries = [q for q in parsed[key] if isinstance(q, str)]
                    break
    except Exception:
        queries = []
    store_plan_queries(domain, queries)
    logger.info("Plan generated %d queries for domain '%s'", len(queries), domain)
    return queries


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
            legal_reasoning=raw.get("legal_reasoning", ""),
            law_text=law_text,
            domain=domain,
        )
    except (ValueError, KeyError) as exc:
        logger.warning("Skipping malformed problem: %s — %s", raw, exc)
        return None


def _build_cross_check_queries(problem: Problem) -> list[str]:
    """Build targeted Nia queries to find laws related to a specific problem."""
    queries: list[str] = []
    # The specific article in context of its law
    queries.append(f"{problem.law_title} {problem.article}")
    # The article within the broader domain
    if problem.domain:
        queries.append(f"{problem.domain} {problem.article} Казахстан")
    # Each explicitly listed affected article (up to 3)
    for art in problem.affected_articles[:3]:
        queries.append(art)
    return queries


def _parse_cross_check_json(raw: str) -> dict:
    """Extract a JSON object from Claude's cross-check response."""
    cleaned = raw.strip()
    fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", cleaned)
    if fence_match:
        cleaned = fence_match.group(1)
    else:
        obj_match = re.search(r"\{[\s\S]*\}", cleaned)
        if obj_match:
            cleaned = obj_match.group(0)
    return json.loads(cleaned)


async def _enrich_with_cross_check(
    problem: Problem, related_frags: list[NiaSearchResult]
) -> Problem:
    """Ask Claude to compare the original law against related laws and confirm
    contradictions with specific article citations. Returns an updated Problem."""
    related_text = "\n\n---\n\n".join(
        f"[Источник: {f.url or 'N/A'}]\n{f.content}" for f in related_frags
    )
    user_msg = CROSS_CHECK_USER_TEMPLATE.format(
        law_title=problem.law_title,
        article=problem.article,
        problem_type=problem.problem_type.value,
        description=problem.description,
        legal_reasoning=problem.legal_reasoning or "(не указано)",
        law_text=problem.law_text[:2000] if problem.law_text else "(не загружен)",
        related_fragments=related_text,
    )
    try:
        raw = await asyncio.to_thread(
            claude_client.complete, CROSS_CHECK_SYSTEM, user_msg, use_thinking=True
        )
        data = _parse_cross_check_json(raw)
        if not data.get("confirmed", True):
            logger.info("Cross-check did not confirm problem %s — keeping original", problem.id)
            return problem
        return problem.model_copy(update={
            "legal_reasoning": data.get("legal_reasoning") or problem.legal_reasoning,
            "affected_articles": data.get("affected_articles") or problem.affected_articles,
        })
    except Exception as exc:
        logger.error("Cross-check enrichment failed for problem %s: %s", problem.id, exc)
        return problem


async def _second_pass(domain: str, problems: list[Problem]) -> list[Problem]:
    """For each HIGH severity problem, search Nia for related laws and re-confirm
    contradictions with Claude using specific article-level comparisons."""
    high_problems = [p for p in problems if p.severity == Severity.HIGH]
    if not high_problems:
        logger.info("No HIGH severity problems — skipping second pass")
        return problems

    logger.info("Second pass: cross-checking %d HIGH severity problems", len(high_problems))

    enriched: dict[str, Problem] = {}
    for problem in high_problems:
        queries = _build_cross_check_queries(problem)
        related: list[NiaSearchResult] = []
        for query in queries:
            try:
                results = await nia_client.search(query)
                related.extend(results)
                logger.info("Cross-check query '%s': %d results", query, len(results))
            except Exception as exc:
                logger.error("Cross-check Nia search failed for '%s': %s", query, exc)

        related = _dedup_fragments(related)
        if not related:
            logger.warning("No related fragments found for problem %s — skipping", problem.id)
            continue

        updated = await _enrich_with_cross_check(problem, related)
        enriched[problem.id] = updated
        logger.info("Cross-check complete for problem %s", problem.id)

    return [enriched.get(p.id, p) for p in problems]


async def run_audit(domain: str) -> list[Problem]:
    """
    Full audit pipeline:
    1. Query Nia with domain-specific queries
    2. Deduplicate results
    3. Batch into Claude-sized chunks
    4. Claude audits each batch
    5. Collect + sort problems by severity
    """
    if domain not in DOMAIN_QUERIES:
        raise ValueError(f"Unknown domain: {domain}")

    _audit_status[domain] = AuditStatus(
        status="running", domain=domain, total_batches=0, completed_batches=0
    )

    # Step 1: determine queries — prefer plan-generated, fall back to 6 fixed
    plan_qs = _plan_queries.get(domain, [])
    if plan_qs:
        targeted_queries = plan_qs
        logger.info("Using %d plan-generated queries for domain '%s'", len(targeted_queries), domain)
    else:
        targeted_queries = [
            domain,
            f"{domain} устаревшие нормы",
            f"{domain} противоречия в законодательстве",
            f"{domain} дублирование норм",
            f"{domain} утратил силу",
            f"{domain} правовые пробелы",
        ]

    all_fragments: list[NiaSearchResult] = []
    for query in targeted_queries:
        try:
            results = await nia_client.search(query)
            all_fragments.extend(results)
            logger.info("Nia query '%s': %d results", query, len(results))
        except Exception as exc:
            logger.error("Nia search failed for '%s': %s", query, exc)

    # Step 2: deduplicate; fall back to static laws if Nia returned nothing
    unique = _dedup_fragments(all_fragments)
    if not unique:
        logger.warning("Nia returned 0 results for '%s' — loading fallback_laws.json", domain)
        unique = _load_fallback(domain)
    if not unique:
        _audit_status[domain] = AuditStatus(
            status="error",
            domain=domain,
            error="Nia returned no results and fallback is empty.",
        )
        return []

    logger.info("Fragments: %d total, %d unique", len(all_fragments), len(unique))

    # Step 3: token-capped batches
    batches = _token_cap_batches(unique)
    logger.info(
        "Domain '%s': %d fragments → %d token-capped batches (parallel)",
        domain, len(unique), len(batches),
    )
    _audit_status[domain].total_batches = len(batches)

    # Step 4: run all batches in parallel
    batch_results = await asyncio.gather(
        *[_audit_batch_async(batch, domain, i) for i, batch in enumerate(batches)],
        return_exceptions=False,
    )
    all_problems: list[Problem] = [p for probs in batch_results for p in probs]
    _audit_status[domain].completed_batches = len(batches)
    _audit_status[domain].problems_found = len(all_problems)

    # Step 5: sort by severity
    all_problems.sort(key=lambda p: SEVERITY_ORDER.get(p.severity, 99))

    # Step 6: second pass — cross-check HIGH severity problems against related laws
    all_problems = await _second_pass(domain, all_problems)

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
