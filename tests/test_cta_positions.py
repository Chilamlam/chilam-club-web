import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
from streamlit.testing.v1 import AppTest

import cta_positions as calc
import cta_data as data


def fixture():
    days = pd.date_range(end='2026-11-17',periods=60,freq='W-TUE').strftime('%Y-%m-%d').tolist()
    rows = [dict(date=day,code=code,long=200+i,short=100,spreading=50,oi=1000,market='fixture',units='contracts')
            for i,day in enumerate(days) for code in calc.MARKETS]
    schedule = dict(year=2026,dates=['2026-11-20','2026-11-30'])
    snap = dict(rows=rows,checked_at='2026-11-25T12:00:00+08:00',errors=[],schedule=schedule,
                sources={f:dict(checked_at='2026-11-25T12:00:00+08:00',latest=days[-1]) for f in calc.FAMILIES})
    fresh = dict(start='2026-11-01',end='2026-11-25',checked_at='2026-11-25T13:00:00+08:00',families={},schedule=schedule,errors=[])
    return days,snap,fresh


def raw(code='088691',date='2026-11-17',**extra):
    family = calc.MARKETS[code]['family']
    fields = calc.FAMILIES[family]['fields']
    return dict(cftc_contract_market_code=code,report_date_as_yyyy_mm_dd=date+'T00:00:00.000',
                futonly_or_combined='FutOnly',open_interest_all='1000',**dict(zip(fields,['200','100','50'])),**extra)


class PositionCalculations(unittest.TestCase):
    def test_denominator_and_fx_sign_are_explicit(self):
        days,snap,_ = fixture()
        frame = calc.frame(snap['rows'],days[-1])
        metal = calc.summaries(frame,['088691'],days[-1])[0]
        fx = calc.summaries(frame,['099741','098662'],days[-1],dollar=True)
        self.assertAlmostEqual(metal['net_pct'],15.9)
        self.assertAlmostEqual(fx[0]['net_pct'],-15.9)
        self.assertAlmostEqual(fx[1]['net_pct'],15.9)
        self.assertEqual(metal['long_change'],1)
        self.assertEqual(metal['short_change'],0)
        self.assertEqual(fx[0]['week_net'],-1)

    def test_exact_dates_missing_weeks_and_future_exclusion(self):
        days,snap,_ = fixture()
        rows = [r for r in snap['rows'] if r['date'] not in (days[-2],days[-5])]
        frame = calc.frame(rows,days[-1])
        row = calc.summaries(frame,['088691'],days[-1])[0]
        self.assertIsNone(row['week_pp']);self.assertIsNone(row['month_pp'])
        points = calc.chart_points(frame,'088691',days[-6],days[-1])
        self.assertTrue(any(p['value'] is None for p in points))
        historical = calc.summaries(frame,['088691'],days[-3])[0]
        self.assertEqual(historical['samples'],57)
        self.assertEqual(calc.frame(rows,days[-3]).date.max(),days[-3])
        missing = calc.summaries(frame,['088691'],days[-2])[0]
        self.assertFalse(missing['available'])

    def test_conflicts_invalid_oi_and_tied_percentile(self):
        days,snap,_ = fixture()
        rows = [dict(r,long=200) for r in snap['rows']]
        tied = calc.summaries(calc.frame(rows,days[-1]),['088691'],days[-1])[0]
        self.assertEqual(tied['percentile'],50)
        rows += [dict(rows[-1],long=201)]
        self.assertFalse(calc.summaries(calc.frame(rows,days[-1]),[rows[-1]['code']],days[-1])[0]['available'])
        invalid = [dict(r,oi=0) for r in snap['rows']]
        self.assertTrue(calc.frame(invalid,days[-1]).empty)

    def test_official_holiday_schedule_and_daylight_saving(self):
        schedule = dict(year=2026,dates=['2026-07-10','2026-11-20','2026-11-30'])
        now = dt.datetime.fromisoformat('2026-11-28T12:00:00+08:00')
        state = calc.release_state(schedule,now)
        self.assertEqual(state['expected'],'2026-11-17')
        self.assertEqual(pd.Timestamp(state['next']).tz_convert('Asia/Shanghai').strftime('%m-%d %H:%M'),'12-01 04:30')
        self.assertEqual(calc.release_state(schedule,dt.datetime.fromisoformat('2026-12-01T05:00:00+08:00'))['expected'],'2026-11-24')
        summer = calc.release_state(schedule,dt.datetime.fromisoformat('2026-07-10T12:00:00+08:00'))
        self.assertEqual(pd.Timestamp(summer['next']).tz_convert('Asia/Shanghai').strftime('%H:%M'),'03:30')
        self.assertIsNone(calc.release_state(schedule,dt.datetime.fromisoformat('2027-01-02T12:00:00+08:00'))['expected'])


