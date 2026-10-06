from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from typing import List
from app.database import get_db
from app.crud.vehicle import crud_production_batch
from app.crud.bom_version import (
    crud_batch_bom_item, crud_batch_migration, crud_bom_deviation
)
from app.schemas import (
    ProductionBatch, BatchReleaseRequest,
    BatchMigrationEvaluateRequest, BatchMigrationDecision,
    ProductionBatchMigration,
    BOMDeviationCreate, BOMDeviationApprove, ProductionBOMDeviation,
    FrozenBOMItem, ConsumptionCreate, MaterialConsumptionRecord,
    BatchBOMTraceability
)
from app.services.production_batch import ProductionBatchService

router = APIRouter(prefix="/production-batches", tags=["生产批次BOM管理"])


@router.get("/", response_model=List[ProductionBatch])
def list_batches(db: Session = Depends(get_db)):
    return crud_production_batch.get_multi(db)


@router.post("/{batch_id}/release", response_model=ProductionBatch)
def release_batch(
    batch_id: int, payload: BatchReleaseRequest, db: Session = Depends(get_db)
):
    """下达批次：冻结当时生效的 BOM 结构与单位用量。"""
    try:
        return ProductionBatchService.release_batch(
            db, batch_id, bom_version_id=payload.bom_version_id, operator=payload.operator
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{batch_id}/frozen-bom", response_model=List[FrozenBOMItem])
def get_frozen_bom(batch_id: int, db: Session = Depends(get_db)):
    batch = crud_production_batch.get(db, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="生产批次不存在")
    if batch.bom_version_id is None:
        raise HTTPException(status_code=400, detail="批次尚未下达，无冻结BOM")
    return crud_batch_bom_item.list_by_batch(db, batch_id)


@router.post("/{batch_id}/migrations/evaluate", response_model=ProductionBatchMigration)
def evaluate_migration(
    batch_id: int, payload: BatchMigrationEvaluateRequest, db: Session = Depends(get_db)
):
    """未开工批次迁移到新版本前的影响评估（采购建议/库存分配/延期结论）。"""
    try:
        return ProductionBatchService.evaluate_migration(db, batch_id, payload.to_version_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{batch_id}/migrations", response_model=List[ProductionBatchMigration])
def list_migrations(batch_id: int, db: Session = Depends(get_db)):
    if not crud_production_batch.get(db, batch_id):
        raise HTTPException(status_code=404, detail="生产批次不存在")
    return crud_batch_migration.list_by_batch(db, batch_id)


@router.post("/migrations/{migration_id}/decision", response_model=ProductionBatchMigration)
def decide_migration(
    migration_id: int, payload: BatchMigrationDecision, db: Session = Depends(get_db)
):
    try:
        return ProductionBatchService.decide_migration(
            db, migration_id, payload.action, payload.decided_by
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{batch_id}/deviations", response_model=ProductionBOMDeviation)
def create_deviation(
    batch_id: int, payload: BOMDeviationCreate, db: Session = Depends(get_db)
):
    """已领料批次只能登记可追溯偏差，不允许改版本。"""
    try:
        return ProductionBatchService.create_deviation(db, batch_id, payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{batch_id}/deviations", response_model=List[ProductionBOMDeviation])
def list_deviations(batch_id: int, db: Session = Depends(get_db)):
    if not crud_production_batch.get(db, batch_id):
        raise HTTPException(status_code=404, detail="生产批次不存在")
    return crud_bom_deviation.list_by_batch(db, batch_id)


@router.post("/deviations/{deviation_id}/approve", response_model=ProductionBOMDeviation)
def approve_deviation(
    deviation_id: int, payload: BOMDeviationApprove, db: Session = Depends(get_db)
):
    try:
        return ProductionBatchService.approve_deviation(
            db, deviation_id, payload.approved_by,
            payload.purchase_impact, payload.inventory_impact, payload.delay_impact
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/deviations/{deviation_id}/apply", response_model=ProductionBOMDeviation)
def apply_deviation(deviation_id: int, db: Session = Depends(get_db)):
    """执行已审批偏差：扣减库存、写入实际消耗。"""
    try:
        return ProductionBatchService.apply_deviation(db, deviation_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{batch_id}/consumptions", response_model=MaterialConsumptionRecord)
def record_consumption(
    batch_id: int, payload: ConsumptionCreate, db: Session = Depends(get_db)
):
    try:
        record = ProductionBatchService.record_consumption(
            db, batch_id, payload.material_id, payload.consumed_quantity, payload.remark
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    material = record.material
    return MaterialConsumptionRecord(
        material_id=record.material_id,
        material_code=material.code if material else "",
        material_name=material.name if material else "",
        required_quantity=record.required_quantity,
        consumed_quantity=record.consumed_quantity,
        variance_quantity=record.consumed_quantity - record.required_quantity,
        source=record.source,
        remark=record.remark
    )


@router.get("/{batch_id}/traceability", response_model=BatchBOMTraceability)
def get_traceability(batch_id: int, db: Session = Depends(get_db)):
    """从任一生产批次还原：原始冻结BOM、后续版本变更、实际消耗差异及偏差/迁移记录。"""
    try:
        data = ProductionBatchService.get_traceability(db, batch_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return BatchBOMTraceability(
        production_batch=data["production_batch"],
        frozen_bom=data["frozen_bom"],
        subsequent_changes=data["subsequent_changes"],
        consumption=[MaterialConsumptionRecord(**row) for row in data["consumption"]],
        deviations=data["deviations"],
        migrations=data["migrations"]
    )
