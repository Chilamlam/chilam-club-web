"""Refresh the three-case abnormal-window pilot; failed feeds retain last good bars."""
import abnormal_data as data


def main():
    result=data.refresh(data.read(),data.calendar_dates())
    data.save(result)
    print(f"Abnormal-window pilot: {result.get('asof','unavailable')}; {len(result.get('errors',[]))} source errors")
    return 1 if result.get('errors') else 0


if __name__ == '__main__':
    raise SystemExit(main())
