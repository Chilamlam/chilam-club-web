"""Archive the full published candidate universe, without backfilling membership."""
from __future__ import annotations

import json
from pathlib import Path
import tempfile

import pandas as pd

from candidate_compare import archive
from decision_workbench import snapshot_date
from data_freshness import norm_date
from update_schedule import beijing_now


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def build(root):
    frames = {}
    for key, filename in (("strong", "strong_stocks.csv"), ("breakout", "breakout_stocks.csv"), ("etf", "strong_etfs.csv")):
        try:
            frames[key] = pd.read_csv(root / "data" / filename, dtype={"ts_code": str})
        except (OSError, ValueError):
            frames[key] = pd.DataFrame()
    evening = read_json(root / "data/evening_plan/latest.json")
    today = beijing_now().strftime("%Y%m%d")
    dates = [snapshot_date(v) for v in frames.values()] + [norm_date(evening.get("date"))]
    asof = max((d for d in dates if d and d <= today), default="")
    if not asof:
        return None
    result = archive(frames, evening, asof=asof)
    result["collected_at"] = beijing_now().isoformat()
    target = root / f"data/candidates/history/{asof}.json"
    old = read_json(target)
    if norm_date(old.get("date")) == asof:
        for style, block in old.get("styles", {}).items():
            if block.get("complete") and not result["styles"].get(style, {}).get("complete"):
                result["styles"][style] = block
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=target.parent, suffix=".tmp", delete=False) as output:
        json.dump(result, output, ensure_ascii=False, indent=2, allow_nan=False)
        temporary = Path(output.name)
    temporary.replace(target)
    return result


if __name__ == "__main__":
    result = build(Path(__file__).resolve().parent)
    if result is None:
        raise SystemExit("没有可归档的有效日期")
    print(result["date"], {style: {"count": len(value["rows"]), "complete": value["complete"]}
                           for style, value in result["styles"].items()})
