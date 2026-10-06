import json
import pytest
from datetime import date, timedelta

from tests.test_data_factory import DataFactory
from app.services.bom_versioning import BOMVersionService, BatchBOMService
from app.services.requirement import RequirementService
from app.services.delay_analysis import DelayAnalysisService
from app.crud.vehicle import crud_production_batch, crud_vehicle
from app.crud.bom import (
    crud_bom_version, crud_batch_bom_item, crud_batch_bom_migration,
    crud_bom_deviation, crud_material_issue,
)
from app.schemas import (
    BOMVersionCreate, BOMVersionApprove, BOMVersionReject,
    BatchReleaseRequest, BOMMigrationApprove,
    BOMDeviationCreate, BOMDeviationApprove,
    BatchIssueMaterialRequest,
)


@pytest.fixture
def factory(db_session):
    f = DataFactory(db_session)
    f.setup_basic_supply_chain()
    return f


def _baseline_version(db, vehicle_code, factory):
    return crud_bom_version.get_effective_on(
        db, factory.vehicle_models[vehicle_code].id, date(2026, 1, 1)
    )


def _create_approve_version(db, vehicle_id, effective, items, *,
                            change_type="normal", impact=None, submitted_by="工程",
                            approved_by="工程经理"):
    version = BOMVersionService.create_version(db, BOMVersionCreate(
        vehicle_model_id=vehicle_id,
        effective_date=effective,
        change_type=change_type,
        change_reason="测试变更",
        items=items,
    ))
    BOMVersionService.submit_version(db, version.id, submitted_by)
    return BOMVersionService.approve_version(db, version.id, BOMVersionApprove(
        approved_by=approved_by, impact_summary=impact
    ))


