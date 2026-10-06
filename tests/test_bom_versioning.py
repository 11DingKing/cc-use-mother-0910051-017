import pytest
from datetime import date, timedelta
from tests.test_data_factory import DataFactory

from app.services.bom_version import BOMVersionService
from app.services.production_batch import ProductionBatchService
from app.services.requirement import RequirementService
from app.services.bom_resolver import BOMResolver
from app.crud.vehicle import crud_production_batch
from app.crud.bom_version import (
    crud_bom_version, crud_batch_bom_item, crud_batch_migration,
    crud_bom_deviation, crud_material_consumption
)
from app.schemas import BOMVersionCreate, BOMVersionItemCreate, BOMDeviationCreate


@pytest.fixture
def factory(db_session):
    f = DataFactory(db_session)
    f.setup_basic_supply_chain()
    return f


TODAY = date.today()


class TestBOMVersionLifecycle:
    def test_draft_submit_approve_flow(self, factory, db_session):
        v = factory.create_bom_version(
            "TV001", {"TM001": 1, "TM003": 1},
            effective_date=TODAY, approve=False
        )
        assert v.status == "draft"
        v = BOMVersionService.submit_version(db_session, v.id, operator="工程师")
        assert v.status == "submitted"
        v = BOMVersionService.approve_version(db_session, v.id, approved_by="主管")
        assert v.status == "approved"
        assert v.version_no == "V001"

    def test_reject_draft(self, factory, db_session):
        v = factory.create_bom_version(
            "TV001", {"TM001": 1}, effective_date=TODAY, approve=False
        )
        v = BOMVersionService.reject_version(db_session, v.id, "用量填写错误")
        assert v.status == "rejected"
        assert "用量填写错误" in v.reject_reason

    def test_effective_date_intervals_must_not_overlap(self, factory, db_session):
        factory.create_bom_version("TV001", {"TM001": 1}, effective_date=TODAY)
        overlap = factory.create_bom_version(
            "TV001", {"TM001": 2}, effective_date=TODAY, approve=False
        )
        BOMVersionService.submit_version(db_session, overlap.id)
        with pytest.raises(ValueError, match="重叠"):
            BOMVersionService.approve_version(db_session, overlap.id)

    def test_non_overlapping_intervals_allowed(self, factory, db_session):
        v1 = factory.create_bom_version("TV001", {"TM001": 1}, effective_date=TODAY)
        v2 = factory.create_bom_version("TV001", {"TM001": 2}, effective_date=TODAY + timedelta(days=10))
        assert v1.status == "approved"  # 旧版本保持 approved 以支撑历史区间解析
        assert v2.status == "approved"
        # 不同日期解析到不同版本
        assert crud_bom_version.get_effective_version(db_session, v1.vehicle_model_id, TODAY).id == v1.id
        assert crud_bom_version.get_effective_version(db_session, v1.vehicle_model_id, TODAY + timedelta(days=10)).id == v2.id

    def test_emergency_requires_three_impact_notes(self, factory, db_session):
        with pytest.raises(ValueError, match="紧急更改/回退"):
            factory.create_bom_version(
                "TV001", {"TM001": 1},
                effective_date=TODAY, change_type="emergency", approve=False
            )

    def test_emergency_with_impacts_approved(self, factory, db_session):
        v = factory.create_bom_version(
            "TV001", {"TM001": 1},
            effective_date=TODAY, change_type="emergency",
            impacts={
                "purchase_impact": "新增紧急采购建议",
                "inventory_impact": "优先分配库存",
                "delay_impact_note": "无延期"
            }
        )
        assert v.status == "approved"
        assert v.purchase_impact == "新增紧急采购建议"

    def test_diff_added_removed_changed(self, factory, db_session):
        v1 = factory.create_bom_version(
            "TV001", {"TM001": 1, "TM003": 1}, effective_date=TODAY
        )
        v2 = factory.create_bom_version(
            "TV001", {"TM001": 2, "TM002": 1},
            effective_date=TODAY + timedelta(days=5)
        )
        diff = BOMVersionService.get_diff(db_session, v1.vehicle_model_id, v1.id, v2.id)
        by_mat = {d.material_code: d for d in diff.items}
        assert by_mat["TM001"].change_type == "quantity_changed"
        assert by_mat["TM001"].old_quantity == 1
        assert by_mat["TM001"].new_quantity == 2
        assert by_mat["TM003"].change_type == "removed"
        assert by_mat["TM002"].change_type == "added"


