# 制造零部件供应协同服务

本项目是使用 Python、FastAPI 与 SQLite 实现的服务端应用，覆盖车型、物料、BOM、供应商、采购、到货、检验、库存和短缺分析。它可在单个 Linux 应用容器内完成安装、测试、编译和接口验收，不依赖浏览器、外部数据库、缓存、消息队列或额外运行服务。

## 安装

```bash
python3 -m pip install -r requirements.txt -r requirements-dev.txt
```

## 测试

```bash
python3 -m pytest -q tests
```

## 编译

```bash
python3 -m compileall -q .
```

## 接口验收

```bash
python3 -c "from app.main import app; assert len(app.routes) > 5; print(len(app.routes))"
```

## 启动

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

## BOM 版本化与批次冻结

为避免 BOM 直接维护导致已下达批次的短缺、采购建议与延期结论被重算，BOM 采用带生效日期与审批状态的版本管理：

- **版本**：`BOMVersion`（车型、版本号、`draft/submitted/approved/rejected`、`normal/emergency/rollback`、生效区间 `[effective_date, expire_date)`）。同车型已审批版本区间不得重叠，新版本审批生效时自动把上一开放版本截断为相邻区间；紧急更改与回退必须填写对**采购建议、库存分配、延期结论**的影响说明，回退还须指明回退所依据的基准版本。
- **下达冻结**：生产批次仅在 `planned` 状态可下达（`POST /api/v1/bom/batches/{id}/release`），下达时把当时生效版本的结构、单位用量与替代关系完整复制为批次快照。需求计算、延期分析、供应商短缺重算对每个批次分别取数：已下达（`released`）读冻结快照，未下达读其计划日生效的版本。
- **批次迁移**：`released` 未开工批次可先做影响评估（`POST .../migrations/assess/{target_version_id}`，逐物料给出新旧需求差、库存/待处理建议/在途、缺口变化与采购建议，并汇总库存重分配与延期结论），审批通过后迁移到新版本；迁移保留来源版本与评估记录，可追溯。批次一旦 `started/issued/completed` 即锁定，禁止迁移。
- **偏差处理**：已开工/已领料批次的结构或用量差异只能走可审批、可追溯的偏差单（`BOMDeviation`：数量/替代/新增/移除，含冻结用量、实际用量、单车用量差与影响说明），审批通过后方可据偏差领料。
- **领料与实耗**：`POST /api/v1/bom/batches/{id}/issues` 登记按冻结 BOM 的应领量与实领量并扣减库存。
- **追溯查询**：`GET /api/v1/bom/batches/{id}/trace` 可从任一生产批次还原原始冻结 BOM、之后的版本变更谱系（逐版差异，含紧急/回退标记）、迁移记录、偏差单以及"冻结应领 vs 实际消耗"的差异。
- 历史直接维护入口（新增/删除当前 BOM 行）已关闭（HTTP 405）；存量 BOM 在启动时自动导入为生效日 2000-01-01 的基线版本，已下达状态的存量批次自动补冻结快照，旧 SQLite 库自动补列。

### 批次状态生命周期

`planned → released → started → issued → completed → closed`（`released/started` 可直接 `completed`）。只有 `planned` 可下达冻结；`released` 可迁移；`started` 及之后只能偏差处理。

### 主要接口（前缀 `/api/v1/bom`）

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET/POST | `/vehicles/{vehicle_id}/versions` | 版本列表 / 创建版本（草稿） |
| GET | `/versions/{version_id}` | 版本详情（含冻结的替代关系） |
| GET | `/vehicles/{vehicle_id}/versions/{version_id}/diff` | 与前驱/指定版本的差异 |
| POST | `/versions/{id}/submit|approve|reject` | 提交 / 审批通过 / 驳回 |
| POST | `/batches/{id}/release` | 批次下达并冻结 BOM |
| POST | `/batches/{id}/status/{target_status}` | 批次状态推进 |
| POST | `/batches/{id}/migrations/assess/{target_version_id}` | 未开工批次迁移影响评估 |
| POST | `/migrations/{id}/approve|reject` | 迁移审批 / 驳回 |
| POST/GET | `/batches/{id}/deviations` | 偏差单创建 / 列表 |
| POST | `/deviations/{id}/submit|approve` | 偏差提交 / 审批（须影响说明） |
| POST/GET | `/batches/{id}/issues` | 批次领料（实耗）登记 |
| GET | `/batches/{id}/trace` | 批次 BOM 全链路追溯 |
