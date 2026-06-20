# Fact-Check Agent — Truth Layer

An automated claim-verification tool that reads a PDF, extracts checkable factual
claims (stats, dates, financial figures, technical specs), cross-references each
one against **live web search results**, and reports a grounded verdict:
**Verified / Inaccurate / False / Unverifiable**.

Built for the "Fact-Check Agent" assignment — see [Assignment context](#assignment-context) below.

---

## Live Demo

| | |
|---|---|
| **App** | `<ADD YOUR DEPLOYED URL HERE AFTER DEPLOYING — e.g. https://your-app.streamlit.app>` |
| **Demo video** | `<ADD YOUR 30s SCREEN RECORDING LINK HERE>` |

> Deploy in ~10 minutes using the [Deployment](#deployment-streamlit-community-cloud) section below — this repo is deploy-ready as-is.

---

## How it works

```
PDF upload
    |
    v
[1. Read PDF]      pypdf, page-by-page text extraction
    |               (handles encrypted/corrupt/scanned-image PDFs gracefully)
    v
[2. Extract        Claude (tool-use / forced JSON schema) reads the full
   claims]          document and identifies discrete, checkable claims:
    |               statistics, dates, financial figures, technical specs.
    |               De-duplicated, capped at 15 claims/doc.
    v
[3. Verify         For EACH claim, concurrently (bounded thread pool):
   per claim]         a. Claude generates a targeted search query
    |                 b. Google Custom Search retrieves live results
    |                 c. Claude judges the claim using ONLY the retrieved
    |                    evidence (not its own training data)
    v
[4. Report]        Verdict + confidence + explanation + the "correct fact"
                    (if wrong) + source links, rendered in the Streamlit UI.
```

### Why this architecture (design notes for reviewers)

- **Structured output via tool-use, not prompted JSON.** Claim extraction and
  verdicts are returned through Anthropic's tool-calling schema
  (`tool_choice={"type": "tool", ...}`), which is server-validated. This
  eliminates the "model returned malformed JSON" failure class that's common
  with prompt-only `"please respond in JSON"` approaches.

- **Verdicts are grounded, not memorized.** The verdict-judging prompt
  explicitly instructs Claude to base its answer **only on the retrieved
  search snippets**, not on its own knowledge — because the model's training
  data can itself be stale. This is what makes the tool an actual "truth
  layer" rather than a second source of hallucination.

- **A 4th verdict bucket: `Unverifiable`.** The brief specifies three buckets
  (Verified / Inaccurate / False). We added a 4th because forcing a verdict
  when live search genuinely returns no relevant evidence produces false
  confidence — arguably worse than admitting uncertainty. It's visually
  distinct in the UI so it's never confused with "False."

- **Two-step query reformulation.** Rather than searching the claim text
  verbatim, a separate Claude call first reformulates it into a targeted
  search query. Searching `"the platform processes 10,000 requests per
  second"` verbatim usually just finds the same marketing page repeating the
  claim; a reformulated query finds independent sources more reliably.

- **Bounded concurrency.** All claims are verified in parallel via a 5-worker
  thread pool (I/O-bound: 2 LLM calls + 1 search call per claim, all
  independent). This took a 15-claim document from ~90s sequential to
  ~15–20s — the difference between a usable and unusable demo on free-tier
  hosting. Verified end-to-end in `test_assets/test_pipeline_mocked.py`,
  including confirming original document order is preserved despite
  out-of-order thread completion.

- **Graceful degradation everywhere.** Corrupt/encrypted/scanned PDFs, search
  API quota exhaustion, and individual claim-verification failures all
  degrade to a friendly error or an `Unverifiable` result instead of crashing
  the whole batch — so one bad claim or a rate limit never takes down the run.

---

## Repository structure

```
fact-check-agent/
├── app.py                      # Streamlit UI (entry point)
├── requirements.txt
├── .env.example                 # template for local secrets
├── .streamlit/config.toml       # theme
├── src/
│   ├── config.py                 # settings; reads st.secrets (cloud) or .env (local)
│   ├── models.py                 # typed dataclasses: Claim, Evidence, VerificationResult...
│   ├── pdf_reader.py              # PDF -> per-page text, with error handling
│   ├── extractor.py               # Stage 1: Claude claim extraction (tool-use)
│   ├── search_client.py           # Google Custom Search API wrapper
│   ├── verifier.py                # Stage 2+3: query generation + grounded verdict
│   └── pipeline.py                # orchestrates the above, with concurrency
└── test_assets/
    ├── make_trap_doc.py           # generates a synthetic "trap document" PDF
    └── test_pipeline_mocked.py    # mocked end-to-end tests (no API cost)
```

---

## Local setup

**Requirements:** Python 3.10+

```bash
git clone <your-repo-url>
cd fact-check-agent
python -m venv venv && source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` and fill in:

| Key | Where to get it |
|---|---|
| `ANTHROPIC_API_KEY` | [console.anthropic.com/settings/keys](https://console.anthropic.com/settings/keys) |
| `GOOGLE_API_KEY` | [console.cloud.google.com](https://console.cloud.google.com/) → enable "Custom Search API" → Credentials |
| `GOOGLE_CSE_ID` | [programmablesearchengine.google.com](https://programmablesearchengine.google.com/) → create a search engine → set **"Search the entire web"** → copy the Search engine ID |

Run it:

```bash
streamlit run app.py
```

Open `http://localhost:8501`, upload a PDF, click **Run Fact-Check**.

### Run the test suite (no API cost)

```bash
python test_assets/make_trap_doc.py          # generates a sample trap PDF
python test_assets/test_pipeline_mocked.py   # mocked end-to-end pipeline tests
```

---

## Deployment (Streamlit Community Cloud)

1. Push this repo to GitHub (public, or private with Streamlit Cloud given access).
2. Go to [share.streamlit.io](https://share.streamlit.io) → **New app** → select your repo, branch `main`, main file `app.py`.
3. Before deploying, click **Advanced settings → Secrets** and paste:
   ```toml
   ANTHROPIC_API_KEY = "sk-ant-..."
   CLAUDE_MODEL = "claude-sonnet-4-6"
   GOOGLE_API_KEY = "AIza..."
   GOOGLE_CSE_ID = "your-cse-id"
   ```
4. Click **Deploy**. First build takes ~2–3 minutes.
5. Test it by uploading `test_assets/trap_document.pdf` (or your own PDF) at the live URL.

`src/config.py` reads from `st.secrets` automatically when running on Streamlit
Cloud — no code changes needed between local and deployed environments.

---

## Known limitations

- **Scanned/image-only PDFs** without an OCR text layer aren't supported (no
  OCR pipeline in this version — flagged honestly with a clear error rather
  than silently returning nothing).
- **Search quality ceiling.** Verdicts are only as good as what Google Custom
  Search surfaces; very recent events or paywalled sources may return thin
  evidence, correctly producing `Unverifiable` rather than a guess.
- **Claim cap of 15 per document** (configurable via `MAX_CLAIMS_PER_DOC`) to
  keep demo runtime and API cost predictable; raise it for production use.
- **No persistent storage** — each run is stateless within the browser
  session; refreshing the page clears results.

## Possible extensions (not implemented, out of scope for this assignment)

- OCR fallback for scanned PDFs (`pytesseract` + `pdf2image`)
- Source credibility weighting (prioritize `.gov`/`.edu`/major outlets over
  unknown blogs)
- Export report as PDF/CSV
- Multi-document batch mode

---

## Assignment context

This repository fulfills **Part 2** of the assignment brief: *"Build a
deployed web app where users upload a PDF for Automated Factchecking,"*
extracting claims, verifying them against live web data, and reporting
Verified / Inaccurate / False (extended here with Unverifiable) verdicts with
the corrected fact where applicable.
