from sqlalchemy.orm import Session
from typing import List, Dict, Optional
from datetime import date, timedelta
from app.crud.vehicle import crud_vehicle, crud_production_batch
from app.crud.material import crud_material
from app.crud.purchase import crud_inventory_batch, crud_purchase_suggestion, crud_purchase_order
from app.schemas import MaterialRequirement
from app.services.bom_resolver import BOMResolver

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

        # 按车型汇总各物料的毛需求：已下达批次按冻结快照，未下达批次按生效版本，
        # 因此同一车型在 BOM 变更前后下达的批次会按各自版本拆料。
        vehicle_required: Dict[int, Dict[int, int]] = {}
        for vehicle_model in active_models:
            required_map: Dict[int, int] = {}
            # planned=未下达，released=已下达未开工；已领料/完工批次不再计入毛需求
            planned_batches = [
                b for b in crud_production_batch.get_by_vehicle_model(db, vehicle_model.id)
                if b.status in ("planned", "released")
            ]
            for batch in planned_batches:
                bom_map = BOMResolver.get_batch_bom_map(db, batch)
                for material_id, per_unit in bom_map.items():
                    required_map[material_id] = required_map.get(material_id, 0) + per_unit * batch.quantity
            vehicle_required[vehicle_model.id] = required_map

        material_requirements: Dict[int, Dict] = {}
        for vehicle_model in active_models:
            for material_id, required_qty in vehicle_required[vehicle_model.id].items():
                if material_id not in material_requirements:
                    material = crud_material.get(db, material_id)
                    stock_qty = crud_inventory_batch.get_total_stock(db, material_id)
                    pending_qty = crud_purchase_suggestion.get_pending_quantity_by_material(db, material_id)
                    in_transit_qty = crud_purchase_order.get_in_transit_quantity_by_material(db, material_id)
                    material_requirements[material_id] = {
                        "material_id": material_id,
                        "material_code": material.code if material else "",
                        "material_name": material.name if material else "",
                        "category": material.category if material else "",
                        "required_quantity": 0,
                        "stock_quantity": stock_qty,
                        "safety_stock": material.safety_stock if material else 0,
                        "pending_suggestion_quantity": pending_qty,
                        "in_transit_quantity": in_transit_qty,
                        "priority": vehicle_model.priority,
                        "is_critical": material.is_critical if material else False,
                    }
                material_requirements[material_id]["required_quantity"] += required_qty
                if vehicle_model.priority > material_requirements[material_id]["priority"]:
                    material_requirements[material_id]["priority"] = vehicle_model.priority

        result = []
        for mat_id, req in material_requirements.items():
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

    @staticmethod
    def get_requirements_by_vehicle_model(
        db: Session,
        vehicle_model_id: int
    ) -> List[MaterialRequirement]:
        vehicle_model = crud_vehicle.get(db, vehicle_model_id)
        if not vehicle_model:
            return []

        required_map: Dict[int, int] = {}
        planned_batches = [
            b for b in crud_production_batch.get_by_vehicle_model(db, vehicle_model_id)
            if b.status not in ("completed", "closed")
        ]
        for batch in planned_batches:
            bom_map = BOMResolver.get_batch_bom_map(db, batch)
            for material_id, per_unit in bom_map.items():
                required_map[material_id] = required_map.get(material_id, 0) + per_unit * batch.quantity

        result = []
        for material_id, required_qty in required_map.items():
            material = crud_material.get(db, material_id)
            if not material:
                continue
            stock_qty = crud_inventory_batch.get_total_stock(db, material_id)
            pending_qty = crud_purchase_suggestion.get_pending_quantity_by_material(db, material_id)
            in_transit_qty = crud_purchase_order.get_in_transit_quantity_by_material(db, material_id)
            gross_shortage = max(0, required_qty + material.safety_stock - stock_qty)
            net_shortage = max(0, gross_shortage - pending_qty - in_transit_qty)
            result.append(MaterialRequirement(
                material_id=material.id,
                material_code=material.code,
                material_name=material.name,
                category=material.category,
                required_quantity=required_qty,
                stock_quantity=stock_qty,
                safety_stock=material.safety_stock,
                pending_suggestion_quantity=pending_qty,
                in_transit_quantity=in_transit_qty,
                gross_shortage=gross_shortage,
                shortage=net_shortage,
                priority=vehicle_model.priority,
                is_critical=material.is_critical,
            ))
        return result
