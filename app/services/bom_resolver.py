from sqlalchemy.orm import Session
from typing import Dict, List, Optional
from datetime import date
from app.crud.bom_version import (
    crud_bom_version, crud_bom_version_item, crud_batch_bom_item
)
from app.crud.vehicle import crud_vehicle
from app.models import ProductionBatch


class BOMResolver:
    """所有需求/采购/延期分析统一通过本解析器读取单位用量：
    已下达批次 → 冻结快照；未下达批次 → 计划日生效的已审批版本。"""

    @staticmethod
    def _legacy_map(db: Session, vehicle_model_id: int) -> Dict[int, int]:
        """遗留直接维护式 BOM 的兼容兜底（版本体系建立前的历史数据）。"""
        return {
            bi.material_id: bi.quantity
            for bi in crud_vehicle.get_bom_items(db, vehicle_model_id)
        }

    @staticmethod
    def get_batch_bom_map(db: Session, batch: ProductionBatch) -> Dict[int, int]:
        """返回 {material_id: 单位用量}。"""
        # 1) 批次下达时冻结的快照优先
        if batch.bom_version_id is not None:
            frozen = crud_batch_bom_item.get_map_by_batch(db, batch.id)
            if frozen:
                return {mid: item.quantity_per_unit for mid, item in frozen.items()}

        # 2) 未冻结批次按计划日对应的生效版本；版本生效日均晚于计划日时退回到最新已审批版本
        version = crud_bom_version.get_effective_version(db, batch.vehicle_model_id, batch.plan_date)
        if version is None:
            version = crud_bom_version.get_latest_approved(db, batch.vehicle_model_id)
        if version is not None:
            items = crud_bom_version_item.get_map_by_version(db, version.id)
            return {mid: item.quantity for mid, item in items.items()}

        # 3) 尚无任何版本时使用遗留 BOM 表兜底
        return BOMResolver._legacy_map(db, batch.vehicle_model_id)

    @staticmethod
    def get_batch_material_qty(
        db: Session, batch: ProductionBatch, material_id: int
    ) -> Optional[int]:
        return BOMResolver.get_batch_bom_map(db, batch).get(material_id)

    @staticmethod
    def get_current_bom_map(
        db: Session, vehicle_model_id: int, on_date: Optional[date] = None
    ) -> Dict[int, int]:
        on_date = on_date or date.today()
        version = crud_bom_version.get_effective_version(db, vehicle_model_id, on_date)
        if version is None:
            version = crud_bom_version.get_latest_approved(db, vehicle_model_id)
        if version is not None:
            items = crud_bom_version_item.get_map_by_version(db, version.id)
            return {mid: item.quantity for mid, item in items.items()}
        return BOMResolver._legacy_map(db, vehicle_model_id)
