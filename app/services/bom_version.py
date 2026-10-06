import json
from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Tuple
from datetime import datetime
from app.crud.bom_version import (
    crud_bom_version, crud_bom_version_item
)
from app.crud.vehicle import crud_vehicle
from app.crud.material import crud_material
from app.models import BOMVersion, BOMVersionItem
from app.schemas import (
    BOMVersionCreate, BOMVersionUpdate,
    BOMVersionDiff, BOMDiffItem
)

# 需要强制说明三类影响的变更类型
IMPACT_NOTE_REQUIRED_TYPES = ("emergency", "rollback")


class BOMVersionService:
    # ---------- 版本创建 ----------

    @staticmethod
    def create_version(
        db: Session, version_in: BOMVersionCreate, operator: Optional[str] = None
    ) -> BOMVersion:
        vehicle = crud_vehicle.get(db, version_in.vehicle_model_id)
        if not vehicle:
            raise ValueError(f"车型不存在: {version_in.vehicle_model_id}")
        if version_in.change_type not in ("normal", "emergency", "rollback"):
            raise ValueError("变更类型必须是 normal/emergency/rollback")

        # 回退/紧急版本在创建时也要求带来源与影响说明
        if version_in.change_type in IMPACT_NOTE_REQUIRED_TYPES:
            BOMVersionService._require_impact_notes(
                version_in.purchase_impact,
                version_in.inventory_impact,
                version_in.delay_impact_note
            )

        source_version: Optional[BOMVersion] = None
        if version_in.source_version_id is not None:
            source_version = crud_bom_version.get(db, version_in.source_version_id)
            if not source_version:
                raise ValueError(f"来源版本不存在: {version_in.source_version_id}")
            if source_version.vehicle_model_id != version_in.vehicle_model_id:
                raise ValueError("来源版本与车型不匹配")
            if source_version.status != "approved":
                raise ValueError("只能复制已审批版本（回退须指向历史已审批版本）")

        # 确定条目：指定来源版本时复制其全部条目；否则用提交的条目
        if source_version is not None and not version_in.items:
            source_map = crud_bom_version_item.get_map_by_version(db, source_version.id)
            payload_items = [
                {"material_id": mid, "quantity": it.quantity, "remark": it.remark}
                for mid, it in source_map.items()
            ]
        else:
            payload_items = [it.model_dump() for it in version_in.items]

        BOMVersionService._validate_items(db, version_in.vehicle_model_id, payload_items)

        latest = crud_bom_version.get_latest_approved(db, version_in.vehicle_model_id)
        version_no = crud_bom_version.next_version_no(db, version_in.vehicle_model_id)

        version = BOMVersion(
            vehicle_model_id=version_in.vehicle_model_id,
            version_no=version_no,
            change_type=version_in.change_type,
            status="draft",
            effective_date=version_in.effective_date,
            reason=version_in.reason,
            source_version_id=source_version.id if source_version else None,
            purchase_impact=version_in.purchase_impact,
            inventory_impact=version_in.inventory_impact,
            delay_impact_note=version_in.delay_impact_note,
            submitted_by=operator,
        )
        db.add(version)
        db.flush()

        old_map = crud_bom_version_item.get_map_by_version(db, latest.id) if latest else {}
        for payload in payload_items:
            material_id = payload["material_id"]
            old_item = old_map.get(material_id)
            if not old_item:
                change_flag = "added"
            elif old_item.quantity != payload["quantity"]:
                change_flag = "quantity_changed"
            else:
                change_flag = "unchanged"
            db.add(BOMVersionItem(
                version_id=version.id,
                material_id=material_id,
                quantity=payload["quantity"],
                change_flag=change_flag,
                remark=payload.get("remark")
            ))
        db.commit()
        db.refresh(version)
        return version

    @staticmethod
    def _validate_items(db: Session, vehicle_model_id: int, items: List[dict]) -> None:
        if not items:
            raise ValueError("BOM 版本至少包含一条物料")
        material_ids = [it["material_id"] for it in items]
        if len(set(material_ids)) != len(material_ids):
            raise ValueError("同一物料在一个版本中只能出现一次")
        for it in items:
            if it["quantity"] <= 0:
                raise ValueError("单位用量必须大于0")
            material = crud_material.get(db, it["material_id"])
            if not material:
                raise ValueError(f"物料不存在: {it['material_id']}")

    @staticmethod
    def _require_impact_notes(purchase_impact: Optional[str], inventory_impact: Optional[str],
                              delay_impact_note: Optional[str]) -> None:
        missing = []
        if not purchase_impact:
            missing.append("采购建议影响")
        if not inventory_impact:
            missing.append("库存分配影响")
        if not delay_impact_note:
            missing.append("延期结论影响")
        if missing:
            raise ValueError("紧急更改/回退必须说明：" + "、".join(missing))

    # ---------- 草稿维护 ----------

    @staticmethod
    def update_draft(db: Session, version_id: int, update_in: BOMVersionUpdate) -> BOMVersion:
        version = crud_bom_version.get(db, version_id)
        if not version:
            raise ValueError(f"BOM版本不存在: {version_id}")
        if version.status != "draft":
            raise ValueError(f"仅草稿状态可修改，当前状态: {version.status}")
        data = update_in.model_dump(exclude_unset=True)
        if version.change_type in IMPACT_NOTE_REQUIRED_TYPES:
            merged = {
                "purchase_impact": version.purchase_impact,
                "inventory_impact": version.inventory_impact,
                "delay_impact_note": version.delay_impact_note,
            }
            merged.update(data)
            BOMVersionService._require_impact_notes(
                merged["purchase_impact"], merged["inventory_impact"], merged["delay_impact_note"]
            )
        return crud_bom_version.update(db, db_obj=version, obj_in=data)

    # ---------- 提交/审批/驳回 ----------

    @staticmethod
    def submit_version(db: Session, version_id: int, operator: Optional[str] = None) -> BOMVersion:
        version = crud_bom_version.get(db, version_id)
        if not version:
            raise ValueError(f"BOM版本不存在: {version_id}")
        if version.status != "draft":
            raise ValueError(f"仅草稿状态可提交审批，当前状态: {version.status}")
        items = crud_bom_version_item.list_by_version(db, version_id)
        if not items:
            raise ValueError("空 BOM 版本不可提交")
        if version.change_type in IMPACT_NOTE_REQUIRED_TYPES:
            BOMVersionService._require_impact_notes(
                version.purchase_impact, version.inventory_impact, version.delay_impact_note
            )
        return crud_bom_version.update(db, db_obj=version, obj_in={
            "status": "submitted",
            "submitted_by": operator or version.submitted_by,
            "submitted_at": datetime.now()
        })

    @staticmethod
    def approve_version(
        db: Session, version_id: int,
        approved_by: Optional[str] = None,
        purchase_impact: Optional[str] = None,
        inventory_impact: Optional[str] = None,
        delay_impact_note: Optional[str] = None
    ) -> BOMVersion:
        version = crud_bom_version.get(db, version_id)
        if not version:
            raise ValueError(f"BOM版本不存在: {version_id}")
        if version.status not in ("submitted", "draft"):
            raise ValueError(f"当前状态不允许审批: {version.status}")

        # 紧急/回退：以审批入参补齐或覆盖影响说明，仍然缺失则拒绝审批
        if version.change_type in IMPACT_NOTE_REQUIRED_TYPES:
            final_purchase = purchase_impact or version.purchase_impact
            final_inventory = inventory_impact or version.inventory_impact
            final_delay = delay_impact_note or version.delay_impact_note
            BOMVersionService._require_impact_notes(final_purchase, final_inventory, final_delay)
        else:
            final_purchase = purchase_impact if purchase_impact is not None else version.purchase_impact
            final_inventory = inventory_impact if inventory_impact is not None else version.inventory_impact
            final_delay = delay_impact_note if delay_impact_note is not None else version.delay_impact_note

        # 版本区间不得重叠：同一车型同一生效日期只允许一个已审批版本
        overlap = crud_bom_version.find_overlapping_approved(
            db, version.vehicle_model_id, version.effective_date,
            exclude_version_id=version.id
        )
        if overlap:
            raise ValueError(
                f"生效日期 {version.effective_date} 与已审批版本 {overlap.version_no} 重叠，版本区间不得重叠"
            )

        # 原当前版本（生效日 <= 新版本生效日 的最新版本）被新版本取代。
        # 注意：不把旧版本改成 archived，否则历史区间内的批次将无法解析到当时的版本。
        # 旧版本保持 approved 并用 superseded_by_id 标记，生效区间仍按生效日切分、互不重叠。
        previous = crud_bom_version.get_effective_version(
            db, version.vehicle_model_id, version.effective_date
        )
        now = datetime.now()
        if previous:
            crud_bom_version.update(db, db_obj=previous, obj_in={
                "superseded_by_id": version.id
            })

        return crud_bom_version.update(db, db_obj=version, obj_in={
            "status": "approved",
            "approved_by": approved_by,
            "approved_at": now,
            "purchase_impact": final_purchase,
            "inventory_impact": final_inventory,
            "delay_impact_note": final_delay
        })

    @staticmethod
    def reject_version(
        db: Session, version_id: int, reject_reason: str, operator: Optional[str] = None
    ) -> BOMVersion:
        version = crud_bom_version.get(db, version_id)
        if not version:
            raise ValueError(f"BOM版本不存在: {version_id}")
        if version.status not in ("draft", "submitted"):
            raise ValueError(f"当前状态不允许驳回: {version.status}")
        return crud_bom_version.update(db, db_obj=version, obj_in={
            "status": "rejected",
            "reject_reason": reject_reason
        })

    # ---------- 回退（以历史版本为源产生新版本） ----------

    @staticmethod
    def rollback_to_version(
        db: Session,
        vehicle_model_id: int,
        source_version_id: int,
        effective_date,
        reason: str,
        purchase_impact: str,
        inventory_impact: str,
        delay_impact_note: str,
        operator: Optional[str] = None
    ) -> BOMVersion:
        """回退不是把旧版本改回 approved，而是复制历史结构产生一个 rollback 新版本，链路可追溯。"""
        version_in = BOMVersionCreate(
            vehicle_model_id=vehicle_model_id,
            effective_date=effective_date,
            reason=reason or f"回退至历史版本结构",
            change_type="rollback",
            items=[],
            purchase_impact=purchase_impact,
            inventory_impact=inventory_impact,
            delay_impact_note=delay_impact_note,
            source_version_id=source_version_id
        )
        version = BOMVersionService.create_version(db, version_in, operator=operator)
        # 回退版本直接进入待审批
        return BOMVersionService.submit_version(db, version.id, operator=operator)

    # ---------- 差异比较 ----------

    @staticmethod
    def get_diff(
        db: Session, vehicle_model_id: int,
        from_version_id: Optional[int], to_version_id: int
    ) -> BOMVersionDiff:
        to_version = crud_bom_version.get(db, to_version_id)
        if not to_version or to_version.vehicle_model_id != vehicle_model_id:
            raise ValueError(f"目标版本不存在或不属于该车型: {to_version_id}")
        if from_version_id is not None:
            from_version = crud_bom_version.get(db, from_version_id)
            if not from_version or from_version.vehicle_model_id != vehicle_model_id:
                raise ValueError(f"来源版本不存在或不属于该车型: {from_version_id}")
        else:
            from_version = None

        old_map = crud_bom_version_item.get_map_by_version(db, from_version_id) if from_version else {}
        new_map = crud_bom_version_item.get_map_by_version(db, to_version_id)
        diff_items: List[BOMDiffItem] = []
        all_material_ids = sorted(set(old_map) | set(new_map))
        for material_id in all_material_ids:
            material = crud_material.get(db, material_id)
            old_item = old_map.get(material_id)
            new_item = new_map.get(material_id)
            if old_item and not new_item:
                change_type = "removed"
            elif not old_item and new_item:
                change_type = "added"
            elif old_item.quantity != new_item.quantity:
                change_type = "quantity_changed"
            else:
                change_type = "unchanged"
            diff_items.append(BOMDiffItem(
                material_id=material_id,
                material_code=material.code if material else "",
                material_name=material.name if material else "",
                change_type=change_type,
                old_quantity=old_item.quantity if old_item else None,
                new_quantity=new_item.quantity if new_item else None
            ))
        return BOMVersionDiff(
            vehicle_model_id=vehicle_model_id,
            from_version_id=from_version_id,
            to_version_id=to_version_id,
            items=diff_items
        )
