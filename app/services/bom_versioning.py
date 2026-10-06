import json
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Tuple
from datetime import date, datetime

from app.models import (
    BOMVersion, BOMVersionItem, BOMVersionAlternative, BOMItem,
    ProductionBatch, ProductionBatchBOMItem, ProductionBatchBOMMigration,
    BOMDeviation, ProductionMaterialIssue,
    BOM_FROZEN_BATCH_STATUSES, BOM_LOCKED_BATCH_STATUSES,
)
from app.crud.bom import (
    crud_bom_version, crud_bom_version_item, crud_bom_version_alternative,
    crud_batch_bom_item, crud_batch_bom_migration,
    crud_bom_deviation, crud_material_issue,
)
from app.crud.vehicle import crud_vehicle, crud_production_batch
from app.crud.material import crud_material
from app.crud.alternative import crud_alternative_material, crud_alternative_restriction
from app.crud.purchase import (
    crud_inventory_batch, crud_purchase_suggestion, crud_purchase_order
)
from app.schemas import (
    BOMVersionCreate, BOMVersionApprove, BOMVersionReject,
    BOMVersion as BOMVersionSchema, BOMVersionDetail, BOMVersionAlternativeOut,
    BOMVersionDiff, BOMVersionDiffItem,
    BatchReleaseRequest, BatchIssueMaterialRequest,
    BOMMigrationAssessment, MigrationImpactItem, BOMMigrationApprove,
    BOMDeviationCreate, BOMDeviationApprove, BOMDeviationOut,
    BatchBOMItemSnapshot, BatchBOMTrace, BatchBOMTraceLineage,
    BatchConsumptionVariance, ProductionMaterialIssueOut,
)

# 批次状态生命周期
BATCH_STATUS_FLOW = {
    "planned": {"released"},
    "released": {"started", "completed"},
    "started": {"issued", "completed"},
    "issued": {"completed"},
    "completed": {"closed"},
    "closed": set(),
}


def _material_brief(db: Session, material_id: int) -> Tuple[str, str]:
    mat = crud_material.get(db, material_id)
    return (mat.code if mat else "", mat.name if mat else "")


def _sync_legacy_bom(db: Session, vehicle_model_id: int) -> None:
    """让遗留的 bom_items 表始终等于当前生效版本，保证未迁移读路径口径一致。"""
    current = crud_bom_version.get_effective_on(db, vehicle_model_id, date.today())
    if not current:
        return
    existing = db.query(BOMItem).filter(
        BOMItem.vehicle_model_id == vehicle_model_id
    ).all()
    for row in existing:
        db.delete(row)
    for item in current.items:
        db.add(BOMItem(
            vehicle_model_id=vehicle_model_id,
            material_id=item.material_id,
            quantity=item.quantity,
            remark=item.remark,
        ))
    db.flush()


def _freeze_alternatives(db: Session, version: BOMVersion) -> None:
    """审批生效时冻结当时的替代关系（按车型限制展开）。"""
    db.query(BOMVersionAlternative).filter(
        BOMVersionAlternative.bom_version_id == version.id
    ).delete()
    for item in version.items:
        alts = crud_alternative_material.get_alternatives_for_material(
            db, item.material_id
        )
        for alt in alts:
            is_allowed = crud_alternative_restriction.is_alternative_allowed(
                db, alt.id, version.vehicle_model_id
            )
            db.add(BOMVersionAlternative(
                bom_version_id=version.id,
                material_id=item.material_id,
                alternative_material_id=alt.alternative_material_id,
                priority=alt.priority,
                is_allowed=is_allowed,
                remark=alt.remark,
            ))