class TestBatchFreeze:
    def test_release_freezes_structure_and_quantity(self, factory, db_session):
        v1 = factory.create_bom_version(
            "TV001", {"TM001": 1, "TM003": 1}, effective_date=TODAY
        )
        batch = factory.create_production_batch(
            "FRZ001", "TV001", 100, TODAY + timedelta(days=20)
        )
        released = ProductionBatchService.release_batch(db_session, batch.id)
        assert released.bom_version_id == v1.id
        assert released.status == "released"
        frozen = crud_batch_bom_item.list_by_batch(db_session, batch.id)
        assert {f.material_id: f.quantity_per_unit for f in frozen} == {
            factory.materials["TM001"].id: 1,
            factory.materials["TM003"].id: 1
        }
        assert all(f.required_quantity == 100 for f in frozen)
        # 不可重复下达
        with pytest.raises(ValueError, match="不可重复下达"):
            ProductionBatchService.release_batch(db_session, batch.id)

    def test_bom_change_does_not_recalculate_frozen_batch(self, factory, db_session):
        """核心诉求：BOM 变更后，已下达批次仍按冻结结构计算短缺。"""
        factory.create_bom_version(
            "TV001", {"TM001": 1, "TM003": 1}, effective_date=TODAY
        )
        frozen_batch = factory.create_production_batch(
            "FRZ002", "TV001", 100, TODAY + timedelta(days=20)
        )
        ProductionBatchService.release_batch(db_session, frozen_batch.id)

        # 工程变更：TM001 单位用量 1 -> 2，5天后生效
        factory.create_bom_version(
            "TV001", {"TM001": 2, "TM003": 1},
            effective_date=TODAY + timedelta(days=5)
        )

        # 冻结批次仍按 1 拆解
        frozen_map = BOMResolver.get_batch_bom_map(db_session,
            crud_production_batch.get(db_session, frozen_batch.id))
        assert frozen_map[factory.materials["TM001"].id] == 1

        # 未下达批次 TB001(50, +10天) TB002(30, +30天) 自动按新版本 2 拆解
        tb001 = crud_production_batch.get_by_batch_no(db_session, "TB001")
        assert BOMResolver.get_batch_bom_map(db_session, tb001)[factory.materials["TM001"].id] == 2

        # 合计需求 = 冻结批次 100*1 + 未下达批次 (50+30)*2 = 260
        reqs = RequirementService.calculate_material_requirements(db_session)
        tm001 = next(r for r in reqs if r.material_code == "TM001")
        assert tm001.required_quantity == 260

    def test_unreleased_batch_without_version_uses_latest_approved(self, factory, db_session):
        # 版本生效日晚于计划日时，按最新已审批版本下达
        v = factory.create_bom_version(
            "TV001", {"TM001": 1}, effective_date=TODAY + timedelta(days=30)
        )
        batch = factory.create_production_batch(
            "FRZ003", "TV001", 10, TODAY + timedelta(days=5)
        )
        released = ProductionBatchService.release_batch(db_session, batch.id)
        assert released.bom_version_id == v.id

    def test_release_requires_approved_version(self, factory, db_session):
        factory.create_vehicle_model("TV009", "无BOM车型")
        batch = factory.create_production_batch(
            "FRZ004", "TV009", 10, TODAY + timedelta(days=5)
        )
        with pytest.raises(ValueError, match="尚无已审批"):
            ProductionBatchService.release_batch(db_session, batch.id)


