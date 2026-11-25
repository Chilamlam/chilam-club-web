from theme_relay_data import refresh

if __name__ == "__main__":
    pool, archive = refresh()
    print(f"题材接力 {pool['date']}：涨停池 {len(pool['stocks'])} 只，行情异常 {len(archive['errors'])} 项")
    raise SystemExit(1 if archive['errors'] else 0)
