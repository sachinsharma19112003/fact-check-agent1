"""
pipeline.py
-----------
Orchestrates the full flow: PDF -> extracted claims -> verified results.

Claims are verified concurrently (bounded thread pool) since each
verification is dominated by network I/O (2 LLM calls + 1 search call per
claim, all independent of each other). For a 15-claim document this is the
difference between ~90 seconds sequential and ~15-20 seconds concurrent --
directly relevant to a usable demo experience on Streamlit Cloud's free tier.
"""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed

from src.extractor import ExtractionError, extract_claims
from src.models import Claim, PipelineReport, VerificationResult, Verdict
from src.pdf_reader import PDFExtractionError, combine_pages, extract_pages
from src.verifier import VerificationError, verify_claim

MAX_WORKERS = 5  # bounded to stay polite to API rate limits


def run_pipeline(
    file_bytes: bytes,
    filename: str,
    progress_callback: Callable[[str, int, int], None] | None = None,
) -> PipelineReport:
    """Run the full extract -> verify pipeline on an uploaded PDF.

    progress_callback(stage_label, completed_count, total_count) is called
    as work completes, so the Streamlit UI can render a live progress bar
    instead of a frozen spinner during the ~15-30s verification phase.
    """
    errors: list[str] = []

    def report_progress(label: str, done: int, total: int) -> None:
        if progress_callback:
            progress_callback(label, done, total)

    # --- Stage 0: PDF -> text ---
    report_progress("Reading PDF...", 0, 1)
    try:
        pages = extract_pages(file_bytes)
        document_text = combine_pages(pages)
    except PDFExtractionError as e:
        return PipelineReport(filename=filename, total_claims=0, results=[], errors=[str(e)])
    report_progress("Reading PDF...", 1, 1)

    # --- Stage 1: extract claims ---
    report_progress("Extracting claims...", 0, 1)
    try:
        claims: list[Claim] = extract_claims(document_text)
    except ExtractionError as e:
        return PipelineReport(filename=filename, total_claims=0, results=[], errors=[str(e)])
    report_progress("Extracting claims...", 1, 1)

    if not claims:
        return PipelineReport(
            filename=filename,
            total_claims=0,
            results=[],
            errors=["No checkable factual claims were found in this document."],
        )

    # --- Stage 2+3: verify each claim concurrently ---
    results: list[VerificationResult] = []
    total = len(claims)
    report_progress("Verifying claims against live web data...", 0, total)

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_to_claim = {pool.submit(_safe_verify, c): c for c in claims}
        done_count = 0
        for future in as_completed(future_to_claim):
            result = future.result()
            results.append(result)
            done_count += 1
            report_progress("Verifying claims against live web data...", done_count, total)

    # Preserve original document order in the final report rather than
    # whatever order the thread pool happened to finish in.
    order = {c.id: i for i, c in enumerate(claims)}
    results.sort(key=lambda r: order.get(r.claim.id, 0))

    return PipelineReport(filename=filename, total_claims=len(claims), results=results, errors=errors)


def _safe_verify(claim: Claim) -> VerificationResult:
    """Wrap verify_claim so one claim's API failure can't kill the whole batch."""
    try:
        return verify_claim(claim)
    except VerificationError as e:
        return VerificationResult(
            claim=claim,
            verdict=Verdict.UNVERIFIABLE,
            confidence=0.0,
            explanation=f"Verification failed due to a system error: {e}",
            correct_fact=None,
            evidence=[],
            search_query_used="",
        )
