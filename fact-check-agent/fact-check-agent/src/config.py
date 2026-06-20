"""
config.py
---------
Centralized configuration. Reads secrets in this priority order:
  1. Streamlit secrets (st.secrets)   -> used in deployed Streamlit Cloud env
  2. Environment variables (.env)      -> used in local dev

This dual-path is what lets the exact same codebase run locally during
development and on Streamlit Cloud at deploy time with zero code changes.
"""

from __future__ import annotations

import os

try:
    import streamlit as st
    _HAS_STREAMLIT = True
except ImportError:
    _HAS_STREAMLIT = False

from dotenv import load_dotenv

load_dotenv()  # no-op in prod if no .env file exists


def _get_secret(key: str, default: str = "") -> str:
    try:
        import streamlit as st
        try:
            return str(st.secrets[key])
        except Exception:
            pass
    except Exception:
        pass

    return os.getenv(key, default)

class Settings:
    # --- Anthropic (claim extraction + reasoning over evidence) ---
    ANTHROPIC_API_KEY: str = _get_secret("ANTHROPIC_API_KEY")
    CLAUDE_MODEL: str = _get_secret("CLAUDE_MODEL", "claude-sonnet-4-6")

    # --- Google Programmable Search (live web grounding) ---
    GOOGLE_API_KEY: str = _get_secret("GOOGLE_API_KEY")
    GOOGLE_CSE_ID: str = _get_secret("GOOGLE_CSE_ID")  # Custom Search Engine ID

    # --- Pipeline tuning ---
    MAX_CLAIMS_PER_DOC: int = int(_get_secret("MAX_CLAIMS_PER_DOC", "15"))
    SEARCH_RESULTS_PER_CLAIM: int = int(_get_secret("SEARCH_RESULTS_PER_CLAIM", "5"))
    MAX_PDF_PAGES: int = int(_get_secret("MAX_PDF_PAGES", "30"))
    REQUEST_TIMEOUT: int = 20  # seconds, for outbound HTTP calls

    @classmethod
    def validate(cls) -> list[str]:
        return []


settings = Settings()
