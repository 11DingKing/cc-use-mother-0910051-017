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

BOM 不再被直接维护覆盖。所有变更都以「版本 + 生效日期 + 审批状态」管理，生产批次下达时冻结所采用的结构与单位用量，后续变更不会重算已下达批次。

### 版本与审批

- `BOMVersion`：版本头，状态流转 `draft → submitted → approved/rejected`；`change_type` 为 `normal/emergency/rollback`。
- 版本生效区间为 `[生效日, 下一版本生效日)`，按生效日切分；同一车型同一天只允许一个已审批版本，**版本区间不得重叠**。
- 紧急更改（emergency）与回退（rollback）在创建/审批时**必须说明对采购建议、库存分配、延期结论三项影响**，否则无法提交或审批。
- 回退不是修改旧版本，而是复制历史版本结构生成一个新的 `rollback` 版本重新走审批，版本链通过 `source_version_id / superseded_by_id` 可追溯。

主要接口（前缀 `/api/v1`）：

| 接口 | 说明 |
| --- | --- |
| `POST /bom-versions/` | 创建版本（草稿，可带条目/来源版本） |
| `POST /bom-versions/{id}/submit` `/approve` `/reject` | 提交审批、审批、驳回 |
| `POST /bom-versions/rollback` | 以历史版本为源生成回退版本（强制三项影响说明） |
| `GET /bom-versions/{id}/diff?from_version_id=` | 版本结构差异（新增/删除/用量变化） |
| `GET /bom-versions/effective/latest?vehicle_model_id=&on_date=` | 查询某日生效版本 |

> 旧的 `/vehicles/{id}/bom` 直接维护接口已标记为弃用，仅保留兼容，业务变更请使用版本流程。

### 生产批次冻结、迁移与偏差

- **下达冻结**：`POST /production-batches/{id}/release` 将生效版本的物料、单位用量、应耗量复制为不可变快照 `BatchBOMItem`，批次状态置为 `released`。需求、采购、延期分析对已下达批次一律读取快照，未下达批次读取计划日生效版本。
- **未开工批次迁移**：`POST /production-batches/{id}/migrations/evaluate` 先做影响评估，逐项给出单位用量/应耗量变化、采购建议增减、库存（库存+待处理建议+在途）分配、最早齐料日与预计延期；再经 `POST /production-batches/migrations/{id}/decision` 决策 `migrate`（重新冻结）或 `reject`（保留原版本）。
- **已领料批次偏差**：批次进入 `in_progress` 后不允许迁移，只能通过 `POST /production-batches/{id}/deviations` 登记可追溯偏差（替代/超领/短领/工程变更），替代必须命中已登记且车型允许的替代关系；偏差经审批、执行后扣减库存并写入实际消耗。
- **消耗与差异**：`POST /production-batches/{id}/consumptions` 登记实际领料。
- **追溯还原**：`GET /production-batches/{id}/traceability` 从任一批次还原：原始冻结 BOM、冻结日之后的全部版本变更、迁移与偏差记录、按物料汇总的应耗/实耗/差异。

