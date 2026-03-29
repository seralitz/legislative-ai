from __future__ import annotations

import logging
from typing import Any, Optional

import httpx

from backend.config import NIA_API_KEY, NIA_BASE_URL, MAX_NIA_RESULTS, ADILET_URL
from backend.models import NiaSearchResult

logger = logging.getLogger(__name__)

_TIMEOUT = httpx.Timeout(60.0, connect=15.0)


def _headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {NIA_API_KEY}",
        "Content-Type": "application/json",
    }


async def create_data_source(url: str) -> dict[str, Any]:
    """Register a website for Nia to index via the /sources endpoint."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{NIA_BASE_URL}/sources",
            headers=_headers(),
            json={"type": "documentation", "url": url},
        )
        resp.raise_for_status()
        data = resp.json()
        logger.info("Nia source created: %s", data)
        return data


async def check_source_status(source_id: str) -> dict[str, Any]:
    """Check indexing status for a source."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            f"{NIA_BASE_URL}/sources/{source_id}",
            headers=_headers(),
        )
        resp.raise_for_status()
        return resp.json()


async def search(query: str, limit: int = MAX_NIA_RESULTS) -> list[NiaSearchResult]:
    """
    Search for Kazakhstan law content. Uses web-search with site-scoped
    queries to get content from adilet.zan.kz, then falls back to
    universal-search if the data source is indexed.
    """
    results: list[NiaSearchResult] = []

    scoped_query = f"Казахстан закон {query} site:adilet.zan.kz"
    try:
        results = await _web_search(scoped_query, limit)
        if results:
            logger.info("Web search for '%s': %d results", query, len(results))
            return results
    except Exception as exc:
        logger.warning("Web search failed for '%s': %s", query, exc)

    try:
        results = await _universal_search(query, limit)
        logger.info("Universal search for '%s': %d results", query, len(results))
    except Exception as exc:
        logger.error("Universal search also failed for '%s': %s", query, exc)

    return results


async def _web_search(query: str, limit: int) -> list[NiaSearchResult]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{NIA_BASE_URL}/web-search",
            headers=_headers(),
            json={"query": query},
        )
        resp.raise_for_status()
        payload = resp.json()

    logger.info("Nia web-search raw keys: %s", list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__)
    logger.info("Nia web-search raw payload: %s", str(payload)[:1000])
    return _parse_web_results(payload, limit)


async def _universal_search(query: str, limit: int) -> list[NiaSearchResult]:
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            f"{NIA_BASE_URL}/universal-search",
            headers=_headers(),
            json={
                "query": query,
                "data_sources": [ADILET_URL],
                "limit": limit,
            },
        )
        resp.raise_for_status()
        payload = resp.json()

    logger.info("Nia universal-search raw keys: %s", list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__)
    logger.debug("Nia universal-search raw payload: %s", str(payload)[:500])

    results: list[NiaSearchResult] = []
    raw_items = (
        payload if isinstance(payload, list)
        else payload.get("results", payload.get("data", payload.get("items", [])))
    )
    for item in raw_items:
        if isinstance(item, str):
            results.append(NiaSearchResult(content=item))
            continue
        results.append(
            NiaSearchResult(
                content=item.get("content", item.get("text", item.get("snippet", item.get("description", "")))),
                url=item.get("url", item.get("source_url", item.get("link", ""))),
                title=item.get("title", item.get("name", "")),
                score=float(item.get("score", item.get("relevance", 0.0))),
            )
        )
    return [r for r in results if r.content][:limit]


def _parse_web_results(payload: dict, limit: int) -> list[NiaSearchResult]:
    results: list[NiaSearchResult] = []

    if isinstance(payload, list):
        raw_items = payload
    else:
        # Try every known key Nia might use
        raw_items = (
            payload.get("organic_results")
            or payload.get("organic")
            or payload.get("web_results")
            or payload.get("other_content")
            or payload.get("results")
            or payload.get("items")
            or payload.get("data")
            or []
        )

    for item in raw_items:
        if isinstance(item, str):
            results.append(NiaSearchResult(content=item))
            continue
        content = (
            item.get("snippet")
            or item.get("description")
            or item.get("summary")
            or item.get("content")
            or item.get("text")
            or ""
        )
        if content:
            results.append(
                NiaSearchResult(
                    content=content,
                    url=item.get("url", item.get("link", item.get("source_url", ""))),
                    title=item.get("title", item.get("name", "")),
                )
            )

    # Original format: documentation + github_repos
    for doc in payload.get("documentation", []) if isinstance(payload, dict) else []:
        content_parts = []
        if doc.get("summary"):
            content_parts.append(doc["summary"])
        for hl in doc.get("highlights", []):
            content_parts.append(hl)
        if content_parts:
            results.append(
                NiaSearchResult(
                    content="\n\n".join(content_parts),
                    url=doc.get("url", ""),
                    title=doc.get("title", ""),
                )
            )

    for repo in payload.get("github_repos", []) if isinstance(payload, dict) else []:
        if repo.get("description"):
            results.append(
                NiaSearchResult(
                    content=repo.get("description", ""),
                    url=repo.get("url", ""),
                    title=repo.get("name", ""),
                )
            )

    return results[:limit]
