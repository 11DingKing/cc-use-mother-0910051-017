import json
from sqlalchemy.orm import Session
from typing import List, Optional, Dict
from datetime import datetime, date
from app.crud.vehicle import crud_vehicle, crud_production_batch
from app.crud.bom_version import (
    crud_bom_version, crud_bom_version_item, crud_batch_bom_item,
    crud_batch_migration, crud_bom_deviation, crud_material_consumption
)
from app.crud.purchase import (
    crud_inventory_batch, crud_purchase_suggestion, crud_purchase_order
)
from app.crud.material import crud_material
from app.crud.alternative import crud_alternative_material, crud_alternative_restriction
from app.models import (
    ProductionBatch, BatchBOMItem, ProductionBatchMigration,
    ProductionBOMDeviation, MaterialConsumption, BOMVersion
)
from app.schemas import BOMDeviationCreate
from app.services.bom_resolver import BOMResolver
from app.services.inspection import InspectionService

# 下达后未开工
RELEASED_STATUS = "released"
# 已领料/开工之后的状态
STARTED_STATUSES = ("in_progress", "completed", "closed")


class ProductionBatchService:
    # ---------- 批次下达：冻结 BOM 结构与单位用量 ----------

    @staticmethod
    def release_batch(
        db: Session, batch_id: int, bom_version_id: Optional[int] = None,
        operator: Optional[str] = None
    ) -> ProductionBatch:
        batch = crud_production_batch.get(db, batch_id)
        if not batch:
            raise ValueError(f"生产批次不存在: {batch_id}")
        if batch.bom_version_id is not None:
            raise ValueError(f"批次已下达，冻结版本为 V{batch.bom_version_id}，不可重复下达")
        if batch.status != "planned":
            raise ValueError(f"仅计划状态批次可下达，当前状态: {batch.status}")

        if bom_version_id is not None:
            version = crud_bom_version.get(db, bom_version_id)
            if not version or version.vehicle_model_id != batch.vehicle_model_id:
                raise ValueError("指定的 BOM 版本不存在或与车型不匹配")
            if version.status != "approved":
                raise ValueError(f"只能按已审批版本下达，版本当前状态: {version.status}")
        else:
            version = crud_bom_version.get_effective_version(
                db, batch.vehicle_model_id, batch.plan_date
            )
            if version is None:
                # 计划日早于任何版本生效日时，按最新已审批版本下达
                version = crud_bom_version.get_latest_approved(db, batch.vehicle_model_id)
            if version is None:
                raise ValueError("该车型尚无已审批生效的 BOM 版本，无法下达")

        items = crud_bom_version_item.list_by_version(db, version.id)
        if not items:
            raise ValueError("BOM 版本没有物料条目，无法下达")

        for item in items:
            db.add(BatchBOMItem(
                production_batch_id=batch.id,
                bom_version_id=version.id,
                material_id=item.material_id,
                quantity_per_unit=item.quantity,
                required_quantity=item.quantity * batch.quantity
            ))
        crud_production_batch.update(db, db_obj=batch, obj_in={
            "bom_version_id": version.id,
            "frozen_at": datetime.now(),
            "status": RELEASED_STATUS,
            "remark": (batch.remark + f"；已按版本{version.version_no}下达冻结" if batch.remark
                       else f"已按版本{version.version_no}下达冻结")
        })
        return crud_production_batch.get(db, batch_id)

    # ---------- 未开工批次：迁移影响评估 ----------

    @staticmethod
    def evaluate_migration(
        db: Session, batch_id: int, to_version_id: Optional[int] = None
    ) -> ProductionBatchMigration:
        batch = crud_production_batch.get(db, batch_id)
        ProductionBatchService._assert_released_not_started(batch)

        if to_version_id is not None:
            target = crud_bom_version.get(db, to_version_id)
            if not target or target.vehicle_model_id != batch.vehicle_model_id:
                raise ValueError("目标版本不存在或与车型不匹配")
            if target.status != "approved":
                raise ValueError("只能迁移到已审批版本")
        else:
            target = crud_bom_version.get_latest_approved(db, batch.vehicle_model_id)
        if target is None:
            raise ValueError("该车型没有可迁移的已审批版本")
        if target.id == batch.bom_version_id:
            raise ValueError("目标版本与批次冻结版本相同，无需迁移")

        detail = ProductionBatchService._build_impact_detail(db, batch, target)

        migration = ProductionBatchMigration(
            production_batch_id=batch.id,
            from_version_id=batch.bom_version_id,
            to_version_id=target.id,
            status="evaluated",
            impact_summary=detail["summary"],
            purchase_impact=detail["purchase_text"],
            inventory_impact=detail["inventory_text"],
            delay_impact=detail["delay_text"],
            impact_detail_json=json.dumps(detail["materials"], ensure_ascii=False)
        )
        db.add(migration)
        db.commit()
        db.refresh(migration)
        return migration

    @staticmethod
    def _assert_released_not_started(batch: Optional[ProductionBatch]) -> None:
        if not batch:
            raise ValueError("生产批次不存在")
        if batch.bom_version_id is None:
            raise ValueError("批次尚未下达，无冻结版本；未下达批次自动按当前生效版本计算")
        if batch.status in STARTED_STATUSES:
            raise ValueError("批次已开工/已领料，不能迁移版本，只能走可追溯的偏差处理")
        if batch.status != RELEASED_STATUS:
            raise ValueError(f"当前批次状态 {batch.status} 不允许迁移")

    @staticmethod
    def _build_impact_detail(db: Session, batch: ProductionBatch, target: BOMVersion) -> Dict:
        frozen = crud_batch_bom_item.get_map_by_batch(db, batch.id)
        new_map = crud_bom_version_item.get_map_by_version(db, target.id)
        qty = batch.quantity
        material_rows = []

        for material_id in sorted(set(frozen) | set(new_map)):
            old_per = frozen[material_id].quantity_per_unit if material_id in frozen else 0
            new_per = new_map[material_id].quantity if material_id in new_map else 0
            old_req = old_per * qty
            new_req = new_per * qty
            delta = new_req - old_req
            if old_per == 0 and new_per > 0:
                change_type = "added"
            elif new_per == 0 and old_per > 0:
                change_type = "removed"
            elif delta != 0:
                change_type = "quantity_changed"
            else:
                change_type = "unchanged"

            stock = crud_inventory_batch.get_total_stock(db, material_id)
            pending = crud_purchase_suggestion.get_pending_quantity_by_material(db, material_id)
            in_transit = crud_purchase_order.get_in_transit_quantity_by_material(db, material_id)
            new_shortage = max(0, new_req - stock - pending - in_transit)
            old_shortage = max(0, old_req - stock - pending - in_transit)

            earliest = None
            if new_shortage > 0:
                orders = crud_purchase_order.get_in_transit_by_material_ordered_by_date(db, material_id)
                # 顺序累加在途到货，找到首个累计可满足需求的日期
                cumulative = stock + pending
                for order in orders:
                    cumulative += order["remaining_quantity"]
                    if cumulative >= new_req:
                        earliest = order["expected_date"].isoformat()
                        break
            delay_days = None
            if earliest:
                delay_days = max(0, (date.fromisoformat(earliest) - batch.plan_date).days)

            material_rows.append({
                "material_id": material_id,
                "change_type": change_type,
                "old_quantity_per_unit": old_per,
                "new_quantity_per_unit": new_per,
                "old_required": old_req,
                "new_required": new_req,
                "delta": delta,
                "stock": stock,
                "pending_suggestion": pending,
                "in_transit": in_transit,
                "new_shortage": new_shortage,
                "shortage_delta": new_shortage - old_shortage,
                "earliest_available_date": earliest,
                "estimated_delay_days": delay_days
            })

        changed = [r for r in material_rows if r["change_type"] != "unchanged"]
        increased = [r for r in changed if r["delta"] > 0]
        decreased = [r for r in changed if r["delta"] < 0]
        shortage_rows = [r for r in material_rows if r["new_shortage"] > 0]
        max_delay = max((r["estimated_delay_days"] or 0) for r in material_rows)

        from_version = crud_bom_version.get(db, batch.bom_version_id)
        from_no = from_version.version_no if from_version else f"#{batch.bom_version_id}"
        to_no = target.version_no

        if increased:
            purchase_text = "；".join(
                f"物料{r['material_id']}增量需求{r['delta']}（新缺口{r['new_shortage']}）"
                for r in increased
            )
        else:
            purchase_text = f"从{from_no}迁移到{to_no}不增加任何物料的采购需求"
        if decreased:
            purchase_text += "；减少：" + "；".join(
                f"物料{r['material_id']}释放{-r['delta']}需求量" for r in decreased
            )

        allocations = [r for r in material_rows if r["new_required"] > 0]
        if allocations:
            inventory_text = "；".join(
                f"物料{r['material_id']}需求{r['new_required']}/可用(库存{r['stock']}+待处理建议{r['pending_suggestion']}+在途{r['in_transit']})"
                for r in allocations
            )
        else:
            inventory_text = f"迁移到{to_no}后该批次无物料分配需求"

        if not shortage_rows:
            delay_text = f"从{from_no}迁移到{to_no}：现有库存、待处理采购建议与在途订单可覆盖迁移后需求，不改变延期结论"
        else:
            delay_text = f"从{from_no}迁移到{to_no}：" + "；".join(
                f"物料{r['material_id']}缺口{r['new_shortage']}"
                + (f"，最早{r['earliest_available_date']}齐料，预计延期{r['estimated_delay_days']}天"
                   if r["earliest_available_date"] else "，无明确在途补充，需重新评估延期")
                for r in shortage_rows
            )
            delay_text += f"；批次最大延期约{max_delay}天"

        summary = (
            f"{from_no}->{to_no} 变更{len(changed)}项（增加{len(increased)}/减少{len(decreased)}），"
            f"短缺物料{len(shortage_rows)}种，最大延期约{max_delay}天"
        )
        return {
            "summary": summary,
            "purchase_text": purchase_text,
            "inventory_text": inventory_text,
            "delay_text": delay_text,
            "materials": material_rows
        }

    # ---------- 迁移决策 ----------

    @staticmethod
    def decide_migration(
        db: Session, migration_id: int, action: str, decided_by: Optional[str] = None
    ) -> ProductionBatchMigration:
        migration = crud_batch_migration.get(db, migration_id)
        if not migration:
            raise ValueError(f"迁移评估不存在: {migration_id}")
        if migration.status != "evaluated":
            raise ValueError(f"评估单已处理: {migration.status}")
        if action not in ("migrate", "reject"):
            raise ValueError("决策必须是 migrate 或 reject")

        batch = crud_production_batch.get(db, migration.production_batch_id)
        ProductionBatchService._assert_released_not_started(batch)

        if action == "reject":
            return crud_batch_migration.update(db, db_obj=migration, obj_in={
                "status": "rejected",
                "decided_by": decided_by,
                "decided_at": datetime.now()
            })

        target = crud_bom_version.get(db, migration.to_version_id)
        new_map = crud_bom_version_item.get_map_by_version(db, target.id)

        # 重新冻结：先删除旧快照并立即落库（避免与新行的唯一约束冲突），再写入目标版本快照
        old_items = crud_batch_bom_item.list_by_batch(db, batch.id)
        for old in old_items:
            db.delete(old)
        db.flush()
        for material_id, item in new_map.items():
            db.add(BatchBOMItem(
                production_batch_id=batch.id,
                bom_version_id=target.id,
                material_id=material_id,
                quantity_per_unit=item.quantity,
                required_quantity=item.quantity * batch.quantity
            ))
        db.flush()
        crud_production_batch.update(db, db_obj=batch, obj_in={
            "bom_version_id": target.id,
            "frozen_at": datetime.now(),
            "remark": (batch.remark + f"；已迁移至版本{target.version_no}（评估单#{migration.id}）")
        })
        crud_batch_migration.update(db, db_obj=migration, obj_in={
            "status": "migrated",
            "decided_by": decided_by,
            "decided_at": datetime.now()
        })
        return crud_batch_migration.get(db, migration_id)

    # ---------- 已领料批次：可追溯偏差处理 ----------

    @staticmethod
    def create_deviation(
        db: Session, batch_id: int, deviation_in: BOMDeviationCreate
    ) -> ProductionBOMDeviation:
        batch = crud_production_batch.get(db, batch_id)
        if not batch:
            raise ValueError("生产批次不存在")
        if batch.bom_version_id is None:
            raise ValueError("批次尚未下达，不能登记偏差")
        if batch.status not in STARTED_STATUSES:
            raise ValueError("只有已开工/已领料批次允许偏差处理；未开工批次请走版本迁移")

        frozen_map = crud_batch_bom_item.get_map_by_batch(db, batch.id)
        frozen = frozen_map.get(deviation_in.material_id)
        if not frozen:
            raise ValueError("偏差物料不在批次冻结 BOM 中")
        required = frozen.required_quantity

        dtype = deviation_in.deviation_type
        if dtype not in ("substitute", "over_issue", "short_issue", "engineering_change"):
            raise ValueError("偏差类型必须是 substitute/over_issue/short_issue/engineering_change")
        if not deviation_in.reason or not deviation_in.reason.strip():
            raise ValueError("偏差必须说明原因，保证可追溯")

        if dtype == "substitute":
            if not deviation_in.substitute_material_id:
                raise ValueError("替代偏差必须指定替代物料")
            if deviation_in.substitute_material_id == deviation_in.material_id:
                raise ValueError("替代物料不能与原物料相同")
            if not crud_alternative_material.get_by_materials(
                db, deviation_in.material_id, deviation_in.substitute_material_id
            ):
                raise ValueError("替代物料未在替代关系中登记")
            if not crud_alternative_restriction.is_alternative_allowed_by_materials(
                db, deviation_in.material_id, deviation_in.substitute_material_id,
                batch.vehicle_model_id
            ):
                raise ValueError("该车型不允许使用此替代物料")
            if deviation_in.substitute_quantity <= 0:
                raise ValueError("替代数量必须大于0")
        elif dtype == "over_issue" and deviation_in.actual_quantity <= required:
            raise ValueError(f"超领偏差实际数量须大于应耗{required}")
        elif dtype == "short_issue" and not (0 <= deviation_in.actual_quantity < required):
            raise ValueError(f"短领偏差实际数量须在0与应耗{required}之间")
        elif dtype == "engineering_change" and deviation_in.actual_quantity < 0:
            raise ValueError("实际数量不能为负")

        deviation_no = crud_bom_deviation.next_deviation_no(db)
        deviation = ProductionBOMDeviation(
            deviation_no=deviation_no,
            production_batch_id=batch.id,
            bom_version_id=batch.bom_version_id,
            deviation_type=dtype,
            material_id=deviation_in.material_id,
            substitute_material_id=deviation_in.substitute_material_id,
            required_quantity=required,
            actual_quantity=deviation_in.actual_quantity,
            substitute_quantity=deviation_in.substitute_quantity,
            reason=deviation_in.reason,
            purchase_impact=deviation_in.purchase_impact,
            inventory_impact=deviation_in.inventory_impact,
            delay_impact=deviation_in.delay_impact,
            status="draft",
            requested_by=deviation_in.requested_by
        )
        db.add(deviation)
        db.commit()
        db.refresh(deviation)
        return deviation

    @staticmethod
    def approve_deviation(
        db: Session, deviation_id: int, approved_by: Optional[str] = None,
        purchase_impact: Optional[str] = None,
        inventory_impact: Optional[str] = None,
        delay_impact: Optional[str] = None
    ) -> ProductionBOMDeviation:
        deviation = crud_bom_deviation.get(db, deviation_id)
        if not deviation:
            raise ValueError("偏差单不存在")
        if deviation.status not in ("draft", "rejected"):
            raise ValueError(f"当前偏差状态 {deviation.status} 不允许审批")
        return crud_bom_deviation.update(db, db_obj=deviation, obj_in={
            "status": "approved",
            "approved_by": approved_by,
            "approved_at": datetime.now(),
            "purchase_impact": purchase_impact if purchase_impact is not None else deviation.purchase_impact,
            "inventory_impact": inventory_impact if inventory_impact is not None else deviation.inventory_impact,
            "delay_impact": delay_impact if delay_impact is not None else deviation.delay_impact
        })

    @staticmethod
    def apply_deviation(db: Session, deviation_id: int) -> ProductionBOMDeviation:
        """执行已审批偏差：扣减库存并写入实际消耗，差异可在追溯查询中还原。"""
        deviation = crud_bom_deviation.get(db, deviation_id)
        if not deviation:
            raise ValueError("偏差单不存在")
        if deviation.status != "approved":
            raise ValueError("仅已审批偏差可执行")
        batch = crud_production_batch.get(db, deviation.production_batch_id)

        # 扣减实际领用的库存（原物料 + 替代料）
        if deviation.actual_quantity > 0:
            if not InspectionService.consume_material(db, deviation.material_id, deviation.actual_quantity):
                raise ValueError(
                    f"原物料库存不足以执行偏差，应/实领 {deviation.required_quantity}/{deviation.actual_quantity}"
                )
        if deviation.substitute_quantity > 0:
            if not InspectionService.consume_material(
                db, deviation.substitute_material_id, deviation.substitute_quantity
            ):
                raise ValueError("替代物料库存不足以执行偏差")

        ProductionBatchService._upsert_consumption(
            db, batch.id, deviation.material_id,
            consumed=deviation.actual_quantity, source="deviation",
            deviation_id=deviation.id
        )
        if deviation.substitute_quantity > 0:
            ProductionBatchService._upsert_consumption(
                db, batch.id, deviation.substitute_material_id,
                consumed=deviation.substitute_quantity, source="deviation",
                deviation_id=deviation.id
            )

        return crud_bom_deviation.update(db, db_obj=deviation, obj_in={
            "status": "applied",
            "applied_at": datetime.now()
        })

    # ---------- 实际消耗登记 ----------

    @staticmethod
    def record_consumption(
        db: Session, batch_id: int, material_id: int, consumed_quantity: int,
        remark: Optional[str] = None
    ) -> MaterialConsumption:
        batch = crud_production_batch.get(db, batch_id)
        if not batch:
            raise ValueError("生产批次不存在")
        if batch.bom_version_id is None:
            raise ValueError("批次尚未下达，不能登记消耗")
        frozen = crud_batch_bom_item.get_map_by_batch(db, batch.id).get(material_id)
        if not frozen:
            raise ValueError("物料不在批次冻结 BOM 中")
        if not InspectionService.consume_material(db, material_id, consumed_quantity):
            raise ValueError("库存不足，无法登记领料消耗")
        record = ProductionBatchService._upsert_consumption(
            db, batch.id, material_id, consumed=consumed_quantity,
            source="manual", remark=remark
        )
        return record

    @staticmethod
    def _upsert_consumption(
        db: Session, batch_id: int, material_id: int, consumed: int,
        source: str, deviation_id: Optional[int] = None,
        remark: Optional[str] = None
    ) -> MaterialConsumption:
        frozen = crud_batch_bom_item.get_map_by_batch(db, batch_id).get(material_id)
        required = frozen.required_quantity if frozen else 0
        record = crud_material_consumption.get_by_batch_and_material(db, batch_id, material_id)
        if record and record.source == "deviation" and source == "deviation" and record.deviation_id != deviation_id:
            record = None
        if record:
            crud_obj = crud_material_consumption
            record.consumed_quantity = (record.consumed_quantity or 0) + consumed
            record.required_quantity = required
            if deviation_id:
                record.deviation_id = deviation_id
            if remark:
                record.remark = remark
            db.add(record)
            db.commit()
            db.refresh(record)
            return record
        record = MaterialConsumption(
            production_batch_id=batch_id,
            material_id=material_id,
            required_quantity=required,
            consumed_quantity=consumed,
            source=source,
            deviation_id=deviation_id,
            remark=remark
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return record

    # ---------- 追溯：从任一生产批次还原 BOM、后续变更与实际差异 ----------

    @staticmethod
    def get_traceability(db: Session, batch_id: int) -> Dict:
        batch = crud_production_batch.get(db, batch_id)
        if not batch:
            raise ValueError("生产批次不存在")

        frozen_items = crud_batch_bom_item.list_by_batch(db, batch.id)
        frozen_version = (
            crud_bom_version.get(db, batch.bom_version_id)
            if batch.bom_version_id else None
        )

        # 后续变更：冻结版本生效日之后该车型所有已生效版本（含已被取代归档的版本）
        subsequent: List[BOMVersion] = []
        if frozen_version:
            subsequent = crud_bom_version.get_versions_between(
                db, batch.vehicle_model_id, frozen_version.effective_date
            )
            subsequent = [v for v in subsequent if v.id != frozen_version.id]

        consumptions = crud_material_consumption.list_by_batch(db, batch.id)
        # 同一物料可能因多次领料/多张偏差单产生多条记录，按物料聚合
        consumption_agg: Dict[int, Dict] = {}
        for c in consumptions:
            entry = consumption_agg.setdefault(c.material_id, {
                "consumed": 0, "required": c.required_quantity,
                "source": c.source, "remark": c.remark
            })
            entry["consumed"] += c.consumed_quantity or 0
            entry["required"] = c.required_quantity or entry["required"]
            # 偏差执行优先标注为 deviation 来源
            if c.source == "deviation":
                entry["source"] = "deviation"

        consumption_rows = []
        for frozen in frozen_items:
            agg = consumption_agg.pop(frozen.material_id, None)
            consumed = agg["consumed"] if agg else 0
            material = frozen.material
            consumption_rows.append({
                "material_id": frozen.material_id,
                "material_code": material.code if material else "",
                "material_name": material.name if material else "",
                "required_quantity": frozen.required_quantity,
                "consumed_quantity": consumed,
                "variance_quantity": consumed - frozen.required_quantity,
                "source": agg["source"] if agg else "not_consumed",
                "remark": agg["remark"] if agg else None
            })
        # 替代料等不在冻结 BOM 中的实际消耗也要体现
        for material_id, agg in consumption_agg.items():
            material = crud_material.get(db, material_id)
            consumption_rows.append({
                "material_id": material_id,
                "material_code": material.code if material else "",
                "material_name": material.name if material else "",
                "required_quantity": agg["required"],
                "consumed_quantity": agg["consumed"],
                "variance_quantity": agg["consumed"] - agg["required"],
                "source": agg["source"],
                "remark": agg["remark"]
            })

        return {
            "production_batch": batch,
            "frozen_bom": frozen_items,
            "subsequent_changes": subsequent,
            "consumption": consumption_rows,
            "deviations": crud_bom_deviation.list_by_batch(db, batch.id),
            "migrations": crud_batch_migration.list_by_batch(db, batch.id)
        }
