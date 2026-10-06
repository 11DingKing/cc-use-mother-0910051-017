from sqlalchemy.orm import Session
from typing import List, Optional, Dict
from datetime import date, datetime

from app.models import (
    BOMVersion, BOMVersionItem, BOMVersionAlternative,
    ProductionBatchBOMItem, ProductionBatchBOMMigration,
    BOMDeviation, ProductionMaterialIssue
)


class CRUDBOMVersion:
    def get(self, db: Session, version_id: int) -> Optional[BOMVersion]:
        return db.query(BOMVersion).filter(BOMVersion.id == version_id).first()

    def list_by_vehicle(
        self, db: Session, vehicle_model_id: int, status: Optional[str] = None
    ) -> List[BOMVersion]:
        query = db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id
        )
        if status:
            query = query.filter(BOMVersion.status == status)
        return query.order_by(
            BOMVersion.effective_date.desc(), BOMVersion.id.desc()
        ).all()

    def get_by_version_no(
        self, db: Session, vehicle_model_id: int, version_no: str
    ) -> Optional[BOMVersion]:
        return db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id,
            BOMVersion.version_no == version_no
        ).first()

    def next_version_no(self, db: Session, vehicle_model_id: int) -> str:
        count = db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id
        ).count()
        return f"V{vehicle_model_id:03d}-{count + 1:04d}"

    def get_effective_on(
        self, db: Session, vehicle_model_id: int, on_date: date
    ) -> Optional[BOMVersion]:
        """返回 on_date 当天生效（approved 且 effective_date <= on_date < expire_date）的版本。"""
        return db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id,
            BOMVersion.status == "approved",
            BOMVersion.effective_date <= on_date,
            (BOMVersion.expire_date.is_(None)) | (BOMVersion.expire_date > on_date)
        ).order_by(BOMVersion.effective_date.desc(), BOMVersion.id.desc()).first()

    def get_open_approved(self, db: Session, vehicle_model_id: int) -> Optional[BOMVersion]:
        """开放区间（expire_date 为空）的已审批版本。"""
        return db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id,
            BOMVersion.status == "approved",
            BOMVersion.expire_date.is_(None)
        ).order_by(BOMVersion.effective_date.desc()).first()

    def find_approved_overlap(
        self,
        db: Session,
        vehicle_model_id: int,
        effective_date: date,
        exclude_version_id: Optional[int] = None
    ) -> Optional[BOMVersion]:
        """查找与 [effective_date, +∞) 生效区间重叠的已审批版本。"""
        query = db.query(BOMVersion).filter(
            BOMVersion.vehicle_model_id == vehicle_model_id,
            BOMVersion.status == "approved",
            BOMVersion.effective_date <= effective_date,
            (BOMVersion.expire_date.is_(None)) | (BOMVersion.expire_date > effective_date)
        )
        if exclude_version_id:
            query = query.filter(BOMVersion.id != exclude_version_id)
        return query.first()


crud_bom_version = CRUDBOMVersion()


class CRUDBOMVersionItem:
    def get_by_version(self, db: Session, bom_version_id: int) -> List[BOMVersionItem]:
        return db.query(BOMVersionItem).filter(
            BOMVersionItem.bom_version_id == bom_version_id
        ).all()

    def get_quantity_map(
        self, db: Session, bom_version_id: int
    ) -> Dict[int, int]:
        items = self.get_by_version(db, bom_version_id)
        return {i.material_id: i.quantity for i in items}


crud_bom_version_item = CRUDBOMVersionItem()


class CRUDBOMVersionAlternative:
    def get_by_version(
        self, db: Session, bom_version_id: int
    ) -> List[BOMVersionAlternative]:
        return db.query(BOMVersionAlternative).filter(
            BOMVersionAlternative.bom_version_id == bom_version_id
        ).all()


crud_bom_version_alternative = CRUDBOMVersionAlternative()


class CRUDProductionBatchBOMItem:
    def get_by_batch(
        self, db: Session, production_batch_id: int
    ) -> List[ProductionBatchBOMItem]:
        return db.query(ProductionBatchBOMItem).filter(
            ProductionBatchBOMItem.production_batch_id == production_batch_id
        ).all()

    def get_quantity_map(
        self, db: Session, production_batch_id: int
    ) -> Dict[int, int]:
        rows = self.get_by_batch(db, production_batch_id)
        return {r.material_id: r.quantity for r in rows}


