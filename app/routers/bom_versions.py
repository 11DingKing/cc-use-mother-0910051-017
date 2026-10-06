from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List, Optional

from app.database import get_db
from app.crud.vehicle import crud_vehicle, crud_production_batch
from app.services.bom_versioning import BOMVersionService, BatchBOMService
from app.schemas import (
    BOMVersion, BOMVersionDetail, BOMVersionCreate, BOMVersionApprove,
    BOMVersionReject, BOMVersionDiff,
    ProductionBatch,
    BatchReleaseRequest,
    BOMMigrationAssessment, BOMMigrationApprove,
    BOMDeviationCreate, BOMDeviationApprove, BOMDeviationOut,
    BatchIssueMaterialRequest, ProductionMaterialIssueOut,
    BatchBOMTrace,
)

router = APIRouter(prefix="/bom", tags=["BOM版本管理"])


def _get_vehicle_or_404(db: Session, vehicle_id: int):
    vehicle = crud_vehicle.get(db, vehicle_id)
    if vehicle is None:
        raise HTTPException(status_code=404, detail="车型不存在")
    return vehicle


def _get_batch_or_404(db: Session, batch_id: int):
    batch = crud_production_batch.get(db, batch_id)
    if batch is None:
        raise HTTPException(status_code=404, detail="生产批次不存在")
    return batch


# ---------------- 版本 ----------------

@router.get("/vehicles/{vehicle_id}/versions", response_model=List[BOMVersion])
def list_versions(
    vehicle_id: int,
    status: Optional[str] = None,
    db: Session = Depends(get_db)
):
    _get_vehicle_or_404(db, vehicle_id)
    return BOMVersionService.list_versions(db, vehicle_id, status)


@router.post("/vehicles/{vehicle_id}/versions", response_model=BOMVersion, status_code=201)
def create_version(
    vehicle_id: int,
    payload: BOMVersionCreate,
    db: Session = Depends(get_db)
):
    _get_vehicle_or_404(db, vehicle_id)
    payload.vehicle_model_id = vehicle_id
    try:
        return BOMVersionService.create_version(db, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/versions/{version_id}", response_model=BOMVersionDetail)
def get_version(version_id: int, db: Session = Depends(get_db)):
    version = BOMVersionService.get_version(db, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="BOM版本不存在")
    return version


@router.get("/vehicles/{vehicle_id}/versions/{version_id}/diff", response_model=BOMVersionDiff)
def get_version_diff(
    vehicle_id: int,
    version_id: int,
    from_version_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    _get_vehicle_or_404(db, vehicle_id)
    try:
        return BOMVersionService.get_diff(db, vehicle_id, version_id, from_version_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/versions/{version_id}/submit", response_model=BOMVersion)
def submit_version(
    version_id: int,
    submitted_by: str,
    db: Session = Depends(get_db)
):
    try:
        return BOMVersionService.submit_version(db, version_id, submitted_by)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/versions/{version_id}/approve", response_model=BOMVersion)
def approve_version(
    version_id: int,
    payload: BOMVersionApprove,
    db: Session = Depends(get_db)
):
    try:
        return BOMVersionService.approve_version(db, version_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/versions/{version_id}/reject", response_model=BOMVersion)
def reject_version(
    version_id: int,
    payload: BOMVersionReject,
    db: Session = Depends(get_db)
):
    try:
        return BOMVersionService.reject_version(db, version_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------- 批次下达冻结 ----------------

@router.post("/batches/{batch_id}/release", response_model=ProductionBatch)
def release_batch(
    batch_id: int,
    payload: BatchReleaseRequest,
    db: Session = Depends(get_db)
):
    _get_batch_or_404(db, batch_id)
    try:
        return BatchBOMService.release_batch(db, batch_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/batches/{batch_id}/status/{target_status}", response_model=ProductionBatch)
def change_batch_status(
    batch_id: int,
    target_status: str,
    db: Session = Depends(get_db)
):
    _get_batch_or_404(db, batch_id)
    try:
        return BatchBOMService.change_batch_status(db, batch_id, target_status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------- 影响评估与迁移 ----------------

@router.post(
    "/batches/{batch_id}/migrations/assess/{target_version_id}",
    response_model=BOMMigrationAssessment
)
def assess_migration(
    batch_id: int,
    target_version_id: int,
    db: Session = Depends(get_db)
):
    _get_batch_or_404(db, batch_id)
    try:
        return BatchBOMService.assess_migration(db, batch_id, target_version_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/migrations/{migration_id}/approve", response_model=ProductionBatch)
def approve_migration(
    migration_id: int,
    payload: BOMMigrationApprove,
    db: Session = Depends(get_db)
):
    try:
        return BatchBOMService.approve_migration(db, migration_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/migrations/{migration_id}/reject")
def reject_migration(migration_id: int, remark: str, db: Session = Depends(get_db)):
    try:
        BatchBOMService.reject_migration(db, migration_id, remark)
        return {"message": "迁移申请已驳回"}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------- 偏差处理 ----------------

@router.post("/batches/{batch_id}/deviations", response_model=BOMDeviationOut, status_code=201)
def create_deviation(
    batch_id: int,
    payload: BOMDeviationCreate,
    db: Session = Depends(get_db)
):
    _get_batch_or_404(db, batch_id)
    try:
        return BatchBOMService.create_deviation(db, batch_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/batches/{batch_id}/deviations", response_model=List[BOMDeviationOut])
def list_deviations(batch_id: int, db: Session = Depends(get_db)):
    _get_batch_or_404(db, batch_id)
    from app.crud.bom import crud_bom_deviation
    return crud_bom_deviation.list_by_batch(db, batch_id)


@router.post("/deviations/{deviation_id}/submit", response_model=BOMDeviationOut)
def submit_deviation(deviation_id: int, db: Session = Depends(get_db)):
    try:
        return BatchBOMService.submit_deviation(db, deviation_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/deviations/{deviation_id}/approve", response_model=BOMDeviationOut)
def approve_deviation(
    deviation_id: int,
    payload: BOMDeviationApprove,
    db: Session = Depends(get_db)
):
    try:
        return BatchBOMService.approve_deviation(db, deviation_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


# ---------------- 领料 / 实际消耗 ----------------

@router.post("/batches/{batch_id}/issues", response_model=ProductionMaterialIssueOut, status_code=201)
def issue_material(
    batch_id: int,
    payload: BatchIssueMaterialRequest,
    db: Session = Depends(get_db)
):
    _get_batch_or_404(db, batch_id)
    try:
        return BatchBOMService.issue_material(db, batch_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/batches/{batch_id}/issues", response_model=List[ProductionMaterialIssueOut])
def list_issues(batch_id: int, db: Session = Depends(get_db)):
    _get_batch_or_404(db, batch_id)
    from app.crud.bom import crud_material_issue
    return crud_material_issue.list_by_batch(db, batch_id)


# ---------------- 追溯 ----------------

@router.get("/batches/{batch_id}/trace", response_model=BatchBOMTrace)
def trace_batch(batch_id: int, db: Session = Depends(get_db)):
    try:
        return BatchBOMService.trace_batch(db, batch_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
