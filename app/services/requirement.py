from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from datetime import date, timedelta
from app.crud.vehicle import crud_vehicle, crud_production_batch
from app.crud.material import crud_material
from app.crud.purchase import crud_inventory_batch, crud_purchase_suggestion, crud_purchase_order
from app.schemas import MaterialRequirement
from app.services.bom_versioning import get_batch_bom_quantity_map

# 参与净需求计算的批次：已下达未开工（released）仍需备料；开工/领料后由实耗与偏差接管
DEMAND_BATCH_STATUSES = ("planned", "released")


def _enrich_requirement(db: Session, material_id: int, priority: int) -> Dict:
    material = crud_material.get(db, material_id)
    stock_qty = crud_inventory_batch.get_total_stock(db, material_id)
    pending_qty = crud_purchase_suggestion.get_pending_quantity_by_material(db, material_id)
    in_transit_qty = crud_purchase_order.get_in_transit_quantity_by_material(db, material_id)
    return {
        "material_id": material_id,
        "material_code": material.code if material else "",
        "material_name": material.name if material else "",
        "category": material.category if material else "",
        "required_quantity": 0,
        "stock_quantity": stock_qty,
        "safety_stock": material.safety_stock if material else 0,
        "pending_suggestion_quantity": pending_qty,
        "in_transit_quantity": in_transit_qty,
        "priority": priority,
        "is_critical": material.is_critical if material else False,
    }


def _requirements_to_list(material_requirements: Dict[int, Dict], include_safety_stock: bool) -> List[MaterialRequirement]:
    result = []
    for req in material_requirements.values():
        total_needed = req["required_quantity"]
        if include_safety_stock:
            total_needed += req["safety_stock"]
        gross_shortage = max(0, total_needed - req["stock_quantity"])
        net_shortage = max(0, gross_shortage - req["pending_suggestion_quantity"] - req["in_transit_quantity"])
        result.append(MaterialRequirement(
            material_id=req["material_id"],
            material_code=req["material_code"],
            material_name=req["material_name"],
            category=req["category"],
            required_quantity=req["required_quantity"],
            stock_quantity=req["stock_quantity"],
            safety_stock=req["safety_stock"],
            pending_suggestion_quantity=req["pending_suggestion_quantity"],
            in_transit_quantity=req["in_transit_quantity"],
            gross_shortage=gross_shortage,
            shortage=net_shortage,
            priority=req["priority"],
            is_critical=req["is_critical"],
        ))
    result.sort(key=lambda x: (-x.is_critical, -x.priority, -x.shortage))
    return result


class RequirementService:
    @staticmethod
    def calculate_material_requirements(
        db: Session,
        vehicle_model_priorities: Optional[List[int]] = None,
        include_safety_stock: bool = True
    ) -> List[MaterialRequirement]:
        active_models = crud_vehicle.get_active_models(db)
        if vehicle_model_priorities:
            active_models = [
                m for m in active_models if m.priority in vehicle_model_priorities
            ]
        active_models.sort(key=lambda m: m.priority, reverse=True)
        material_requirements: Dict[int, Dict] = {}
        for vehicle_model in active_models:
            all_batches = crud_production_batch.get_by_vehicle_model(db, vehicle_model.id)
            demand_batches = [b for b in all_batches if b.status in DEMAND_BATCH_STATUSES]
            for batch in demand_batches:
                # 关键：每个批次按其冻结版本（已下达）或计划日生效版本（未下达）计算
                bom_quantity_map = get_batch_bom_quantity_map(db, batch)
                for material_id, per_unit_quantity in bom_quantity_map.items():
                    required_qty = per_unit_quantity * batch.quantity
                    if material_id not in material_requirements:
                        material_requirements[material_id] = _enrich_requirement(
                            db, material_id, vehicle_model.priority
                        )
                    material_requirements[material_id]["required_quantity"] += required_qty
                    if vehicle_model.priority > material_requirements[material_id]["priority"]:
                        material_requirements[material_id]["priority"] = vehicle_model.priority
        return _requirements_to_list(material_requirements, include_safety_stock)

    @staticmethod
    def get_requirements_by_vehicle_model(
        db: Session,
        vehicle_model_id: int
    ) -> List[MaterialRequirement]:
        vehicle_model = crud_vehicle.get(db, vehicle_model_id)
        if not vehicle_model:
            return []
        all_batches = crud_production_batch.get_by_vehicle_model(db, vehicle_model_id)
        demand_batches = [b for b in all_batches if b.status in DEMAND_BATCH_STATUSES]
        material_requirements: Dict[int, Dict] = {}
        for batch in demand_batches:
            bom_quantity_map = get_batch_bom_quantity_map(db, batch)
            for material_id, per_unit_quantity in bom_quantity_map.items():
                if material_id not in material_requirements:
                    material_requirements[material_id] = _enrich_requirement(
                        db, material_id, vehicle_model.priority
                    )
                material_requirements[material_id]["required_quantity"] += (
                    per_unit_quantity * batch.quantity
                )
        return _requirements_to_list(material_requirements, include_safety_stock=True)
