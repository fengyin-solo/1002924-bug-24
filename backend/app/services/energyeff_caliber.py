"""能效对标统一口径：单耗指标、偏差比率、超标判定与状态分级只在这一份里算。

列表、详情、导出、登记拦截与口径重算全部走这里的 evaluate/recalculate，
避免同一台设备在不同页面各算一套、偏差对不上。

口径定义
- 单耗指标：同「设备类型 + 记录月份」的所有记录合计耗能量 / 合计产量
  （吨标准煤 / 吨产品）。组内每条记录共用同一个单耗指标。
- 对标基准：按设备类型配置的单耗上限（CaliberBook.benchmarks）。
- 偏差比率 = (单耗指标 - 对标基准) / 对标基准；为正即超过对标基准。
- 状态分级：偏差 ≤ 0 达标；0 < 偏差 ≤ 显著偏差阈值 为轻微偏差；超过为显著偏差。
- 判定快照：执行记录偏差/分析原因/调整优化时，把当时的单耗、基准、偏差冻结在
  记录上；之后口径怎么改，已调整记录保留当初的判定结果。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any

# --- 状态与阈值 -----------------------------------------------------------
STATUS_OK = "达标"
STATUS_MINOR = "轻微偏差"
STATUS_MAJOR = "显著偏差"
STATUS_ADJUSTED = "已调整"
ADJUSTED_STATUSES = {STATUS_ADJUSTED}

# 偏差比率超过该比例（相对基准）记为显著偏差，否则为轻微偏差。
DEFAULT_SIGNIFICANT_THRESHOLD = 0.10

# 默认对标基准：设备类型 -> 单耗上限（吨标准煤 / 吨产品）。
DEFAULT_BENCHMARKS: dict[str, float] = {
    "锅炉": 0.1100,
    "空压机": 0.0900,
    "制冷机组": 0.1400,
    "风机水泵": 0.0800,
}

UNIT_LABEL = "吨标准煤/吨产品"
SNAPSHOT_FIELDS = ("判定时单耗指标", "判定时对标基准", "判定时偏差比率", "判定结论", "判定时间", "口径版本")

_MONTH_LOOSE = re.compile(r"^(\d{4})[-/年](\d{1,2})月?$")
_MONTH_STRICT = re.compile(r"^\d{4}-(?:0[1-9]|1[0-2])$")


class CaliberError(ValueError):
    """口径校验失败：月份格式不对、数值非法或设备类型未配置基准。"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_month(value: Any) -> str:
    """把记录月份归一到 YYYY-MM；兼容 2026/7、2026年7月 等写法。"""
    text = str(value or "").strip()
    loose = _MONTH_LOOSE.match(text)
    if loose:
        year, month = loose.groups()
        text = f"{year}-{int(month):02d}"
    if not _MONTH_STRICT.match(text):
        raise CaliberError(f"记录月份「{value}」格式不正确，应为 YYYY-MM（如 2026-07）")
    return text


