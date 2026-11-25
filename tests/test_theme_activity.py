import copy
import unittest

import pandas as pd
from streamlit.testing.v1 import AppTest

import theme_activity as ta
import sector_rotation as sr
from narrative import _theme

DAYS=['20261117','20261118','20261119','20261120','20261123']
CAL=dict(start_date='20260101',end_date='20261231',open_dates=DAYS)


def archive(date, themes, missing=()):
    stocks=[dict(code=code,name='样本'+code,limit_times=i+1,first_time='09:31:00',industry='错误行业')
            for i,code in enumerate(themes)]
    return dict(date=date,ladder=dict(date=date,stocks=stocks),attribution=dict(date=date,sources={
        'ths_public':dict(status='ok',records={c:dict(code=c,date=date,theme=t) for c,t in themes.items() if c not in missing})}))


class ThemeActivityTests(unittest.TestCase):
    def test_same_source_tags_are_directly_joined_to_stocks_not_indices_or_industry(self):
        a=archive(DAYS[-1],{'000001':'人形机器人+机器人零部件+人形机器人','000002':'人形机器人+汽车零部件'})
        a['attribution']['sources']['kpl']=dict(records={'000001':dict(code='000001',date=DAYS[-1],theme='另一个题材')})
        day=ta.day_snapshot(a)
        self.assertEqual(len(day['groups']['人形机器人']),2)
        self.assertNotIn('另一个题材',day['groups'])
        self.assertNotIn('错误行业',day['groups'])
        self.assertTrue(day['complete'])
        self.assertEqual(ta.day_snapshot(a,'kpl')['matched'],1)

    def test_partial_absence_and_missing_sessions_never_become_zero_or_continuity(self):
        data={d:archive(d,{'000001':'人形机器人','000002':'AI短剧'}) for d in DAYS}
        del data[DAYS[-2]]
        result=ta.activity(data,CAL,DAYS[-1])
        r=next(r for r in result['rows'] if r['theme']=='人形机器人')
        self.assertEqual(r['streak'],1)
        self.assertIsNone(r['previous'])
        self.assertEqual(r['observed'],4)
        partial=ta.day_snapshot(archive(DAYS[-1],{'000001':'人形机器人','000002':'AI短剧'},missing=['000002']))
        self.assertIsNone(ta.observed_count(partial,'AI短剧'))
        self.assertEqual(ta.observed_count(ta.day_snapshot(data[DAYS[-1]]),'无此标签'),0)

    def test_wrong_date_and_conflicting_stock_rows_fail_closed(self):
        a=archive(DAYS[-1],{'000001':'机器人','000002':'消费'})
        bad=copy.deepcopy(a);bad['attribution']['date']=DAYS[-2]
        self.assertFalse(ta.day_snapshot(bad)['available'])
        a['ladder']['stocks'].append(dict(a['ladder']['stocks'][0],limit_times=9))
        day=ta.day_snapshot(a)
        self.assertFalse(day['complete'])
        self.assertEqual((day['matched'],day['total']),(1,2))
        old=archive(DAYS[-2],{'000001':'旧题材'})
        self.assertEqual(ta.activity({DAYS[-1]:old},CAL,DAYS[-1])['rows'],[])

    def test_member_changes_preserve_yesterdays_identity_and_changed_reason(self):
        data={DAYS[-2]:archive(DAYS[-2],{'000001':'人形机器人','000002':'人形机器人'}),
              DAYS[-1]:archive(DAYS[-1],{'000001':'人形机器人','000002':'商业航天','000003':'人形机器人'})}
        result=ta.activity(data,CAL,DAYS[-1]);change=ta.member_changes(result,'人形机器人')
        self.assertEqual(change['retained'],['000001'])
        self.assertEqual([m['code'] for m in change['new']],['000003'])
        self.assertEqual(change['exited'][0]['code'],'000002')
        self.assertTrue(change['exited'][0]['today_limit'])
        self.assertTrue(change['known'])

    def test_future_archive_cannot_change_a_historical_view(self):
        past=archive(DAYS[-2],{'000001':'光刻胶'})
        result=ta.activity({DAYS[-2]:past,DAYS[-1]:archive(DAYS[-1],{'000001':'新题材'})},CAL,DAYS[-2])
        self.assertEqual([r['theme'] for r in result['rows']],['光刻胶'])

    def test_faded_themes_are_kept_and_incomplete_counts_are_not_changes(self):
        data={DAYS[-2]:archive(DAYS[-2],{'000001':'光刻胶','000002':'光刻胶'}),
              DAYS[-1]:archive(DAYS[-1],{'000003':'人形机器人'})}
        result=ta.activity(data,CAL,DAYS[-1])
        faded=next(r for r in result['rows'] if r['theme']=='光刻胶')
        self.assertEqual((faded['count'],faded['previous'],faded['change'],faded['streak']),(0,2,-2,0))
        self.assertEqual(len(ta.member_changes(result,'光刻胶')['exited']),2)
        data[DAYS[-1]]=archive(DAYS[-1],{'000003':'人形机器人','000004':'光刻胶'},missing=['000004'])
        faded=next(r for r in ta.activity(data,CAL,DAYS[-1])['rows'] if r['theme']=='光刻胶')
        self.assertIsNone(faded['count'])
        self.assertIsNone(faded['change'])

    def test_screen_and_index_baskets_are_removed_from_all_concept_comparisons(self):
        bad=['权重股','历史新高','上证50_','金融地产风格','大盘价值','超级品牌','茅指数','科创板做市商',
             '上证180_','红利股','2026三季报预增']
        good=['人形机器人','AI短剧','商业航天','光刻胶','国企改革','并购重组概念']
        for name in bad:
            self.assertTrue(sr.is_style_board(name),name)
            self.assertFalse(_theme(dict(name=name)),name)
        for name in good:self.assertFalse(sr.is_style_board(name),name)
        frame=pd.DataFrame([dict(date='2026-11-23',code=str(i),name=n,pct_10d=100-i)
                            for i,n in enumerate(bad+good)])
        self.assertEqual(set(sr.top_for_date(frame,'2026-11-23')['name']),set(good))