class Adapters(unittest.TestCase):
    def test_reject_combined_wrong_date_duplicates_and_missing_fields(self):
        record=raw()
        self.assertEqual(data.normalize([record],'disagg','2026-11-01','2026-11-25')[0]['long'],200)
        for r in [dict(record,futonly_or_combined='Combined'),dict(record,open_interest_all='NaN'),dict(record,m_money_positions_long_all=None)]:
            with self.assertRaises(ValueError):data.normalize([r],'disagg','2026-11-01','2026-11-25')
        with self.assertRaises(ValueError):data.normalize([record],'tff','2026-11-01','2026-11-25')
        with self.assertRaises(ValueError):data.normalize([record],'disagg','2026-11-01','2026-11-10')
        with self.assertRaises(ValueError):data.normalize([record,record],'disagg','2026-11-01','2026-11-25')

    def test_server_page_clamp_and_all_contracts(self):
        pages=[[raw('088691')],[raw('085692'),raw('084691')],[]]
        with patch.object(data,'request',side_effect=[Mock(json=Mock(return_value=p)) for p in pages]) as get:
            rows=data.fetch_family('disagg','2026-11-01','2026-11-25')
            self.assertEqual(len(rows),3)
            self.assertEqual([c.args[1]['$offset'] for c in get.call_args_list],[0,1,3])
        with patch.object(data,'request',side_effect=[Mock(json=Mock(return_value=[raw()])),Mock(json=Mock(return_value=[]))]):
            with self.assertRaises(ValueError):data.fetch_family('disagg','2026-11-01','2026-11-25')

    def test_partial_failure_preserves_history_and_newer_archive_wins(self):
        days,snap,fresh=fixture()
        updated=[dict(r,long=300) for r in snap['rows'] if calc.MARKETS[r['code']]['family']=='disagg' and r['date']>=fresh['start']]
        fresh['families']={'disagg':dict(status='ok',checked_at=fresh['checked_at'],rows=updated),
                           'tff':dict(status='unavailable',rows=[])}
        fresh['errors']=['tff unavailable']
        merged=data.merge(snap,fresh)
        self.assertEqual(next(r['long'] for r in merged['rows'] if r['code']=='088691' and r['date']==days[-1]),300)
        self.assertEqual(len([r for r in merged['rows'] if r['code']=='099741']),60)
        self.assertEqual(merged['sources']['tff']['checked_at'],snap['checked_at'])
        self.assertEqual(data.merge(merged,dict(fresh,checked_at=snap['checked_at'])),merged)
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'archive.json'
            path.write_text(json.dumps(snap),encoding='utf-8')
            before=path.read_bytes()
            with patch.object(data,'PATH',path):
                with self.assertRaises(ValueError):data.publish(dict(rows=[]))
            self.assertEqual(path.read_bytes(),before)

    def test_stale_source_response_cannot_erase_a_newer_week(self):
        days,snap,fresh=fixture()
        stale=[r for r in snap['rows'] if calc.MARKETS[r['code']]['family']=='disagg'
               and fresh['start']<=r['date']<days[-1]]
        fresh['families']={'disagg':dict(status='ok',checked_at=fresh['checked_at'],rows=stale)}
        merged=data.merge(snap,fresh)
        self.assertEqual(merged['rows'],sorted(snap['rows'],key=lambda r:(r['date'],r['code'])))
        self.assertEqual(merged['status'],'partial')
        self.assertTrue(any('倒退' in e for e in merged['errors']))

    def test_parse_official_release_table_including_delayed_dates(self):
        months='January February March April May June July August September October November December'.split()
        html='<h3>2026 Release Schedule</h3><table>'+''.join('<tr><td>'+m+'</td><td>06</td><td>13</td><td>20</td><td>27*</td></tr>' for m in months)+'</table>'
        schedule=data.parse_schedule(html)
        self.assertEqual(schedule['year'],2026)
        self.assertIn('2026-11-27',schedule['dates'])
        with self.assertRaises(ValueError):data.parse_schedule(html.replace('December','Unknown'))


class Page(unittest.TestCase):
    def test_market_switch_historical_date_and_failed_refresh(self):
        import page_cta_positions as page
        days,snap,fresh=fixture()
        fresh['errors']=['test source unavailable']
        with patch.object(page,'load_json',return_value=snap),patch.object(page,'fetch_recent',return_value=fresh),patch.object(page,'beijing_now',return_value=dt.datetime.fromisoformat('2026-11-25T14:00:00+08:00')):
            app=AppTest.from_string('from page_cta_positions import render_cta_positions\nrender_cta_positions()',default_timeout=30).run()
            self.assertFalse(app.exception)
            self.assertTrue(app.warning)
            self.assertEqual(set(app.dataframe[0].value['合约']),{'黄金','铜','白银'})
            app.selectbox(key='cta_end_disagg').set_value(days[-3]).run()
            self.assertFalse(app.exception)
            self.assertTrue((app.dataframe[0].value['所选日']==days[-3]).all())
            app.radio(key='cta_scope').set_value('美元与主要货币').run()
            self.assertFalse(app.exception)
            euro=app.dataframe[0].value.set_index('合约').loc['欧元','净持仓占比(%)']
            self.assertLess(euro,0)
            app.radio(key='cta_fx_basis').set_value('原合约方向').run()
            self.assertGreater(app.dataframe[0].value.set_index('合约').loc['欧元','净持仓占比(%)'],0)
            app.multiselect(key='cta_fx_codes').set_value([]).run()
            self.assertFalse(app.exception)


if __name__=='__main__':unittest.main()
