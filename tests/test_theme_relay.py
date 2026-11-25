import copy
import datetime as dt
import json
from pathlib import Path
import unittest
from unittest.mock import patch, Mock

from streamlit.testing.v1 import AppTest

import abnormal_windows as calc
import abnormal_data as bars
import theme_relay as relay
import theme_relay_data as data
import decision_workbench as journal

FIXTURES=Path(__file__).parent/'fixtures'
def read(group,name):
    return json.loads((FIXTURES/group/name).read_text(encoding='utf-8'))
POOL=read('relay','pool.json')
PRICES=read('relay','prices.json')
TOPIC=read('relay','topics.json')['topics'][0]
CALENDAR=read('abnormal','calendar.json')
LEDGER=read('abnormal','events.json')
LEADER=next(s for s in POOL['stocks'] if s['symbol']=='sz300703')


def aligned():
    return calc.aligned(PRICES['series']['sz300703'],PRICES['series']['sz399102'],CALENDAR,'2026-11-25')


def render():
    from page_theme_relay import render_theme_relay
    render_theme_relay()


class RelayCalculationTests(unittest.TestCase):
    def test_chuangyuan_uses_chinext_composite_and_twenty_percent_limit(self):
        rules=calc.market_rules('sz300703','创源股份')
        self.assertEqual((rules['benchmark'],rules['ordinary'],rules['count']),('sz399102',30,3))
        self.assertEqual(calc.limit_price(17.8,rules=rules),21.36)
        rows=aligned()
        self.assertEqual([r['event'] for r in calc.path_rows(rows,'2026-11-23',rules)],['收盘涨停']*3)
        with self.assertRaises(ValueError):calc.market_rules('sz300001','ST测试')
        with self.assertRaises(ValueError):calc.market_rules('sh688001','测试')

    def test_one_tick_changes_chuangyuan_serious_condition(self):
        rows=aligned();rules=calc.market_rules('sz300703')
        events=LEDGER['events']['sz300703']
        out=relay.outlook(rows,events,CALENDAR,rules,10,0)
        self.assertAlmostEqual(out['state']['ten']['deviation'],73.7301681,places=5)
        self.assertEqual(relay.nearest(out['tomorrow'])['trigger_price'],20.51)
        self.assertTrue(relay.nearest(out['tomorrow'])['within_limit'])
        self.assertEqual(out['path'][0]['close'],19.58)
        self.assertAlmostEqual(relay.nearest(out['after'])['trigger_pct'],4.7497446,places=5)
        for value,hit in [(20.50,False),(20.51,True)]:
            assumed=dict(rows[-1],date='2026-11-26',close=value,adj_close=rows[-1]['adj_close']*value/17.8)
            state=calc.analyse(rows+[assumed],events,rules)[-1]
            self.assertEqual(bool(state['serious_reasons']),hit)
        capped=relay.outlook(rows,events,CALENDAR,rules,20,0)
        self.assertTrue(capped['path'][0]['serious_reasons'])
        self.assertIsNone(capped['after'])

    def test_chinext_ordinary_count_differs_from_mainboard(self):
        rows=[]
        for i,date in enumerate(CALENDAR[150:191]):
            close=10 if i<=34 else 10*1.2**(i-34)
            rows.append(dict(date=date,close=close,adj_close=close,index=100))
        state=calc.analyse(rows,rules=calc.GEM)[-1]
        self.assertEqual(state['ordinary_count'],3)
        self.assertIn('10日内第三次同向价格异动',state['serious_reasons'])
        self.assertNotEqual(calc.analyse(rows,rules=calc.MAIN)[-1]['ordinary_count'],3)

    def test_watch_discovers_chuangyuan_before_its_next_board(self):
        watch=relay.leader_watch(POOL,PRICES['series'],LEDGER['events'],CALENDAR)
        row=next(r for r in watch['rows'] if r['symbol']=='sz300703')
        self.assertEqual(row['when'],'2026-11-26')
        self.assertEqual(row['boundary'],20.51)
        self.assertTrue(watch['missing']) # fixture deliberately covers only a few stocks


