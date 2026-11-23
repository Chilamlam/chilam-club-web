"""Local stock-name lookup shared by lists and Streamlit option labels."""
from __future__ import annotations

from functools import lru_cache
import re
import unicodedata

import pandas as pd
from pypinyin import Style, lazy_pinyin


def _text(value) -> str:
    return "" if value is None or pd.isna(value) else str(value).strip()


def _normal(value) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", _text(value))).casefold()


@lru_cache(maxsize=20000)
def name_aliases(name: str) -> tuple[str, str]:
    """Keep Latin prefixes (ST, ETF) and numbers; use phrase-aware readings."""
    name = unicodedata.normalize("NFKC", name)
    initials = "".join(lazy_pinyin(name, style=Style.FIRST_LETTER))
    spelling = "".join(lazy_pinyin(name))
    return tuple(re.sub(r"[^a-z0-9]", "", value.casefold()) for value in (initials, spelling))


def stock_label(name, code, detail="") -> str:
    """Native selectboxes search their display labels, including these initials."""
    name, code = _text(name), _text(code)
    parts = [part for part in (name, code) if part]
    initials, _ = name_aliases(name)
    if initials and initials != _normal(name):
        parts.append(initials.upper())
    if detail:
        parts.append(str(detail))
    return " · ".join(parts)


def matches_stock(query, name, code, *extra) -> bool:
    query = _normal(query)
    if not query:
        return True
    name = _text(name)
    return any(query in _normal(value) for value in (name, code, *extra, *name_aliases(name)))


def stock_mask(frame: pd.DataFrame, query, *, code_col="ts_code", name_col="name") -> pd.Series:
    return pd.Series([matches_stock(query, name, code)
                      for name, code in zip(frame[name_col], frame[code_col])],
                     index=frame.index, dtype=bool)
