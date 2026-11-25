import copy
import unittest
from unittest.mock import patch
from unittest.mock import Mock

import pandas as pd
from streamlit.testing.v1 import AppTest

import etf_flows as calc
import etf_flow_data as data


def fixture():
    days=pd.bdate_range('2026-09-01',periods=70).strftime('%Y%m%d').tolist()
    cal=dict(start_date=days[0],end_date=days[-1],open_dates=days)
    rows=[dict(symbol=c,date=d,shares=100e8+i*1e8,nav=2.+i*.001) for c in calc.POOL for i,d in enumerate(days)]
    splits={c:dict(status='ok',dates=[]) for c in calc.POOL}
    margin=[dict(date=d,industry=ind,balance_yi=1000-i*(1 if ind=='半导体' else 2),count=20)
            for i,d in enumerate(days) for ind in ('半导体','银行','未分类')]
    snap=dict(rows=rows,splits=splits,cutoff=days[-1],margin=margin,industry_date=days[0],errors=[],collected_at='2026-12-07T18:00:00+08:00')
    return days,cal,snap


class ETFCalculations(unittest.TestCase):
    def test_nav_price_increase_is_not_flow(self):
        days,cal,snap=fixture()
        rows=[dict(symbol='510300.SH',date=days[i],shares=1e8,nav=nav) for i,nav in enumerate((1.,1.1,1.2))]
        frame=calc.daily_flows(rows,cal,days[2],snap['splits'])
        summary=calc.summarize(frame,['510300.SH'],days[0],days[2])[0]
        self.assertEqual(summary['flow_yi'],0)
        self.assertEqual(summary['change_pct'],0)
        self.assertEqual(summary['assets_yi'],1.2)

    def test_flow_sums_daily_nav_not_final_nav_and_redemptions_are_negative(self):
        days,cal,snap=fixture()
        rows=[dict(symbol='510300.SH',date=days[i],shares=shares,nav=nav) for i,(shares,nav) in enumerate(((1e8,2),(1.1e8,2.2),(1e8,2.4)))]
        frame=calc.daily_flows(rows,cal,days[2],snap['splits'])
        s=calc.summarize(frame,['510300.SH'],days[0],days[2])[0]
        self.assertAlmostEqual(s['flow_yi'],-.02)
        self.assertEqual(s['change_yi'],0)

    def test_missing_session_or_nav_not_zero_or_adjacent(self):
        days,cal,snap=fixture()
        rows=[r for r in snap['rows'] if r['symbol']=='510300.SH' and r['date']!=days[-2]]
        frame=calc.daily_flows(rows,cal,days[-1],snap['splits'])
        s=calc.summarize(frame,['510300.SH'],days[-3],days[-1])[0]
        self.assertIsNone(s['flow_yi'])
        self.assertEqual(s['observed'],0)
        rows=copy.deepcopy(snap['rows']);rows[-1]['nav']=None
        h=calc.daily_flows(rows,cal,days[-1],snap['splits'])
        self.assertTrue(calc.group_flows(h,list(calc.POOL)).iloc[-1].isna()['flow_yi'])

    def test_conflicts_splits_and_unverified_actions_excluded(self):
        days,cal,snap=fixture()
        rows=[r for r in snap['rows'] if r['symbol']=='510300.SH']
        rows.append(dict(rows[-1],shares=999e8))
        h=calc.daily_flows(rows,cal,days[-1],snap['splits'])
        self.assertTrue(pd.isna(h.iloc[-1].shares))
        splits=copy.deepcopy(snap['splits']);splits['510300.SH']['dates']=[days[-1]]
        h=calc.daily_flows(snap['rows'],cal,days[-1],splits)
        r=calc.summarize(h,['510300.SH'],days[-2],days[-1])[0]
        self.assertIsNone(r['flow_yi']);self.assertIsNone(r['change_pct'])
        h=calc.daily_flows(snap['rows'],cal,days[-1],{})
        self.assertTrue(h.flow_yi.isna().all())

    def test_fixed_pool_requires_all_members_and_ignores_future(self):
        days,cal,snap=fixture()
        h=calc.daily_flows(snap['rows'],cal,days[-2],snap['splits'])
        self.assertEqual(h.date.max(),days[-2])
        self.assertTrue(calc.group_flows(h,['510300.SH','999999.SH']).flow_yi.isna().all())
        pool=calc.group_flows(h,['510300.SH','510300.SH'])
        self.assertEqual(pool.iloc[-1].expected,1)
        summary=calc.summarize(h,['510300.SH'],days[-3],days[-2])[0]
        self.assertEqual(summary['history_count'],69)
        self.assertEqual(summary['peak_ratio'],100)

    def test_split_before_available_history_does_not_hide_comparable_peak(self):
        days,cal,snap=fixture()
        rows=[r for r in snap['rows'] if r['date']>=days[10]]
        snap['splits']['510300.SH']['dates']=[days[2]]
        h=calc.daily_flows(rows,cal,days[-1],snap['splits'])
        self.assertEqual(calc.summarize(h,['510300.SH'],days[-3],days[-1])[0]['peak_ratio'],100)
        snap['splits']['510300.SH']['dates']=[days[20]]
        h=calc.daily_flows(rows,cal,days[-1],snap['splits'])
        self.assertIsNone(calc.summarize(h,['510300.SH'],days[-3],days[-1])[0]['peak_ratio'])

    def test_margin_basket_keeps_unclassified_and_uses_exact_endpoints(self):
        days,cal,snap=fixture()
        results,_=calc.margin_comparison(snap['margin'],['半导体'],days[0],days[-1])
        groups={r['group']:r for r in results}
        self.assertEqual(set(groups),{'所选科技行业','其他已分类A股','未分类'})
        self.assertEqual(groups['所选科技行业']['change'],-69)
        self.assertEqual(groups['其他已分类A股']['change'],-138)
        self.assertEqual(calc.margin_comparison(snap['margin'],['半导体'],'20260101',days[-1])[0],[])


