"""Archive ETF shares and stock financing; sends no messages."""
import argparse
from etf_flow_data import collect, publish


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--backfill',type=int,default=0)
    args=parser.parse_args()
    data=collect(args.backfill)
    publish(data)
    print(f"ETF份额 {len(data['rows'])} 条；融资行业 {len(data['margin'])} 条；{data['status']}",flush=True)
    for error in data['errors']:
        print(error,flush=True)
    return 2 if data['errors'] else 0


if __name__=='__main__':
    raise SystemExit(main())
