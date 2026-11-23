from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import config_gurus
import tech_analysis
import tech_overlay


class TechnicalPositionLanguageTests(unittest.TestCase):
    def test_dma_and_levels_are_geometric_positions_not_directional_verdicts(self):
        state = tech_analysis._describe_state(
            100.0,
            [],
            [],
            {
                "3x3": {"last": 95.0},
                "7x5": {"last": 90.0},
                "25x5": {"last": 80.0},
            },
            [],
            {},
        )
        text = " ".join(state.values()) if isinstance(state["warn"], str) else (
            state["chan"] + " " + state["dinapoli"] + " " + state["target"]
            + " " + " ".join(state["warn"])
        )
        for phrase in ("趋势健康", "趋势转弱", "支撑", "阻力", "变盘"):
            self.assertNotIn(phrase, text)
        self.assertIn("几何位置", text)
        self.assertIn("不能单独证明后续方向", text)

        equal_state = tech_analysis._describe_state(
            100.0,
            [],
            [],
            {
                "3x3": {"last": 100.0},
                "7x5": {"last": 100.0},
                "25x5": {"last": 100.0},
            },
            [],
            {},
        )["dinapoli"]
        self.assertIn("现价等于 3x3 DMA", equal_state)
        self.assertIn("3x3 DMA 与 7x5 DMA 相同", equal_state)
        self.assertIn("现价等于该均线", equal_state)

        levels = tech_analysis._key_levels(
            100.0,
            [{"zg": 105.0, "zd": 95.0}],
            [],
            {"cop": 110.0, "op": 115.0, "xop": 120.0},
            [],
        )
        self.assertEqual({item["side"] for item in levels}, {"上方", "下方"})
        equal_levels = tech_analysis._key_levels(
            100.0,
            [{"zg": 100.0, "zd": 95.0}],
            [],
            {},
            [],
        )
        self.assertIn("当前价", {item["side"] for item in equal_levels})

    def test_overlay_disclaimer_keeps_the_uncertainty_boundary(self):
        self.assertIn("不能单独证明后续方向", tech_overlay._DISCLAIMER)
        self.assertIn("几何位置", tech_overlay._DISCLAIMER)


class MacroAndGuruLanguageTests(unittest.TestCase):
    def test_erp_copy_removes_certainty_labels_and_explains_limits(self):
        source = (ROOT / "page_macro_erp.py").read_text(encoding="utf-8")
        for phrase in (
            "黄金坑", "抄底", "极便宜", "赔率巨大", "股票越占优",
            "风向标", "定价锚", "情绪指引", "外资流出压力",
            "全球经济与能源需求温度计", "资金热度", "全球宏观资产联动",
            "中枢",
        ):
            self.assertNotIn(phrase, source)
        self.assertIn("并非严格的无风险利率", source)
        self.assertIn("不能单独证明股债相对方向", source)
        self.assertIn("不能单独归因于工业需求、避险情绪、复苏或衰退", source)

    def test_guru_labels_are_descriptive(self):
        labels = " ".join(value["style"] for value in config_gurus.GURUS.values())
        for phrase in ("股神", "大空头", "抄底之王", "芒格传人"):
            self.assertNotIn(phrase, labels)
        self.assertIn("价值投资", labels)
        self.assertIn("集中持仓", labels)

    def test_guru_page_does_not_claim_to_follow_superior_money(self):
        source = (ROOT / "app.py").read_text(encoding="utf-8")
        for phrase in (
            "跟随全球顶尖大脑",
            "寻找下一个 Alpha",
            "国会山股神",
            "基金经理名人堂",
        ):
            self.assertNotIn(phrase, source)
        self.assertIn("披露后仓位可能已经变化", source)
        self.assertIn("不是当前建议", source)


if __name__ == "__main__":
    unittest.main()
