"""Archive official public CFTC reports; never sends messages."""
import argparse
from cta_data import fetch, merge, publish, read


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--full', action='store_true', help='重新核对2012年11月以来的完整历史')
    args = parser.parse_args()
    old = read()
    fresh = fetch(full=args.full or not old.get('rows'))
    result = merge(old, fresh)
    publish(result)
    print(f"CFTC持仓：{len(result['rows'])} 条；{result['status']}",flush=True)
    for error in result['errors']: print(error,flush=True)
    return 2 if result['errors'] else 0


if __name__ == '__main__': raise SystemExit(main())
