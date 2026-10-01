"""能效对标统一口径的回归测试：不依赖 FastAPI，直接跑 python -m unittest。

覆盖：单耗分组计算、列表/详情一致、超标拦截、动作分级与快照、
已调整不进待分析、口径更新后历史重算且快照保留、月份排序。
"""
from __future__ import annotations

import unittest

from app.services.energyeff import EnergyeffService
from app.services.energyeff_caliber import CaliberError, parse_month
from app.store import store


def seed_rows() -> list[dict]:
    return [
        {"id": 1, "status": "达标", "pending": True, "abnormal": False, "判定快照": None,
         "记录编号": "T-0001", "设备类型": "锅炉", "耗能量": 58.0, "产量": 480.0, "记录月份": "2026-06"},
        {"id": 2, "status": "达标", "pending": True, "abnormal": False, "判定快照": None,
         "记录编号": "T-0002", "设备类型": "锅炉", "耗能量": 62.0, "产量": 520.0, "记录月份": "2026-06"},
        {"id": 3, "status": "达标", "pending": False, "abnormal": False, "判定快照": None,
         "记录编号": "T-0003", "设备类型": "锅炉", "耗能量": 150.0, "产量": 1150.0, "记录月份": "2026-08"},
        {"id": 4, "status": "已调整", "pending": False, "abnormal": False,
         "记录编号": "T-0004", "设备类型": "锅炉", "耗能量": 117.7, "产量": 1000.0, "记录月份": "2026-05",
         "判定快照": {"判定时单耗指标": 0.1177, "判定时对标基准": 0.11,
                   "判定时偏差比率": 0.07, "判定时偏差比率显示": "+7.00%",
                   "判定结论": "已调整", "判定时间": "2026-06-05 10:30:00", "口径版本": 1}},
    ]


