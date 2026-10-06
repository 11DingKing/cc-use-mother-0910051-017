from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Text, Date
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base

class Material(Base):
    __tablename__ = "materials"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    category = Column(String(50), nullable=False)
    spec = Column(String(200))
    unit = Column(String(20), nullable=False)
    safety_stock = Column(Integer, default=0)
    is_critical = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    bom_items = relationship("BOMItem", back_populates="material")
    supply_capacities = relationship("SupplyCapacity", back_populates="material")
    purchase_suggestions = relationship("PurchaseSuggestion", back_populates="material")
    purchase_orders = relationship("PurchaseOrder", back_populates="material")
    deliveries = relationship("Delivery", back_populates="material")
    inventory_batches = relationship("InventoryBatch", back_populates="material")
    alternative_materials = relationship("AlternativeMaterial", 
                                         foreign_keys="AlternativeMaterial.material_id", 
                                         back_populates="material")
    alternative_for = relationship("AlternativeMaterial",
                                   foreign_keys="AlternativeMaterial.alternative_material_id",
                                   back_populates="alternative_material")

class VehicleModel(Base):
    __tablename__ = "vehicle_models"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    priority = Column(Integer, default=5)
    description = Column(Text)
    status = Column(String(20), default="active")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    bom_items = relationship("BOMItem", back_populates="vehicle_model")
    production_batches = relationship("ProductionBatch", back_populates="vehicle_model")
    alternative_restrictions = relationship("AlternativeMaterialRestriction", back_populates="vehicle_model")
    bom_versions = relationship("BOMVersion", back_populates="vehicle_model")

