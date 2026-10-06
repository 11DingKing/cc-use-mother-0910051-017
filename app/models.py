from sqlalchemy import Column, Integer, String, Float, DateTime, ForeignKey, Boolean, Text, Date, UniqueConstraint
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
    bom_versions = relationship("BOMVersion", back_populates="vehicle_model")
    production_batches = relationship("ProductionBatch", back_populates="vehicle_model")
    alternative_restrictions = relationship("AlternativeMaterialRestriction", back_populates="vehicle_model")

class BOMItem(Base):
    """历史遗留的直接维护式 BOM，仅用于种子兼容；业务一律使用 BOMVersion/BOMVersionItem。"""
    __tablename__ = "bom_items"
    id = Column(Integer, primary_key=True, index=True)
    vehicle_model_id = Column(Integer, ForeignKey("vehicle_models.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    remark = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    vehicle_model = relationship("VehicleModel", back_populates="bom_items")
    material = relationship("Material", back_populates="bom_items")

class BOMVersion(Base):
    """车型 BOM 的版本头：带生效日期与审批状态，生效区间互不重叠。"""
    __tablename__ = "bom_versions"
    id = Column(Integer, primary_key=True, index=True)
    vehicle_model_id = Column(Integer, ForeignKey("vehicle_models.id"), nullable=False, index=True)
    version_no = Column(String(30), nullable=False)
    change_type = Column(String(20), nullable=False, default="normal")  # normal/emergency/rollback
    status = Column(String(20), nullable=False, default="draft")  # draft/submitted/approved/rejected/archived
    effective_date = Column(Date, nullable=False)
    reason = Column(Text)
    source_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=True)  # 复制/回退来源
    superseded_by_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=True)
    # 回退或紧急更改必须说明三类影响
    purchase_impact = Column(Text)
    inventory_impact = Column(Text)
    delay_impact_note = Column(Text)
    submitted_by = Column(String(50))
    submitted_at = Column(DateTime(timezone=True))
    approved_by = Column(String(50))
    approved_at = Column(DateTime(timezone=True))
    reject_reason = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    __table_args__ = (
        UniqueConstraint("vehicle_model_id", "version_no", name="uq_bom_version_no"),
    )

    vehicle_model = relationship(
        "VehicleModel", back_populates="bom_versions",
        foreign_keys=[vehicle_model_id]
    )
    items = relationship(
        "BOMVersionItem", back_populates="version",
        cascade="all, delete-orphan"
    )
    source_version = relationship(
        "BOMVersion", remote_side=[id], foreign_keys=[source_version_id]
    )

class BOMVersionItem(Base):
    """BOM 版本行：版本内的物料及单位用量，按 (版本, 物料) 唯一。"""
    __tablename__ = "bom_version_items"
    id = Column(Integer, primary_key=True, index=True)
    version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=False, index=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity = Column(Integer, nullable=False)
    change_flag = Column(String(20), nullable=False, default="unchanged")  # added/removed/quantity_changed/unchanged
    remark = Column(String(200))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("version_id", "material_id", name="uq_bom_version_item_material"),
    )

    version = relationship("BOMVersion", back_populates="items")
    material = relationship("Material")

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
    status = Column(String(20), default="planned")  # planned/in_progress/completed/closed
    remark = Column(Text)
    # 批次下达时冻结所采用的 BOM 版本；之后 BOM 变更不再影响本批次
    bom_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=True)
    frozen_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    vehicle_model = relationship("VehicleModel", back_populates="production_batches")
    bom_version = relationship("BOMVersion", foreign_keys=[bom_version_id])
    frozen_bom_items = relationship(
        "BatchBOMItem", back_populates="production_batch",
        cascade="all, delete-orphan"
    )
    migrations = relationship(
        "ProductionBatchMigration", back_populates="production_batch",
        cascade="all, delete-orphan"
    )
    deviations = relationship(
        "ProductionBOMDeviation", back_populates="production_batch",
        cascade="all, delete-orphan"
    )
    consumptions = relationship(
        "MaterialConsumption", back_populates="production_batch",
        cascade="all, delete-orphan"
    )
    delay_impacts = relationship("DelayImpact", back_populates="production_batch")