class TestBOMVersioningLifecycle:
    """版本、审批状态与生效区间。"""

    def test_baseline_version_created_from_legacy_bom(self, db_session, factory):
        # 存量BOM通过工厂写入时已自动建立基线版本
        baseline = _baseline_version(db_session, "TV001", factory)
        assert baseline is not None
        assert baseline.status == "approved"
        from app.crud.bom import crud_bom_version_item
        qmap = crud_bom_version_item.get_quantity_map(db_session, baseline.id)
        assert qmap == {
            factory.materials["TM001"].id: 1,
            factory.materials["TM003"].id: 1,
        }

    def test_approve_truncates_previous_open_interval(self, db_session, factory):
        vm = factory.vehicle_models["TV001"]
        baseline = _baseline_version(db_session, "TV001", factory)
        new_eff = date.today() + timedelta(days=15)
        v2 = _create_approve_version(db_session, vm.id, new_eff, [
            {"material_id": factory.materials["TM001"].id, "quantity": 2},
            {"material_id": factory.materials["TM003"].id, "quantity": 1},
        ])
        db_session.refresh(baseline)
        assert baseline.expire_date == new_eff  # 相邻截断
        assert v2.expire_date is None
        # 区间内任意日期恰好命中一个版本
        assert crud_bom_version.get_effective_on(
            db_session, vm.id, new_eff - timedelta(days=1)
        ).id == baseline.id
        assert crud_bom_version.get_effective_on(db_session, vm.id, new_eff).id == v2.id

    def test_draft_versions_do_not_affect_effective_bom(self, db_session, factory):
        vm = factory.vehicle_models["TV001"]
        draft = BOMVersionService.create_version(db_session, BOMVersionCreate(
            vehicle_model_id=vm.id,
            effective_date=date.today() + timedelta(days=1),
            items=[{"material_id": factory.materials["TM001"].id, "quantity": 9}],
        ))
        assert draft.status == "draft"
        # 未审批：当前生效仍是基线
        current = crud_bom_version.get_effective_on(db_session, vm.id, date.today() + timedelta(days=10))
        assert current.id == _baseline_version(db_session, "TV001", factory).id

    def test_overlapping_approved_intervals_rejected(self, db_session, factory):
        vm = factory.vehicle_models["TV001"]
        # V2 在 +15 天生效，截断基线
        _create_approve_version(db_session, vm.id, date.today() + timedelta(days=15), [
            {"material_id": factory.materials["TM001"].id, "quantity": 2},
            {"material_id": factory.materials["TM003"].id, "quantity": 1},
        ])
        # V3 想在 +10 天生效：落在基线区间 [2000, +15) 且早于开放版本 → 拒绝
        v3 = BOMVersionService.create_version(db_session, BOMVersionCreate(
            vehicle_model_id=vm.id,
            effective_date=date.today() + timedelta(days=10),
            items=[{"material_id": factory.materials["TM001"].id, "quantity": 3}],
        ))
        BOMVersionService.submit_version(db_session, v3.id, "工程")
        with pytest.raises(ValueError, match="重叠|早于"):
            BOMVersionService.approve_version(db_session, v3.id, BOMVersionApprove(
                approved_by="经理"
            ))

    def test_same_effective_date_as_open_version_rejected(self, db_session, factory):
        vm = factory.vehicle_models["TV001"]
        eff = date.today() + timedelta(days=12)
        _create_approve_version(db_session, vm.id, eff, [
            {"material_id": factory.materials["TM001"].id, "quantity": 2},
            {"material_id": factory.materials["TM003"].id, "quantity": 1},
        ])
        v3 = BOMVersionService.create_version(db_session, BOMVersionCreate(
            vehicle_model_id=vm.id, effective_date=eff,
            items=[{"material_id": factory.materials["TM001"].id, "quantity": 3}],
        ))
        BOMVersionService.submit_version(db_session, v3.id, "工程")
        with pytest.raises(ValueError, match="相同"):
            BOMVersionService.approve_version(db_session, v3.id, BOMVersionApprove(
                approved_by="经理"
            ))

    def test_emergency_and_rollback_require_impact_summary(self, db_session, factory):
        vm = factory.vehicle_models["TV001"]
        # 紧急更改缺影响说明
        em = BOMVersionService.create_version(db_session, BOMVersionCreate(
            vehicle_model_id=vm.id, effective_date=date.today(),
            change_type="emergency", change_reason="产线紧急切换",
            items=[{"material_id": factory.materials["TM001"].id, "quantity": 2},
                   {"material_id": factory.materials["TM003"].id, "quantity": 1}],
        ))
        BOMVersionService.submit_version(db_session, em.id, "工程")
        with pytest.raises(ValueError, match="影响说明"):
            BOMVersionService.approve_version(db_session, em.id, BOMVersionApprove(
                approved_by="经理"
            ))
        approved = BOMVersionService.approve_version(db_session, em.id, BOMVersionApprove(
            approved_by="经理",
            impact_summary="采购建议：TM001追加50；库存：优先保供TB001；延期结论：不变"
        ))
        assert approved.change_type == "emergency"
        assert "采购建议" in approved.impact_summary

        # 回退必须带基准版本
        with pytest.raises(ValueError, match="回退"):
            BOMVersionService.create_version(db_session, BOMVersionCreate(
                vehicle_model_id=vm.id, effective_date=date.today() + timedelta(days=2),
                change_type="rollback", items=[],
            ))


