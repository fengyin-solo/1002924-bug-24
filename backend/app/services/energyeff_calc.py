"""能效对标的统一口径：单耗指标、偏差比率、超标判定全部在这里算。

列表与详情只能调用本模块的函数取结果，不允许各自再实现一套，
从根上消除"同一台设备两处偏差比率对不上"的问题。

口径定义（按设备类型 + 记录月份对标）：
- 单耗指标 = 耗能量 / 该设备类型在记录月份的对标产量
- 偏差比率 = (单耗指标 - 对标基准) / 对标基准 × 100%
- 偏差比率 > 轻微阈值：显著偏差；在轻微阈值与 0 之间：轻微偏差；否则达标
- 超过对标基准（偏差比率为正）即视为超标，超标量 = 单耗指标 - 对标基准
"""
from __future__ import annotations

from typing import Any

# 轻微偏差阈值：偏差比率不超过该值时算轻微偏差，超过算显著偏差
SLIGHT_DEVIATION_LIMIT = 5.0

UNIT_ROUND = 4      # 单耗指标保留 4 位小数
DEVIATION_ROUND = 2  # 偏差比率保留 2 位小数（百分数）

# 对标基准表所在的内存表名，store.overview 统计模块数量时需要跳过
BENCHMARK_TABLE = "energyeff_benchmark"


def to_float(value: Any) -> float | None:
    """把耗能量、对标产量等输入解析成数字；解析不了（空串、样例文字）返回 None。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def normalize_month(value: Any) -> str | None:
    """记录月份统一收成「YYYY-MM」；「YYYY-MM-DD」取前 7 位，其余格式视为非法。"""
    text = str(value or "").strip()
    if len(text) >= 7 and text[4] == "-" and text[:7][:4].isdigit():
        month = text[:7]
        month_num = to_float(month[5:7])
        if month_num is not None and 1 <= int(month_num) <= 12:
            return month
    return None


def find_benchmark(
    benchmarks: list[dict[str, Any]],
    device_type: str,
    month: str,
) -> dict[str, Any] | None:
    """按设备类型 + 记录月份取唯一一条对标口径。"""
    for row in benchmarks:
        if str(row.get("设备类型", "")).strip() == device_type and row.get("记录月份") == month:
            return row
    return None


def evaluate(entry: dict[str, Any], benchmarks: list[dict[str, Any]]) -> dict[str, Any]:
    """按统一口径算出一条记录的对标结果。

    返回字段：单耗指标、对标基准、偏差比率、偏差状态、超标、超标量、可对标、原因。
    缺月份、缺口径或数字非法时「可对标」为 False，并在「原因」里说明，调用方据此拦截。
    """
    result: dict[str, Any] = {
        "单耗指标": None,
        "对标基准": None,
        "偏差比率": None,
        "偏差状态": None,
        "超标": False,
        "超标量": None,
        "可对标": False,
        "原因": "",
    }

    device_type = str(entry.get("设备类型", "")).strip()
    month = normalize_month(entry.get("记录月份"))
    if not device_type:
        result["原因"] = "缺少设备类型，无法匹配对标口径"
        return result
    if month is None:
        result["原因"] = "记录月份格式应为 YYYY-MM，暂无法对标"
        return result

    benchmark = find_benchmark(benchmarks, device_type, month)
    if benchmark is None:
        result["原因"] = f"{month} 的设备类型「{device_type}」尚未维护对标口径"
        return result

    basis = to_float(benchmark.get("对标基准"))
    monthly_output = to_float(benchmark.get("对标产量"))
    energy = to_float(entry.get("耗能量"))
    if basis is None or monthly_output is None:
        result["原因"] = f"设备类型「{device_type}」{month} 的对标口径不完整（基准或产量缺失）"
        return result
    if energy is None:
        result["原因"] = "耗能量不是有效数字，无法计算单耗指标"
        return result
    if monthly_output <= 0:
        result["原因"] = f"设备类型「{device_type}」{month} 的对标产量必须为正数"
        return result

    unit = round(energy / monthly_output, UNIT_ROUND)
    result["对标基准"] = basis
    result["单耗指标"] = unit

    if basis <= 0:
        result["原因"] = f"设备类型「{device_type}」{month} 的对标基准必须为正数"
        return result

    deviation = round((unit - basis) / basis * 100, DEVIATION_ROUND)
    result["偏差比率"] = deviation
    result["超标"] = deviation > 0
    result["超标量"] = round(max(unit - basis, 0.0), UNIT_ROUND)
    if deviation > SLIGHT_DEVIATION_LIMIT:
        result["偏差状态"] = "显著偏差"
    elif deviation > 0:
        result["偏差状态"] = "轻微偏差"
    else:
        result["偏差状态"] = "达标"
    result["可对标"] = True
    return result


def over_limit_message(result: dict[str, Any]) -> str:
    """超标拦截时给用户的说明：超了多少基准、偏差多少。"""
    return (
        f"单耗指标 {result['单耗指标']} 超过对标基准 {result['对标基准']}，"
        f"超标量 {result['超标量']}（偏差比率 {result['偏差比率']}%），请调整后再登记；"
        "如确需登记异常记录可强制提交，将自动进入待分析清单"
    )


ADJUSTED_STATUS = "已调整"


def apply_spec(rows: list[dict[str, Any]], benchmarks: list[dict[str, Any]]) -> int:
    """口径变更（或启动初始化）后按现行口径重算全部记录。

    只刷新未调整记录的状态、超标与待分析标记；已调整记录不回退状态、不重进待分析，
    其偏差快照在调整时固化，这里一律不覆盖。
    """
    count = 0
    for entry in rows:
        result = evaluate(entry, benchmarks)
        if entry.get("status") != ADJUSTED_STATUS:
            if result["偏差状态"] is not None:
                entry["status"] = result["偏差状态"]
            entry["pending"] = result["超标"]
            entry["abnormal"] = result["超标"]
        count += 1
    return count