class RelayEvidenceTests(unittest.TestCase):
    def test_extensions_are_dated_hypotheses_not_exact_theme_matches(self):
        explicit=relay.candidates(POOL,'sz300703',TOPIC['tags'],TOPIC)
        self.assertEqual([s['symbol'] for s in explicit[:2]],['sz301066','sh600229'])
        self.assertTrue(all(r['relation']['kind']=='叙事扩散假设' for r in explicit))
        self.assertEqual(relay.candidates(POOL,'sz300703',TOPIC['tags'],TOPIC,False),[])
        old=dict(POOL,date='2026-11-24')
        self.assertEqual(relay.candidates(old,'sz300703',TOPIC['tags'],TOPIC),[])
        # A similarly named stock is not included by spelling or industry.
        pool=dict(POOL,stocks=POOL['stocks']+[dict(LEADER,symbol='sz002999',name='财神科技',theme='芯片')])
        self.assertFalse(any(r['symbol']=='sz002999' for r in relay.candidates(pool,'sz300703',TOPIC['tags'],TOPIC)))

    def test_first_board_does_not_erase_prior_limit_history(self):
        self.assertEqual(relay.stage('10天6板'),'反复涨停')
        self.assertEqual(relay.stage('3天3板'),'连板')
        a=relay.price_context(PRICES['series'],'sz301066',POOL['date'],CALENDAR)
        b=relay.price_context(PRICES['series'],'sh600229',POOL['date'],CALENDAR)
        self.assertEqual(a['prior_limits'],0)
        self.assertEqual(b['prior_limits'],2)
        self.assertAlmostEqual(a['ten'],4,places=2)
        missing=copy.deepcopy(PRICES['series'])
        missing['sz301066']=[r for r in missing['sz301066'] if r['date']!='2026-11-24']
        self.assertFalse(relay.price_context(missing,'sz301066',POOL['date'],CALENDAR)['available'])

    def test_future_prices_do_not_change_original_view(self):
        future=copy.deepcopy(PRICES['series'])
        future['sz301066'].append(dict(future['sz301066'][-1],date='2026-11-26',adj_close=99999))
        self.assertEqual(relay.price_context(future,'sz301066',POOL['date'],CALENDAR),
                         relay.price_context(PRICES['series'],'sz301066',POOL['date'],CALENDAR))

    def test_saved_plan_freezes_evidence_and_tracks_only_actual_next_sessions(self):
        candidate=relay.candidates(POOL,'sz300703',TOPIC['tags'],TOPIC)[0]
        out=relay.outlook(aligned(),LEDGER['events']['sz300703'],CALENDAR,calc.GEM)
        fields=relay.plan_fields(candidate,LEADER,TOPIC['name'],POOL['date'],out,
            dict(thesis='题材扩散假设',trigger='承接转强才考虑',invalid='同步走弱取消',exit_rule='两个交易日复核',cost_risk='可成交性待确认'),POOL['date'])
        plan=journal.new_plan(fields,now='2026-11-25T21:00:00+08:00')
        evidence=plan['plan']['market_evidence']['theme_relay']
        candidate['relation']['basis']='edited'
        out['tomorrow'][0]['trigger_price']=9999
        self.assertNotEqual(evidence['candidate']['relation']['basis'],'edited')
        self.assertNotEqual(evidence['outlook']['tomorrow'][0]['trigger_price'],9999)
        self.assertTrue(all(r['close'] is None for r in relay.follow_up(evidence,PRICES['series'],CALENDAR)))
        newer=copy.deepcopy(PRICES['series'])
        newer['sz301066'].append(dict(newer['sz301066'][-1],date='2026-11-26',adj_close=14.014,close=14.014))
        checks=relay.follow_up(evidence,newer,CALENDAR)
        actual=next(r for r in checks if r['symbol']=='sz301066' and r['date']=='2026-11-26')
        self.assertAlmostEqual(actual['change'],10)
        self.assertEqual(journal.import_journal(journal.export_journal([plan]))[0]['plan']['market_evidence'],plan['plan']['market_evidence'])