class BatchBOMItem(Base):
    """生产批次下达时冻结的 BOM 结构与单位用量，不可变。"""
    __tablename__ = "batch_bom_items"
    id = Column(Integer, primary_key=True, index=True)
    production_batch_id = Column(Integer, ForeignKey("production_batches.id"), nullable=False, index=True)
    bom_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=False)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    quantity_per_unit = Column(Integer, nullable=False)
    required_quantity = Column(Integer, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("production_batch_id", "material_id", name="uq_batch_bom_material"),
    )

    production_batch = relationship("ProductionBatch", back_populates="frozen_bom_items")
    bom_version = relationship("BOMVersion")
    material = relationship("Material")

class ProductionBatchMigration(Base):
    """未开工批次迁移到新 BOM 版本的影响评估与决策记录。"""
    __tablename__ = "production_batch_migrations"
    id = Column(Integer, primary_key=True, index=True)
    production_batch_id = Column(Integer, ForeignKey("production_batches.id"), nullable=False, index=True)
    from_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=False)
    to_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=False)
    status = Column(String(20), nullable=False, default="evaluated")  # evaluated/migrated/rejected
    impact_summary = Column(Text)
    purchase_impact = Column(Text)
    inventory_impact = Column(Text)
    delay_impact = Column(Text)
    impact_detail_json = Column(Text)
    decided_by = Column(String(50))
    decided_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    production_batch = relationship("ProductionBatch", back_populates="migrations")
    from_version = relationship("BOMVersion", foreign_keys=[from_version_id])
    to_version = relationship("BOMVersion", foreign_keys=[to_version_id])

class ProductionBOMDeviation(Base):
    """已领料批次不能迁移版本，只能走可追溯的偏差处理。"""
    __tablename__ = "production_bom_deviations"
    id = Column(Integer, primary_key=True, index=True)
    deviation_no = Column(String(50), unique=True, index=True, nullable=False)
    production_batch_id = Column(Integer, ForeignKey("production_batches.id"), nullable=False, index=True)
    bom_version_id = Column(Integer, ForeignKey("bom_versions.id"), nullable=False)
    deviation_type = Column(String(30), nullable=False)  # substitute/over_issue/short_issue/engineering_change
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)  # 冻结BOM物料
    substitute_material_id = Column(Integer, ForeignKey("materials.id"), nullable=True)  # 替代料
    required_quantity = Column(Integer, nullable=False)  # 冻结BOM应耗
    actual_quantity = Column(Integer, nullable=False, default=0)  # 实际领用(原物料)
    substitute_quantity = Column(Integer, nullable=False, default=0)
    reason = Column(Text, nullable=False)
    purchase_impact = Column(Text)
    inventory_impact = Column(Text)
    delay_impact = Column(Text)
    status = Column(String(20), nullable=False, default="draft")  # draft/approved/applied/rejected
    requested_by = Column(String(50))
    approved_by = Column(String(50))
    approved_at = Column(DateTime(timezone=True))
    applied_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    production_batch = relationship("ProductionBatch", back_populates="deviations")
    bom_version = relationship("BOMVersion")
    material = relationship("Material", foreign_keys=[material_id])
    substitute_material = relationship("Material", foreign_keys=[substitute_material_id])

class MaterialConsumption(Base):
    """批次实际领料/消耗记录，用于还原 BOM 与实际消耗差异。"""
    __tablename__ = "material_consumptions"
    id = Column(Integer, primary_key=True, index=True)
    production_batch_id = Column(Integer, ForeignKey("production_batches.id"), nullable=False, index=True)
    material_id = Column(Integer, ForeignKey("materials.id"), nullable=False)
    required_quantity = Column(Integer, nullable=False, default=0)  # 快照应耗
    consumed_quantity = Column(Integer, nullable=False, default=0)
    source = Column(String(30), nullable=False, default="manual")  # manual/deviation
    deviation_id = Column(Integer, ForeignKey("production_bom_deviations.id"), nullable=True)
    remark = Column(String(300))
    consumed_at = Column(DateTime(timezone=True), server_default=func.now())
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    production_batch = relationship("ProductionBatch", back_populates="consumptions")
    material = relationship("Material")
    deviation = relationship("ProductionBOMDeviation", foreign_keys=[deviation_id])

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
