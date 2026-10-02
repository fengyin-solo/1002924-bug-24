"""能效监测接口：统一口径计算单耗指标与偏差比率，超标拦截，并维护对标基准。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.energyeff import DISPLAY_FIELDS, EnergyeffService

router = APIRouter(prefix="/api/energyeff", tags=["能效监测"])

service = EnergyeffService()

LIST_FIELDS = DISPLAY_FIELDS
STATUSES = ["达标", "轻微偏差", "显著偏差", "已调整"]


# 固定路径必须排在 /{entry_id} 前面，否则会被当成记录编号拦截


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="达标、轻微偏差、显著偏差、已调整"),
    pending: bool | None = Query(default=None, description="true 只看待分析清单（已调整的不会出现）"),
    over_limit: bool | None = Query(default=None, description="true 只看超过对标基准的记录"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按记录编号、状态、待分析、超标标记过滤；记录月份统一升序排列。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword, status=status, pending=pending, over_limit=over_limit, page=page, size=size
    )
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/benchmarks/all")
def list_benchmarks() -> dict[str, Any]:
    """查看当前全部对标口径（按设备类型、记录月份排列）。"""
    items = service.list_benchmarks()
    return {"module": "energyeff_benchmark", "total": len(items), "items": items}


@router.post("/benchmarks", response_model=ActionResult)
def upsert_benchmark(payload: EntryPayload) -> ActionResult:
    """新增或调整某设备类型某月份的对标口径；保存后历史数据按新规则自动重算一遍。"""
    benchmark, message = service.upsert_benchmark(payload.values)
    if benchmark is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=benchmark)


@router.post("/recalculate", response_model=ActionResult)
def recalculate() -> ActionResult:
    """按现行口径手动重算全部历史记录的超标与待分析标记（历史偏差快照不受影响）。"""
    count = service.recalculate()
    return ActionResult(ok=True, message=f"已按最新口径重算 {count} 条历史记录")


@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出能效监测清单：返回全量数据，口径与列表一致。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "energyeff", "total": total, "items": items}


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条能效记录明细；单耗指标与偏差比率和列表同源，保证两处一致。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"能效记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记能效记录：按设备类型与记录月份算单耗指标，超对标基准直接拦下并说明超了多少。

    values 里传 force=true 可强制登记超标记录，该记录自动进入待分析清单。
    """
    values = dict(payload.values)
    force = bool(values.pop("force", False))
    entry, message = service.create_entry(values, force=force)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="能效记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """记录偏差、分析原因、调整优化；已调整的不再进待分析清单，调整时保留历史偏差。"""
    values = dict(payload.values)
    action = str(values.pop("action", "")).strip()
    entry, message = service.run_action(entry_id, action, values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
