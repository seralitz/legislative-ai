from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class ProblemType(str, Enum):
    OUTDATED = "outdated"
    CONTRADICTION = "contradiction"
    REDUNDANT = "redundant"


class Severity(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


SEVERITY_ORDER = {Severity.HIGH: 0, Severity.MEDIUM: 1, Severity.LOW: 2}


class Problem(BaseModel):
    id: str = Field(default="", description="Auto-generated identifier")
    law_title: str
    article: str
    problem_type: ProblemType
    severity: Severity
    description: str
    affected_articles: list[str] = Field(default_factory=list)
    law_text: str = Field(default="", description="Source law fragment from Nia")
    domain: str = Field(default="")
    source_url: str = Field(default="")


class AuditRequest(BaseModel):
    domain: str = Field(
        default="здравоохранение",
        description="Domain key from DOMAIN_QUERIES",
    )


class AuditStatus(BaseModel):
    status: str  # "running" | "completed" | "error"
    domain: str
    total_batches: int = 0
    completed_batches: int = 0
    problems_found: int = 0
    error: Optional[str] = None


class AuditResult(BaseModel):
    domain: str
    problems: list[Problem]
    total: int
    status: str


class FixRequest(BaseModel):
    problem: Problem
    law_text: str = Field(default="", description="Full law text for context")


class FixResponse(BaseModel):
    problem_id: str
    proposed_fix: str
    explanation: str
    affected_articles: list[str] = Field(default_factory=list)


class NiaSearchResult(BaseModel):
    content: str
    url: str = ""
    title: str = ""
    score: float = 0.0
