from sqlalchemy.orm import Session
from datetime import date
from typing import List

from app.models import (
    VehicleModel, BOMItem, BOMVersion, BOMVersionItem, BOMVersionAlternative,
    ProductionBatch, ProductionBatchBOMItem,
)
from app.crud.bom import (
    crud_bom_version, crud_bom_version_item, crud_batch_bom_item,
)
from app.crud.vehicle import crud_vehicle
from app.crud.alternative import crud_alternative_material, crud_alternative_restriction

# 基线版本统一生效日：早于任何业务日期，保证存量计划批次都能命中
BASELINE_EFFECTIVE_DATE = date(2000, 1, 1)


def ensure_baseline_versions(db: Session) -> List[BOMVersion]:
    """
    为仍直接维护在 bom_items 表中的存量车型结构建立已审批基线版本。
    幂等：已有任意版本的车型跳过。
    """
    created: List[BOMVersion] = []
    vehicles = db.query(VehicleModel).all()
    for vehicle in vehicles:
        existing = crud_bom_version.list_by_vehicle(db, vehicle.id)
        if existing:
            continue
        legacy_items = db.query(BOMItem).filter(
            BOMItem.vehicle_model_id == vehicle.id
        ).all()
        if not legacy_items:
            continue
        version = BOMVersion(
            vehicle_model_id=vehicle.id,
            version_no=crud_bom_version.next_version_no(db, vehicle.id),
            status="approved",
            change_type="normal",
            effective_date=BASELINE_EFFECTIVE_DATE,
            expire_date=None,
            change_reason="基线版本：由存量BOM结构导入",
        )
        db.add(version)
        db.flush()
        for legacy in legacy_items:
            db.add(BOMVersionItem(
                bom_version_id=version.id,
                material_id=legacy.material_id,
                quantity=legacy.quantity,
                remark=legacy.remark,
            ))
        db.flush()
        # 冻结基线时点的替代关系
        for legacy in legacy_items:
            for alt in crud_alternative_material.get_alternatives_for_material(
                db, legacy.material_id
            ):
                is_allowed = crud_alternative_restriction.is_alternative_allowed(
                    db, alt.id, vehicle.id
                )
                db.add(BOMVersionAlternative(
                    bom_version_id=version.id,
                    material_id=legacy.material_id,
                    alternative_material_id=alt.alternative_material_id,
                    priority=alt.priority,
                    is_allowed=is_allowed,
                    remark=alt.remark,
                ))
        # 为已经处于下达后状态但缺少快照的存量批次补冻结快照
        frozen_batches = db.query(ProductionBatch).filter(
            ProductionBatch.vehicle_model_id == vehicle.id,
            ProductionBatch.status.in_(["released", "started", "issued", "completed", "closed"])
        ).all()
        for batch in frozen_batches:
            if crud_batch_bom_item.get_by_batch(db, batch.id):
                continue
            for legacy in legacy_items:
                db.add(ProductionBatchBOMItem(
                    production_batch_id=batch.id,
                    bom_version_id=version.id,
                    material_id=legacy.material_id,
                    quantity=legacy.quantity,
                    remark=legacy.remark,
                ))
            batch.bom_version_id = version.id
            db.add(batch)
        db.flush()
        created.append(version)
    if created:
        db.commit()
        for version in created:
            db.refresh(version)
    return created


def ensure_item_in_open_version(
    db: Session,
    vehicle_model_id: int,
    material_id: int,
    quantity: int,
    remark: str = None,
) -> BOMVersion:
    """
    存量直接维护入口（种子/测试工厂）使用：确保该行存在于当前开放的已审批版本中。
    若车型尚无版本，则建立生效日为 2000-01-01 的基线版本。
    """
    version = crud_bom_version.get_open_approved(db, vehicle_model_id)
    if version is None:
        existing_any = crud_bom_version.list_by_vehicle(db, vehicle_model_id)
        if existing_any:
            raise ValueError("车型不存在开放区间的已审批版本，不能直接维护BOM行")
        version = BOMVersion(
            vehicle_model_id=vehicle_model_id,
            version_no=crud_bom_version.next_version_no(db, vehicle_model_id),
            status="approved",
            change_type="normal",
            effective_date=BASELINE_EFFECTIVE_DATE,
            expire_date=None,
            change_reason="基线版本：由存量BOM结构导入",
        )
        db.add(version)
        db.flush()
    item = db.query(BOMVersionItem).filter(
        BOMVersionItem.bom_version_id == version.id,
        BOMVersionItem.material_id == material_id
    ).first()
    if item:
        item.quantity = quantity
        if remark is not None:
            item.remark = remark
    else:
        db.add(BOMVersionItem(
            bom_version_id=version.id,
            material_id=material_id,
            quantity=quantity,
            remark=remark,
        ))
    db.commit()
    db.refresh(version)
    return version


def ensure_schema_columns(engine) -> None:
    """SQLite 存量库补列（create_all 不会 ALTER 已有表）。"""
    from sqlalchemy import text, inspect
    with engine.begin() as conn:
        if "production_batches" not in inspect(conn).get_table_names():
            return  # 全新库，create_all 会直接建出含新列的表
        cols = [row[1] for row in conn.execute(text("PRAGMA table_info(production_batches)"))]
        additions = {
            "bom_version_id": "INTEGER",
            "released_at": "DATETIME",
            "freeze_reason": "VARCHAR(300)",
        }
        for name, col_type in additions.items():
            if name not in cols:
                conn.execute(text(
                    f"ALTER TABLE production_batches ADD COLUMN {name} {col_type}"
                ))