class RelayDataTests(unittest.TestCase):
    def raw_pool(self):
        stamp=int(dt.datetime(2026,11,25,10,7,tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp())
        return {'status_code':0,'data':{'date':'20261125','page':{'total':1,'count':1,'page':1},'info':[
            dict(code='301066',name='万事利',reason_type='丝绸文创+AIGC',high_days='首板',
                 first_limit_up_time=stamp,latest=12.74,change_rate=19.96)]}}

    def test_wrong_day_partial_page_and_corrupt_attribution_are_rejected(self):
        self.assertEqual(len(data.parse_pool(self.raw_pool(),'2026-11-25')['stocks']),1)
        for mutate in [lambda p:p['data'].update(date='20261124'),
                       lambda p:p['data']['page'].update(total=2),
                       lambda p:p['data']['info'][0].update(reason_type='')]:
            payload=self.raw_pool();mutate(payload)
            with self.assertRaises(ValueError):data.parse_pool(payload,'2026-11-25')

    def test_fetch_failure_keeps_previous_prices_and_date(self):
        archive=copy.deepcopy(PRICES)
        with patch.object(bars,'fetch_series',side_effect=ValueError('offline')):
            updated=data.refresh_prices(archive,[LEADER],'2026-11-26')
        self.assertEqual(updated['series'],archive['series'])
        self.assertTrue(updated['errors'])

    def test_pool_pagination_collects_all_and_rejects_date_drift(self):
        first=self.raw_pool();second=copy.deepcopy(first)
        first['data']['page'].update(total=2,count=2)
        second['data']['page'].update(total=2,count=2,page=2)
        second['data']['info'][0].update(code='600229',name='城市传媒')
        def response(payload):
            return Mock(content=json.dumps(payload,ensure_ascii=False).encode('utf-8'))
        with patch.object(data.requests,'get',side_effect=[response(first),response(second)]):
            result=data.fetch_pool('2026-11-25')
        self.assertEqual(len(result['stocks']),2)
        second['data']['date']='20261124'
        with patch.object(data.requests,'get',side_effect=[response(first),response(second)]):
            with self.assertRaises(ValueError):data.fetch_pool('2026-11-25')


class RelayPageTests(unittest.TestCase):
    def test_choose_filter_and_save_an_actionable_guest_plan_without_network(self):
        original_read=bars.read
        def fixture_read(path=bars.PATH):
            if path==bars.EVENT_PATH:return LEDGER
            if path==data.TOPIC_PATH:return {'topics':[TOPIC]}
            return original_read(path)
        import research_store as store
        with patch.object(data,'available_dates',return_value=['2026-11-25']),\
             patch.object(data,'load_pool',return_value=POOL),\
             patch.object(data,'load_series',return_value=PRICES),\
             patch.object(data,'refresh_prices') as network,\
             patch.object(bars,'read',side_effect=fixture_read),\
             patch.object(store,'owner',return_value=None):
            app=AppTest.from_function(render).run(timeout=30)
            self.assertFalse(app.exception)
            self.assertTrue(any('20.51' in r.value for r in app.markdown))
            prefix='relay_2026-11-25_sz300703'
            app.text_input(key=prefix+'_query').set_value('wsl').run(timeout=30)
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key=prefix+'_candidate').value['symbol'],'sz301066')
            button=next(b for b in app.button if b.label=='保存观察预案')
            button.click().run(timeout=30)
            self.assertFalse(app.exception)
            records=app.session_state['decision_journal_guest']
            self.assertEqual(len(records),1)
            plan=next(iter(records.values()))
            self.assertIn('theme_relay',plan['plan']['market_evidence'])
            self.assertEqual(plan['outcome']['research']['review_on'],'20261127')
            self.assertEqual(plan['plan']['mode'],'模拟观察')
            next(b for b in app.button if b.label=='保存观察预案').click().run(timeout=30)
            self.assertEqual(len(app.session_state['decision_journal_guest']),1)
            network.assert_not_called()


if __name__=='__main__':unittest.main()
