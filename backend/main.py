from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend.audit_pipeline import get_results, get_status, run_audit
from backend.config import ADILET_URL, DOMAIN_LABELS, DOMAIN_QUERIES
from backend.fix_pipeline import generate_fix
from backend.models import (
    AuditRequest,
    AuditResult,
    AuditStatus,
    FixRequest,
    FixResponse,
    Problem,
)
from backend import nia_client

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Legislative Auditor starting up")
    logger.info("Frontend dir: %s (exists=%s)", FRONTEND_DIR, FRONTEND_DIR.exists())
    yield
    logger.info("Legislative Auditor shutting down")


app = FastAPI(
    title="Legislative Auditor — Kazakhstan",
    description="AI-powered legislative audit system for Kazakhstan laws",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------


@app.get("/api/health")
async def health():
    return {"status": "ok"}


@app.get("/api/domains")
async def list_domains():
    return {
        "domains": [
            {"key": key, "label": label}
            for key, label in DOMAIN_LABELS.items()
        ]
    }


@app.post("/api/nia/index")
async def trigger_nia_index():
    """One-time: tell Nia to crawl adilet.zan.kz."""
    try:
        result = await nia_client.create_data_source(ADILET_URL)
        return {"status": "indexing_started", "detail": result}
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc))


@app.post("/api/audit/run")
async def start_audit(req: AuditRequest, background_tasks: BackgroundTasks):
    if req.domain not in DOMAIN_QUERIES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown domain '{req.domain}'. Available: {list(DOMAIN_QUERIES.keys())}",
        )
    current = get_status(req.domain)
    if current and current.status == "running":
        return {"status": "already_running", "detail": current.model_dump()}

    background_tasks.add_task(_run_audit_bg, req.domain)
    return {"status": "started", "domain": req.domain}


async def _run_audit_bg(domain: str):
    try:
        await run_audit(domain)
    except Exception as exc:
        logger.exception("Background audit failed for '%s'", domain)


@app.get("/api/audit/status")
async def audit_status(domain: str = "здравоохранение") -> AuditStatus:
    status = get_status(domain)
    if not status:
        return AuditStatus(status="idle", domain=domain)
    return status


@app.get("/api/audit/results")
async def audit_results(
    domain: str = "здравоохранение",
    page: int = 1,
    page_size: int = 50,
    problem_type: Optional[str] = None,
    severity: Optional[str] = None,
) -> AuditResult:
    problems = get_results(domain)

    if problem_type:
        problems = [p for p in problems if p.problem_type.value == problem_type]
    if severity:
        problems = [p for p in problems if p.severity.value == severity]

    total = len(problems)
    start = (page - 1) * page_size
    end = start + page_size

    return AuditResult(
        domain=domain,
        problems=problems[start:end],
        total=total,
        status=get_status(domain).status if get_status(domain) else "idle",
    )


@app.post("/api/fix")
async def propose_fix(req: FixRequest) -> FixResponse:
    try:
        return await generate_fix(req.problem, req.law_text)
    except Exception as exc:
        logger.exception("Fix generation failed")
        raise HTTPException(status_code=500, detail=str(exc))


# ---------------------------------------------------------------------------
# Serve Frontend
# ---------------------------------------------------------------------------

app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="frontend")


@app.get("/")
async def serve_index():
    index = FRONTEND_DIR / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="Frontend not found")
    return FileResponse(str(index))


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.main:app", host="0.0.0.0", port=8000, reload=True)
