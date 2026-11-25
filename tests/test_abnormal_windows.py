import copy
import datetime as dt
import unittest
from pathlib import Path
import json
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import abnormal_data as data
import abnormal_windows as calc


FIXTURES=Path(__file__).parent/'fixtures'/'abnormal'
SNAPSHOT=json.loads((FIXTURES/'prices.json').read_text(encoding='utf-8'))
LEDGER=json.loads((FIXTURES/'events.json').read_text(encoding='utf-8'))
CALENDAR=json.loads((FIXTURES/'calendar.json').read_text(encoding='utf-8'))


def case(code,date):
    return calc.aligned(SNAPSHOT['series'][code],SNAPSHOT['series'][calc.CASES[code]['benchmark']],CALENDAR,date)


def render():
    from page_abnormal_windows import render_abnormal_windows
    render_abnormal_windows()


class AbnormalCaseTests(unittest.TestCase):
    def test_announcement_values_are_reproduced(self):
        for code,events in LEDGER['events'].items():
            for event in events:
                if 'value' not in event: continue
                rows=case(code,event['end'])
                start=next(i for i,r in enumerate(rows) if r['date']==event['start'])
                value=next(w for w in calc.windows(rows,len(rows)-1,len(rows),start) if w['start']==event['start'])
                with self.subTest(code=code,end=event['end']):
                    self.assertAlmostEqual(round(value['deviation'],2),event['value'],places=2)

    def test_jiuyang_rises_while_old_gains_roll_out(self):
        checks=[('2026-10-16','ten',99.54),('2026-10-19','ten',88.08),
                ('2026-11-13','thirty',199.94),('2026-11-16','thirty',191.85)]
        for date,field,value in checks:
            state=calc.analyse(case('sz002242',date),LEDGER['events']['sz002242'])[-1]
            self.assertAlmostEqual(state[field]['deviation'],value,places=2)
            self.assertFalse(state['serious_reasons'])

    def test_all_starts_within_window_are_checked(self):
        # On 11/23 Beiwei's strongest 10-day period begins 11/10, not 11/11.
        rows=case('sz002148','2026-11-23')
        value=calc.strongest(rows,len(rows)-1,10)
        self.assertEqual(value['start'],'2026-11-10')
        self.assertAlmostEqual(value['deviation'],97.899,places=3)
        rows=case('sz002242','2026-10-14')
        value=calc.strongest(rows,len(rows)-1,10)
        self.assertEqual(value['length'],8)
        self.assertEqual(value['start'],'2026-09-28')

    def test_one_cent_changes_beiwei_trigger(self):
        rows=case('sz002148','2026-11-23')
        actual=case('sz002148','2026-11-24')[-1]
        index_change=(actual['index']/rows[-1]['index']-1)*100
        boundary=next(b for b in calc.next_boundaries(rows,LEDGER['events']['sz002148'],'2026-11-24',index_change) if b['rule']=='10日')
        self.assertEqual(boundary['trigger_price'],11.41)
        for price,hit in [(11.40,False),(11.41,True)]:
            assumed=rows+[dict(actual,close=price,adj_close=actual['adj_close']*price/actual['close'])]
            state=calc.analyse(assumed,LEDGER['events']['sz002148'])[-1]
            self.assertEqual(bool(state['serious_reasons']),hit)

    def test_ordinary_triggers_are_not_counted_on_every_overlapping_window(self):
        rows=case('sz002242','2026-10-16')
        state=calc.analyse(rows,LEDGER['events']['sz002242'])[-1]
        self.assertEqual([r['end'] for r in state['triggers']],['2026-09-29','2026-10-08','2026-10-12'])
        self.assertEqual(state['ordinary_count'],3)
        # Eight sequential 10% sessions produce four non-overlapping events.
        synthetic=[]
        for i in range(41):
            close=10.0 if i<=32 else 10*1.1**(i-32)
            synthetic.append(dict(date=f'2026-{8+i//28:02d}-{1+i%28:02d}',close=close,adj_close=close,index=100))
        fourth=calc.analyse(synthetic)[-1]
        self.assertEqual(fourth['ordinary_count'],4)
        self.assertIn('10日内第四次同向价格异动',fourth['serious_reasons'])

    def test_serious_reset_needs_known_disclosure_and_does_not_erase_history(self):
        events=LEDGER['events']['sz002148']
        before=calc.analyse(case('sz002148','2026-11-24'),events)[-1]
        self.assertTrue(before['serious_reasons'])
        self.assertFalse(before['official_reset'])
        after=calc.analyse(case('sz002148','2026-11-25'),events)
        self.assertTrue(after[-1]['official_reset'])
        self.assertEqual(after[-1]['ten']['length'],1)
        self.assertTrue(after[-2]['serious_reasons'])
        unknown=calc.analyse(case('sz002148','2026-11-25'),[])[-1]
        self.assertFalse(unknown['official_reset'])
        self.assertGreater(unknown['ten']['length'],1)

    def test_no_future_prices_or_announcements_in_historical_projection(self):
        code='sz002148';cutoff='2026-11-23'
        stock=copy.deepcopy(SNAPSHOT['series'][code])
        for r in stock:
            if r['date']>cutoff:r['close']=r['adj_close']=99999
        rows=calc.aligned(stock,SNAPSHOT['series']['sz399107'],CALENDAR,cutoff)
        self.assertEqual(rows,case(code,cutoff))
        events=LEDGER['events'][code]
        self.assertFalse(any(e['kind']=='serious' for e in calc.known_events(events,cutoff)))
        dates=calc.future_dates(CALENDAR,cutoff)
        self.assertEqual(calc.project(rows,events,dates),calc.project(rows,calc.known_events(events,cutoff),dates))

    def test_simulation_stops_at_first_serious_trigger(self):
        rows=case('sz002148','2026-11-23');events=LEDGER['events']['sz002148']
        projected=calc.project(rows,events,calc.future_dates(CALENDAR,'2026-11-23'))
        self.assertEqual(len(projected),1)
        self.assertTrue(projected[0]['serious_reasons'])
        self.assertEqual(calc.project(case('sz002148','2026-11-24'),events,calc.future_dates(CALENDAR,'2026-11-24')),[])

    def test_small_stock_limit_ticks_and_touch_versus_close(self):
        self.assertEqual(calc.limit_price(3.15),3.47)
        rows=calc.path_rows(case('sh600467','2026-11-25'),'2026-11-24')
        self.assertEqual(rows[0]['event'],'触板未封')
        self.assertEqual(rows[1]['event'],'收盘涨停')

    def test_missing_bar_index_or_calendar_blocks_calculation(self):
        stock=SNAPSHOT['series']['sz002242'];index=SNAPSHOT['series']['sz399107']
        with self.assertRaisesRegex(ValueError,'缺少行情'):
            calc.aligned([r for r in stock if r['date']!='2026-10-14'],index,CALENDAR,'2026-10-16')
        with self.assertRaisesRegex(ValueError,'所选日缺少'):
            calc.aligned(stock,[r for r in index if r['date']!='2026-10-16'],CALENDAR,'2026-10-16')
        with self.assertRaisesRegex(ValueError,'交易日历'):
            calc.aligned(stock,index,[],'2026-10-16')

    def test_holidays_are_not_projection_days_and_expiries_are_exact(self):
        self.assertEqual(calc.future_dates(CALENDAR,'2026-09-30',2),['2026-10-08','2026-10-09'])
        with self.assertRaises(ValueError):calc.future_dates(['2026-10-16'],'2026-10-16')
        rows=case('sz002242','2026-10-16')
        schedule=calc.roll_schedule(rows,LEDGER['events']['sz002242'],['2026-10-19'])
        self.assertEqual(schedule[0]['expires10'],'2026-09-28')
        self.assertEqual(schedule[0]['start10'],'2026-09-29')
        self.assertEqual(calc.event_expiry(CALENDAR,'2026-09-29'),'2026-10-20')
        self.assertIsNone(calc.event_expiry(['2026-09-29'],'2026-09-29'))

    def test_roll_after_reset_does_not_claim_old_records_are_still_counted(self):
        rows=case('sz002148','2026-11-25')
        schedule=calc.roll_schedule(rows,LEDGER['events']['sz002148'],calc.future_dates(CALENDAR,'2026-11-25'))
        self.assertTrue(all(r['expires10'] is None and r['expires30'] is None for r in schedule))

    def test_adjustment_scale_does_not_change_boundary_or_deviation(self):
        rows=case('sz002242','2026-10-16');scaled=[dict(r,adj_close=r['adj_close']*.37) for r in rows]
        for a,b in zip(calc.next_boundaries(rows,[],'2026-10-19'),calc.next_boundaries(scaled,[],'2026-10-19')):
            self.assertEqual(a['trigger_price'],b['trigger_price'])
        self.assertAlmostEqual(calc.analyse(rows)[-1]['ten']['deviation'],calc.analyse(scaled)[-1]['ten']['deviation'])


