"""
Mocked end-to-end test of the pipeline's ORCHESTRATION logic (extraction
parsing, concurrency, error handling, result ordering) without making real
paid API calls. This does not validate the *quality* of Claude's fact-checking
judgment -- only that the code paths are correct and won't crash in production.
"""
import sys
import types
from unittest.mock import MagicMock, patch

sys.path.insert(0, "/home/claude/fact-check-agent")

from src.models import Claim, ClaimType, Evidence, Verdict


def fake_tool_response(tool_name, input_dict):
    """Build a fake anthropic.Message-like object with one tool_use block."""
    block = types.SimpleNamespace(type="tool_use", name=tool_name, input=input_dict)
    return types.SimpleNamespace(content=[block])


def test_extractor():
    print("=== Testing extractor.py ===")
    from src import extractor

    fake_claims = {
        "claims": [
            {"text": "raised $50 million in Series B financing", "claim_type": "Financial", "context": "funding", "page_number": 1},
            {"text": "processes over 2 trillion API requests per day", "claim_type": "Statistic", "context": "scale", "page_number": 1},
            {"text": "founded in 2015", "claim_type": "Date", "context": "history", "page_number": 1},
            # duplicate (case-insensitive) -- should be filtered
            {"text": "Founded in 2015", "claim_type": "Date", "context": "history dup", "page_number": 1},
        ]
    }

    mock_client = MagicMock()
    mock_client.messages.create.return_value = fake_tool_response("record_claims", fake_claims)

    with patch("src.extractor.anthropic.Anthropic", return_value=mock_client), \
         patch("src.extractor.settings.ANTHROPIC_API_KEY", "fake-key"):
        claims = extractor.extract_claims("dummy document text [p.1]")

    assert len(claims) == 3, f"Expected 3 deduped claims, got {len(claims)}"
    assert claims[0].claim_type == ClaimType.FINANCIAL
    assert all(c.id for c in claims), "All claims need an id"
    print(f"  PASS: extracted {len(claims)} claims, dedup worked correctly")
    return claims


def test_verifier_verified_case():
    print("=== Testing verifier.py (Verified case) ===")
    from src import verifier

    claim = Claim(id="abc123", text="founded in 2015", claim_type=ClaimType.DATE, context="history", page_number=1)

    mock_evidence = [Evidence(title="NexaCloud About", url="https://nexacloud.example/about",
                               snippet="NexaCloud was founded in 2015.", source_domain="nexacloud.example")]

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        fake_tool_response("generate_search_query", {"query": "NexaCloud founded year"}),
        fake_tool_response("record_verdict", {
            "verdict": "Verified", "confidence": 0.92,
            "explanation": "Independent source confirms 2015 founding date.",
            "correct_fact": None,
        }),
    ]

    with patch("src.verifier.anthropic.Anthropic", return_value=mock_client), \
         patch("src.verifier.settings.ANTHROPIC_API_KEY", "fake-key"), \
         patch("src.verifier.web_search", return_value=mock_evidence):
        result = verifier.verify_claim(claim)

    assert result.verdict == Verdict.VERIFIED
    assert result.confidence == 0.92
    assert result.correct_fact is None
    print(f"  PASS: verdict={result.verdict.value}, confidence={result.confidence}")


def test_verifier_false_case():
    print("=== Testing verifier.py (False/fabricated stat case) ===")
    from src import verifier

    claim = Claim(id="xyz789", text="processes over 2 trillion API requests per day",
                  claim_type=ClaimType.STATISTIC, context="scale", page_number=1)

    # Simulate a real search returning nothing supporting this absurd claim
    mock_evidence = [Evidence(title="Cloudflare network report", url="https://cloudflare.example/report",
                               snippet="Cloudflare processes around 50 million requests per second globally.",
                               source_domain="cloudflare.example")]

    mock_client = MagicMock()
    mock_client.messages.create.side_effect = [
        fake_tool_response("generate_search_query", {"query": "NexaCloud API requests per day benchmark"}),
        fake_tool_response("record_verdict", {
            "verdict": "False", "confidence": 0.85,
            "explanation": "No evidence supports this figure; it exceeds even the largest known providers by orders of magnitude.",
            "correct_fact": "Industry leaders like Cloudflare report roughly 50 million requests per second (~4.3 trillion/day) at most, and no independent source corroborates NexaCloud's specific claim.",
        }),
    ]

    with patch("src.verifier.anthropic.Anthropic", return_value=mock_client), \
         patch("src.verifier.settings.ANTHROPIC_API_KEY", "fake-key"), \
         patch("src.verifier.web_search", return_value=mock_evidence):
        result = verifier.verify_claim(claim)

    assert result.verdict == Verdict.FALSE
    assert result.correct_fact is not None
    print(f"  PASS: verdict={result.verdict.value}, correct_fact found={bool(result.correct_fact)}")


def test_verifier_search_failure_degrades_gracefully():
    print("=== Testing verifier.py (search API failure -> graceful Unverifiable) ===")
    from src import verifier
    from src.search_client import SearchError

    claim = Claim(id="err001", text="some claim", claim_type=ClaimType.OTHER)

    mock_client = MagicMock()
    mock_client.messages.create.return_value = fake_tool_response("generate_search_query", {"query": "some claim query"})

    with patch("src.verifier.anthropic.Anthropic", return_value=mock_client), \
         patch("src.verifier.settings.ANTHROPIC_API_KEY", "fake-key"), \
         patch("src.verifier.web_search", side_effect=SearchError("quota exceeded")):
        result = verifier.verify_claim(claim)

    assert result.verdict == Verdict.UNVERIFIABLE
    assert "quota exceeded" in result.explanation
    print(f"  PASS: degraded gracefully to {result.verdict.value} instead of crashing")


def test_pipeline_concurrency_and_ordering():
    print("=== Testing pipeline.py (concurrency + order preservation) ===")
    from src import pipeline

    claims = [
        Claim(id=f"c{i}", text=f"claim number {i}", claim_type=ClaimType.OTHER)
        for i in range(6)
    ]

    def fake_verify(claim):
        # Simulate variable completion times to actually exercise reordering logic
        import time, random
        time.sleep(random.uniform(0.01, 0.05))
        from src.models import VerificationResult
        return VerificationResult(
            claim=claim, verdict=Verdict.VERIFIED, confidence=0.9,
            explanation="ok", correct_fact=None, evidence=[], search_query_used="q",
        )

    with patch("src.pipeline.extract_pages") as mock_extract_pages, \
         patch("src.pipeline.combine_pages", return_value="dummy text"), \
         patch("src.pipeline.extract_claims", return_value=claims), \
         patch("src.pipeline.verify_claim", side_effect=fake_verify):

        mock_extract_pages.return_value = [MagicMock()]
        report = pipeline.run_pipeline(b"fake-pdf-bytes", "trap_document.pdf")

    assert report.total_claims == 6
    assert len(report.results) == 6
    # Verify original order preserved despite concurrent completion
    result_ids = [r.claim.id for r in report.results]
    assert result_ids == [f"c{i}" for i in range(6)], f"Order not preserved: {result_ids}"
    print(f"  PASS: {len(report.results)} claims processed concurrently, original order preserved")
    print(f"  Summary counts: {report.summary_counts}")


if __name__ == "__main__":
    test_extractor()
    test_verifier_verified_case()
    test_verifier_false_case()
    test_verifier_search_failure_degrades_gracefully()
    test_pipeline_concurrency_and_ordering()
    print("\nALL TESTS PASSED")
