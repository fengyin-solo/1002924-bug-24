"""能效监测业务规则：登记、状态流转、筛选口径都收在这里。

对标相关的单耗指标、偏差比率、超标判定统一走 energyeff_caliber，本模块只负责
记录本身的增删改查、动作流转与口径重算编排。
"""
from __future__ import annotations

from typing import Any

from app.services.energyeff_caliber import (
    ADJUSTED_STATUSES,
    CaliberBook,
    CaliberError,
    STATUS_ADJUSTED,
    STATUS_MAJOR,
    STATUS_MINOR,
    decorate_rows,
    make_snapshot,
    parse_month,
    parse_number,
    recalculate_rows,
    unit_index_groups,
)
from app.store import store

MODULE = "energyeff"
REQUIRED_FIELDS = ["设备类型", "耗能量", "产量", "记录月份"]
ACTION_RULES = {"记录偏差": STATUS_MINOR, "分析原因": STATUS_MAJOR, "调整优化": STATUS_ADJUSTED}
# 哪些动作会把记录重新送回待分析（调整优化则视为处理完毕）。
PENDING_ACTIONS = {"记录偏差", "分析原因"}


class EnergyeffService:
    def __init__(self) -> None:
        self.book = CaliberBook()

    # --- 读取：列表 / 详情 / 统计，全部经统一口径装饰 --------------------

    def _decorated(self) -> list[dict[str, Any]]:
        return decorate_rows(store.rows(MODULE), self.book)

    def list_entries(
        self,
        *,
        keyword: str | None = None,
        status: str | None = None,
        pending: bool | None = None,
        over: bool | None = None,
        equipment_type: str | None = None,
        month: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = self._decorated()
        if keyword:
            rows = [row for row in rows if keyword in str(row.get("记录编号", ""))]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        if pending is not None:
            rows = [row for row in rows if bool(row.get("pending")) == pending]
        if over:
            rows = [row for row in rows if row.get("超标")]
        if equipment_type:
            rows = [row for row in rows if row.get("设备类型") == equipment_type]
        if month:
            rows = [row for row in rows if row.get("记录月份") == month]
        total = len(rows)
        start = max(page - 1, 0) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        row = store.find(MODULE, entry_id)
        if row is None:
            return None
        for view in self._decorated():
            if view.get("id") == entry_id:
                return view
        return None

    def stats(self) -> dict[str, int]:
        rows = self._decorated()
        return {
            "达标设备": sum(1 for row in rows if row.get("status") == "达标"),
            "偏差设备": sum(1 for row in rows if row.get("status") == "轻微偏差"),
            "显著偏差设备": sum(1 for row in rows if row.get("status") == "显著偏差"),
            "待分析": sum(1 for row in rows if row.get("pending")),
            "超标记录": sum(1 for row in rows if row.get("超标")),
            "已调整": sum(1 for row in rows if row.get("status") == "已调整"),
        }

    # --- 登记：超过对标基准直接拦下 --------------------------------------

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        try:
            equipment_type = str(values.get("设备类型") or "").strip()
            month = parse_month(values.get("记录月份"))
            energy = parse_number("耗能量", values.get("耗能量"))
            output = parse_number("产量", values.get("产量"))
        except CaliberError as exc:
            return None, str(exc)
        if not equipment_type:
            return None, "设备类型不能为空"
        if self.book.benchmark_of(equipment_type) is None:
            types = "、".join(self.book.equipment_types())
            return None, f"设备类型「{equipment_type}」尚未配置对标基准，可选类型：{types}"

        # 登记时按"含本条"的口径试算单耗与偏差，超标则不允许入库并说明超了多少。
        candidate = {
            "设备类型": equipment_type,
            "记录月份": month,
            "耗能量": energy,
            "产量": output,
        }
        rows = store.rows(MODULE)
        groups = unit_index_groups(rows)
        aggregate = groups.setdefault((equipment_type, month), {"耗能量": 0.0, "产量": 0.0})
        group_energy = aggregate["耗能量"] + energy
        group_output = aggregate["产量"] + output
        if group_output <= 0:
            return None, "该记录与同设备同月份的合计产量为 0，算不出单耗指标，请核对产量"
        unit_index = group_energy / group_output
        benchmark = self.book.benchmark_of(equipment_type)
        deviation = (unit_index - benchmark) / benchmark
        if deviation > 0:
            return None, (
                f"登记已拦下：单耗指标 {unit_index:.4f}（同设备同月份合计口径）"
                f"超过对标基准 {benchmark:.4f}，超出 {unit_index - benchmark:.4f}"
                f"（{deviation * 100:+.2f}%）。请先分析偏差或调整用能后再登记"
            )

        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update(candidate)
        entry.update({
            "记录编号": str(values.get("记录编号") or "").strip() or self._next_code(rows),
            "status": "达标",
            "pending": False,
            "abnormal": False,
            "判定快照": None,
        })
        rows.append(entry)
        return self.get_entry(entry["id"]), ""

    @staticmethod
    def _next_code(rows: list[dict[str, Any]]) -> str:
        return f"ENER-{max((int(row.get('id', 0)) for row in rows), default=0) + 1:04d}"

    # --- 状态流转：动作时冻结判定快照，已调整记录不再进待分析 ------------

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"能效记录 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于能效监测可执行范围"
        if entry.get("status") in ADJUSTED_STATUSES:
            return None, "该记录已调整完成，不再进入待分析；如需重开请先修改对标口径"

        view = self.get_entry(entry_id)
        if view and view.get("口径错误"):
            return None, f"记录数据无法对标：{view['口径错误']}"

        target = ACTION_RULES[action]
        # 动作推进不能跳得比当前偏差判定更远：超标但只在轻微区间的记录，
        # 直接「分析原因」会被拦下；达标记录没有偏差可记录。
        graded = view.get("status") if view else target
        if action == "记录偏差" and graded == "达标":
            return None, "该记录单耗未超过对标基准，没有偏差可记录"
        if action == "分析原因" and graded != "显著偏差":
            return None, "该记录偏差尚未达到显著偏差区间，请先执行「记录偏差」"

        entry["status"] = target
        entry["pending"] = action in PENDING_ACTIONS
        entry["abnormal"] = action in PENDING_ACTIONS
        entry["判定快照"] = make_snapshot(view, target, self.book)
        return self.get_entry(entry_id), f"能效记录已{action}"

    # --- 对标口径：查看 / 更新，更新后整表按新规则重算 --------------------

    def caliber(self) -> dict[str, Any]:
        return self.book.as_dict()

    def update_caliber(self, benchmarks: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        try:
            message = self.book.update_benchmarks(benchmarks)
        except CaliberError as exc:
            return None, str(exc)
        summary = recalculate_rows(store.rows(MODULE), self.book)
        message += f"：重算 {summary['recalculated']} 条，待分析 {summary['待分析']} 条"
        return {"book": self.book.as_dict(), "summary": summary, "message": message}, message

    def refresh_seed(self) -> None:
        """服务启动时按现行口径校准一次种子记录的状态/待分析/异常标记。"""
        recalculate_rows(store.rows(MODULE), self.book)