class BOMVersionService:
    """BOM 版本、审批与生效区间管理。"""

    @staticmethod
    def list_versions(db: Session, vehicle_model_id: int, status: Optional[str] = None):
        return crud_bom_version.list_by_vehicle(db, vehicle_model_id, status)

    @staticmethod
    def get_version(db: Session, version_id: int) -> Optional[BOMVersionDetail]:
        version = crud_bom_version.get(db, version_id)
        if not version:
            return None
        detail = BOMVersionDetail.model_validate(version)
        detail.alternatives = [
            BOMVersionAlternativeOut.model_validate(a)
            for a in crud_bom_version_alternative.get_by_version(db, version_id)
        ]
        return detail

    @staticmethod
    def get_diff(
        db: Session,
        vehicle_model_id: int,
        to_version_id: int,
        from_version_id: Optional[int] = None
    ) -> BOMVersionDiff:
        to_version = crud_bom_version.get(db, to_version_id)
        if not to_version or to_version.vehicle_model_id != vehicle_model_id:
            raise ValueError("目标版本不存在或不属于该车型")
        if from_version_id is None:
            # 默认前驱：生效日不晚于目标版本的最近一个已审批版本（区间相邻截断后即为上一版）
            candidates = [
                v for v in crud_bom_version.list_by_vehicle(
                    db, vehicle_model_id, "approved"
                )
                if v.id != to_version_id and v.effective_date <= to_version.effective_date
            ]
            predecessor = max(
                candidates, key=lambda v: (v.effective_date, v.id), default=None
            )
            from_version_id = predecessor.id if predecessor else None
        old_map: Dict[int, int] = {}
        if from_version_id:
            old_version = crud_bom_version.get(db, from_version_id)
            if not old_version or old_version.vehicle_model_id != vehicle_model_id:
                raise ValueError("基准版本不存在或不属于该车型")
            old_map = crud_bom_version_item.get_quantity_map(db, from_version_id)
        new_map = crud_bom_version_item.get_quantity_map(db, to_version_id)
        items: List[BOMVersionDiffItem] = []
        all_material_ids = set(old_map) | set(new_map)
        for material_id in sorted(all_material_ids):
            code, name = _material_brief(db, material_id)
            old_qty = old_map.get(material_id)
            new_qty = new_map.get(material_id)
            if old_qty is None:
                change = "added"
            elif new_qty is None:
                change = "removed"
            elif old_qty != new_qty:
                change = "quantity_changed"
            else:
                continue
            items.append(BOMVersionDiffItem(
                change=change,
                material_id=material_id,
                material_code=code,
                material_name=name,
                old_quantity=old_qty,
                new_quantity=new_qty,
            ))
        return BOMVersionDiff(
            vehicle_model_id=vehicle_model_id,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            items=items,
        )

    @staticmethod
    def create_version(db: Session, payload: BOMVersionCreate) -> BOMVersion:
        vehicle = crud_vehicle.get(db, payload.vehicle_model_id)
        if not vehicle:
            raise ValueError("车型不存在")
        if payload.change_type not in ("normal", "emergency", "rollback"):
            raise ValueError("变更类型必须为 normal/emergency/rollback")
        if payload.change_type == "rollback" and payload.base_version_id is None:
            raise ValueError("回退版本必须指定回退所依据的基准版本 base_version_id")

        if payload.items:
            materials = {i.material_id: i.quantity for i in payload.items}
            for material_id, qty in materials.items():
                if not crud_material.get(db, material_id):
                    raise ValueError(f"物料不存在: {material_id}")
                if qty <= 0:
                    raise ValueError(f"物料 {material_id} 单位用量必须大于0")
        else:
            base_id = payload.base_version_id
            if base_id is None:
                open_version = crud_bom_version.get_open_approved(db, payload.vehicle_model_id)
                base_id = open_version.id if open_version else None
            if base_id is None:
                raise ValueError("车型尚无BOM版本，创建首个版本必须提供明细")
            base = crud_bom_version.get(db, base_id)
            if not base or base.vehicle_model_id != payload.vehicle_model_id:
                raise ValueError("基准版本不存在或不属于该车型")
            materials = crud_bom_version_item.get_quantity_map(db, base_id)

        version = BOMVersion(
            vehicle_model_id=payload.vehicle_model_id,
            version_no=crud_bom_version.next_version_no(db, payload.vehicle_model_id),
            status="draft",
            change_type=payload.change_type,
            effective_date=payload.effective_date,
            expire_date=None,
            change_reason=payload.change_reason,
            rolled_back_from_version_id=payload.base_version_id
            if payload.change_type == "rollback" else None,
        )
        db.add(version)
        db.flush()
        remarks = {i.material_id: i.remark for i in payload.items}
        for material_id, qty in materials.items():
            db.add(BOMVersionItem(
                bom_version_id=version.id,
                material_id=material_id,
                quantity=qty,
                remark=remarks.get(material_id),
            ))
        db.commit()
        db.refresh(version)
        return version

    @staticmethod
    def submit_version(
        db: Session, version_id: int, submitted_by: str
    ) -> BOMVersion:
        version = crud_bom_version.get(db, version_id)
        if not version:
            raise ValueError("BOM版本不存在")
        if version.status not in ("draft", "rejected"):
            raise ValueError(f"当前状态 {version.status} 不可提交审批")
        if not version.items:
            raise ValueError("版本没有任何BOM明细，不可提交")
        version.status = "submitted"
        version.submitted_by = submitted_by
        version.submitted_at = datetime.now()
        db.add(version)
        db.commit()
        db.refresh(version)
        return version

    @staticmethod
    def approve_version(
        db: Session, version_id: int, payload: BOMVersionApprove
    ) -> BOMVersion:
        version = crud_bom_version.get(db, version_id)
        if not version:
            raise ValueError("BOM版本不存在")
        if version.status != "submitted":
            raise ValueError(f"当前状态 {version.status} 不可审批通过")
        # 回退或紧急更改必须说明对采购建议、库存分配、延期结论的影响
        if version.change_type in ("emergency", "rollback") and not payload.impact_summary:
            raise ValueError("紧急更改/回退必须填写影响说明（采购建议、库存分配、延期结论）")

        overlap = crud_bom_version.find_approved_overlap(
            db, version.vehicle_model_id, version.effective_date,
            exclude_version_id=version.id
        )
        open_version = crud_bom_version.get_open_approved(db, version.vehicle_model_id)
        if overlap and overlap.id != getattr(open_version, "id", None):
            raise ValueError("生效区间与已审批版本重叠，且无法通过截断开放区间解决")
        if open_version:
            if version.effective_date < open_version.effective_date:
                raise ValueError(
                    "新生效日期早于当前开放版本的生效日期，区间将发生重叠；"
                    "请按回退流程选择不早于当前版本生效日的日期"
                )
            if version.effective_date == open_version.effective_date:
                raise ValueError("生效日期与当前开放版本完全相同，区间不可避免地重叠")
            # 区间相邻截断：旧版本 [old_eff, None) -> [old_eff, new_eff)
            open_version.expire_date = version.effective_date
            db.add(open_version)

        version.status = "approved"
        version.approved_by = payload.approved_by
        version.approved_at = datetime.now()
        if payload.impact_summary:
            version.impact_summary = payload.impact_summary
        db.add(version)
        db.flush()
        _freeze_alternatives(db, version)
        _sync_legacy_bom(db, version.vehicle_model_id)
        db.commit()
        db.refresh(version)
        return version

    @staticmethod
    def reject_version(
        db: Session, version_id: int, payload: BOMVersionReject
    ) -> BOMVersion:
        version = crud_bom_version.get(db, version_id)
        if not version:
            raise ValueError("BOM版本不存在")
        if version.status != "submitted":
            raise ValueError(f"当前状态 {version.status} 不可驳回")
        version.status = "rejected"
        version.reject_reason = payload.reject_reason
        db.add(version)
        db.commit()
        db.refresh(version)
        return version