crud_batch_bom_item = CRUDProductionBatchBOMItem()


class CRUDProductionBatchBOMMigration:
    def create(self, db: Session, **fields) -> ProductionBatchBOMMigration:
        obj = ProductionBatchBOMMigration(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def update(self, db: Session, db_obj: ProductionBatchBOMMigration, **fields):
        for key, value in fields.items():
            setattr(db_obj, key, value)
        db.add(db_obj)
        db.commit()
        db.refresh(db_obj)
        return db_obj

    def get(self, db: Session, migration_id: int) -> Optional[ProductionBatchBOMMigration]:
        return db.query(ProductionBatchBOMMigration).filter(
            ProductionBatchBOMMigration.id == migration_id
        ).first()

    def list_by_batch(
        self, db: Session, production_batch_id: int
    ) -> List[ProductionBatchBOMMigration]:
        return db.query(ProductionBatchBOMMigration).filter(
            ProductionBatchBOMMigration.production_batch_id == production_batch_id
        ).order_by(ProductionBatchBOMMigration.id).all()

    def find_latest(
        self,
        db: Session,
        production_batch_id: int,
        to_bom_version_id: int
    ) -> Optional[ProductionBatchBOMMigration]:
        return db.query(ProductionBatchBOMMigration).filter(
            ProductionBatchBOMMigration.production_batch_id == production_batch_id,
            ProductionBatchBOMMigration.to_bom_version_id == to_bom_version_id
        ).order_by(ProductionBatchBOMMigration.id.desc()).first()


crud_batch_bom_migration = CRUDProductionBatchBOMMigration()


class CRUDBOMDeviation:
    def get(self, db: Session, deviation_id: int) -> Optional[BOMDeviation]:
        return db.query(BOMDeviation).filter(BOMDeviation.id == deviation_id).first()

    def get_by_no(self, db: Session, deviation_no: str) -> Optional[BOMDeviation]:
        return db.query(BOMDeviation).filter(
            BOMDeviation.deviation_no == deviation_no
        ).first()

    def next_deviation_no(self, db: Session) -> str:
        count = db.query(BOMDeviation).count()
        return f"DEV-{datetime.now().strftime('%Y%m%d')}-{count + 1:04d}"

    def list_by_batch(self, db: Session, production_batch_id: int) -> List[BOMDeviation]:
        return db.query(BOMDeviation).filter(
            BOMDeviation.production_batch_id == production_batch_id
        ).order_by(BOMDeviation.id).all()


crud_bom_deviation = CRUDBOMDeviation()


class CRUDProductionMaterialIssue:
    def create(self, db: Session, **fields) -> ProductionMaterialIssue:
        obj = ProductionMaterialIssue(**fields)
        db.add(obj)
        db.commit()
        db.refresh(obj)
        return obj

    def get_by_no(self, db: Session, issue_no: str) -> Optional[ProductionMaterialIssue]:
        return db.query(ProductionMaterialIssue).filter(
            ProductionMaterialIssue.issue_no == issue_no
        ).first()

    def next_issue_no(self, db: Session) -> str:
        count = db.query(ProductionMaterialIssue).count()
        return f"ISS-{datetime.now().strftime('%Y%m%d')}-{count + 1:04d}"

    def list_by_batch(
        self, db: Session, production_batch_id: int
    ) -> List[ProductionMaterialIssue]:
        return db.query(ProductionMaterialIssue).filter(
            ProductionMaterialIssue.production_batch_id == production_batch_id
        ).order_by(ProductionMaterialIssue.id).all()

    def get_issued_map(
        self, db: Session, production_batch_id: int
    ) -> Dict[int, int]:
        rows = self.list_by_batch(db, production_batch_id)
        result: Dict[int, int] = {}
        for row in rows:
            result[row.material_id] = result.get(row.material_id, 0) + row.issued_quantity
        return result


crud_material_issue = CRUDProductionMaterialIssue()
