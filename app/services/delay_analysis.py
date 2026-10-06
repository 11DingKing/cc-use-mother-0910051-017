from sqlalchemy.orm import Session
from typing import List, Optional, Dict, Tuple
from datetime import date, timedelta
from app.crud.purchase import crud_purchase_order, crud_delay_impact, crud_inventory_batch
from app.crud.vehicle import crud_production_batch
from app.crud.material import crud_material
from app.schemas import DelayImpactAnalysisResult, ProductionBatch, DelayImpactCreate
from app.models import PurchaseOrder, ProductionBatch
from app.services.bom_versioning import (
    get_batch_bom_quantity_map, get_batch_alternative_rules
)

# 延期分析只考虑未开工的备料批次：已开工/已领料批次以偏差和实耗为准
DELAY_ANALYSIS_BATCH_STATUSES = ("planned", "released")


def _batch_available_alternatives(db: Session, batch, material_id: int) -> Dict:
    """按批次冻结的替代关系（未下达批次按当前规则）返回可用替代料及库存。"""
    rules = get_batch_alternative_rules(db, batch).get(material_id, [])
    available = []
    total_stock = 0
    for rule in rules:
        if not rule["is_allowed"]:
            continue
        alt_material_id = rule["material_id"]
        alt_stock = crud_inventory_batch.get_total_stock(db, alt_material_id)
        if alt_stock > 0:
            alt_mat = crud_material.get(db, alt_material_id)
            available.append({
                "alternative_id": None,
                "material_id": alt_material_id,
                "material_name": alt_mat.name if alt_mat else "未知",
                "stock": alt_stock,
                "priority": rule["priority"],
            })
            total_stock += alt_stock
    available.sort(key=lambda a: a["priority"])
    return {"available": available, "total_stock": total_stock}


