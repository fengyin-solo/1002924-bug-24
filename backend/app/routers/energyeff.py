"""能效监测接口：维护能效记录，覆盖记录偏差、分析原因、调整优化与对标口径管理。"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.schemas import ActionResult, EntryPayload, PageResult
from app.services.energyeff import EnergyeffService

router = APIRouter(prefix="/api/energyeff", tags=["能效监测"])

service = EnergyeffService()
service.refresh_seed()

STATUSES = ["达标", "轻微偏差", "显著偏差", "已调整"]


@router.get("", response_model=PageResult[dict])
def list_entries(
    keyword: str | None = Query(default=None, description="按记录编号检索"),
    status: str | None = Query(default=None, description="达标、轻微偏差、显著偏差、已调整"),
    pending: bool | None = Query(default=None, description="true 只看待分析；已调整记录不会出现"),
    over: bool | None = Query(default=None, description="true 只看超过对标基准的记录"),
    equipment_type: str | None = Query(default=None, description="按设备类型过滤"),
    month: str | None = Query(default=None, description="按记录月份过滤，格式 YYYY-MM"),
    page: int = 1,
    size: int = 20,
) -> PageResult[dict]:
    """按条件过滤能效记录；单耗指标、偏差比率等均由统一口径实时计算并按月份排序。"""
    if size > 200:
        raise HTTPException(status_code=400, detail="每页最多 200 条，请缩小分页范围")
    items, total = service.list_entries(
        keyword=keyword,
        status=status,
        pending=pending,
        over=over,
        equipment_type=equipment_type,
        month=month,
        page=page,
        size=size,
    )
    return PageResult(items=items, total=total, page=page, size=size)


# 注意：/export 必须排在 /{entry_id} 之前，否则 export 会被当成记录编号解析。
@router.get("/export")
def export_entries() -> dict[str, Any]:
    """导出能效监测清单：统一口径计算后的全量数据，按记录月份排序。"""
    items, total = service.list_entries(page=1, size=10000)
    return {"module": "energyeff", "total": total, "items": items, "caliber": service.caliber()}


@router.get("/stats")
def entry_stats() -> dict[str, int]:
    """顶部统计卡片：达标、轻微/显著偏差、待分析、超标与已调整数量。"""
    return service.stats()


@router.get("/caliber")
def get_caliber() -> dict[str, Any]:
    """读取当前对标口径：各设备类型的对标基准、显著偏差阈值与口径版本。"""
    return service.caliber()


@router.put("/caliber", response_model=ActionResult)
def update_caliber(payload: EntryPayload) -> ActionResult:
    """修改对标基准；改完历史记录立即按新口径重算，已调整记录保留当初判定。"""
    benchmarks = payload.values.get("benchmarks", payload.values)
    if not isinstance(benchmarks, dict):
        return ActionResult(ok=False, message="benchmarks 必须是「设备类型: 对标基准」的键值集合")
    result, message = service.update_caliber(benchmarks)
    if result is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=result)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条能效记录明细；与列表走同一口径，偏差比率保持一致。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"能效记录 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: EntryPayload) -> ActionResult:
    """登记一条能效记录；单耗超过对标基准的直接拦下并说明超出多少。"""
    entry, message = service.create_entry(payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message="能效记录已登记", entry=entry)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: EntryPayload) -> ActionResult:
    """对单条记录执行记录偏差、分析原因、调整优化；判定瞬间的偏差会冻结成快照。"""
    action = str(payload.values.get("action") or "").strip()
    entry, message = service.run_action(entry_id, action)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