def parse_number(field: str, value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise CaliberError(f"{field}必须是数字，当前为「{value}」") from None
    if number < 0:
        raise CaliberError(f"{field}不能为负数，当前为 {number}")
    return number


def format_percent(deviation: float | None) -> str:
    if deviation is None:
        return "—"
    return f"{deviation * 100:+.2f}%"


class CaliberBook:
    """对标口径配置：基准与阈值唯一可变的来源，改完即触发历史数据重算。"""

    def __init__(
        self,
        benchmarks: dict[str, float] | None = None,
        significant_threshold: float = DEFAULT_SIGNIFICANT_THRESHOLD,
    ) -> None:
        self.benchmarks: dict[str, float] = {k: round(v, 6) for k, v in (benchmarks or DEFAULT_BENCHMARKS).items()}
        self.significant_threshold = significant_threshold
        self.version = 1
        self.updated_at = _now()

    def equipment_types(self) -> list[str]:
        return sorted(self.benchmarks)

    def benchmark_of(self, equipment_type: str) -> float | None:
        return self.benchmarks.get(equipment_type)

    def as_dict(self) -> dict[str, Any]:
        return {
            "benchmarks": dict(sorted(self.benchmarks.items())),
            "unit": UNIT_LABEL,
            "significant_threshold": self.significant_threshold,
            "version": self.version,
            "updated_at": self.updated_at,
        }

    def update_benchmarks(self, benchmarks: dict[str, Any]) -> str:
        """合并更新各设备类型的对标基准；基准被调低而导致历史记录超标时，由重算体现。"""
        cleaned: dict[str, float] = {}
        errors: list[str] = []
        for raw_type, raw_value in benchmarks.items():
            name = str(raw_type or "").strip()
            if not name:
                errors.append("设备类型不能为空")
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                errors.append(f"设备类型「{name}」的对标基准必须是数字")
                continue
            if value <= 0:
                errors.append(f"设备类型「{name}」的对标基准必须大于 0")
                continue
            cleaned[name] = round(value, 6)
        if errors:
            raise CaliberError("；".join(errors))

        changed = {
            name: value
            for name, value in cleaned.items()
            if self.benchmarks.get(name) != value
        }
        if not changed:
            return "对标口径与现行规则一致，历史数据无需重算"
        self.benchmarks.update(cleaned)
        self.version += 1
        self.updated_at = _now()
        detail = "、".join(
            f"{name} 基准调至 {value:.4f}" for name, value in sorted(changed.items())
        )
        return f"对标口径已更新（{detail}，口径版本 v{self.version}），历史数据已按新规则重算"


# --- 单耗分组计算 ---------------------------------------------------------

def _normalized(row: dict[str, Any]) -> tuple[str, str, float, float] | None:
    """取出参与分组计算的四元组；月份或数值不合法时返回 None（由装饰层提示）。"""
    try:
        equipment_type = str(row.get("设备类型") or "").strip()
        month = parse_month(row.get("记录月份"))
        energy = parse_number("耗能量", row.get("耗能量"))
        output = parse_number("产量", row.get("产量"))
    except CaliberError:
        return None
    return equipment_type, month, energy, output


def unit_index_groups(rows: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, float]]:
    """按设备类型 + 记录月份汇总耗能量与产量，单耗指标全组共用。"""
    groups: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        normalized = _normalized(row)
        if normalized is None:
            continue
        equipment_type, month, energy, output = normalized
        aggregate = groups.setdefault((equipment_type, month), {"耗能量": 0.0, "产量": 0.0})
        aggregate["耗能量"] += energy
        aggregate["产量"] += output
    return groups


def grade_status(deviation: float | None, book: CaliberBook) -> str:
    if deviation is None or deviation <= 0:
        return STATUS_OK
    if deviation <= book.significant_threshold:
        return STATUS_MINOR
    return STATUS_MAJOR


def evaluate(
    row: dict[str, Any],
    groups: dict[tuple[str, str], dict[str, float]],
    book: CaliberBook,
) -> dict[str, Any]:
    """按统一口径计算一条记录的单耗指标、对标基准、偏差比率与超标量。"""
    equipment_type = str(row.get("设备类型") or "").strip()
    month = parse_month(row.get("记录月份"))
    energy = parse_number("耗能量", row.get("耗能量"))
    output = parse_number("产量", row.get("产量"))

    aggregate = groups.get((equipment_type, month), {"耗能量": energy, "产量": output})
    if aggregate["产量"] > 0:
        unit_index: float | None = round(aggregate["耗能量"] / aggregate["产量"], 6)
    else:
        unit_index = None

    benchmark = book.benchmark_of(equipment_type)
    if benchmark is None:
        raise CaliberError(f"设备类型「{equipment_type}」尚未配置对标基准，请先维护对标口径")

    if unit_index is None:
        deviation: float | None = None
        excess: float | None = None
    else:
        deviation = round((unit_index - benchmark) / benchmark, 6)
        excess = round(unit_index - benchmark, 6)

    over = bool(deviation is not None and deviation > 0)
    if over:
        over_message = (
            f"单耗指标 {unit_index:.4f} {UNIT_LABEL}，超过对标基准 {benchmark:.4f}，"
            f"超出 {excess:.4f}（{format_percent(deviation)}）"
        )
    else:
        over_message = ""

    return {
        "设备类型": equipment_type,
        "记录月份": month,
        "耗能量": energy,
        "产量": output,
        "单耗指标": unit_index,
        "对标基准": benchmark,
        "偏差比率": deviation,
        "偏差比率显示": format_percent(deviation),
        "超标量": excess,
        "超标": over,
        "超标提示": over_message,
    }