class BOMItem(Base):
    __tablename__ = "bom_items"
    id = Column(Integer, primary_key=True, index=True)
    vehicle_model_id = Column(Integer, ForeignKey("vehicle_models.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    remark = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    vehicle_model = relationship("VehicleModel", back_populates="bom_items")
    material = relationship("Material", back_populates="bom_items")

class Supplier(Base):
    __tablename__ = "suppliers"
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True, index=True, nullable=False)
    name = Column(String(100), nullable=False)
    contact = Column(String(50))
    phone = Column(String(30))
    address = Column(String(300))
    rating = Column(Float, default=0)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    supply_capacities = relationship("SupplyCapacity", back_populates="supplier")
    purchase_orders = relationship("PurchaseOrder", back_populates="supplier")
    deliveries = relationship("Delivery", back_populates="supplier")

class SupplyCapacity(Base):
    __tablename__ = "supply_capacities"
    id = Column(Integer, primary_key=True, index=True)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    daily_capacity = Column(Integer, nullable=False)
    delivery_days = Column(Integer, nullable=False)
    pass_rate = Column(Float, nullable=False)
    current_stock = Column(Integer, default=0)
    unit_price = Column(Float, default=0)
    is_preferred = Column(Boolean, default=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    supplier = relationship("Supplier", back_populates="supply_capacities")
    material = relationship("Material", back_populates="supply_capacities")

class PurchaseSuggestion(Base):
    __tablename__ = "purchase_suggestions"
    id = Column(Integer, primary_key=True, index=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    suggested_quantity = Column(Integer, nullable=False)
    reason = Column(String(300))
    priority = Column(Integer, default=5)
    suggested_supplier_id = Column(Integer, ForeignKey("suppliers.id"))
    expected_delivery_date = Column(Date)
    status = Column(String(20), default="pending")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    material = relationship("Material", back_populates="purchase_suggestions")

class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    id = Column(Integer, primary_key=True, index=True)
    order_no = Column(String(50), unique=True, index=True, nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    expected_date = Column(Date, nullable=False)
    actual_date = Column(Date)
    status = Column(String(20), default="ordered")
    remark = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    supplier = relationship("Supplier", back_populates="purchase_orders")
    material = relationship("Material", back_populates="purchase_orders")
    deliveries = relationship("Delivery", back_populates="purchase_order")
    delay_impacts = relationship("DelayImpact", back_populates="purchase_order")

class Delivery(Base):
    __tablename__ = "deliveries"
    id = Column(Integer, primary_key=True, index=True)
    delivery_no = Column(String(50), unique=True, index=True, nullable=False)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    delivery_date = Column(Date, nullable=False)
    batch_no = Column(String(50))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    purchase_order = relationship("PurchaseOrder", back_populates="deliveries")
    supplier = relationship("Supplier", back_populates="deliveries")
    material = relationship("Material", back_populates="deliveries")
    inspection = relationship("Inspection", back_populates="delivery", uselist=False)
    inventory_batch = relationship("InventoryBatch", back_populates="delivery", uselist=False)

class Inspection(Base):
    __tablename__ = "inspections"
    id = Column(Integer, primary_key=True, index=True)
    delivery_id = Column(Integer, ForeignKey("deliveries.id"), nullable=False)
    sample_size = Column(Integer, nullable=False)
    defective_count = Column(Integer, default=0)
    pass_rate = Column(Float, nullable=False)
    result = Column(String(20), nullable=False)
    inspector = Column(String(50))
    inspection_date = Column(Date, nullable=False)
    remark = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    delivery = relationship("Delivery", back_populates="inspection")

class InventoryBatch(Base):
    __tablename__ = "inventory_batches"
    id = Column(Integer, primary_key=True, index=True)
    delivery_id = Column(Integer, ForeignKey("deliveries.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    available_quantity = Column(Integer, nullable=False)
    is_quarantined = Column(Boolean, default=False)
    quarantine_reason = Column(String(300))
    location = Column(String(100))
    expire_date = Column(Date)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    delivery = relationship("Delivery", back_populates="inventory_batch")
    material = relationship("Material", back_populates="inventory_batches")

class AlternativeMaterial(Base):
    __tablename__ = "alternative_materials"
    id = Column(Integer, primary_key=True, index=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    alternative_material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    priority = Column(Integer, default=1)
    is_active = Column(Boolean, default=True)
    remark = Column(String(300))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    material = relationship("Material", foreign_keys=[material_id], back_populates="alternative_materials")
    alternative_material = relationship("Material", foreign_keys=[alternative_material_id], back_populates="alternative_for")
    restrictions = relationship("AlternativeMaterialRestriction", back_populates="alternative")

class AlternativeMaterialRestriction(Base):
    __tablename__ = "alternative_restrictions"
    id = Column(Integer, primary_key=True, index=True)
    alternative_id = Column(Integer, ForeignKey("alternative_materials.id"), nullable=False)
    vehicle_model_id = Column(Integer, ForeignKey("vehicle_models.id"), nullable=False)
    is_allowed = Column(Boolean, default=False)
    remark = Column(String(300))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    alternative = relationship("AlternativeMaterial", back_populates="restrictions")
    vehicle_model = relationship("VehicleModel", back_populates="alternative_restrictions")

class ProductionBatch(Base):
    __tablename__ = "production_batches"
    id = Column(Integer, primary_key=True, index=True)
    batch_no = Column(String(50), unique=True, index=True, nullable=False)
    vehicle_model_id = Column(Integer, ForeignKey("vehicle_models.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    plan_date = Column(Date, nullable=False)
    status = Column(String(20), default="planned")
    bom_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=True)
    released_at = Column(DateTime(timezone=True))
    freeze_reason = Column(String(300))
    remark = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    vehicle_model = relationship("VehicleModel", back_populates="production_batches")
    bom_version = relationship("BOMVersion", foreign_keys=[bom_version_id])
    bom_snapshot = relationship(
        "ProductionBatchBOMItem",
        back_populates="production_batch",
        cascade="all, delete-orphan"
    )
    material_issues = relationship(
        "ProductionMaterialIssue",
        back_populates="production_batch",
        cascade="all, delete-orphan"
    )
    bom_deviations = relationship(
        "BOMDeviation",
        back_populates="production_batch",
        cascade="all, delete-orphan"
    )
    bom_migrations = relationship(
        "ProductionBatchBOMMigration",
        back_populates="production_batch",
        cascade="all, delete-orphan"
    )
    delay_impacts = relationship("DelayImpact", back_populates="production_batch")

# 已开工（已进入实物阶段）的批次状态集合：这些批次不允许迁移 BOM，只能走偏差处理
BOM_LOCKED_BATCH_STATUSES = ("started", "issued", "completed", "closed")
# 已下达的批次状态集合：下达即冻结 BOM
BOM_FROZEN_BATCH_STATUSES = ("released",) + BOM_LOCKED_BATCH_STATUSES

class DelayImpact(Base):
    __tablename__ = "delay_impacts"
    id = Column(Integer, primary_key=True, index=True)
    purchase_order_id = Column(Integer, ForeignKey("purchase_orders.id"), nullable=False)
    production_batch_id = Column(Integer, ForeignKey("production_batches.id"), nullable=False)
    impact_level = Column(String(20), nullable=False)
    estimated_delay_days = Column(Integer, default=0)
    remark = Column(String(300))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    purchase_order = relationship("PurchaseOrder", back_populates="delay_impacts")
    production_batch = relationship("ProductionBatch", back_populates="delay_impacts")

class SupplierConfirmation(Base):
    __tablename__ = "supplier_confirmations"
    id = Column(Integer, primary_key=True, index=True)
    confirmation_no = Column(String(50), unique=True, index=True, nullable=False)
    purchase_suggestion_id = Column(Integer, ForeignKey("purchase_suggestions.id"), nullable=False)
    supplier_id = Column(Integer, ForeignKey("suppliers.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    requested_quantity = Column(Integer, nullable=False)
    committed_quantity = Column(Integer, nullable=False)
    committed_delivery_date = Column(Date)
    shortage_quantity = Column(Integer, default=0)
    status = Column(String(20), default="pending")
    confirmation_note = Column(Text)
    confirmed_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    supplier = relationship("Supplier")
    material = relationship("Material")
    purchase_suggestion = relationship("PurchaseSuggestion")
    batches = relationship("SupplierConfirmationBatch", back_populates="confirmation", cascade="all, delete-orphan")
    shortage_impacts = relationship("SupplierShortageImpact", back_populates="confirmation", cascade="all, delete-orphan")

class SupplierConfirmationBatch(Base):
    __tablename__ = "supplier_confirmation_batches"
    id = Column(Integer, primary_key=True, index=True)
    confirmation_id = Column(Integer, ForeignKey("supplier_confirmations.id"), nullable=False)
    batch_no = Column(String(50), nullable=False)
    quantity = Column(Integer, nullable=False)
    planned_date = Column(Date, nullable=False)
    remark = Column(String(300))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    confirmation = relationship("SupplierConfirmation", back_populates="batches")

class SupplierShortageImpact(Base):
    __tablename__ = "supplier_shortage_impacts"
    id = Column(Integer, primary_key=True, index=True)
    confirmation_id = Column(Integer, ForeignKey("supplier_confirmations.id"), nullable=False)
    production_batch_id = Column(Integer, ForeignKey("production_batches.id"), nullable=False)
    affected_vehicle_model_id = Column(Integer, ForeignKey("vehicle_models.id"), nullable=False)
    shortage_material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    shortage_quantity = Column(Integer, nullable=False)
    impact_level = Column(String(20), nullable=False)
    estimated_delay_days = Column(Integer, default=0)
    remark = Column(String(300))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    confirmation = relationship("SupplierConfirmation", back_populates="shortage_impacts")
    production_batch = relationship("ProductionBatch")
    vehicle_model = relationship("VehicleModel")
    material = relationship("Material")


class BOMVersion(Base):
    """BOM 版本：同一车型下生效日期区间 [effective_date, expire_date) 不得重叠。"""
    __tablename__ = "bom_versions"
    id = Column(Integer, primary_key=True, index=True)
    vehicle_model_id = Column(Integer, ForeignKey("vehicle_models.id"), nullable=False, index=True)
    version_no = Column(String(20), nullable=False)
    status = Column(String(20), nullable=False, default="draft")  # draft/submitted/approved/rejected
    change_type = Column(String(20), nullable=False, default="normal")  # normal/emergency/rollback
    effective_date = Column(Date, nullable=False)
    expire_date = Column(Date, nullable=True)  # NULL 表示当前生效的开放区间版本
    change_reason = Column(Text)
    impact_summary = Column(Text)  # 回退/紧急更改时说明对采购建议、库存分配、延期结论的影响
    submitted_by = Column(String(50))
    submitted_at = Column(DateTime(timezone=True))
    approved_by = Column(String(50))
    approved_at = Column(DateTime(timezone=True))
    reject_reason = Column(Text)
    rolled_back_from_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    vehicle_model = relationship("VehicleModel", back_populates="bom_versions")
    items = relationship(
        "BOMVersionItem",
        back_populates="bom_version",
        cascade="all, delete-orphan"
    )
    rolled_back_from = relationship("BOMVersion", remote_side=[id], foreign_keys=[rolled_back_from_version_id])

    UNIQUE_CONSTRAINT_DESCRIPTION = "同一车型版本号唯一"


class BOMVersionItem(Base):
    """BOM 版本明细：版本审批生效后即为不可变的结构与单位用量依据。"""
    __tablename__ = "bom_version_items"
    id = Column(Integer, primary_key=True, index=True)
    bom_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=False, index=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity = Column(Integer, nullable=False)  # 单位用量
    remark = Column(String(200))

    bom_version = relationship("BOMVersion", back_populates="items")
    material = relationship("Material")


class BOMVersionAlternative(Base):
    """版本审批时冻结的替代关系（含车型限制），避免替代规则后续调整影响历史批次。"""
    __tablename__ = "bom_version_alternatives"
    id = Column(Integer, primary_key=True, index=True)
    bom_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=False, index=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    alternative_material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    priority = Column(Integer, default=1)
    is_allowed = Column(Boolean, default=True)
    remark = Column(String(300))

    bom_version = relationship("BOMVersion")
    material = relationship("Material", foreign_keys=[material_id])
    alternative_material = relationship("Material", foreign_keys=[alternative_material_id])


class ProductionBatchBOMItem(Base):
    """生产批次下达时冻结的 BOM 快照（结构 + 单位用量 + 版本来源）。"""
    __tablename__ = "production_batch_bom_items"
    id = Column(Integer, primary_key=True, index=True)
    production_batch_id = Column(Integer, ForeignKey("production_batches.id"), nullable=False, index=True)
    bom_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity = Column(Integer, nullable=False)  # 冻结时的单位用量
    remark = Column(String(200))
    frozen_at = Column(DateTime(timezone=True), server_default=func.now())

    production_batch = relationship("ProductionBatch", back_populates="bom_snapshot")
    bom_version = relationship("BOMVersion")
    material = relationship("Material")


class ProductionBatchBOMMigration(Base):
    """未开工批次迁移到新版本 BOM 的审批与影响评估记录。"""
    __tablename__ = "production_batch_bom_migrations"
    id = Column(Integer, primary_key=True, index=True)
    production_batch_id = Column(Integer, ForeignKey("production_batches.id"), nullable=False, index=True)
    from_bom_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=False)
    to_bom_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=False)
    status = Column(String(20), nullable=False, default="assessed")  # assessed/approved/rejected/migrated
    impact_assessment = Column(Text)       # JSON：采购建议、库存分配、延期结论影响
    approved_by = Column(String(50))
    approved_at = Column(DateTime(timezone=True))
    remark = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    production_batch = relationship("ProductionBatch", back_populates="bom_migrations")
    from_bom_version = relationship("BOMVersion", foreign_keys=[from_bom_version_id])
    to_bom_version = relationship("BOMVersion", foreign_keys=[to_bom_version_id])


class BOMDeviation(Base):
    """已领料/已开工批次的 BOM 偏差处理单：必须可追溯、经审批。"""
    __tablename__ = "bom_deviations"
    id = Column(Integer, primary_key=True, index=True)
    deviation_no = Column(String(50), unique=True, index=True, nullable=False)
    production_batch_id = Column(Integer, ForeignKey("production_batches.id"), nullable=False)
    bom_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=False)
    deviation_type = Column(String(20), nullable=False)  # quantity/substitute/add_material/remove_material
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    substitute_material_id = Column(Integer, ForeignKey("materials.id"), nullable=True)
    frozen_quantity = Column(Integer, nullable=False, default=0)   # 冻结单位用量
    actual_quantity = Column(Integer, nullable=False, default=0)   # 实际单位用量
    quantity_delta = Column(Integer, nullable=False, default=0)    # 单车用量差（实际-冻结）
    reason = Column(Text, nullable=False)
    impact_summary = Column(Text)  # 对采购建议、库存分配、延期结论的影响说明
    status = Column(String(20), nullable=False, default="draft")   # draft/submitted/approved/rejected/applied
    requested_by = Column(String(50))
    approved_by = Column(String(50))
    approved_at = Column(DateTime(timezone=True))
    applied_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    production_batch = relationship("ProductionBatch", back_populates="bom_deviations")
    bom_version = relationship("BOMVersion")
    material = relationship("Material", foreign_keys=[material_id])
    substitute_material = relationship("Material", foreign_keys=[substitute_material_id])


class ProductionMaterialIssue(Base):
    """批次领料/实际消耗记录，用于还原实际消耗与冻结 BOM 的差异。"""
    __tablename__ = "production_material_issues"
    id = Column(Integer, primary_key=True, index=True)
    issue_no = Column(String(50), unique=True, index=True, nullable=False)
    production_batch_id = Column(Integer, ForeignKey("production_batches.id"), nullable=False, index=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    required_quantity = Column(Integer, nullable=False, default=0)  # 按冻结 BOM 应领
    issued_quantity = Column(Integer, nullable=False)              # 实领/实耗
    deviation_id = Column(Integer, ForeignKey("bom_deviations.id"), nullable=True)
    issue_date = Column(Date, nullable=False)
    remark = Column(String(300))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    production_batch = relationship("ProductionBatch", back_populates="material_issues")
    material = relationship("Material")
    deviation = relationship("BOMDeviation")
