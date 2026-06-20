"""
verifier.py
-----------
Stage 2 + 3 of the pipeline: for each extracted claim, generate a targeted
search query, retrieve live evidence, then have Claude reason ONLY over
that retrieved evidence to produce a grounded verdict.

This two-step "search query generation" -> "grounded reasoning" split
(rather than searching the claim text verbatim) matters: a claim like
"the platform processes 10,000 requests per second" searched verbatim
often returns marketing pages repeating the same claim. A reformulated
query ("Company X requests per second benchmark 2026") finds independent
sources more reliably.
"""

from __future__ import annotations

import anthropic

from src.config import settings
from src.models import Claim, Evidence, Verdict, VerificationResult
from src.search_client import SearchError, web_search

_QUERY_TOOL = {
    "name": "generate_search_query",
    "description": "Produce the single best web search query to verify this claim.",
    "input_schema": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "A concise, specific search query (under 12 words) likely "
                    "to surface independent sources confirming or refuting "
                    "the claim. Include entity names and numbers; omit filler words."
                ),
            }
        },
        "required": ["query"],
    },
}

_VERDICT_TOOL = {
    "name": "record_verdict",
    "description": "Record the final fact-check verdict for this claim based on the evidence provided.",
    "input_schema": {
        "type": "object",
        "properties": {
            "verdict": {
                "type": "string",
                "enum": ["Verified", "Inaccurate", "False", "Unverifiable"],
                "description": (
                    "Verified = evidence confirms the claim as stated. "
                    "Inaccurate = claim is in the right ballpark / was once true "
                    "but is outdated, partially wrong, or imprecise versus current data. "
                    "False = evidence directly contradicts the claim, or it appears fabricated. "
                    "Unverifiable = evidence found is insufficient or irrelevant to judge either way."
                ),
            },
            "confidence": {
                "type": "number",
                "description": "Self-reported confidence in this verdict, from 0.0 to 1.0.",
            },
            "explanation": {
                "type": "string",
                "description": "2-3 sentences explaining the reasoning, referencing what the evidence showed.",
            },
            "correct_fact": {
                "type": ["string", "null"],
                "description": (
                    "If verdict is Inaccurate or False, state the correct/current "
                    "figure or fact found in the evidence. Null if Verified or Unverifiable."
                ),
            },
        },
        "required": ["verdict", "confidence", "explanation", "correct_fact"],
    },
}

_QUERY_SYSTEM_PROMPT = (
    "You write effective fact-checking search queries. Given a claim, "
    "produce ONE search query that will find independent, authoritative "
    "sources to confirm or refute it. Call generate_search_query exactly once."
)

_VERDICT_SYSTEM_PROMPT = """You are a rigorous, skeptical fact-checker. You will be \
given a CLAIM and a set of live web SEARCH RESULTS retrieved to verify it.

Base your verdict STRICTLY on the provided search results -- not on your own \
background knowledge, since your training data may itself be outdated. If the \
search results don't clearly address the claim, say Unverifiable rather than \
guessing.

Be skeptical by default: marketing claims, vague superlatives stated as fact, \
and suspiciously round or extreme numbers deserve extra scrutiny. If evidence \
shows a different, more current number/date than the claim states, that is \
Inaccurate (not False) when the claim was plausibly true in the past. Use False \
when evidence actively contradicts the claim with no reasonable past-truth \
explanation, or when there is simply no corroborating evidence anywhere despite \
a real search being run for it.

Call record_verdict exactly once."""


class VerificationError(Exception):
    pass


def _client() -> anthropic.Anthropic:
    if not settings.ANTHROPIC_API_KEY:
        raise VerificationError("ANTHROPIC_API_KEY is not configured.")
    return anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)


def _generate_query(client: anthropic.Anthropic, claim: Claim) -> str:
    try:
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=300,
            system=_QUERY_SYSTEM_PROMPT,
            tools=[_QUERY_TOOL],
            tool_choice={"type": "tool", "name": "generate_search_query"},
            messages=[
                {
                    "role": "user",
                    "content": f"Claim: {claim.text}\nContext: {claim.context}",
                }
            ],
        )
        block = next(b for b in response.content if b.type == "tool_use")
        return block.input.get("query", claim.text)
    except Exception:
        # Fall back to the raw claim text as the query rather than failing the
        # whole pipeline over a non-critical optimization step.
        return claim.text


def _evidence_to_prompt_block(evidence: list[Evidence]) -> str:
    if not evidence:
        return "(No search results were found for this query.)"
    lines = []
    for i, e in enumerate(evidence, start=1):
        lines.append(f"[{i}] {e.title} ({e.source_domain})\n    {e.snippet}\n    {e.url}")
    return "\n".join(lines)


def _judge_claim(
    client: anthropic.Anthropic, claim: Claim, query: str, evidence: list[Evidence]
) -> VerificationResult:
    user_msg = (
        f"CLAIM: {claim.text}\n"
        f"CLAIM CONTEXT: {claim.context}\n\n"
        f"SEARCH QUERY USED: {query}\n\n"
        f"SEARCH RESULTS:\n{_evidence_to_prompt_block(evidence)}"
    )

    try:
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=600,
            system=_VERDICT_SYSTEM_PROMPT,
            tools=[_VERDICT_TOOL],
            tool_choice={"type": "tool", "name": "record_verdict"},
            messages=[{"role": "user", "content": user_msg}],
        )
    except anthropic.APIError as e:
        raise VerificationError(f"Claude API error during verdict reasoning: {e}") from e

    block = next((b for b in response.content if b.type == "tool_use"), None)
    if block is None:
        raise VerificationError("Claude did not return a structured verdict.")

    v = block.input
    try:
        verdict = Verdict(v.get("verdict", "Unverifiable"))
    except ValueError:
        verdict = Verdict.UNVERIFIABLE

    confidence = v.get("confidence", 0.5)
    try:
        confidence = max(0.0, min(1.0, float(confidence)))
    except (TypeError, ValueError):
        confidence = 0.5

    return VerificationResult(
        claim=claim,
        verdict=verdict,
        confidence=confidence,
        explanation=v.get("explanation", "").strip(),
        correct_fact=v.get("correct_fact") or None,
        evidence=evidence,
        search_query_used=query,
    )


def verify_claim(claim: Claim) -> VerificationResult:
    """Full single-claim pipeline: generate query -> search -> grounded judgment.

    Designed to degrade gracefully: a search failure produces an Unverifiable
    result with the error surfaced in the explanation, rather than crashing
    the entire batch over one bad claim.
    """
    client = _client()
    query = _generate_query(client, claim)

    try:
        evidence = web_search(query)
    except SearchError as e:
        return VerificationResult(
            claim=claim,
            verdict=Verdict.UNVERIFIABLE,
            confidence=0.0,
            explanation=f"Could not retrieve search evidence: {e}",
            correct_fact=None,
            evidence=[],
            search_query_used=query,
        )

    return _judge_claim(client, claim, query, evidence)