class TestBatchFreeze:
    """批次下达冻结结构与单位用量。"""

    def test_release_freezes_bom_snapshot(self, db_session, factory):
        batch = factory.production_batches["TB001"]  # plan +10天，50台
        baseline = _baseline_version(db_session, "TV001", factory)
        released = BatchBOMService.release_batch(
            db_session, batch.id, BatchReleaseRequest(operator="计划员")
        )
        assert released.status == "released"
        assert released.bom_version_id == baseline.id
        assert released.released_at is not None
        snapshot = crud_batch_bom_item.get_by_batch(db_session, batch.id)
        assert {r.material_id: r.quantity for r in snapshot} == {
            factory.materials["TM001"].id: 1,
            factory.materials["TM003"].id: 1,
        }
        # 重复下达拒绝
        with pytest.raises(ValueError, match="重复冻结"):
            BatchBOMService.release_batch(db_session, batch.id, BatchReleaseRequest())

    def test_frozen_batch_keeps_old_requirement_after_bom_change(self, db_session, factory):
        """核心诉求：已下达批次不随后续BOM维护重算短缺。"""
        b1 = factory.production_batches["TB001"]  # +10天 50台
        b2 = factory.production_batches["TB002"]  # +30天 30台
        BatchBOMService.release_batch(db_session, b1.id, BatchReleaseRequest())
        # 新版本 TM001=3/台，+15天生效：TB001已冻结1/台，TB002按计划日读到3/台
        _create_approve_version(
            db_session, factory.vehicle_models["TV001"].id,
            date.today() + timedelta(days=15),
            [{"material_id": factory.materials["TM001"].id, "quantity": 3},
             {"material_id": factory.materials["TM003"].id, "quantity": 1}],
        )
        reqs = RequirementService.calculate_material_requirements(db_session)
        tm001 = next(r for r in reqs if r.material_code == "TM001")
        # 冻结的 TB001：1*50=50；未下达的 TB002 按新版本：3*30=90
        assert tm001.required_quantity == 140

    def test_planned_batch_uses_version_effective_on_plan_date(self, db_session, factory):
        # 不做下达：+10天的批次命中旧版，+30天的批次命中新版
        _create_approve_version(
            db_session, factory.vehicle_models["TV001"].id,
            date.today() + timedelta(days=15),
            [{"material_id": factory.materials["TM001"].id, "quantity": 2},
             {"material_id": factory.materials["TM003"].id, "quantity": 1}],
        )
        reqs = RequirementService.get_requirements_by_vehicle_model(
            db_session, factory.vehicle_models["TV001"].id
        )
        tm001 = next(r for r in reqs if r.material_code == "TM001")
        # TB001(+10) 1*50 + TB002(+30) 2*30 = 110
        assert tm001.required_quantity == 110

    def test_cannot_release_without_effective_version(self, db_session, factory):
        vm = factory.create_vehicle_model("TV999", "无BOM车型", priority=1)
        batch = factory.create_production_batch(
            "TB999", "TV999", 10, date.today() + timedelta(days=5)
        )
        with pytest.raises(ValueError, match="没有已审批生效的BOM版本"):
            BatchBOMService.release_batch(db_session, batch.id, BatchReleaseRequest())


class TestBatchMigration:
    """未开工批次的影响评估与迁移。"""

    def _prepare(self, db, factory):
        b1 = factory.production_batches["TB001"]
        BatchBOMService.release_batch(db, b1.id, BatchReleaseRequest())
        v2 = _create_approve_version(
            db, factory.vehicle_models["TV001"].id,
            date.today() + timedelta(days=15),
            [{"material_id": factory.materials["TM001"].id, "quantity": 4},
             {"material_id": factory.materials["TM003"].id, "quantity": 1}],
        )
        return b1, v2

    def test_assess_and_approve_migration(self, db_session, factory):
        b1, v2 = self._prepare(db_session, factory)
        assessment = BatchBOMService.assess_migration(db_session, b1.id, v2.id)
        tm001 = next(i for i in assessment.impact_items if i.material_code == "TM001")
        assert tm001.old_required == 50     # 1 * 50
        assert tm001.new_required == 200    # 4 * 50
        assert tm001.delta == 150
        assert "采购" in tm001.purchase_advice
        assert assessment.existing_assessment_id is not None

        updated = BatchBOMService.approve_migration(
            db_session, assessment.existing_assessment_id,
            BOMMigrationApprove(approved_by="计划经理", remark="供应可覆盖")
        )
        assert updated.bom_version_id == v2.id
        snapshot_map = crud_batch_bom_item.get_quantity_map(db_session, b1.id)
        assert snapshot_map[factory.materials["TM001"].id] == 4
        # 迁移记录保留来源版本，可追溯
        mig = crud_batch_bom_migration.list_by_batch(db_session, b1.id)[0]
        assert mig.status == "migrated"
        assert mig.to_bom_version_id == v2.id
        assert mig.from_bom_version_id != v2.id

    def test_started_batch_cannot_migrate_only_deviation(self, db_session, factory):
        b1, v2 = self._prepare(db_session, factory)
        BatchBOMService.change_batch_status(db_session, b1.id, "started")
        with pytest.raises(ValueError, match="已开工|偏差"):
            BatchBOMService.assess_migration(db_session, b1.id, v2.id)

    def test_reject_migration_keeps_frozen_snapshot(self, db_session, factory):
        b1, v2 = self._prepare(db_session, factory)
        assessment = BatchBOMService.assess_migration(db_session, b1.id, v2.id)
        mig = crud_batch_bom_migration.get(db_session, assessment.existing_assessment_id)
        BatchBOMService.reject_migration(db_session, mig.id, "风险过大")
        db_session.refresh(mig)
        assert mig.status == "rejected"
        assert crud_batch_bom_item.get_quantity_map(
            db_session, b1.id
        )[factory.materials["TM001"].id] == 1


