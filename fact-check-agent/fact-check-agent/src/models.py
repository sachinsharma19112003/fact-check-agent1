"""
models.py
---------
Typed data structures shared across the pipeline.

Using dataclasses (instead of raw dicts) gives us:
  - IDE autocomplete + type safety while the codebase grows
  - A single source of truth for the JSON shape exchanged with Claude
  - Easy serialization to dict/JSON for the Streamlit UI and any future API layer
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Optional


class Verdict(str, Enum):
    """The four possible outcomes for a checked claim.

    Note: the brief asks for 3 buckets (Verified / Inaccurate / False).
    We add UNVERIFIABLE as a 4th, because forcing a verdict when the live
    web genuinely returns no relevant evidence produces false confidence --
    arguably worse than admitting uncertainty. This is surfaced distinctly
    in the UI so it never gets confused with "False".
    """

    VERIFIED = "Verified"
    INACCURATE = "Inaccurate"
    FALSE = "False"
    UNVERIFIABLE = "Unverifiable"


VERDICT_STYLE = {
    Verdict.VERIFIED: {"color": "#16a34a", "bg": "#dcfce7", "icon": "\u2705"},
    Verdict.INACCURATE: {"color": "#d97706", "bg": "#fef3c7", "icon": "\u26a0\ufe0f"},
    Verdict.FALSE: {"color": "#dc2626", "bg": "#fee2e2", "icon": "\u274c"},
    Verdict.UNVERIFIABLE: {"color": "#64748b", "bg": "#f1f5f9", "icon": "\u2754"},
}


class ClaimType(str, Enum):
    STATISTIC = "Statistic"
    DATE = "Date"
    FINANCIAL = "Financial"
    TECHNICAL = "Technical"
    OTHER = "Other"


@dataclass
class Claim:
    """A single factual assertion extracted from the source PDF."""

    id: str
    text: str                      # the claim, verbatim as it appears in the doc
    claim_type: ClaimType
    context: str = ""              # surrounding sentence(s) for disambiguation
    page_number: Optional[int] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["claim_type"] = self.claim_type.value
        return d


@dataclass
class Evidence:
    """A single web search result used to support/refute a claim."""

    title: str
    url: str
    snippet: str
    source_domain: str = ""


@dataclass
class VerificationResult:
    """The final, fully-reasoned outcome for one claim."""

    claim: Claim
    verdict: Verdict
    confidence: float              # 0.0 - 1.0, model's self-reported confidence
    explanation: str               # why this verdict was reached
    correct_fact: Optional[str]    # the real/current figure, if claim was wrong
    evidence: list[Evidence] = field(default_factory=list)
    search_query_used: str = ""

    def to_dict(self) -> dict:
        return {
            "claim": self.claim.to_dict(),
            "verdict": self.verdict.value,
            "confidence": self.confidence,
            "explanation": self.explanation,
            "correct_fact": self.correct_fact,
            "evidence": [asdict(e) for e in self.evidence],
            "search_query_used": self.search_query_used,
        }


@dataclass
class PipelineReport:
    """Top-level summary returned to the UI after a full run."""

    filename: str
    total_claims: int
    results: list[VerificationResult]
    errors: list[str] = field(default_factory=list)

    @property
    def summary_counts(self) -> dict:
        counts = {v.value: 0 for v in Verdict}
        for r in self.results:
            counts[r.verdict.value] += 1
        return counts