class DelayAnalysisService:
    @staticmethod
    def analyze_delay_impact(
        db: Session,
        purchase_order_id: int,
        new_expected_date: Optional[date] = None,
        delay_days: Optional[int] = None
    ) -> DelayImpactAnalysisResult:
        purchase_order = crud_purchase_order.get(db, purchase_order_id)
        if not purchase_order:
            raise ValueError(f"采购订单不存在: {purchase_order_id}")

        original_date = purchase_order.expected_date
        if new_expected_date:
            actual_delay_days = (new_expected_date - original_date).days
        elif delay_days:
            actual_delay_days = delay_days
            new_expected_date = original_date + timedelta(days=delay_days)
        else:
            actual_delay_days = 0
            new_expected_date = original_date

        material_id = purchase_order.material_id
        material = purchase_order.material
        material_name = material.name if material else "未知物料"

        if actual_delay_days <= 0:
            crud_delay_impact.delete_by_purchase_order(db, purchase_order_id)
            return DelayImpactAnalysisResult(
                purchase_order_id=purchase_order_id,
                material_name=material_name,
                affected_batches=[],
                total_affected_quantity=0,
                impact_level="none",
                estimated_delay_days=0,
                remark="无延期影响",
                analysis_details=[],
                material_balance={}
            )

        current_stock = crud_inventory_batch.get_total_stock(db, material_id)

        in_transit_orders = crud_purchase_order.get_in_transit_by_material_ordered_by_date(db, material_id)

        adjusted_in_transit = []
        for order in in_transit_orders:
            if order["order_id"] == purchase_order_id:
                adjusted_in_transit.append({
                    "order_id": order["order_id"],
                    "expected_date": new_expected_date,
                    "remaining_quantity": order["remaining_quantity"]
                })
            else:
                adjusted_in_transit.append(order)

        adjusted_in_transit.sort(key=lambda x: x["expected_date"])

        # 逐批次按其冻结版本（已下达）或计划日生效版本（未下达）确定单位用量
        all_batches = crud_production_batch.get_batches_for_supply_sorted(db)
        batch_bom_quantity: Dict[int, int] = {}
        for batch in all_batches:
            bom_map = get_batch_bom_quantity_map(db, batch)
            if material_id in bom_map:
                batch_bom_quantity[batch.id] = bom_map[material_id]

        if not batch_bom_quantity:
            crud_delay_impact.delete_by_purchase_order(db, purchase_order_id)
            return DelayImpactAnalysisResult(
                purchase_order_id=purchase_order_id,
                material_name=material_name,
                affected_batches=[],
                total_affected_quantity=0,
                impact_level="none",
                estimated_delay_days=0,
                remark="无批次使用该物料（按各批次冻结/生效BOM判定）",
                analysis_details=[],
                material_balance={
                    "current_stock": current_stock,
                    "in_transit_orders": adjusted_in_transit
                }
            )

        relevant_batches = [
            b for b in all_batches
            if b.id in batch_bom_quantity
        ]

        relevant_batches.sort(key=lambda b: (
            b.plan_date,
            -b.vehicle_model.priority if b.vehicle_model else 0
        ))

        # 替代料可用性按批次冻结的替代关系逐个计算
        alternatives_data: Dict[int, Dict] = {}
        for batch in relevant_batches:
            alternatives_data[batch.id] = _batch_available_alternatives(
                db, batch, material_id
            )

        class MaterialPool:
            def __init__(self, initial_stock: int, in_transit: List[dict]):
                self.stock = initial_stock
                self.in_transit = in_transit
                self.alt_stock: Dict[int, int] = {}

            def get_available_by(self, check_date: date) -> int:
                available = self.stock
                for order in self.in_transit:
                    if order["expected_date"] <= check_date:
                        available += order["remaining_quantity"]
                return available

            def consume(self, amount: int, consume_date: date) -> Tuple[int, List[dict]]:
                consumed_details = []
                remaining = amount

                if self.stock >= remaining:
                    self.stock -= remaining
                    consumed_details.append({"source": "stock", "quantity": remaining})
                    return remaining, consumed_details

                consumed_from_stock = self.stock
                remaining -= self.stock
                self.stock = 0
                if consumed_from_stock > 0:
                    consumed_details.append({"source": "stock", "quantity": consumed_from_stock})

                for order in self.in_transit:
                    if remaining <= 0:
                        break
                    if order["expected_date"] <= consume_date and order["remaining_quantity"] > 0:
                        take = min(remaining, order["remaining_quantity"])
                        order["remaining_quantity"] -= take
                        remaining -= take
                        consumed_details.append({
                            "source": "in_transit",
                            "order_id": order["order_id"],
                            "expected_date": order["expected_date"].isoformat(),
                            "quantity": take
                        })

                return amount - remaining, consumed_details

            def get_earliest_available_date(self, required_qty: int, start_date: date) -> Optional[date]:
                available = self.stock
                if available >= required_qty:
                    return start_date

                cumulative = available
                for order in sorted(self.in_transit, key=lambda x: x["expected_date"]):
                    if order["remaining_quantity"] <= 0:
                        continue
                    cumulative += order["remaining_quantity"]
                    if cumulative >= required_qty:
                        return max(order["expected_date"], start_date)

                return None

        pool = MaterialPool(current_stock, adjusted_in_transit)
        for batch_alt in alternatives_data.values():
            for alt in batch_alt["available"]:
                if alt["material_id"] not in pool.alt_stock:
                    pool.alt_stock[alt["material_id"]] = alt["stock"]

        affected_batches: List[ProductionBatch] = []
        analysis_details = []
        delayed_orders_count = 0

        for batch in relevant_batches:
            vm_id = batch.vehicle_model_id
            bom_qty = batch_bom_quantity.get(batch.id, 0)
            required_qty = bom_qty * batch.quantity

            batch_detail = {
                "batch_id": batch.id,
                "batch_no": batch.batch_no,
                "vehicle_model_id": vm_id,
                "vehicle_model_name": batch.vehicle_model.name if batch.vehicle_model else "未知",
                "plan_date": batch.plan_date.isoformat(),
                "quantity": batch.quantity,
                "priority": batch.vehicle_model.priority if batch.vehicle_model else 0,
                "required_material_qty": required_qty,
                "bom_qty_per_unit": bom_qty
            }

            available_on_time = pool.get_available_by(batch.plan_date)

            if available_on_time >= required_qty:
                consumed, details = pool.consume(required_qty, batch.plan_date)
                batch_detail["status"] = "covered"
                batch_detail["cover_details"] = details
                analysis_details.append(batch_detail)
                continue

            alt_data = alternatives_data.get(batch.id, {})
            total_alt_stock = alt_data.get("total_stock", 0)
            shortfall = required_qty - available_on_time

            if total_alt_stock >= shortfall:
                consumed_main, main_details = pool.consume(available_on_time, batch.plan_date)
                remaining_alt_need = required_qty - consumed_main

                alt_consumed_details = []
                alts_available = alt_data.get("available", [])
                alts_available.sort(key=lambda a: a["priority"])
                for alt in alts_available:
                    if remaining_alt_need <= 0:
                        break
                    alt_id = alt["material_id"]
                    take = min(remaining_alt_need, pool.alt_stock.get(alt_id, 0))
                    if take > 0:
                        pool.alt_stock[alt_id] -= take
                        remaining_alt_need -= take
                        alt_consumed_details.append({
                            "alternative_material_id": alt_id,
                            "alternative_material_name": alt["material_name"],
                            "quantity": take
                        })

                batch_detail["status"] = "covered_by_alternative"
                batch_detail["cover_details"] = main_details
                batch_detail["alternative_used"] = alt_consumed_details
                analysis_details.append(batch_detail)
                continue

            earliest_date = pool.get_earliest_available_date(required_qty, batch.plan_date)
            if earliest_date and earliest_date > batch.plan_date:
                delay_days_for_batch = (earliest_date - batch.plan_date).days
                pool.consume(available_on_time, batch.plan_date)
                for order in pool.in_transit:
                    if order["expected_date"] <= earliest_date and order["remaining_quantity"] > 0:
                        take = min(required_qty - pool.stock, order["remaining_quantity"])
                        order["remaining_quantity"] -= take
                        pool.stock += take

                affected_batches.append(batch)
                delayed_orders_count += 1
                batch_detail["status"] = "delayed"
                batch_detail["delay_days"] = delay_days_for_batch
                batch_detail["shortfall_qty"] = required_qty - available_on_time
                batch_detail["earliest_available_date"] = earliest_date.isoformat()
                batch_detail["reason"] = f"物料缺口{required_qty - available_on_time}，最早{earliest_date.isoformat()}可用，延期{delay_days_for_batch}天"
                analysis_details.append(batch_detail)
            else:
                pool.consume(available_on_time, batch.plan_date)
                batch_detail["status"] = "insufficient_supply"
                batch_detail["shortfall_qty"] = required_qty - available_on_time
                batch_detail["reason"] = "总体供应不足，无法满足需求"
                affected_batches.append(batch)
                delayed_orders_count += 1
                analysis_details.append(batch_detail)

        affected_batches = list({b.id: b for b in affected_batches}.values())
        total_affected_qty = sum(b.quantity for b in affected_batches)

        if delayed_orders_count == 0:
            impact_level = "none"
            remark = f"延期{actual_delay_days}天，但库存、在途及替代料可覆盖所有批次，无实际影响"
        elif actual_delay_days <= 3 and total_affected_qty < 100:
            impact_level = "low"
            remark = f"延期{actual_delay_days}天，影响{len(affected_batches)}个批次共{total_affected_qty}台车，影响较小"
        elif actual_delay_days <= 7 or total_affected_qty < 500:
            impact_level = "medium"
            remark = f"延期{actual_delay_days}天，影响{len(affected_batches)}个批次共{total_affected_qty}台车，需关注"
        else:
            impact_level = "high"
            remark = f"延期{actual_delay_days}天，影响{len(affected_batches)}个批次共{total_affected_qty}台车，严重影响，请立即处理"

        crud_delay_impact.delete_by_purchase_order(db, purchase_order_id)
        for batch in affected_batches:
            batch_detail = next(
                (d for d in analysis_details if d["batch_id"] == batch.id),
                None
            )
            batch_delay_days = batch_detail.get("delay_days", actual_delay_days) if batch_detail else actual_delay_days
            batch_remark = batch_detail.get("reason", remark) if batch_detail else remark
            impact_in = DelayImpactCreate(
                purchase_order_id=purchase_order_id,
                production_batch_id=batch.id,
                impact_level=impact_level,
                estimated_delay_days=batch_delay_days,
                remark=batch_remark
            )
            crud_delay_impact.create(db, obj_in=impact_in)

        material_balance = {
            "current_stock": current_stock,
            "remaining_stock": pool.stock,
            "in_transit_orders": [
                {
                    "order_id": o["order_id"],
                    "expected_date": o["expected_date"].isoformat(),
                    "remaining_quantity": o["remaining_quantity"]
                }
                for o in adjusted_in_transit
            ],
            "alternative_stock_remaining": pool.alt_stock,
            "total_in_transit_qty": sum(o["remaining_quantity"] for o in adjusted_in_transit)
        }

        return DelayImpactAnalysisResult(
            purchase_order_id=purchase_order_id,
            material_name=material_name,
            affected_batches=affected_batches,
            total_affected_quantity=total_affected_qty,
            impact_level=impact_level,
            estimated_delay_days=actual_delay_days,
            remark=remark,
            analysis_details=analysis_details,
            material_balance=material_balance
        )

    @staticmethod
    def get_affected_batches(db: Session, purchase_order_id: int) -> List[ProductionBatch]:
        impacts = crud_delay_impact.get_by_purchase_order(db, purchase_order_id)
        return [impact.production_batch for impact in impacts if impact.production_batch]

    @staticmethod
    def get_impacted_purchase_orders(db: Session, production_batch_id: int) -> List[PurchaseOrder]:
        impacts = crud_delay_impact.get_by_production_batch(db, production_batch_id)
        return [impact.purchase_order for impact in impacts if impact.purchase_order]
