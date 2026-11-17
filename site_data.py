"""Local published artifacts, invalidated on replacement rather than only by TTL."""
from pathlib import Path
import json

import pandas as pd
import streamlit as st

ROOT = Path(__file__).resolve().parent


def _file(path):
    full = ROOT / path
    try:
        stat = full.stat()
        return str(full), (stat.st_mtime_ns, stat.st_size)
    except OSError:
        return str(full), None


@st.cache_data(ttl=600, max_entries=128, show_spinner=False)
def _csv(path, revision):
    try:
        return pd.read_csv(path, dtype={"ts_code": str, "code": str})
    except (OSError, ValueError, pd.errors.ParserError):
        return pd.DataFrame()


@st.cache_data(ttl=600, max_entries=128, show_spinner=False)
def _json(path, revision):
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def load_csv(path):
    full, revision = _file(path)
    return _csv(full, revision) if revision is not None else pd.DataFrame()


def load_json(path):
    full, revision = _file(path)
    return _json(full, revision) if revision is not None else {}
