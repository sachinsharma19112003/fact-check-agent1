"""
extractor.py
------------
Stage 1 of the pipeline: read the document text and pull out a clean,
de-duplicated list of checkable factual claims.

Design choice: we force structured output via Anthropic's tool-use
("function calling") feature rather than asking the model to "return JSON"
in free text. Tool-use schemas are validated server-side, which all but
eliminates the malformed-JSON parsing failures that plague prompt-only
approaches -- a meaningfully more robust pattern for production code.
"""

from __future__ import annotations

import uuid

import anthropic

from src.config import settings
from src.models import Claim, ClaimType

_EXTRACTION_TOOL = {
    "name": "record_claims",
    "description": (
        "Record the list of distinct, independently fact-checkable claims "
        "found in the document."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "claims": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {
                            "type": "string",
                            "description": (
                                "The claim, quoted as closely as possible to "
                                "the source text -- a single self-contained "
                                "factual statement."
                            ),
                        },
                        "claim_type": {
                            "type": "string",
                            "enum": ["Statistic", "Date", "Financial", "Technical", "Other"],
                        },
                        "context": {
                            "type": "string",
                            "description": "One surrounding sentence for disambiguation.",
                        },
                        "page_number": {
                            "type": "integer",
                            "description": "Page number this claim appears on, from the [p.N] markers.",
                        },
                    },
                    "required": ["text", "claim_type"],
                },
            }
        },
        "required": ["claims"],
    },
}

_SYSTEM_PROMPT = """You are a meticulous fact-checking analyst. Your only job \
right now is EXTRACTION, not verification.

Read the supplied document text and identify every discrete, independently \
verifiable factual claim -- specifically:
  - Statistics and percentages ("conversion rates increased by 47%")
  - Dates and timeframes ("launched in March 2019", "within the last 6 months")
  - Financial figures ("raised $50M Series B", "valued at $2.1 billion")
  - Technical/quantitative specs ("processes 10,000 requests per second")

Rules:
  - Extract claims as standalone, self-contained statements -- a reader should \
not need the rest of the document to understand what is being claimed.
  - Do NOT extract vague marketing fluff ("we are the best in the industry") \
that has no checkable fact in it.
  - Do NOT invent claims that are not in the text.
  - Merge true duplicates, but keep claims separate if they cite different \
numbers/sources even if topically similar.
  - Use the page markers like [p.3] in the source text to set page_number.
  - Call the record_claims tool exactly once with the full list."""


class ExtractionError(Exception):
    pass


def extract_claims(document_text: str) -> list[Claim]:
    """Send the full document text to Claude and get back structured Claim objects."""
    if not settings.ANTHROPIC_API_KEY:
        raise ExtractionError("ANTHROPIC_API_KEY is not configured.")

    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    try:
        response = client.messages.create(
            model=settings.CLAUDE_MODEL,
            max_tokens=4000,
            system=_SYSTEM_PROMPT,
            tools=[_EXTRACTION_TOOL],
            tool_choice={"type": "tool", "name": "record_claims"},
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Extract all checkable claims from this document:\n\n"
                        f"{document_text}"
                    ),
                }
            ],
        )
    except anthropic.APIError as e:
        raise ExtractionError(f"Claude API error during extraction: {e}") from e

    tool_use_block = next(
        (b for b in response.content if b.type == "tool_use"), None
    )
    if tool_use_block is None:
        raise ExtractionError("Claude did not return structured claim data.")

    raw_claims = tool_use_block.input.get("claims", [])

    claims: list[Claim] = []
    seen_texts: set[str] = set()
    for rc in raw_claims:
        text = (rc.get("text") or "").strip()
        if not text or text.lower() in seen_texts:
            continue
        seen_texts.add(text.lower())

        try:
            claim_type = ClaimType(rc.get("claim_type", "Other"))
        except ValueError:
            claim_type = ClaimType.OTHER

        claims.append(
            Claim(
                id=str(uuid.uuid4())[:8],
                text=text,
                claim_type=claim_type,
                context=(rc.get("context") or "").strip(),
                page_number=rc.get("page_number"),
            )
        )

    # Cap to keep search/verification costs and demo runtime predictable.
    return claims[: settings.MAX_CLAIMS_PER_DOC]
