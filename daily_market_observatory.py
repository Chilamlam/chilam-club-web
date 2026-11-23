"""Archive public market data without sending messages or touching private records."""
import argparse

from global_markets import collect_onchain, collect_polymarket
from market_watch_data import collect_etf, collect_futures, publish

COLLECTORS = {"futures":collect_futures, "etf":collect_etf, "polymarket":collect_polymarket, "onchain":collect_onchain}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", choices=list(COLLECTORS))
    args = parser.parse_args()
    failed = False
    for kind, collect in COLLECTORS.items():
        if args.only and args.only != kind:
            continue
        try:
            data = collect()
            publish(kind, data)
            print(f"{kind}: {data['status']} · {len(data['rows'])} 条 · {data['collected_at']}", flush=True)
            failed |= data["status"] != "ok"
        except Exception as exc:
            print(f"{kind}: 更新失败（{type(exc).__name__}），保留历史归档", flush=True)
            failed = True
    return 2 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