class BatchBOMService:
    """批次下达冻结、迁移、偏差、领料与追溯。"""

    # ---------- 批次状态与下达 ----------

    @staticmethod
    def change_batch_status(db: Session, batch_id: int, target_status: str) -> ProductionBatch:
        batch = crud_production_batch.get(db, batch_id)
        allowed = BATCH_STATUS_FLOW.get(batch.status, set())
        if target_status not in allowed:
            raise ValueError(f"批次状态不允许从 {batch.status} 变更为 {target_status}")
        if target_status == "released":
            return BatchBOMService.release_batch(
                db, batch_id, BatchReleaseRequest()
            )
        batch.status = target_status
        db.add(batch)
        db.commit()
        db.refresh(batch)
        return batch

    @staticmethod
    def release_batch(
        db: Session, batch_id: int, payload: BatchReleaseRequest
    ) -> ProductionBatch:
        batch = crud_production_batch.get(db, batch_id)
        if batch is None:
            raise ValueError("生产批次不存在")
        if crud_batch_bom_item.get_by_batch(db, batch_id):
            raise ValueError("批次已冻结过BOM，不可重复冻结")
        if batch.status != "planned":
            raise ValueError(f"批次当前状态为 {batch.status}，只有 planned 批次可以下达")

        if payload.bom_version_id:
            version = crud_bom_version.get(db, payload.bom_version_id)
            if not version or version.vehicle_model_id != batch.vehicle_model_id:
                raise ValueError("指定的BOM版本不存在或不属于该车型")
            if version.status != "approved":
                raise ValueError("只有审批通过的BOM版本可以被批次冻结")
        else:
            version = crud_bom_version.get_effective_on(
                db, batch.vehicle_model_id, batch.plan_date
            )
            if not version:
                raise ValueError(
                    f"车型在计划日 {batch.plan_date} 没有已审批生效的BOM版本，无法下达"
                )

        for item in version.items:
            db.add(ProductionBatchBOMItem(
                production_batch_id=batch.id,
                bom_version_id=version.id,
                material_id=item.material_id,
                quantity=item.quantity,
                remark=item.remark,
            ))
        batch.bom_version_id = version.id
        batch.status = "released"
        batch.released_at = datetime.now()
        batch.freeze_reason = payload.freeze_reason or (
            f"下达时冻结BOM版本 {version.version_no}"
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)
        return batch

    # ---------- 影响评估与迁移 ----------

    @staticmethod
    def assess_migration(
        db: Session, batch_id: int, target_version_id: int
    ) -> BOMMigrationAssessment:
        batch = crud_production_batch.get(db, batch_id)
        if batch is None:
            raise ValueError("生产批次不存在")
        if batch.status != "released":
            if batch.status in BOM_LOCKED_BATCH_STATUSES:
                raise ValueError("批次已开工或已领料，不能迁移BOM，只能走偏差处理")
            raise ValueError(f"批次当前状态 {batch.status} 不可评估迁移")
        snapshot = crud_batch_bom_item.get_by_batch(db, batch_id)
        if not snapshot:
            raise ValueError("批次缺少冻结BOM快照")
        target = crud_bom_version.get(db, target_version_id)
        if not target or target.vehicle_model_id != batch.vehicle_model_id:
            raise ValueError("目标BOM版本不存在或不属于该车型")
        if target.status != "approved":
            raise ValueError("目标版本尚未审批通过")
        if target.id == batch.bom_version_id:
            raise ValueError("批次已经采用该版本")

        old_map = {row.material_id: row.quantity for row in snapshot}
        new_map = crud_bom_version_item.get_quantity_map(db, target.id)
        impact_items: List[MigrationImpactItem] = []
        increase_total = 0
        decrease_total = 0
        critical_gap = False
        for material_id in sorted(set(old_map) | set(new_map)):
            old_per = old_map.get(material_id, 0)
            new_per = new_map.get(material_id, 0)
            old_required = old_per * batch.quantity
            new_required = new_per * batch.quantity
            delta = new_required - old_required
            if delta == 0:
                continue
            stock = crud_inventory_batch.get_total_stock(db, material_id)
            pending = crud_purchase_suggestion.get_pending_quantity_by_material(db, material_id)
            in_transit = crud_purchase_order.get_in_transit_quantity_by_material(db, material_id)
            old_gap = max(0, old_required - stock)
            new_gap = max(0, new_required - stock)
            mat = crud_material.get(db, material_id)
            if delta > 0:
                increase_total += delta
                advice = f"净增需求{delta}（库存{stock}/待处理建议{pending}/在途{in_transit}），建议追加采购或协调提前到货"
                if new_gap - pending - in_transit > 0:
                    advice += "；扣除待处理与在途后仍有缺口"
                    if mat and mat.is_critical:
                        critical_gap = True
            else:
                decrease_total += -delta
                advice = f"净减需求{-delta}，可核减未转单采购建议或释放预留库存"
            code, name = _material_brief(db, material_id)
            impact_items.append(MigrationImpactItem(
                material_id=material_id,
                material_code=code,
                material_name=name,
                old_per_unit=old_per,
                new_per_unit=new_per,
                old_required=old_required,
                new_required=new_required,
                delta=delta,
                current_stock=stock,
                pending_suggestion_quantity=pending,
                in_transit_quantity=in_transit,
                old_gross_shortage=old_gap,
                new_gross_shortage=new_gap,
                shortage_delta=new_gap - old_gap,
                purchase_advice=advice,
            ))

        if increase_total or decrease_total:
            alloc_summary = (
                f"迁移后新增占用/需求{increase_total}，释放{decrease_total}；"
                "库存需按新版本单位用量重新分配，已分配给本批次的库存预留应同步调整"
            )
        else:
            alloc_summary = "新旧版本单位用量一致，库存分配无需调整"
        if critical_gap:
            delay_conclusion = "关键物料在库存+在途+待处理建议之外仍有缺口，本批次存在延期风险，迁移后应重跑延期分析"
            overall = "存在采购缺口与延期风险，需采购与计划确认后方可迁移"
        elif increase_total > 0:
            delay_conclusion = "需求增加但现有库存/在途/建议可覆盖，预期不改变延期结论，迁移后建议重算确认"
            overall = "需求增加，供应总体可覆盖，审批后可迁移"
        else:
            delay_conclusion = "需求不增加，不会产生新的延期结论"
            overall = "变更不增加需求，审批后可迁移"

        assessment_json = json.dumps({
            "impact_items": [item.model_dump() for item in impact_items],
            "inventory_allocation_summary": alloc_summary,
            "delay_conclusion": delay_conclusion,
        }, ensure_ascii=False, default=str)

        existing = crud_batch_bom_migration.find_latest(db, batch_id, target_version_id)
        if existing and existing.status in ("assessed", "approved"):
            existing.impact_assessment = assessment_json
            existing.status = "assessed"
            crud_batch_bom_migration.update(db, existing, impact_assessment=assessment_json, status="assessed")
            migration = existing
        else:
            migration = crud_batch_bom_migration.create(
                db,
                production_batch_id=batch_id,
                from_bom_version_id=batch.bom_version_id,
                to_bom_version_id=target.id,
                status="assessed",
                impact_assessment=assessment_json,
            )

        return BOMMigrationAssessment(
            production_batch_id=batch_id,
            batch_no=batch.batch_no,
            from_bom_version_id=batch.bom_version_id,
            to_bom_version_id=target.id,
            impact_items=impact_items,
            inventory_allocation_summary=alloc_summary,
            delay_conclusion=delay_conclusion,
            overall_conclusion=overall,
            existing_assessment_id=migration.id,
        )

    @staticmethod
    def approve_migration(
        db: Session, migration_id: int, payload: BOMMigrationApprove
    ) -> ProductionBatch:
        migration = crud_batch_bom_migration.get(db, migration_id)
        if not migration:
            raise ValueError("迁移评估记录不存在")
        if migration.status not in ("assessed", "rejected"):
            raise ValueError(f"评估记录状态 {migration.status} 不可审批")
        batch = crud_production_batch.get(db, migration.production_batch_id)
        if batch.status != "released":
            raise ValueError("批次已开工或已领料，不能迁移BOM，只能走偏差处理")

        crud_batch_bom_migration.update(
            db, migration,
            status="approved",
            approved_by=payload.approved_by,
            approved_at=datetime.now(),
            remark=payload.remark,
        )

        # 重新冻结：清除旧快照，按新版本复制；旧版本号仍保留在迁移记录中可追溯
        old_snapshot = crud_batch_bom_item.get_by_batch(db, batch.id)
        for row in old_snapshot:
            db.delete(row)
        db.flush()
        target = crud_bom_version.get(db, migration.to_bom_version_id)
        for item in target.items:
            db.add(ProductionBatchBOMItem(
                production_batch_id=batch.id,
                bom_version_id=target.id,
                material_id=item.material_id,
                quantity=item.quantity,
                remark=item.remark,
            ))
        batch.bom_version_id = target.id
        batch.freeze_reason = (
            f"经审批迁移至BOM版本 {target.version_no}（迁移记录#{migration.id}）"
        )
        db.add(batch)
        crud_batch_bom_migration.update(db, migration, status="migrated")
        db.commit()
        db.refresh(batch)
        return batch

    @staticmethod
    def reject_migration(db: Session, migration_id: int, remark: str) -> ProductionBatchBOMMigration:
        migration = crud_batch_bom_migration.get(db, migration_id)
        if not migration:
            raise ValueError("迁移评估记录不存在")
        if migration.status not in ("assessed",):
            raise ValueError(f"评估记录状态 {migration.status} 不可驳回")
        return crud_batch_bom_migration.update(db, migration, status="rejected", remark=remark)

    # ---------- 偏差处理 ----------

    @staticmethod
    def create_deviation(
        db: Session, batch_id: int, payload: BOMDeviationCreate
    ) -> BOMDeviation:
        batch = crud_production_batch.get(db, batch_id)
        if batch is None:
            raise ValueError("生产批次不存在")
        if batch.status not in BOM_LOCKED_BATCH_STATUSES:
            raise ValueError(
                f"批次状态 {batch.status} 不应使用偏差处理：未开工批次请走影响评估迁移流程"
            )
        snapshot_map = crud_batch_bom_item.get_quantity_map(db, batch_id)
        if not snapshot_map:
            raise ValueError("批次缺少冻结BOM快照")
        if payload.deviation_type not in (
            "quantity", "substitute", "add_material", "remove_material"
        ):
            raise ValueError("偏差类型必须为 quantity/substitute/add_material/remove_material")
        frozen_per = snapshot_map.get(payload.material_id, 0)
        if payload.deviation_type in ("quantity", "remove_material", "substitute") and frozen_per == 0:
            raise ValueError("冻结BOM中不存在该物料，数量/替代/删除类偏差不成立")
        if payload.deviation_type == "add_material" and frozen_per > 0:
            raise ValueError("物料已在冻结BOM中，新增类偏差不成立")

        if payload.actual_quantity is not None:
            actual_per = payload.actual_quantity
        elif payload.deviation_type == "remove_material":
            actual_per = 0
        elif payload.deviation_type == "add_material":
            actual_per = 0  # 新增物料的总用量通过领料直接体现
        else:
            actual_per = frozen_per
        delta_per = actual_per - frozen_per
        if payload.deviation_type == "substitute":
            if not payload.substitute_material_id:
                raise ValueError("替代类偏差必须指定替代物料")
            if not crud_material.get(db, payload.substitute_material_id):
                raise ValueError("替代物料不存在")
            delta_per = -frozen_per  # 原物料按全额切走

        deviation = BOMDeviation(
            deviation_no=crud_bom_deviation.next_deviation_no(db),
            production_batch_id=batch_id,
            bom_version_id=batch.bom_version_id,
            deviation_type=payload.deviation_type,
            material_id=payload.material_id,
            substitute_material_id=payload.substitute_material_id,
            frozen_quantity=frozen_per,
            actual_quantity=actual_per,
            quantity_delta=delta_per,
            reason=payload.reason,
            impact_summary=payload.impact_summary,
            status="draft",
            requested_by=payload.requested_by,
        )
        db.add(deviation)
        db.commit()
        db.refresh(deviation)
        return deviation

    @staticmethod
    def submit_deviation(db: Session, deviation_id: int) -> BOMDeviation:
        deviation = crud_bom_deviation.get(db, deviation_id)
        if not deviation:
            raise ValueError("偏差单不存在")
        if deviation.status not in ("draft", "rejected"):
            raise ValueError(f"偏差单状态 {deviation.status} 不可提交")
        deviation.status = "submitted"
        db.add(deviation)
        db.commit()
        db.refresh(deviation)
        return deviation

    @staticmethod
    def approve_deviation(
        db: Session, deviation_id: int, payload: BOMDeviationApprove
    ) -> BOMDeviation:
        deviation = crud_bom_deviation.get(db, deviation_id)
        if not deviation:
            raise ValueError("偏差单不存在")
        if deviation.status != "submitted":
            raise ValueError(f"偏差单状态 {deviation.status} 不可审批")
        if not payload.impact_summary and not deviation.impact_summary:
            raise ValueError("审批偏差必须说明对采购建议、库存分配、延期结论的影响")
        deviation.status = "approved"
        deviation.approved_by = payload.approved_by
        deviation.approved_at = datetime.now()
        if payload.impact_summary:
            deviation.impact_summary = payload.impact_summary
        db.add(deviation)
        db.commit()
        db.refresh(deviation)
        return deviation

    # ---------- 领料 / 实际消耗 ----------

    @staticmethod
    def issue_material(
        db: Session, batch_id: int, payload: BatchIssueMaterialRequest
    ) -> ProductionMaterialIssueOut:
        batch = crud_production_batch.get(db, batch_id)
        if batch is None:
            raise ValueError("生产批次不存在")
        if batch.status not in BOM_FROZEN_BATCH_STATUSES:
            raise ValueError("批次尚未下达冻结BOM，不能登记领料")
        snapshot_map = crud_batch_bom_item.get_quantity_map(db, batch_id)

        deviation = None
        if payload.deviation_id:
            deviation = crud_bom_deviation.get(db, payload.deviation_id)
            if not deviation or deviation.production_batch_id != batch_id:
                raise ValueError("偏差单不存在或不属于该批次")
            if deviation.status != "approved":
                raise ValueError(f"偏差单状态 {deviation.status}，只有已审批偏差可用于领料")

        if payload.required_quantity is not None:
            required = payload.required_quantity
        else:
            required = snapshot_map.get(payload.material_id, 0) * batch.quantity
            if deviation and deviation.substitute_material_id == payload.material_id:
                required = 0  # 替代料不在原BOM应领范围内

        if crud_material_issue.get_by_no(db, payload.issue_no or "") is not None:
            raise ValueError("领料单号已存在")
        issue_no = payload.issue_no or crud_material_issue.next_issue_no(db)

        # 实际库存扣减（保持与库存模块一致）
        from app.services.inspection import InspectionService
        if not InspectionService.consume_material(db, payload.material_id, payload.issued_quantity):
            raise ValueError(
                f"物料 {payload.material_id} 可用库存不足 {payload.issued_quantity}，无法领料"
            )

        issue = crud_material_issue.create(
            db,
            issue_no=issue_no,
            production_batch_id=batch_id,
            material_id=payload.material_id,
            required_quantity=required,
            issued_quantity=payload.issued_quantity,
            deviation_id=deviation.id if deviation else None,
            issue_date=payload.issue_date or date.today(),
            remark=payload.remark,
        )
        if deviation and deviation.status == "approved":
            deviation.status = "applied"
            deviation.applied_at = datetime.now()
            db.add(deviation)
            db.commit()
        # 领料后批次进入已领料阶段
        if batch.status not in ("issued", "completed", "closed"):
            batch.status = "issued"
            db.add(batch)
            db.commit()
        return ProductionMaterialIssueOut.model_validate(issue)

    # ---------- 追溯 ----------

    @staticmethod
    def trace_batch(db: Session, batch_id: int) -> BatchBOMTrace:
        batch = crud_production_batch.get(db, batch_id)
        if batch is None:
            raise ValueError("生产批次不存在")

        snapshot_rows = crud_batch_bom_item.get_by_batch(db, batch_id)
        original_bom: List[BatchBOMItemSnapshot] = []
        frozen_map: Dict[int, int] = {}
        frozen_version_no = None
        frozen_at = None
        if snapshot_rows:
            frozen_at = snapshot_rows[0].frozen_at
            for row in snapshot_rows:
                frozen_map[row.material_id] = row.quantity
                code, name = _material_brief(db, row.material_id)
                original_bom.append(BatchBOMItemSnapshot(
                    id=row.id,
                    bom_version_id=row.bom_version_id,
                    material_id=row.material_id,
                    material_code=code,
                    material_name=name,
                    quantity=row.quantity,
                    remark=row.remark,
                    frozen_at=row.frozen_at,
                ))
        if batch.bom_version_id:
            fv = crud_bom_version.get(db, batch.bom_version_id)
            frozen_version_no = fv.version_no if fv else None

        # 后续变更：从批次最早冻结过的版本起，列出之后的所有已审批版本（含迁移经过的版本）
        lineage: List[BatchBOMTraceLineage] = []
        if batch.bom_version_id:
            frozen_ids = {batch.bom_version_id}
            for mig in crud_batch_bom_migration.list_by_batch(db, batch_id):
                frozen_ids.add(mig.from_bom_version_id)
                frozen_ids.add(mig.to_bom_version_id)
            frozen_versions = [
                v for v in (crud_bom_version.get(db, vid) for vid in frozen_ids)
                if v is not None
            ]
            earliest = min(frozen_versions, key=lambda v: (v.effective_date, v.id))
            later_versions = [
                v for v in crud_bom_version.list_by_vehicle(
                    db, batch.vehicle_model_id, "approved"
                )
                if v.effective_date >= earliest.effective_date and v.id != earliest.id
            ]
            later_versions.sort(key=lambda v: (v.effective_date, v.id))
            prev_id = earliest.id
            for version in later_versions:
                diff = BOMVersionService.get_diff(
                    db, batch.vehicle_model_id, version.id, prev_id
                )
                lineage.append(BatchBOMTraceLineage(
                    change="followed",
                    version_id=version.id,
                    version_no=version.version_no,
                    change_type=version.change_type,
                    status=version.status,
                    effective_date=version.effective_date,
                    expire_date=version.expire_date,
                    change_reason=version.change_reason,
                    items=diff.items,
                ))
                prev_id = version.id

        migrations = []
        for mig in crud_batch_bom_migration.list_by_batch(db, batch_id):
            tv = crud_bom_version.get(db, mig.to_bom_version_id)
            fv = crud_bom_version.get(db, mig.from_bom_version_id)
            migrations.append({
                "id": mig.id,
                "from_bom_version_id": mig.from_bom_version_id,
                "from_version_no": fv.version_no if fv else None,
                "to_bom_version_id": mig.to_bom_version_id,
                "to_version_no": tv.version_no if tv else None,
                "status": mig.status,
                "impact_assessment": json.loads(mig.impact_assessment) if mig.impact_assessment else None,
                "approved_by": mig.approved_by,
                "approved_at": mig.approved_at,
            })

        deviations = [
            BOMDeviationOut.model_validate(d)
            for d in crud_bom_deviation.list_by_batch(db, batch_id)
        ]

        # 实际消耗差异：冻结应领 vs 累计实领
        issued_map = crud_material_issue.get_issued_map(db, batch_id)
        issues = crud_material_issue.list_by_batch(db, batch_id)
        deviation_links: Dict[int, Tuple[List[int], List[str]]] = {}
        for issue in issues:
            if issue.deviation_id:
                dev = crud_bom_deviation.get(db, issue.deviation_id)
                ids_nos = deviation_links.setdefault(issue.material_id, ([], []))
                ids_nos[0].append(issue.deviation_id)
                ids_nos[1].append(dev.deviation_no if dev else str(issue.deviation_id))
        variances: List[BatchConsumptionVariance] = []
        for material_id in sorted(set(frozen_map) | set(issued_map)):
            frozen_required = frozen_map.get(material_id, 0) * batch.quantity
            actual_issued = issued_map.get(material_id, 0)
            code, name = _material_brief(db, material_id)
            ids_nos = deviation_links.get(material_id, ([], []))
            variances.append(BatchConsumptionVariance(
                material_id=material_id,
                material_code=code,
                material_name=name,
                frozen_per_unit=frozen_map.get(material_id, 0),
                frozen_required=frozen_required,
                actual_issued=actual_issued,
                variance=actual_issued - frozen_required,
                deviation_ids=sorted(set(ids_nos[0])),
                deviation_nos=sorted(set(ids_nos[1])),
            ))

        return BatchBOMTrace(
            production_batch_id=batch_id,
            batch_no=batch.batch_no,
            vehicle_model_id=batch.vehicle_model_id,
            status=batch.status,
            frozen_bom_version_id=batch.bom_version_id,
            frozen_bom_version_no=frozen_version_no,
            frozen_at=frozen_at,
            original_bom=original_bom,
            lineage=lineage,
            migrations=migrations,
            deviations=deviations,
            consumption_variances=variances,
        )


