"""能效对标统一口径的业务测试。

运行：PYTHONPATH=. python3 -m unittest app.tests.test_energyeff -v
"""
from __future__ import annotations

import unittest

from app.services import energyeff_calc as calc
from app.services.energyeff import BENCHMARK_TABLE, EnergyeffService
from app.store import store


BENCHMARKS = [
    {"id": 1, "设备类型": "燃气锅炉", "记录月份": "2026-09", "对标基准": 1.0, "对标产量": 1000.0},
    {"id": 2, "设备类型": "螺杆空压机", "记录月份": "2026-08", "对标基准": 5.0, "对标产量": 200.0},
]


class CalcTest(unittest.TestCase):
    def test_unit_and_deviation(self):
        result = calc.evaluate({"设备类型": "燃气锅炉", "耗能量": 1320, "记录月份": "2026-09"}, BENCHMARKS)
        self.assertTrue(result["可对标"])
        self.assertEqual(result["单耗指标"], 1.32)
        self.assertEqual(result["偏差比率"], 32.0)
        self.assertTrue(result["超标"])
        self.assertEqual(result["超标量"], 0.32)
        self.assertEqual(result["偏差状态"], "显著偏差")

    def test_slight_deviation_band(self):
        result = calc.evaluate({"设备类型": "燃气锅炉", "耗能量": 1040, "记录月份": "2026-09"}, BENCHMARKS)
        self.assertEqual(result["偏差比率"], 4.0)
        self.assertEqual(result["偏差状态"], "轻微偏差")

    def test_within_basis(self):
        result = calc.evaluate({"设备类型": "燃气锅炉", "耗能量": 980, "记录月份": "2026-09"}, BENCHMARKS)
        self.assertEqual(result["偏差状态"], "达标")
        self.assertFalse(result["超标"])

    def test_missing_benchmark_reason(self):
        result = calc.evaluate({"设备类型": "电动叉车", "耗能量": 100, "记录月份": "2026-09"}, BENCHMARKS)
        self.assertFalse(result["可对标"])
        self.assertIn("尚未维护对标口径", result["原因"])

    def test_bad_month(self):
        self.assertIsNone(calc.normalize_month("2026/09"))
        self.assertEqual(calc.normalize_month("2026-09-15"), "2026-09")


class ServiceTest(unittest.TestCase):
    def setUp(self):
        # 每个用例用一份干净的内存数据
        store._tables = {
            "energyeff": [
                {"id": 1, "status": "显著偏差", "pending": True, "abnormal": True,
                 "记录编号": "ENER-T1", "设备类型": "燃气锅炉", "耗能量": 1320.0, "记录月份": "2026-09"},
                {"id": 2, "status": "已调整", "pending": False, "abnormal": False,
                 "记录编号": "ENER-T2", "设备类型": "螺杆空压机", "耗能量": 1150.0, "记录月份": "2026-08",
                 "偏差快照": 15.0, "快照基准": 5.0, "快照单耗": 5.75},
            ],
            BENCHMARK_TABLE: [dict(row) for row in BENCHMARKS],
        }
        self.service = EnergyeffService()

    def test_list_and_detail_share_same_caliber(self):
        items, _ = self.service.list_entries(page=1, size=20)
        for row in items:
            detail = self.service.get_entry(int(row["id"]))
            self.assertEqual(detail["单耗指标"], row["单耗指标"])
            self.assertEqual(detail["偏差比率"], row["偏差比率"])
            self.assertEqual(detail["对标基准"], row["对标基准"])

    def test_months_sorted_ascending(self):
        # 再塞两条打乱月份的记录
        rows = store.rows("energyeff")
        rows.append({"id": 3, "记录编号": "ENER-T3", "设备类型": "燃气锅炉", "耗能量": 990.0, "记录月份": "2026-07"})
        self.service.upsert_benchmark(
            {"设备类型": "燃气锅炉", "记录月份": "2026-07", "对标基准": 1.0, "对标产量": 1000.0}
        )
        months = [r["记录月份"] for r in self.service.list_entries(page=1, size=50)[0]]
        self.assertEqual(months, sorted(months))

    def test_over_limit_blocked_with_amount(self):
        entry, message = self.service.create_entry(
            {"记录编号": "ENER-T4", "设备类型": "燃气锅炉", "耗能量": 1500, "记录月份": "2026-09"}
        )
        self.assertIsNone(entry)
        self.assertIn("超过对标基准", message)
        self.assertIn("超标量 0.5", message)
        self.assertIn("50.0%", message)

    def test_force_create_enters_pending(self):
        entry, message = self.service.create_entry(
            {"记录编号": "ENER-T5", "设备类型": "燃气锅炉", "耗能量": 1500, "记录月份": "2026-09"},
            force=True,
        )
        self.assertIsNotNone(entry)
        self.assertTrue(entry["pending"])
        self.assertTrue(entry["overLimit"])

    def test_adjusted_not_in_pending_and_snapshot_kept(self):
        pending_ids = {r["id"] for r in self.service.list_entries(pending=True)[0]}
        self.assertNotIn(2, pending_ids)

        entry, _ = self.service.run_action(1, "调整优化", {"调整后耗能量": 990})
        self.assertEqual(entry["status"], "已调整")
        self.assertFalse(entry["pending"])
        self.assertEqual(entry["偏差快照"], 32.0)       # 当初判定时的偏差
        self.assertEqual(entry["偏差比率"], 32.0)       # 主列保留历史偏差
        self.assertEqual(entry["当前偏差比率"], -1.0)    # 调整后按现行口径已达标

        again, message = self.service.run_action(1, "分析原因", {})
        self.assertIsNone(again)
        self.assertIn("已调整", message)

    def test_spec_change_recalculates_history_but_keeps_snapshot(self):
        self.service.run_action(1, "调整优化", {"调整后耗能量": 990})
        _, message = self.service.upsert_benchmark(
            {"设备类型": "燃气锅炉", "记录月份": "2026-09", "对标基准": 1.5, "对标产量": 1000.0}
        )
        self.assertIn("重算", message)

        entry = self.service.get_entry(1)
        self.assertEqual(entry["偏差快照"], 32.0)   # 历史偏差不被新口径覆盖
        self.assertEqual(entry["偏差比率"], 32.0)
        self.assertEqual(entry["当前偏差比率"], -34.0)
        self.assertFalse(entry["pending"])          # 已调整记录不因重算回到待分析

    def test_pending_only_contains_overlimit_unadjusted(self):
        pending = self.service.list_entries(pending=True)[0]
        self.assertEqual([r["id"] for r in pending], [1])
        over_limit = {r["id"] for r in self.service.list_entries(over_limit=True)[0]}
        self.assertIn(1, over_limit)  # 超标记录可单独筛出
        self.assertIn(2, over_limit)  # 已调整但仍超标的历史记录也可查到


if __name__ == "__main__":
    unittest.main()