class TestBatchMigration:
    def _release_on_v1(self, db_session, factory, batch_no="MIG001", qty=100):
        factory.create_bom_version(
            "TV001", {"TM001": 1, "TM003": 1}, effective_date=TODAY
        )
        batch = factory.create_production_batch(
            batch_no, "TV001", qty, TODAY + timedelta(days=20)
        )
        ProductionBatchService.release_batch(db_session, batch.id)
        v2 = factory.create_bom_version(
            "TV001", {"TM001": 2, "TM003": 1},
            effective_date=TODAY + timedelta(days=5)
        )
        return batch, v2

    def test_evaluate_impact_covers_purchase_inventory_delay(self, factory, db_session):
        batch, v2 = self._release_on_v1(db_session, factory)
        migration = ProductionBatchService.evaluate_migration(db_session, batch.id, v2.id)
        assert migration.status == "evaluated"
        assert "增量需求100" in migration.purchase_impact
        assert "可用(库存0" in migration.inventory_impact
        # 无在途补充 -> 给出重新评估延期的结论
        assert "延期" in migration.delay_impact
        import json
        rows = {r["material_id"]: r for r in json.loads(migration.impact_detail_json)}
        tm001 = rows[factory.materials["TM001"].id]
        assert tm001["delta"] == 100
        assert tm001["old_required"] == 100
        assert tm001["new_required"] == 200

    def test_migrate_decision_refreezes_batch(self, factory, db_session):
        batch, v2 = self._release_on_v1(db_session, factory)
        migration = ProductionBatchService.evaluate_migration(db_session, batch.id, v2.id)
        done = ProductionBatchService.decide_migration(db_session, migration.id, "migrate", "计划员")
        assert done.status == "migrated"
        refreshed = crud_production_batch.get(db_session, batch.id)
        assert refreshed.bom_version_id == v2.id
        frozen = crud_batch_bom_item.list_by_batch(db_session, batch.id)
        assert {f.material_id: f.quantity_per_unit for f in frozen} == {
            factory.materials["TM001"].id: 2,
            factory.materials["TM003"].id: 1
        }

    def test_reject_migration_keeps_frozen_version(self, factory, db_session):
        v1_id = None
        batch, v2 = self._release_on_v1(db_session, factory, "MIG002")
        migration = ProductionBatchService.evaluate_migration(db_session, batch.id, v2.id)
        v1_id = crud_production_batch.get(db_session, batch.id).bom_version_id
        ProductionBatchService.decide_migration(db_session, migration.id, "reject")
        assert crud_production_batch.get(db_session, batch.id).bom_version_id == v1_id
        assert crud_batch_migration.get(db_session, migration.id).status == "rejected"

    def test_started_batch_cannot_migrate(self, factory, db_session):
        batch, v2 = self._release_on_v1(db_session, factory, "MIG003")
        crud_production_batch.update(
            db_session, db_obj=crud_production_batch.get(db_session, batch.id),
            obj_in={"status": "in_progress"}
        )
        with pytest.raises(ValueError, match="已开工"):
            ProductionBatchService.evaluate_migration(db_session, batch.id, v2.id)


class TestDeviation:
    def _started_batch(self, db_session, factory, batch_no="DEV001", qty=100):
        factory.create_bom_version(
            "TV001", {"TM001": 1, "TM003": 1}, effective_date=TODAY
        )
        batch = factory.create_production_batch(
            batch_no, "TV001", qty, TODAY + timedelta(days=20)
        )
        ProductionBatchService.release_batch(db_session, batch.id)
        crud_production_batch.update(
            db_session, db_obj=crud_production_batch.get(db_session, batch.id),
            obj_in={"status": "in_progress"}
        )
        return crud_production_batch.get(db_session, batch.id)

    def test_deviation_only_for_started_batch(self, factory, db_session):
        factory.create_bom_version("TV001", {"TM001": 1}, effective_date=TODAY)
        batch = factory.create_production_batch("DEV000", "TV001", 10, TODAY + timedelta(days=20))
        ProductionBatchService.release_batch(db_session, batch.id)
        with pytest.raises(ValueError, match="未开工批次请走版本迁移"):
            ProductionBatchService.create_deviation(
                db_session, batch.id,
                BOMDeviationCreate(
                    deviation_type="over_issue", material_id=factory.materials["TM001"].id,
                    actual_quantity=11, reason="超领"
                )
            )

    def test_substitute_deviation_full_flow(self, factory, db_session):
        batch = self._started_batch(db_session, factory)
        # 登记替代关系 TM001 -> TM002（TV001 无限制行，默认允许）
        factory.create_alternative_material("TM001", "TM002", priority=1)
        factory.create_inventory_batch("TM002", 100)

        deviation = ProductionBatchService.create_deviation(
            db_session, batch.id,
            BOMDeviationCreate(
                deviation_type="substitute",
                material_id=factory.materials["TM001"].id,
                substitute_material_id=factory.materials["TM002"].id,
                actual_quantity=0,
                substitute_quantity=100,
                reason="TM001断料，工程批准以TM002替代",
                requested_by="车间主任"
            )
        )
        assert deviation.status == "draft"
        assert deviation.required_quantity == 100

        # 未登记的替代关系必须被拒绝
        with pytest.raises(ValueError):
            ProductionBatchService.create_deviation(
                db_session, batch.id,
                BOMDeviationCreate(
                    deviation_type="substitute",
                    material_id=factory.materials["TM003"].id,
                    substitute_material_id=factory.materials["TM004"].id,
                    substitute_quantity=10, reason="非法替代"
                )
            )

        deviation = ProductionBatchService.approve_deviation(
            db_session, deviation.id, approved_by="工艺工程师",
            purchase_impact="TM001采购建议暂缓100件",
            inventory_impact="TM002库存划拨本批次100件",
            delay_impact="不延期"
        )
        assert deviation.status == "approved"
        applied = ProductionBatchService.apply_deviation(db_session, deviation.id)
        assert applied.status == "applied"
        # 替代料库存被扣减
        from app.crud.purchase import crud_inventory_batch
        assert crud_inventory_batch.get_total_stock(db_session, factory.materials["TM002"].id) == 0

        trace = ProductionBatchService.get_traceability(db_session, batch.id)
        var_by_mat = {row["material_code"]: row for row in trace["consumption"]}
        assert var_by_mat["TM001"]["consumed_quantity"] == 0
        assert var_by_mat["TM001"]["variance_quantity"] == -100
        assert var_by_mat["TM002"]["consumed_quantity"] == 100
        assert var_by_mat["TM002"]["variance_quantity"] == 100
        assert len(trace["deviations"]) == 1

    def test_over_issue_consumption_variance(self, factory, db_session):
        batch = self._started_batch(db_session, factory, "DEV002", qty=50)
        factory.create_inventory_batch("TM001", 120)
        record = ProductionBatchService.record_consumption(
            db_session, batch.id, factory.materials["TM001"].id, 60, remark="损耗补领10件"
        )
        assert record.required_quantity == 50
        trace = ProductionBatchService.get_traceability(db_session, batch.id)
        row = next(r for r in trace["consumption"] if r["material_code"] == "TM001")
        assert row["consumed_quantity"] == 60
        assert row["variance_quantity"] == 10

    def test_deviation_requires_reason(self, factory, db_session):
        batch = self._started_batch(db_session, factory, "DEV003")
        with pytest.raises(ValueError, match="可追溯"):
            ProductionBatchService.create_deviation(
                db_session, batch.id,
                BOMDeviationCreate(
                    deviation_type="short_issue",
                    material_id=factory.materials["TM001"].id,
                    actual_quantity=90, reason="   "
                )
            )


