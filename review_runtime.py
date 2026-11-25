"""Refresh the review dependency graph when a running app receives new source."""
from __future__ import annotations

import hashlib
import importlib
from pathlib import Path
import sys
import threading


# Dependencies precede their consumers, including consumers using `from ... import`.
REVIEW_MODULES = (
    "data_freshness", "site_data", "stock_search", "navigation", "investor_journey", "fund_review",
    "market_watch", "market_watch_data", "global_markets", "etf_flows", "page_etf_flows",
    "cta_positions", "cta_data", "page_cta_positions",
    "abnormal_windows", "abnormal_data", "page_abnormal_windows",
    "strategy_playbooks", "decision_workbench", "journal_store",
    "ladder_themes", "theme_catalog", "sector_rotation", "review_template", "sentiment",
    "industry_signal", "review_language", "evening_plan", "scorecard", "page_scorecard", "narrative", "review_evidence", "digest",
    "market_insights", "theme_activity", "theme_activity_ui", "research_workspace", "research_store", "candidate_compare",
    "theme_relay", "theme_relay_data", "page_theme_relay",
    "workbench_insights_ui", "research_ui", "candidate_ui", "page_personal_review", "page_decision_workbench",
    "page_sentiment", "page_digest", "page_review", "page_industry_signal",
    "page_evening_plan", "convertible", "page_convertible",
    "page_live_quote", "page_fibonacci", "page_watchlist", "page_core_driver",
    "page_sector_rotation", "page_macro_erp", "page_market_observatory",
)
_ROOT = Path(__file__).resolve().parent
# Keep the lock stable when app.py refreshes this helper itself.
_lock = globals().get("_lock") or threading.RLock()
_revision = None


def refresh_review_modules() -> tuple[str, ...]:
    """Reload cached review modules once per source revision, before page imports.

    Streamlit can rerun app.py while retaining imported modules. The first call
    also refreshes already-loaded modules, whose source revision is unknown.
    Auth/session modules and data caches outside this graph are left to Streamlit.
    """
    global _revision
    with _lock:
        sources = {name: (_ROOT / f"{name}.py").read_bytes() for name in REVIEW_MODULES}
        revision = tuple(hashlib.sha256(source).digest() for source in sources.values())
        if revision == _revision:
            return ()

        # Reject a broken deployment before replacing any running module.
        for name, source in sources.items():
            compile(source, str(_ROOT / f"{name}.py"), "exec")
        importlib.invalidate_caches()
        refreshed = []
        for name in REVIEW_MODULES:
            module = sys.modules.get(name)
            if module is None:
                continue
            # A same-size edit within one second may otherwise reuse stale pyc.
            cached = getattr(module, "__cached__", None)
            if cached:
                cache_path = Path(cached).resolve()
                if cache_path.parent == _ROOT / "__pycache__":
                    cache_path.unlink(missing_ok=True)
            importlib.reload(module)
            refreshed.append(name)
        _revision = revision
        return tuple(refreshed)