class DataAndPageTests(unittest.TestCase):
    def test_refresh_failure_preserves_usable_archive(self):
        with patch.object(data,'fetch_series',side_effect=ValueError('offline')):
            result=data.refresh(SNAPSHOT,CALENDAR,dt.datetime(2026,11,25,16,tzinfo=dt.timezone(dt.timedelta(hours=8))))
        self.assertEqual(result['series'],SNAPSHOT['series'])
        self.assertEqual(result['asof'],SNAPSHOT['asof'])
        self.assertTrue(result['errors'])

    def test_intraday_updates_exclude_today_and_regressing_feed_is_rejected(self):
        def fetch(symbol,end):
            self.assertEqual(end,'2026-11-24')
            return [r for r in SNAPSHOT['series'][symbol] if r['date']<=end]
        with patch.object(data,'fetch_series',side_effect=fetch):
            result=data.refresh(SNAPSHOT,CALENDAR,dt.datetime(2026,11,25,10,tzinfo=dt.timezone(dt.timedelta(hours=8))))
        self.assertEqual(result['series'],SNAPSHOT['series'])
        self.assertTrue(result['errors'])

    def test_page_stock_date_and_scenario_controls(self):
        with patch.object(data,'refresh') as network:
            app=AppTest.from_function(render).run(timeout=30)
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value,'99.54%')
            app.button(key='abnormal_next').click().run(timeout=30)
            self.assertEqual(app.metric[0].value,'88.08%')
            app.selectbox(key='abnormal_symbol').select('sz002148').run(timeout=30)
            self.assertFalse(app.exception)
            self.assertEqual(app.metric[0].value,'97.90%')
            app.selectbox(key='abnormal_date_sz002148').select('2026-11-24').run(timeout=30)
            self.assertEqual(app.metric[0].value,'100.09%')
            self.assertTrue(any('停止继续推演' in i.value for i in app.info))
            app.selectbox(key='abnormal_symbol').select('sh600467').run(timeout=30)
            app.selectbox(key='abnormal_mode').select('先横盘，再每天收涨停').run(timeout=30)
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key='abnormal_wait').value,1)
            app.number_input(key='abnormal_index_change').set_value(-1.0).run(timeout=30)
            self.assertFalse(app.exception)
            network.assert_not_called()


if __name__=='__main__':
    unittest.main()
