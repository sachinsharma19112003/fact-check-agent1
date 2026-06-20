"""
app.py
------
Streamlit front-end for the Fact-Check Agent.

Design intent: this is a "truth layer" tool, so the visual language leans
into evidence/case-file conventions (docket numbers, monospace data,
verdict stamps) rather than a generic SaaS dashboard look -- it should feel
like reviewing an inspection report, not a marketing analytics panel.
"""

from __future__ import annotations

import time

import streamlit as st

from src.config import settings
from src.models import PipelineReport, Verdict, VerificationResult, VERDICT_STYLE
from src.pipeline import run_pipeline

st.set_page_config(
    page_title="Fact-Check Agent - Truth Layer",
    page_icon="\U0001F50E",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ----------------------------------------------------------------------------
# Styling
# ----------------------------------------------------------------------------
st.markdown(
    """
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Fraunces:wght@500;600&family=JetBrains+Mono:wght@400;500&family=Inter:wght@400;500;600&display=swap');

    html, body, [class*="css"] { font-family: 'Inter', sans-serif; }

    .docket-header {
        font-family: 'Fraunces', serif;
        font-size: 2.4rem;
        font-weight: 600;
        color: #1c1c1a;
        letter-spacing: -0.01em;
        margin-bottom: 0;
    }
    .docket-sub {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.82rem;
        color: #6b6a64;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        border-bottom: 1px solid #d8d6cf;
        padding-bottom: 14px;
        margin-bottom: 24px;
    }
    .claim-card {
        border: 1px solid #e4e2da;
        border-left-width: 5px;
        border-radius: 6px;
        padding: 18px 22px;
        margin-bottom: 14px;
        background: #ffffff;
    }
    .claim-text {
        font-size: 1.02rem;
        font-weight: 500;
        color: #1c1c1a;
        margin-bottom: 6px;
    }
    .verdict-stamp {
        display: inline-block;
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.72rem;
        font-weight: 600;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        padding: 3px 10px;
        border-radius: 4px;
        margin-bottom: 10px;
    }
    .meta-row {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.75rem;
        color: #8a8980;
        margin-bottom: 10px;
    }
    .explanation-text { font-size: 0.92rem; color: #3a3a36; line-height: 1.5; }
    .correct-fact-box {
        background: #f6f5f0;
        border: 1px dashed #c9c7bc;
        border-radius: 5px;
        padding: 10px 14px;
        margin-top: 10px;
        font-size: 0.88rem;
    }
    .evidence-link {
        font-family: 'JetBrains Mono', monospace;
        font-size: 0.78rem;
        color: #1d4ed8;
        text-decoration: none;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# ----------------------------------------------------------------------------
# Header
# ----------------------------------------------------------------------------
st.markdown('<div class="docket-header">Fact-Check Agent</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="docket-sub">Truth Layer - automated claim verification against live web data</div>',
    unsafe_allow_html=True,
)
st.write(settings.validate())

# ----------------------------------------------------------------------------
# Sidebar
# ----------------------------------------------------------------------------
with st.sidebar:
    st.markdown("### How it works")
    st.markdown(
        "1. **Extract** - Claude reads the PDF and isolates checkable claims "
        "(stats, dates, financial & technical figures).\n"
        "2. **Verify** - each claim is searched live on the web via Google "
        "Custom Search.\n"
        "3. **Report** - Claude reasons over the retrieved evidence only, "
        "and issues a verdict."
    )
    st.markdown("---")
    st.markdown("### Verdict key")
    for v in Verdict:
        style = VERDICT_STYLE[v]
        st.markdown(
            f'<span class="verdict-stamp" style="background:{style["bg"]};color:{style["color"]}">'
            f'{style["icon"]} {v.value}</span>',
            unsafe_allow_html=True,
        )
    st.markdown("---")

    missing = settings.validate()
    if missing:
        st.error("**Configuration incomplete:**\n" + "\n".join(f"- {m}" for m in missing))
    else:
        st.success("All API connections configured.")

# ----------------------------------------------------------------------------
# Main: upload
# ----------------------------------------------------------------------------
uploaded_file = st.file_uploader(
    "Upload a PDF to fact-check",
    type=["pdf"],
    help=f"Max {settings.MAX_PDF_PAGES} pages. Marketing decks, press releases, and reports work best.",
)

run_clicked = st.button(
    "Run Fact-Check",
    type="primary",
    disabled=uploaded_file is None or bool(settings.validate()),
    use_container_width=False,
)

# ----------------------------------------------------------------------------
# Run pipeline
# ----------------------------------------------------------------------------
if run_clicked and uploaded_file is not None:
    file_bytes = uploaded_file.read()

    progress_bar = st.progress(0, text="Starting...")
    status_text = st.empty()

    def on_progress(label: str, done: int, total: int) -> None:
        pct = int((done / total) * 100) if total else 0
        progress_bar.progress(min(pct, 100), text=f"{label} ({done}/{total})")

    start = time.time()
    report: PipelineReport = run_pipeline(file_bytes, uploaded_file.name, progress_callback=on_progress)
    elapsed = time.time() - start

    progress_bar.empty()
    status_text.empty()

    st.session_state["last_report"] = report
    st.session_state["last_elapsed"] = elapsed

# ----------------------------------------------------------------------------
# Render results
# ----------------------------------------------------------------------------
report: PipelineReport | None = st.session_state.get("last_report")

if report:
    if report.errors and not report.results:
        for err in report.errors:
            st.error(err)
    else:
        counts = report.summary_counts
        elapsed = st.session_state.get("last_elapsed", 0)

        st.markdown("### Summary")
        cols = st.columns(5)
        cols[0].metric("Claims checked", report.total_claims)
        for i, v in enumerate(Verdict, start=1):
            cols[i].metric(f"{VERDICT_STYLE[v]['icon']} {v.value}", counts.get(v.value, 0))
        st.caption(f"Completed in {elapsed:.1f}s | Source: {report.filename}")

        st.markdown("---")
        st.markdown("### Claim-by-claim report")

        filter_choice = st.multiselect(
            "Filter by verdict",
            options=[v.value for v in Verdict],
            default=[v.value for v in Verdict],
        )

        visible_results: list[VerificationResult] = [
            r for r in report.results if r.verdict.value in filter_choice
        ]

        for r in visible_results:
            style = VERDICT_STYLE[r.verdict]
            with st.container():
                st.markdown(
                    f"""
                    <div class="claim-card" style="border-left-color:{style['color']}">
                        <span class="verdict-stamp" style="background:{style['bg']};color:{style['color']}">
                            {style['icon']} {r.verdict.value} - {r.confidence*100:.0f}% confidence
                        </span>
                        <div class="claim-text">"{r.claim.text}"</div>
                        <div class="meta-row">
                            {r.claim.claim_type.value}
                            {f" - page {r.claim.page_number}" if r.claim.page_number else ""}
                            - query: "{r.search_query_used}"
                        </div>
                        <div class="explanation-text">{r.explanation}</div>
                        {f'<div class="correct-fact-box"><strong>Correct fact found:</strong> {r.correct_fact}</div>' if r.correct_fact else ''}
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                if r.evidence:
                    with st.expander(f"View {len(r.evidence)} source(s)"):
                        for e in r.evidence:
                            st.markdown(
                                f"**{e.title}** -- *{e.source_domain}*  \n"
                                f"{e.snippet}  \n"
                                f'<a class="evidence-link" href="{e.url}" target="_blank">{e.url}</a>',
                                unsafe_allow_html=True,
                            )
                            st.markdown("")

        if report.errors:
            st.warning("Some non-fatal issues occurred:\n" + "\n".join(f"- {e}" for e in report.errors))
else:
    st.info("Upload a PDF above and click **Run Fact-Check** to begin.")