# ---------- 共享读路径：批次实际采用的BOM行 ----------

def get_batch_bom_quantity_map(db: Session, batch: ProductionBatch) -> Dict[int, int]:
    """已下达批次读冻结快照；未下达批次读计划日生效版本；再退化为遗留BOM表。"""
    snapshot = crud_batch_bom_item.get_quantity_map(db, batch.id)
    if snapshot:
        return snapshot
    version = crud_bom_version.get_effective_on(db, batch.vehicle_model_id, batch.plan_date)
    if version:
        return crud_bom_version_item.get_quantity_map(db, version.id)
    return {
        item.material_id: item.quantity
        for item in db.query(BOMItem).filter(
            BOMItem.vehicle_model_id == batch.vehicle_model_id
        ).all()
    }


def get_batch_alternative_rules(
    db: Session, batch: ProductionBatch
) -> Dict[int, List[dict]]:
    """
    返回 {原始物料ID: [{material_id(替代料), priority, is_allowed}]}。
    已下达批次使用版本冻结的替代关系；未下达批次使用当前规则。
    """
    result: Dict[int, List[dict]] = {}
    if batch.bom_version_id:
        rows = crud_bom_version_alternative.get_by_version(db, batch.bom_version_id)
        for row in rows:
            result.setdefault(row.material_id, []).append({
                "material_id": row.alternative_material_id,
                "priority": row.priority,
                "is_allowed": row.is_allowed,
            })
        return result
    # 未下达批次：按当前替代规则和车型限制现场计算
    bom_map = get_batch_bom_quantity_map(db, batch)
    for material_id in bom_map:
        for alt in crud_alternative_material.get_alternatives_for_material(db, material_id):
            is_allowed = crud_alternative_restriction.is_alternative_allowed(
                db, alt.id, batch.vehicle_model_id
            )
            result.setdefault(material_id, []).append({
                "material_id": alt.alternative_material_id,
                "priority": alt.priority,
                "is_allowed": is_allowed,
            })
    return result