class TestDeviationAndConsumption:
    """已开工/已领料批次只能走可审批、可追溯的偏差。"""

    def test_deviation_full_flow_and_consumption_variance(self, db_session, factory):
        b1 = factory.production_batches["TB001"]  # 50台，TM001 1/台
        BatchBOMService.release_batch(db_session, b1.id, BatchReleaseRequest())
        v2 = _create_approve_version(
            db_session, factory.vehicle_models["TV001"].id,
            date.today() + timedelta(days=15),
            [{"material_id": factory.materials["TM001"].id, "quantity": 3},
             {"material_id": factory.materials["TM003"].id, "quantity": 1}],
        )
        # 迁移到3/台后开工
        assessment = BatchBOMService.assess_migration(db_session, b1.id, v2.id)
        BatchBOMService.approve_migration(
            db_session, assessment.existing_assessment_id,
            BOMMigrationApprove(approved_by="经理")
        )
        BatchBOMService.change_batch_status(db_session, b1.id, "started")

        # 未开工状态才能迁移；已开工用偏差：实际4/台
        with pytest.raises(ValueError):
            BatchBOMService.assess_migration(db_session, b1.id, v2.id)

        dev = BatchBOMService.create_deviation(db_session, b1.id, BOMDeviationCreate(
            deviation_type="quantity",
            material_id=factory.materials["TM001"].id,
            actual_quantity=4,
            reason="现场工装要求增加加强件用量",
            requested_by="车间主任",
        ))
        assert dev.frozen_quantity == 3
        assert dev.quantity_delta == 1
        assert dev.status == "draft"
        BatchBOMService.submit_deviation(db_session, dev.id)
        # 审批必须写影响说明
        with pytest.raises(ValueError, match="影响"):
            BatchBOMService.approve_deviation(
                db_session, dev.id, BOMDeviationApprove(approved_by="质量经理")
            )
        BatchBOMService.approve_deviation(db_session, dev.id, BOMDeviationApprove(
            approved_by="质量经理",
            impact_summary="采购：追加50件TM001；库存：从TV002预留调拨；延期：不影响交付",
        ))

        # 按审批偏差领料：备足库存，实领 4*50=200，冻结应领 3*50=150
        factory.create_inventory_batch("TM001", 300, 300)
        issue = BatchBOMService.issue_material(db_session, b1.id, BatchIssueMaterialRequest(
            material_id=factory.materials["TM001"].id,
            issued_quantity=200,
            deviation_id=dev.id,
            remark="按偏差单领料",
        ))
        assert issue.required_quantity == 150
        assert issue.issued_quantity == 200
        db_session.refresh(dev)
        assert dev.status == "applied"

        # 追溯：实耗差异 +50，且关联偏差单号
        trace = BatchBOMService.trace_batch(db_session, b1.id)
        variance = next(v for v in trace.consumption_variances if v.material_code == "TM001")
        assert variance.frozen_required == 150
        assert variance.actual_issued == 200
        assert variance.variance == 50
        assert dev.deviation_no in variance.deviation_nos

    def test_issue_without_stock_fails(self, db_session, factory):
        b1 = factory.production_batches["TB001"]
        BatchBOMService.release_batch(db_session, b1.id, BatchReleaseRequest())
        BatchBOMService.change_batch_status(db_session, b1.id, "started")
        with pytest.raises(ValueError, match="库存不足"):
            BatchBOMService.issue_material(db_session, b1.id, BatchIssueMaterialRequest(
                material_id=factory.materials["TM003"].id, issued_quantity=10,
            ))


