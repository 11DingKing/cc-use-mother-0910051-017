from sqlalchemy.orm import Session
from typing import List, Optional, Dict
from datetime import date
from app.crud.base import CRUDBase
from app.models import (
    BOMVersion, BOMVersionItem, BatchBOMItem,
    ProductionBatchMigration, ProductionBOMDeviation, MaterialConsumption
)
from app.schemas import BOMVersionCreate


class CRUDBOMVersion(CRUDBase[BOMVersion, BOMVersionCreate, dict]):
    def list_by_vehicle(
        self, db: Session, vehicle_model_id: int,
        status: Optional[str] = None
    ) -> List[BOMVersion]:
        query = db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id
        )
        if status:
            query = query.filter(BOMVersion.status == status)
        return query.order_by(
            BOMVersion.effective_date.desc(), BOMVersion.id.desc()
        ).all()

    def get_by_version_no(self, db: Session, vehicle_model_id: int, version_no: str) -> Optional[BOMVersion]:
        return db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id,
            BOMVersion.version_no == version_no
        ).first()

    def get_approved_versions(self, db: Session, vehicle_model_id: int) -> List[BOMVersion]:
        return db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id,
            BOMVersion.status == "approved"
        ).order_by(BOMVersion.effective_date, BOMVersion.approved_at).all()

    def get_latest_approved(self, db: Session, vehicle_model_id: int) -> Optional[BOMVersion]:
        return db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id,
            BOMVersion.status == "approved"
        ).order_by(
            BOMVersion.effective_date.desc(), BOMVersion.approved_at.desc()
        ).first()

    def get_effective_version(
        self, db: Session, vehicle_model_id: int, on_date: date
    ) -> Optional[BOMVersion]:
        """返回 on_date 当天生效的已审批版本：生效日 <= on_date 中最新的一条。"""
        return db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id,
            BOMVersion.status == "approved",
            BOMVersion.effective_date <= on_date
        ).order_by(
            BOMVersion.effective_date.desc(), BOMVersion.approved_at.desc()
        ).first()

    def get_versions_between(
        self, db: Session, vehicle_model_id: int,
        start_exclusive: Optional[date], end_inclusive: Optional[date] = None
    ) -> List[BOMVersion]:
        """严格晚于 start_exclusive、不晚于 end_inclusive 的已审批版本，按生效日升序。"""
        query = db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id,
            BOMVersion.status == "approved"
        )
        if start_exclusive is not None:
            query = query.filter(BOMVersion.effective_date > start_exclusive)
        if end_inclusive is not None:
            query = query.filter(BOMVersion.effective_date <= end_inclusive)
        return query.order_by(BOMVersion.effective_date, BOMVersion.approved_at).all()

    def next_version_no(self, db: Session, vehicle_model_id: int) -> str:
        versions = db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id
        ).all()
        max_seq = 0
        for v in versions:
            # 形如 V1/V001 的版本号取尾部数字
            digits = "".join(ch for ch in v.version_no if ch.isdigit())
            if digits:
                max_seq = max(max_seq, int(digits))
        return f"V{max_seq + 1:03d}"

    def find_overlapping_approved(
        self, db: Session, vehicle_model_id: int, effective_date: date,
        exclude_version_id: Optional[int] = None
    ) -> Optional[BOMVersion]:
        """同一生效日期的已审批版本即视为区间重叠（区间按生效日切分）。"""
        query = db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id,
            BOMVersion.status == "approved",
            BOMVersion.effective_date == effective_date
        )
        if exclude_version_id is not None:
            query = query.filter(BOMVersion.id != exclude_version_id)
        return query.first()


crud_bom_version = CRUDBOMVersion(BOMVersion)


class CRUDBOMVersionItem:
    def list_by_version(self, db: Session, version_id: int) -> List[BOMVersionItem]:
        return db.query(BOMVersionItem).filter(
            BOMVersionItem.version_id == version_id
        ).order_by(BOMVersionItem.id).all()

    def get_map_by_version(self, db: Session, version_id: int) -> Dict[int, BOMVersionItem]:
        items = self.list_by_version(db, version_id)
        return {item.material_id: item for item in items}

    def replace_items(
        self, db: Session, version_id: int,
        items: List[BOMVersionItem]
    ) -> None:
        db.query(BOMVersionItem).filter(
            BOMVersionItem.version_id == version_id
        ).delete()
        for item in items:
            db.add(item)
        db.commit()


crud_bom_version_item = CRUDBOMVersionItem()


class CRUDBatchBOMItem:
    def list_by_batch(self, db: Session, production_batch_id: int) -> List[BatchBOMItem]:
        return db.query(BatchBOMItem).filter(
            BatchBOMItem.production_batch_id == production_batch_id
        ).order_by(BatchBOMItem.id).all()

    def get_map_by_batch(self, db: Session, production_batch_id: int) -> Dict[int, BatchBOMItem]:
        return {item.material_id: item for item in self.list_by_batch(db, production_batch_id)}


crud_batch_bom_item = CRUDBatchBOMItem()


class CRUDProductionBatchMigration(CRUDBase[ProductionBatchMigration, dict, dict]):
    def list_by_batch(self, db: Session, production_batch_id: int) -> List[ProductionBatchMigration]:
        return db.query(ProductionBatchMigration).filter(
            ProductionBatchMigration.production_batch_id == production_batch_id
        ).order_by(ProductionBatchMigration.id).all()

    def get_latest_by_batch(self, db: Session, production_batch_id: int) -> Optional[ProductionBatchMigration]:
        return db.query(ProductionBatchMigration).filter(
            ProductionBatchMigration.production_batch_id == production_batch_id
        ).order_by(ProductionBatchMigration.id.desc()).first()


crud_batch_migration = CRUDProductionBatchMigration(ProductionBatchMigration)


class CRUDProductionBOMDeviation(CRUDBase[ProductionBOMDeviation, dict, dict]):
    def get_by_deviation_no(self, db: Session, deviation_no: str) -> Optional[ProductionBOMDeviation]:
        return db.query(ProductionBOMDeviation).filter(
            ProductionBOMDeviation.deviation_no == deviation_no
        ).first()

    def list_by_batch(self, db: Session, production_batch_id: int) -> List[ProductionBOMDeviation]:
        return db.query(ProductionBOMDeviation).filter(
            ProductionBOMDeviation.production_batch_id == production_batch_id
        ).order_by(ProductionBOMDeviation.id).all()

    def next_deviation_no(self, db: Session) -> str:
        count = db.query(ProductionBOMDeviation).count()
        return f"DEV-{date.today().strftime('%Y%m%d')}-{count + 1:04d}"


crud_bom_deviation = CRUDProductionBOMDeviation(ProductionBOMDeviation)


class CRUDMaterialConsumption:
    def list_by_batch(self, db: Session, production_batch_id: int) -> List[MaterialConsumption]:
        return db.query(MaterialConsumption).filter(
            MaterialConsumption.production_batch_id == production_batch_id
        ).order_by(MaterialConsumption.id).all()

    def get_by_batch_and_material(
        self, db: Session, production_batch_id: int, material_id: int
    ) -> Optional[MaterialConsumption]:
        return db.query(MaterialConsumption).filter(
            MaterialConsumption.production_batch_id == production_batch_id,
            MaterialConsumption.material_id == material_id
        ).first()


crud_material_consumption = CRUDMaterialConsumption()
