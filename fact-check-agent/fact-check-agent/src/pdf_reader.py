"""
pdf_reader.py
-------------
Extracts clean text from an uploaded PDF, page by page, so downstream
claim extraction can cite a page number (useful for the UI and for the
user to go verify the original document themselves).

Uses pypdf (pure Python, no system dependencies) so it installs cleanly
on Streamlit Cloud without extra apt packages.
"""

from __future__ import annotations

from dataclasses import dataclass
from io import BytesIO

from pypdf import PdfReader

from src.config import settings


@dataclass
class PageText:
    page_number: int  # 1-indexed, matches how a human would reference it
    text: str


class PDFExtractionError(Exception):
    pass


def extract_pages(file_bytes: bytes) -> list[PageText]:
    """Parse a PDF's bytes into per-page text blocks.

    Raises PDFExtractionError on corrupt/encrypted/empty files so the
    caller (Streamlit UI) can show a friendly message instead of a stack trace.
    """
    try:
        reader = PdfReader(BytesIO(file_bytes))
    except Exception as e:
        raise PDFExtractionError(f"Could not open this file as a PDF: {e}") from e

    if reader.is_encrypted:
        try:
            reader.decrypt("")  # try empty password first (common export setting)
        except Exception:
            raise PDFExtractionError(
                "This PDF is password-protected. Please upload an unlocked file."
            )

    num_pages = len(reader.pages)
    if num_pages == 0:
        raise PDFExtractionError("This PDF has no pages.")

    if num_pages > settings.MAX_PDF_PAGES:
        raise PDFExtractionError(
            f"This PDF has {num_pages} pages, which exceeds the "
            f"{settings.MAX_PDF_PAGES}-page limit for this demo. "
            f"Please upload a shorter document."
        )

    pages: list[PageText] = []
    for i, page in enumerate(reader.pages, start=1):
        try:
            text = page.extract_text() or ""
        except Exception:
            text = ""  # skip unreadable pages rather than failing the whole doc
        if text.strip():
            pages.append(PageText(page_number=i, text=text.strip()))

    if not pages:
        raise PDFExtractionError(
            "No extractable text found. This PDF may be scanned images "
            "without an OCR text layer."
        )

    return pages


def combine_pages(pages: list[PageText], max_chars: int = 40_000) -> str:
    """Flatten pages into one string with inline page markers, e.g. [p.3],
    so the LLM can report which page a claim came from. Truncates very
    long documents to keep the extraction prompt within a sane token budget.
    """
    parts = []
    running_len = 0
    for p in pages:
        block = f"\n\n[p.{p.page_number}]\n{p.text}"
        if running_len + len(block) > max_chars:
            parts.append("\n\n[...document truncated for length...]")
            break
        parts.append(block)
        running_len += len(block)
    return "".join(parts).strip()