def decorate_row(
    row: dict[str, Any],
    groups: dict[tuple[str, str], dict[str, float]],
    book: CaliberBook,
) -> dict[str, Any]:
    """在原始记录上叠加统一口径的派生字段，列表/详情/导出共用这一份结果。"""
    view = dict(row)
    view.setdefault("判定快照", None)

    normalized = _normalized(row)
    if normalized is None:
        bad_month = ""
        try:
            parse_month(row.get("记录月份"))
            parse_number("耗能量", row.get("耗能量"))
            parse_number("产量", row.get("产量"))
        except CaliberError as exc:
            bad_month = str(exc)
        view.update(
            {
                "单耗指标": None,
                "对标基准": book.benchmark_of(str(row.get("设备类型") or "").strip()),
                "偏差比率": None,
                "偏差比率显示": "—",
                "超标量": None,
                "超标": False,
                "超标提示": "",
                "口径错误": bad_month,
                "能效状态": row.get("status") or STATUS_OK,
                "pending": False,
                "abnormal": False,
            }
        )
        return view

    metrics = evaluate(row, groups, book)
    graded = grade_status(metrics["偏差比率"], book)
    adjusted = row.get("status") in ADJUSTED_STATUSES

    if adjusted:
        status = STATUS_ADJUSTED
        pending = False
        abnormal = False
    else:
        status = graded
        pending = metrics["超标"]
        abnormal = metrics["超标"]

    view.update(metrics)
    view["status"] = status
    view["能效状态"] = status
    view["pending"] = pending
    view["abnormal"] = abnormal
    view["口径错误"] = ""
    return view


def decorate_rows(rows: list[dict[str, Any]], book: CaliberBook) -> list[dict[str, Any]]:
    """整表装饰：分组只算一次；按记录月份、记录编号排序，未调整超标的自然排在待分析里。"""
    groups = unit_index_groups(rows)
    decorated = [decorate_row(row, groups, book) for row in rows]
    decorated.sort(key=lambda item: (item.get("记录月份") or "9999-99", str(item.get("记录编号", ""))))
    return decorated


def make_snapshot(view: dict[str, Any], conclusion: str, book: CaliberBook) -> dict[str, Any]:
    """冻结判定时刻的单耗、基准与偏差，口径再变也不覆盖。"""
    return {
        "判定时单耗指标": view.get("单耗指标"),
        "判定时对标基准": view.get("对标基准"),
        "判定时偏差比率": view.get("偏差比率"),
        "判定时偏差比率显示": view.get("偏差比率显示"),
        "判定结论": conclusion,
        "判定时间": _now(),
        "口径版本": book.version,
    }


def recalculate_rows(rows: list[dict[str, Any]], book: CaliberBook) -> dict[str, int]:
    """口径变更后整表重算：派生指标读取时实时计算，这里同步刷新状态/待分析/异常标记。

    已调整记录维持「已调整」，不回到待分析，判定快照原样保留；其余记录按新口径
    重新分级，超过新基准的重新进入待分析清单。
    """
    groups = unit_index_groups(rows)
    summary = {"recalculated": 0, "达标": 0, "轻微偏差": 0, "显著偏差": 0, "已调整": 0, "待分析": 0}
    for row in rows:
        view = decorate_row(row, groups, book)
        row["status"] = view["status"]
        row["pending"] = view["pending"]
        row["abnormal"] = view["abnormal"]
        row.setdefault("判定快照", None)
        summary["recalculated"] += 1
        summary[view["status"]] = summary.get(view["status"], 0) + 1
        if view["pending"]:
            summary["待分析"] += 1
    return summary
