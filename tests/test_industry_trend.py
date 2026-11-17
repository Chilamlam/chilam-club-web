import json
import unittest
from unittest.mock import patch

import pandas as pd

import page_industry_signal as page


class IndustryTrendTests(unittest.TestCase):
    def setUp(self):
        self.series = pd.DataFrame({
            "date": ["20260529", "20260601", "20261231", "20270104"],
            "mv_share": [1.0, 0.98, 0.91, 0.86],
            "amt_share": [1.7, 1.5, 1.1, 1.4],
            "mv_pct": [82.6, 75.4, 22.2, 17.8],
            "amt_pct": [52.5, 22.5, 62.5, 87.5],
            "mv_window_len": [250] * 4,
            "amt_window_len": [20] * 4,
        })

    def test_month_and_year_boundaries_use_calendar_dates_on_both_charts(self):
        with patch.object(page, "load_history", return_value=pd.DataFrame()), \
             patch.object(page.isig, "industry_series", return_value=self.series), \
             patch.object(page.st, "plotly_chart") as draw, \
             patch.object(page.st, "caption") as caption:
            page.render_trend("801054.SI", "小金属", {"mv_pct_max": 30, "amt_pct_min": 80})

        self.assertEqual(draw.call_count, 2)
        expected_dates = ["2026-05-29", "2026-06-01", "2026-12-31", "2027-01-04"]
        columns = (("mv_share", "amt_share"), ("mv_pct", "amt_pct"))
        for call, ys in zip(draw.call_args_list, columns):
            figure = call.args[0]
            # 检查交给浏览器的 JSON，而非只检查 Python 中的日期类型。
            spec = json.loads(figure.to_json())
            self.assertEqual(spec["layout"]["xaxis"]["type"], "date")
            self.assertEqual(spec["layout"]["xaxis"]["hoverformat"], "%Y-%m-%d")
            for trace, serialized, column in zip(figure.data, spec["data"], ys):
                self.assertEqual(serialized["x"], expected_dates)
                self.assertEqual(list(trace.y), self.series[column].tolist())
                dates = pd.to_datetime(serialized["x"])
                self.assertEqual((dates[1] - dates[0]).days, 3)
                self.assertEqual((dates[3] - dates[2]).days, 4)
        self.assertEqual(draw.call_args_list[0].args[0].layout.yaxis.title.text, "占参与行业合计(%)")
        text = "\n".join(call.args[0] for call in caption.call_args_list)
        self.assertNotIn("交叉", text)
        self.assertIn("同一交易日", text)
        self.assertIn("恰好等于阈值不算命中", text)

    def test_invalid_calendar_date_is_not_silently_plotted(self):
        self.series.loc[1, "date"] = "20260230"
        with patch.object(page, "load_history", return_value=pd.DataFrame()), \
             patch.object(page.isig, "industry_series", return_value=self.series), \
             patch.object(page.st, "plotly_chart") as draw, \
             patch.object(page.st, "warning") as warning:
            page.render_trend("801054.SI", "小金属", {})
        draw.assert_not_called()
        warning.assert_called_once()
        self.assertIn("无效日期", warning.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