class TestRollback:
    def test_rollback_copies_historical_structure(self, factory, db_session):
        v1 = factory.create_bom_version(
            "TV001", {"TM001": 1, "TM003": 1}, effective_date=TODAY
        )
        factory.create_bom_version(
            "TV001", {"TM001": 2, "TM003": 1},
            effective_date=TODAY + timedelta(days=5)
        )
        # 回退缺少影响说明 -> 拒绝
        with pytest.raises(ValueError):
            BOMVersionService.rollback_to_version(
                db_session, v1.vehicle_model_id, v1.id,
                effective_date=TODAY + timedelta(days=15),
                reason="回退", purchase_impact="", inventory_impact="", delay_impact_note=""
            )
        rb = BOMVersionService.rollback_to_version(
            db_session, v1.vehicle_model_id, v1.id,
            effective_date=TODAY + timedelta(days=15),
            reason="新结构质量问题，回退",
            purchase_impact="撤销增量采购建议100件",
            inventory_impact="释放已分配TM001库存",
            delay_impact_note="回退后恢复原排产，不延期",
            operator="工程经理"
        )
        assert rb.change_type == "rollback"
        assert rb.status == "submitted"
        assert rb.source_version_id == v1.id
        rb = BOMVersionService.approve_version(db_session, rb.id)
        items = {i.material_id: i.quantity for i in rb.items}
        assert items[factory.materials["TM001"].id] == 1  # 复制历史用量
        # 当前生效版本已是回退版本
        current = crud_bom_version.get_effective_version(db_session, v1.vehicle_model_id, TODAY + timedelta(days=20))
        assert current.id == rb.id


class TestTraceability:
    def test_trace_reconstructs_frozen_bom_and_changes(self, factory, db_session):
        v1 = factory.create_bom_version(
            "TV001", {"TM001": 1, "TM003": 1}, effective_date=TODAY
        )
        batch = factory.create_production_batch(
            "TRC001", "TV001", 10, TODAY + timedelta(days=30)
        )
        ProductionBatchService.release_batch(db_session, batch.id)
        v2 = factory.create_bom_version(
            "TV001", {"TM001": 2, "TM003": 1},
            effective_date=TODAY + timedelta(days=5)
        )
        v3 = factory.create_bom_version(
            "TV001", {"TM001": 2, "TM003": 2},
            effective_date=TODAY + timedelta(days=10)
        )
        trace = ProductionBatchService.get_traceability(db_session, batch.id)
        # 原始冻结 BOM
        assert {f.material_id for f in trace["frozen_bom"]} == {
            factory.materials["TM001"].id, factory.materials["TM003"].id
        }
        # 后续变更（冻结版本生效日之后的全部已审批版本）
        later_ids = {v.id for v in trace["subsequent_changes"]}
        assert later_ids == {v2.id, v3.id}
        # 迁移留痕也在追溯链路中
        migration = ProductionBatchService.evaluate_migration(db_session, batch.id, v3.id)
        ProductionBatchService.decide_migration(db_session, migration.id, "migrate")
        trace = ProductionBatchService.get_traceability(db_session, batch.id)
        assert len(trace["migrations"]) == 1
        assert trace["migrations"][0].status == "migrated"