class AdapterTests(unittest.TestCase):
    def test_nav_pagination_uses_actual_server_page_size(self):
        pages=[dict(ErrCode=0,PageSize=1,TotalCount=3,Data={'LSJZList':[dict(FSRQ=day,DWJZ=nav)]})
               for day,nav in [('2026-11-24',2.0),('2026-11-23',1.9),('2026-11-20',1.8)]]
        with patch.object(data,'get',side_effect=[Mock(json=Mock(return_value=p)) for p in pages]) as fetch:
            rows=data.nav_history('510300.SH','20261120','20261124')
            self.assertEqual(fetch.call_count,3)
            self.assertEqual([r['date'] for r in rows],['20261124','20261123','20261120'])

    def test_exchange_units_and_date_validation(self):
        payload=dict(result=[dict(STAT_DATE='2026-11-24',SEC_CODE='510300',TOT_VOL='10000')],pageHelp={'total':1})
        self.assertEqual(data.parse_sse(payload,'20261124')[0]['shares'],1e8)
        with self.assertRaises(ValueError):data.parse_sse(payload,'20261123')
        payload['pageHelp']['total']=2
        with self.assertRaises(ValueError):data.parse_sse(payload,'20261124')
        for column,value in [('基金规模(份)',1e8),('基金规模(万份)',10000)]:
            f=pd.DataFrame([{'日期':'2026-11-24','基金代码':159915,column:value}])
            self.assertEqual(data.parse_szse(f,'20261124','20261124')[0]['shares'],1e8)
        with self.assertRaises(ValueError):data.parse_szse(pd.DataFrame([{'日期':'2026-11-24','基金代码':159915,'规模':1}]),'20261124','20261124')


class ETFPageTests(unittest.TestCase):
    def app(self):
        days,cal,snap=fixture()
        def loader(path):return cal if 'calendar' in path else snap
        self.context=patch('page_etf_flows.load_json',side_effect=loader)
        self.context.start();self.addCleanup(self.context.stop)
        self.close=patch('page_etf_flows.last_close_day',return_value=days[-1])
        self.close.start();self.addCleanup(self.close.stop)
        return AppTest.from_string('from page_etf_flows import render_etf_flows\nrender_etf_flows()',default_timeout=30).run()

    def test_default_dual_search_and_all_views_render(self):
        app=self.app();self.assertFalse(app.exception)
        app.radio(key='flow_scope').set_value('双创宽基').run()
        self.assertFalse(app.exception)
        self.assertEqual(set(app.dataframe[0].value['跟踪指数']),calc.DUAL)
        app.text_input(key='flow_query').input('159915').run()
        self.assertEqual(app.dataframe[1].value['代码'].tolist(),['159915.SZ'])
        app.radio(key='flow_view').set_value('融资结构').run()
        self.assertFalse(app.exception)
        self.assertIn('所选科技行业',app.dataframe[0].value['范围'].tolist())
        app.radio(key='flow_view').set_value('公开披露核对').run()
        self.assertFalse(app.exception)
        self.assertTrue(any('无法单独识别买方' in m.value for m in app.markdown))


if __name__=='__main__':unittest.main()