class TestTraceability:
    """从任一生产批次还原原始BOM、后续变更与实耗差异。"""

    def test_trace_contains_original_lineage_migration(self, db_session, factory):
        b1 = factory.production_batches["TB001"]
        baseline = _baseline_version(db_session, "TV001", factory)
        BatchBOMService.release_batch(db_session, b1.id, BatchReleaseRequest())
        v2 = _create_approve_version(
            db_session, factory.vehicle_models["TV001"].id,
            date.today() + timedelta(days=15),
            [{"material_id": factory.materials["TM001"].id, "quantity": 3},
             {"material_id": factory.materials["TM003"].id, "quantity": 1}],
            change_type="emergency",
            impact="采购追加/库存重分配/延期不变",
        )
        assessment = BatchBOMService.assess_migration(db_session, b1.id, v2.id)
        BatchBOMService.approve_migration(
            db_session, assessment.existing_assessment_id,
            BOMMigrationApprove(approved_by="经理")
        )
        trace = BatchBOMService.trace_batch(db_session, b1.id)
        # 原始版本可经迁移记录还原
        assert trace.migrations[0]["from_version_no"] == baseline.version_no
        assert trace.migrations[0]["to_version_no"] == v2.version_no
        assert trace.frozen_bom_version_no == v2.version_no
        # 后续变更谱系
        followed = [l for l in trace.lineage if l.change == "followed"]
        assert any(l.version_id == v2.id for l in followed)
        v2_line = next(l for l in followed if l.version_id == v2.id)
        assert v2_line.change_type == "emergency"
        changed = next(i for i in v2_line.items if i.material_code == "TM001")
        assert (changed.old_quantity, changed.new_quantity) == (1, 3)

    def test_trace_planned_batch_without_freeze(self, db_session, factory):
        b1 = factory.production_batches["TB001"]
        trace = BatchBOMService.trace_batch(db_session, b1.id)
        assert trace.frozen_bom_version_id is None
        assert trace.original_bom == []
        assert trace.status == "planned"


class TestDelayAnalysisUsesFrozenBOM:
    """延期结论必须以批次冻结结构为准。"""

    def test_delay_analysis_ignores_later_bom_change_for_released_batch(
        self, db_session, factory
    ):
        b1 = factory.production_batches["TB001"]  # +10天 50台
        # 下达并冻结 1/台
        BatchBOMService.release_batch(db_session, b1.id, BatchReleaseRequest())
        # 之后审批新版本 3/台，今天生效
        _create_approve_version(
            db_session, factory.vehicle_models["TV001"].id,
            date.today(),
            [{"material_id": factory.materials["TM001"].id, "quantity": 3},
             {"material_id": factory.materials["TM003"].id, "quantity": 1}],
        )
        po = factory.create_purchase_order(
            "PO-DLY-1", "TS001", "TM001", 100,
            expected_date=date.today() + timedelta(days=12)
        )
        result = DelayAnalysisService.analyze_delay_impact(
            db_session, po.id, delay_days=30
        )
        detail_b1 = next(
            d for d in result.analysis_details if d["batch_no"] == "TB001"
        )
        # 已下达批次仍按冻结的 1/台：需要 50，而不是 150
        assert detail_b1["bom_qty_per_unit"] == 1
        assert detail_b1["required_material_qty"] == 50
        # 未下达的 TB002(+30天, 30台) 按新版本 3/台：90
        detail_b2 = next(
            d for d in result.analysis_details if d["batch_no"] == "TB002"
        )
        assert detail_b2["bom_qty_per_unit"] == 3
        assert detail_b2["required_material_qty"] == 90


class TestBOMAPIDisallowDirectEdit:
    """直接维护当前BOM的旧入口必须关闭（走版本审批）。"""

    def test_legacy_bom_endpoints_blocked(self, db_session, override_get_db, factory):
        from fastapi.testclient import TestClient
        from app.main import app
        client = TestClient(app)
        vm_id = factory.vehicle_models["TV001"].id
        material_id = factory.materials["TM001"].id
        resp = client.post(f"/api/v1/vehicles/{vm_id}/bom", json={
            "vehicle_model_id": vm_id,
            "material_id": material_id,
            "quantity": 1,
        })
        assert resp.status_code == 405
        resp_del = client.delete("/api/v1/vehicles/bom/1")
        assert resp_del.status_code == 405
