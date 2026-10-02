"""能效监测业务规则：对标口径、状态流转、字段校验与筛选都收在这里。

口径变更后调用 recalculate() 按新规则重算全部历史记录的超标/待分析标记，
但「调整优化」时固化的判定快照（偏差快照、快照基准）不动，保留当初判定时的偏差。
"""
from __future__ import annotations

from typing import Any

from app.services import energyeff_calc as calc
from app.store import store

MODULE = "energyeff"
BENCHMARK_TABLE = calc.BENCHMARK_TABLE
REQUIRED_FIELDS = ["记录编号", "设备类型", "耗能量", "记录月份"]
STATUS_ORDER = ["达标", "轻微偏差", "显著偏差", "已调整"]
ACTION_RULES = {"记录偏差": "轻微偏差", "分析原因": "显著偏差", "调整优化": "已调整"}

# 列表展示字段：单耗指标、对标基准、偏差比率统一由 calc.evaluate 现算
DISPLAY_FIELDS = ["记录编号", "设备类型", "耗能量", "单耗指标", "对标基准", "偏差比率", "记录月份", "能效状态"]
BENCHMARK_REQUIRED = ["设备类型", "记录月份", "对标基准", "对标产量"]


class EnergyeffService:
    # ---- 列表/详情：两处都走同一个 decorate，偏差比率不可能再不一致 ----

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        pending: bool | None = None,
        over_limit: bool | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [self.decorate(dict(row)) for row in store.rows(MODULE)]
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("能效状态") == status]
        if pending is not None:
            rows = [row for row in rows if bool(row.get("pending")) == pending]
        if over_limit is not None:
            rows = [row for row in rows if bool(row.get("overLimit")) == over_limit]
        # 记录月份统一升序（同月份按 id），避免字符串乱序
        rows.sort(key=lambda row: (row.get("记录月份") or "", int(row.get("id", 0))))
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self.decorate(dict(entry)) if entry is not None else None

    def decorate(self, entry: dict[str, Any]) -> dict[str, Any]:
        """给原始记录补上统一口径算出的单耗指标、对标基准、偏差比率与超标提示。"""
        result = calc.evaluate(entry, store.rows(BENCHMARK_TABLE))
        entry["单耗指标"] = result["单耗指标"]
        entry["对标基准"] = result["对标基准"]
        entry["偏差比率"] = result["偏差比率"] if result["可对标"] else None
        entry["overLimit"] = result["超标"]
        entry["overLimitAmount"] = result["超标量"]
        entry["benchmarkable"] = result["可对标"]
        if not result["可对标"]:
            entry["benchmarkMessage"] = result["原因"]
        else:
            entry.pop("benchmarkMessage", None)
        entry["能效状态"] = entry.get("status")
        # 已调整记录：主列展示调整时固化的偏差快照（保留当初判定时的偏差），
        # 当前口径重算结果另放「当前偏差比率」供对比；未调整记录两者一致
        entry["当前单耗指标"] = result["单耗指标"]
        entry["当前偏差比率"] = result["偏差比率"] if result["可对标"] else None
        if entry.get("status") == STATUS_ORDER[-1] and entry.get("偏差快照") is not None:
            entry["偏差比率"] = entry.get("偏差快照")
            entry["单耗指标"] = entry.get("快照单耗")
        return entry

    # ---- 登记：超对标基准直接拦下并说明超了多少 ----

    def create_entry(
        self, values: dict[str, Any], *, force: bool = False
    ) -> tuple[dict[str, Any] | None, str]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        if calc.normalize_month(values.get("记录月份")) is None:
            return None, "记录月份格式应为 YYYY-MM"
        if calc.to_float(values.get("耗能量")) is None:
            return None, "耗能量必须是数字，无法计算单耗指标"
        code = str(values.get("记录编号", "")).strip()
        if any(str(row.get("记录编号", "")).strip() == code for row in store.rows(MODULE)):
            return None, f"记录编号 {code} 已存在，请勿重复登记"

        raw: dict[str, Any] = {"记录编号": code, "设备类型": str(values.get("设备类型", "")).strip()}
        raw["耗能量"] = calc.to_float(values.get("耗能量"))
        raw["记录月份"] = calc.normalize_month(values.get("记录月份"))
        result = calc.evaluate(raw, store.rows(BENCHMARK_TABLE))
        if not result["可对标"]:
            return None, result["原因"]
        if result["超标"] and not force:
            return None, calc.over_limit_message(result)

        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update(raw)
        entry["status"] = result["偏差状态"]
        entry["pending"] = force and result["超标"]  # 强制登记的超标记录进待分析清单
        entry["abnormal"] = force and result["超标"]
        rows.append(entry)
        return self.decorate(dict(entry)), ""

    # ---- 状态流转：调整后退出待分析，并固化当初判定时的偏差快照 ----

    def run_action(self, entry_id: int, action: str, values: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"能效记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于能效监测可执行范围"
        target = ACTION_RULES[action]
        if entry.get("status") == STATUS_ORDER[-1]:
            return None, "该记录已调整，不再进入待分析清单，也不能重复流转"

        if action == "调整优化":
            adjusted_energy = None
            if values:
                adjusted_energy = calc.to_float(values.get("调整后耗能量"))
                if str(values.get("调整后耗能量") or "").strip() and adjusted_energy is None:
                    return None, "调整后耗能量必须是数字"
            # 固化调整前（当前口径）的判定结果，作为历史偏差快照
            before = calc.evaluate(entry, store.rows(BENCHMARK_TABLE))
            if before["可对标"]:
                entry["偏差快照"] = before["偏差比率"]
                entry["快照基准"] = before["对标基准"]
                entry["快照单耗"] = before["单耗指标"]
            if adjusted_energy is not None:
                entry["耗能量"] = adjusted_energy
                after = calc.evaluate(entry, store.rows(BENCHMARK_TABLE))
                # 调整后仍超标的，按新口径提示但不阻止闭环；快照已保留原偏差
                entry["调整后超标"] = after["超标"]
            else:
                entry["调整后超标"] = False
            entry["status"] = target
            entry["pending"] = False  # 已调整记录一律不再进待分析清单
            entry["abnormal"] = False
            return self.decorate(dict(entry)), "能效记录已调整并退出待分析清单，已保留判定时的偏差"

        entry["status"] = target
        # 记录偏差/分析原因只改状态，是否待分析仍由当前口径的超标结果决定
        latest = calc.evaluate(entry, store.rows(BENCHMARK_TABLE))
        entry["pending"] = latest["超标"]
        entry["abnormal"] = latest["超标"]
        return self.decorate(dict(entry)), f"能效记录已{action}"

    # ---- 对标口径维护：改完按新规则把历史数据重算一遍 ----

    def list_benchmarks(self) -> list[dict[str, Any]]:
        rows = store.rows(BENCHMARK_TABLE)
        return sorted((dict(row) for row in rows), key=lambda row: (row.get("设备类型", ""), row.get("记录月份", "")))

    def upsert_benchmark(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        missing = [field for field in BENCHMARK_REQUIRED if not str(values.get(field) or "").strip()]
        if missing:
            return None, f"缺少必填字段：{'、'.join(missing)}"
        month = calc.normalize_month(values.get("记录月份"))
        if month is None:
            return None, "记录月份格式应为 YYYY-MM"
        basis = calc.to_float(values.get("对标基准"))
        monthly_output = calc.to_float(values.get("对标产量"))
        if basis is None or monthly_output is None:
            return None, "对标基准与对标产量必须是数字"
        if basis <= 0 or monthly_output <= 0:
            return None, "对标基准与对标产量必须为正数"

        device_type = str(values.get("设备类型", "")).strip()
        existing = calc.find_benchmark(store.rows(BENCHMARK_TABLE), device_type, month)
        if existing is None:
            rows = store.rows(BENCHMARK_TABLE)
            existing = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
            rows.append(existing)
        existing.update({"设备类型": device_type, "记录月份": month, "对标基准": basis, "对标产量": monthly_output})

        recalculated = self.recalculate()
        return dict(existing), f"对标口径已保存，已按新口径重算 {recalculated} 条历史记录"

    def recalculate(self) -> int:
        """口径变更后重算全部记录：刷新超标/待分析标记，历史偏差快照保持不动。"""
        return calc.apply_spec(store.rows(MODULE), store.rows(BENCHMARK_TABLE))
