from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from typing import List, Optional
from datetime import date
from app.database import get_db
from app.crud.bom_version import crud_bom_version, crud_bom_version_item
from app.crud.vehicle import crud_vehicle
from app.schemas import (
    BOMVersion, BOMVersionCreate, BOMVersionUpdate,
    BOMVersionSubmit, BOMVersionApprove, BOMVersionReject,
    BOMRollbackRequest, BOMVersionDiff, BOMVersionItem
)
from app.services.bom_version import BOMVersionService

router = APIRouter(prefix="/bom-versions", tags=["BOM版本管理"])


@router.post("/", response_model=BOMVersion)
def create_bom_version(version_in: BOMVersionCreate, db: Session = Depends(get_db)):
    try:
        return BOMVersionService.create_version(db, version_in)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/", response_model=List[BOMVersion])
def list_bom_versions(
    vehicle_model_id: int,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    return crud_bom_version.list_by_vehicle(db, vehicle_model_id, status)


@router.get("/{version_id}", response_model=BOMVersion)
def get_bom_version(version_id: int, db: Session = Depends(get_db)):
    version = crud_bom_version.get(db, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="BOM版本不存在")
    return version


@router.get("/{version_id}/items", response_model=List[BOMVersionItem])
def get_bom_version_items(version_id: int, db: Session = Depends(get_db)):
    if not crud_bom_version.get(db, version_id):
        raise HTTPException(status_code=404, detail="BOM版本不存在")
    return crud_bom_version_item.list_by_version(db, version_id)


@router.put("/{version_id}", response_model=BOMVersion)
def update_bom_version(
    version_id: int, version_in: BOMVersionUpdate, db: Session = Depends(get_db)
):
    try:
        return BOMVersionService.update_draft(db, version_id, version_in)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{version_id}/submit", response_model=BOMVersion)
def submit_bom_version(
    version_id: int, payload: BOMVersionSubmit, db: Session = Depends(get_db)
):
    try:
        return BOMVersionService.submit_version(db, version_id, payload.submitted_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{version_id}/approve", response_model=BOMVersion)
def approve_bom_version(
    version_id: int, payload: BOMVersionApprove, db: Session = Depends(get_db)
):
    try:
        return BOMVersionService.approve_version(
            db, version_id,
            approved_by=payload.approved_by,
            purchase_impact=payload.purchase_impact,
            inventory_impact=payload.inventory_impact,
            delay_impact_note=payload.delay_impact_note
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{version_id}/reject", response_model=BOMVersion)
def reject_bom_version(
    version_id: int, payload: BOMVersionReject, db: Session = Depends(get_db)
):
    try:
        return BOMVersionService.reject_version(
            db, version_id, payload.reject_reason, payload.operator
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/rollback", response_model=BOMVersion)
def rollback_bom_version(payload: BOMRollbackRequest, db: Session = Depends(get_db)):
    """回退到历史版本：复制其结构生成 rollback 新版本（进入待审批），必须说明三类影响。"""
    source = crud_bom_version.get(db, payload.source_version_id)
    if not source:
        raise HTTPException(status_code=404, detail="来源版本不存在")
    try:
        return BOMVersionService.rollback_to_version(
            db,
            vehicle_model_id=source.vehicle_model_id,
            source_version_id=payload.source_version_id,
            effective_date=payload.effective_date,
            reason=payload.reason,
            purchase_impact=payload.purchase_impact,
            inventory_impact=payload.inventory_impact,
            delay_impact_note=payload.delay_impact_note,
            operator=payload.operator
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{version_id}/diff", response_model=BOMVersionDiff)
def diff_bom_version(
    version_id: int,
    from_version_id: Optional[int] = Query(None),
    db: Session = Depends(get_db)
):
    version = crud_bom_version.get(db, version_id)
    if not version:
        raise HTTPException(status_code=404, detail="BOM版本不存在")
    try:
        return BOMVersionService.get_diff(
            db, version.vehicle_model_id, from_version_id, version_id
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/effective/latest", response_model=BOMVersion)
def get_effective_bom(
    vehicle_model_id: int,
    on_date: Optional[date] = None,
    db: Session = Depends(get_db)
):
    """查询车型在指定日期（默认今天）生效的 BOM 版本。"""
    version = crud_bom_version.get_effective_version(
        db, vehicle_model_id, on_date or date.today()
    )
    if not version:
        raise HTTPException(status_code=404, detail="该日期无已生效的BOM版本")
    return version