def theme_app(archives, tracking=False):
    import pandas as pd
    from theme_activity_ui import render_activity
    days=['20261117','20261118','20261119','20261120','20261123']
    cal=dict(start_date='20260101',end_date='20261231',open_dates=days)
    render_activity(key='test_theme',asof=days[-1],calendar=cal,archives=archives,prices=pd.DataFrame(),tracking=tracking)


class ThemePageTests(unittest.TestCase):
    def test_search_opens_real_members_and_source_switch_has_no_fake_fallback(self):
        data={d:archive(d,{'000001':'人形机器人','000002':'人形机器人','000003':'AI短剧'}) for d in DAYS}
        app=AppTest.from_function(theme_app,args=(data,),default_timeout=20).run()
        self.assertFalse(app.exception)
        app.text_input(key='test_theme_query').input('RXJQR').run()
        self.assertEqual(app.dataframe[0].value['题材'].tolist(),['人形机器人'])
        self.assertEqual(set(app.dataframe[1].value['代码']),{'000001.SZ','000002.SZ'})
        app.button(key='test_theme_follow').click().run()
        self.assertEqual(app.session_state['theme_focus'],dict(theme='人形机器人',source='ths_public',date=DAYS[-1]))
        app.radio(key='test_theme_source').set_value('kpl').run()
        self.assertFalse(app.exception)
        self.assertTrue(any('暂无有效归因' in m.value for m in app.info))

    def test_members_are_paged_and_pinyin_search_survives_large_lists(self):
        data={DAYS[-1]:archive(DAYS[-1],{f'{i:06}':'人形机器人' for i in range(1,44)})}
        app=AppTest.from_function(theme_app,args=(data,True),default_timeout=20).run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.dataframe[1].value),20)
        app.selectbox(key='test_theme_members_page').select(3).run()
        self.assertEqual(len(app.dataframe[1].value),3)
        app.selectbox(key='test_theme_research_pick').select('sz000001').run()
        self.assertFalse(app.exception)

    def test_focus_carries_source_date_and_single_stock_filter(self):
        data={DAYS[-1]:archive(DAYS[-1],{'000001':'人形机器人'}),DAYS[-2]:archive(DAYS[-2],{'000002':'光刻胶'})}
        app=AppTest.from_function(theme_app,args=(data,True),default_timeout=20)
        app.session_state['theme_focus']=dict(theme='光刻胶',date=DAYS[-2],source='ths_public')
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.selectbox(key='test_theme_date').value,DAYS[-2])
        self.assertEqual(app.selectbox(key='test_theme_minimum').value,1)
        self.assertEqual(app.selectbox(key='test_theme_pick').value,'光刻胶')

    def test_yesterdays_theme_remains_researchable_after_all_members_exit(self):
        data={DAYS[-2]:archive(DAYS[-2],{'000001':'光刻胶','000002':'光刻胶'}),
              DAYS[-1]:archive(DAYS[-1],{'000003':'人形机器人','000004':'人形机器人'})}
        app=AppTest.from_function(theme_app,args=(data,True),default_timeout=20).run()
        app.selectbox(key='test_theme_pick').select('光刻胶').run()
        self.assertFalse(app.exception)
        self.assertEqual(app.dataframe[0].value.set_index('题材').loc['光刻胶','本日已归因涨停'],'0')
        app.selectbox(key='test_theme_prior_research_pick').select('sz000001').run()
        app.button(key='test_theme_prior_research_chart').click().run()
        self.assertEqual(app.session_state['journey_target']['symbol'],'sz000001')


if __name__=='__main__':unittest.main()