class CaliberTest(unittest.TestCase):
    def setUp(self) -> None:
        table = store.rows("energyeff")
        table.clear()
        table.extend(seed_rows())
        self.service = EnergyeffService()
        self.service.refresh_seed()

    def _by_id(self, entry_id: int) -> dict:
        return self.service.get_entry(entry_id)

    def test_unit_index_grouped_by_type_and_month(self) -> None:
        # 同组两条共用单耗：(58+62)/(480+520) = 0.12，偏差 +9.09%
        row1, row2 = self._by_id(1), self._by_id(2)
        self.assertAlmostEqual(row1["单耗指标"], 0.12)
        self.assertAlmostEqual(row2["单耗指标"], 0.12)
        self.assertAlmostEqual(row1["偏差比率"], row2["偏差比率"])
        self.assertEqual(row1["status"], "轻微偏差")
        self.assertTrue(row1["超标"])
        self.assertIn("超出 0.01", row1["超标提示"])

    def test_significant_deviation_grade(self) -> None:
        # 150/1150 ≈ 0.1304，相对 0.11 偏差 +18.58%，显著
        row = self._by_id(3)
        self.assertGreater(row["偏差比率"], 0.10)
        self.assertEqual(row["status"], "显著偏差")

    def test_list_and_detail_share_same_caliber(self) -> None:
        listed = next(item for item in self.service.list_entries(page=1, size=100)[0] if item["id"] == 3)
        detail = self._by_id(3)
        self.assertEqual(listed["单耗指标"], detail["单耗指标"])
        self.assertEqual(listed["偏差比率"], detail["偏差比率"])

    def test_list_sorted_by_month(self) -> None:
        months = [item["记录月份"] for item in self.service.list_entries(page=1, size=100)[0]]
        self.assertEqual(months, sorted(months))

    def test_adjusted_kept_out_of_pending_with_snapshot(self) -> None:
        pending_ids = {item["id"] for item, _ in [
            (row, None) for row in self.service.list_entries(pending=True, page=1, size=100)[0]
        ]}
        self.assertNotIn(4, pending_ids)
        row = self._by_id(4)
        self.assertEqual(row["status"], "已调整")
        self.assertFalse(row["pending"])
        self.assertEqual(row["判定快照"]["判定时偏差比率显示"], "+7.00%")

    def test_create_blocked_when_over_benchmark(self) -> None:
        entry, message = self.service.create_entry(
            {"设备类型": "锅炉", "耗能量": 200.0, "产量": 1000.0, "记录月份": "2026-09"})
        self.assertIsNone(entry)
        self.assertIn("登记已拦下", message)
        self.assertIn("超出", message)

    def test_create_uses_group_total_in_dry_run(self) -> None:
        # 组内已有 120/1000 = 0.12（超标）；即便单条自身不超标，合计口径仍超标 -> 拦截
        entry, _ = self.service.create_entry(
            {"设备类型": "锅炉", "耗能量": 12.0, "产量": 100.0, "记录月份": "2026-06"})
        self.assertIsNone(entry)

    def test_create_ok_below_benchmark(self) -> None:
        entry, message = self.service.create_entry(
            {"设备类型": "空压机", "耗能量": 80.0, "产量": 1000.0, "记录月份": "2026-09"})
        self.assertIsNotNone(entry)
        self.assertEqual(entry["status"], "达标")
        self.assertEqual(message, "")

    def test_action_order_guarded_and_snapshot_frozen(self) -> None:
        # 轻微偏差不能越级分析原因
        self.assertIsNone(self.service.run_action(1, "分析原因")[0])
        entry, _ = self.service.run_action(1, "记录偏差")
        self.assertIsNotNone(entry["判定快照"])
        self.assertEqual(entry["判定快照"]["判定结论"], "轻微偏差")
        self.assertAlmostEqual(entry["判定快照"]["判定时偏差比率"], 0.090909)
        # 达标记录没有偏差可记录
        store.rows("energyeff").append(
            {"id": 5, "记录编号": "T-0005", "设备类型": "空压机",
             "耗能量": 80.0, "产量": 1000.0, "记录月份": "2026-06",
             "status": "达标", "pending": False, "abnormal": False, "判定快照": None})
        self.assertIsNone(self.service.run_action(5, "记录偏差")[0])

    def test_caliber_change_recalculates_history_but_keeps_snapshot(self) -> None:
        # 放宽到 0.13：8 月锅炉 0.1304 仍轻微超标；6 月 0.12 变达标
        self.service.update_caliber({"锅炉": 0.13})
        self.assertEqual(self._by_id(1)["status"], "达标")
        self.assertEqual(self._by_id(3)["status"], "轻微偏差")
        self.assertFalse(self._by_id(1)["pending"])
        # 已调整记录仍保留旧快照、不回待分析
        adjusted = self._by_id(4)
        self.assertEqual(adjusted["status"], "已调整")
        self.assertEqual(adjusted["判定快照"]["判定时偏差比率"], 0.07)
        self.assertFalse(adjusted["pending"])

        # 收紧到 0.10：6 月组重新超标进待分析
        self.service.update_caliber({"锅炉": 0.10})
        self.assertEqual(self._by_id(1)["status"], "显著偏差")
        self.assertTrue(self._by_id(1)["pending"])
        pending = self.service.list_entries(pending=True, page=1, size=100)[1]
        self.assertEqual(pending, 3)  # id 1/2/3，id 4 已调整不进

    def test_unknown_type_and_bad_month_rejected(self) -> None:
        with self.assertRaises(CaliberError):
            parse_month("2026.06")
        self.assertEqual(parse_month("2026/6"), "2026-06")
        entry, message = self.service.create_entry(
            {"设备类型": "叉车", "耗能量": 1.0, "产量": 1.0, "记录月份": "2026-06"})
        self.assertIsNone(entry)
        self.assertIn("尚未配置对标基准", message)


if __name__ == "__main__":
    unittest.main()
