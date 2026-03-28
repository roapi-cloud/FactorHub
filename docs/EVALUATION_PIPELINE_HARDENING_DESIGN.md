# 评估链路稳健性修复设计文档

## 1. 背景

当前仓库已经具备以下能力：

- 因子计算与打分：
  [factor_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/factor_service.py)、
  [temporal_scoring_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/temporal_scoring_service.py)
- 时序筛选验证：
  [temporal_filter_validation_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/temporal_filter_validation_service.py)
- 候选策略搜索与 champion 选择：
  [strategy_search_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/strategy_search_service.py)、
  [strategy_evaluation_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/strategy_evaluation_service.py)、
  [champion_strategy_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/champion_strategy_service.py)

但在“评估可信度”层面存在结构性问题：在样本缺失、指标缺失、schema 漂移等情况下，系统仍会输出“看起来完整”的评估结果，容易误导后续因子扩展和策略决策。

本设计文档的目标是：**先修复评估链路可靠性，再扩因子库**。


## 2. 目标与非目标

## 2.1 目标

1. 建立可验证的评估数据契约，避免静默失败。
2. 建立数据库 schema 版本治理，消除模型/表结构漂移。
3. 在样本不足或指标无效时，评估任务应明确失败，不得产出伪有效结果。
4. champion 选择必须通过质量门禁，禁止无效候选入选。
5. 每次评估产出可追溯元数据（数据覆盖率、失败明细、门禁结果、版本指纹）。

## 2.2 非目标

1. 本次不引入外部数据源。
2. 本次不优化因子 alpha 本身，仅修复“评估链路可信度”。
3. 本次不引入新三方库，沿用现有技术栈。


## 3. 当前链路与问题证据

## 3.1 链路分层

现有评估链路可拆成 5 层：

1. `Data`：行情拉取 + 缓存  
   [data_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/data_service.py)
2. `Scoring`：单股/股票池打分  
   [temporal_scoring_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/temporal_scoring_service.py)
3. `Validation`：IC、分层、组合回测  
   [temporal_filter_validation_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/temporal_filter_validation_service.py)
4. `StrategyEvaluation`：walk-forward 聚合  
   [strategy_evaluation_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/strategy_evaluation_service.py)
5. `Champion`：候选过滤与落盘  
   [champion_strategy_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/champion_strategy_service.py)

## 3.2 关键问题清单

### P0-1：数据库 schema 漂移导致评估服务直接失败

- 代码模型定义 `factors.formula_type` 字段：
  [factor.py](/Users/fengzhi/Downloads/git/FactorHub/backend/models/factor.py)
- 现网 SQLite 表中无该列（`pragma table_info(factors)` 可验证）。
- 结果：依赖 ORM 查询全字段的服务（例如 recent mining）会抛出 `no such column: factors.formula_type`。

影响：评估任务无法稳定执行，且失败点发生在业务逻辑之前。

### P0-2：样本/指标无效时仍可选出 champion

- `compute_stability_score` 对 NaN 指标使用默认值，容易得到固定分（常见 27.5）：
  [strategy_evaluation_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/strategy_evaluation_service.py:106)
- `champion_strategy_service` 在“无有效候选”时降级为从全部候选中强行选一个：
  [champion_strategy_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/champion_strategy_service.py:305)

影响：出现“指标全 NaN 但仍有冠军”的伪结果，误导后续策略应用。

### P1-1：数据拉取失败被弱化处理，覆盖率门禁缺失

- `DataService.get_multiple_stocks_data` 失败仅打印 warning，调用方默认继续：
  [data_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/data_service.py:213)
- 验证流程存在 `insufficient_samples` 字段，但缺少统一 fail-fast 门禁：
  [temporal_filter_validation_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/temporal_filter_validation_service.py)

影响：报告可能“可生成但无统计意义”。

### P1-2：评估缓存缺少版本指纹

- 历史评分缓存 key 主要由 code/date/profile 组成；
- 当因子表达式、参数、实现版本变化时，可能命中旧缓存。

影响：结果可重复性和可追溯性不足，难以比较实验。

### P2-1：评估产物可读，但不够可审计

- 有 CSV/JSON/Markdown 产物；
- 缺少统一 `run_manifest`（代码版本、schema 版本、数据覆盖率、门禁判定）。

影响：难以在回顾中回答“这份结论基于什么条件得到”。


## 4. 设计原则

1. **正确优先于可用**：无效数据时应失败，而非输出伪结论。  
2. **显式优先于隐式**：每个降级路径必须显式记录并暴露。  
3. **可追溯优先于一次性可跑**：每次 run 必须具备完整元数据。  
4. **向后兼容优先**：新增字段可选，旧调用方不应被破坏。  
5. **渐进上线**：先引入门禁与观测，再切换强约束策略。


## 5. 目标架构

```text
            +---------------------+
            | Schema Manager      |
            | (version + migrate) |
            +----------+----------+
                       |
                       v
+----------------------+----------------------+
| Data Layer (with coverage report)          |
| get_* -> {data, errors, coverage, status}  |
+----------------------+----------------------+
                       |
                       v
+----------------------+----------------------+
| Scoring Layer (versioned cache + manifest) |
+----------------------+----------------------+
                       |
                       v
+----------------------+----------------------+
| Validation Layer (quality gates)           |
| - min sample                                |
| - min coverage                              |
| - min valid windows                         |
+----------------------+----------------------+
                       |
                       v
+----------------------+----------------------+
| Strategy Evaluation / Champion              |
| - invalid candidate reject                  |
| - no fallback champion on invalid set       |
+----------------------+----------------------+
                       |
                       v
            +----------+----------+
            | Artifacts + Manifest|
            | status + reason code|
            +---------------------+
```


## 6. 详细设计

## 6.1 Schema 治理与迁移

### 6.1.1 新增 `SchemaManager`

新增文件：

- `backend/core/schema_manager.py`

职责：

1. 维护 schema 版本号（`schema_meta` 表）。
2. 启动时检查关键表结构。
3. 自动执行 SQLite 小步迁移（幂等）。

### 6.1.2 启动流程改造

在
[main.py](/Users/fengzhi/Downloads/git/FactorHub/backend/api/main.py)
启动逻辑中，`init_db()` 后执行：

1. `schema_manager.check_and_migrate()`
2. 迁移失败则启动失败（明确报错，不进入服务）。

### 6.1.3 首批迁移任务

`v1 -> v2`：

1. 为 `factors` 表补齐 `formula_type` 列（默认 `expression`）。
2. 为历史数据回填 `formula_type='expression'`。
3. 增加 `schema_meta` 表并记录版本。

### 6.1.4 验收标准

1. 空库启动可自动建表并记录 schema 版本。
2. 老库启动可自动补齐 `formula_type`。
3. 重复启动不重复迁移、不报错。


## 6.2 数据层契约与覆盖率报告

### 6.2.1 新增结构化返回模型

新增数据结构（建议 dataclass）：

```python
class DataFetchReport:
    requested_codes: int
    success_codes: int
    failed_codes: int
    code_coverage: float
    requested_start_date: str
    requested_end_date: str
    actual_min_date: str | None
    actual_max_date: str | None
    failures: list[dict]   # [{code, error_type, message}]
```

并新增 API（保留旧接口）：

```python
get_multiple_stocks_data_with_report(...) -> tuple[data_map, report]
```

### 6.2.2 失败分级

定义统一错误类型：

- `NETWORK_ERROR`
- `EMPTY_DATA`
- `SCHEMA_ERROR`
- `DATA_QUALITY_ERROR`
- `UNKNOWN`

### 6.2.3 覆盖率门禁（默认阈值）

- `min_code_coverage = 0.85`
- `min_effective_dates = 60`（按场景可配）

当不满足门禁：

- Validation/StrategyEvaluation 直接返回 `invalid_run`，不进入 champion 选择。


## 6.3 评分层缓存版本化

### 6.3.1 缓存 key 增加版本指纹

对以下缓存增加 `version_fingerprint`：

1. `score_stock_history` 默认缓存  
2. `score_stock_history_with_profile` profile 缓存

指纹建议由以下字段 hash：

- 因子表达式版本（`factor_expression_source` + expression digest）
- profile id + profile 内容 hash
- scoring service 版本号（手动维护常量）

### 6.3.2 目标

当因子表达式/配置变动时自动失效旧缓存，避免“改了逻辑但读到旧结果”。


## 6.4 Validation 层质量门禁

### 6.4.1 新增 `EvaluationQualityGate`

新增结构：

```python
class EvaluationQualityGate:
    passed: bool
    reason_codes: list[str]
    metrics: dict
```

默认规则：

1. `CODE_COVERAGE_TOO_LOW`
2. `INSUFFICIENT_IC_SAMPLES`
3. `INSUFFICIENT_REBALANCE_POINTS`
4. `TOO_MANY_SCORING_ERRORS`

### 6.4.2 `temporal_filter_validation_service` 行为变更

在
[temporal_filter_validation_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/temporal_filter_validation_service.py)
中：

1. 构建 panel 后先运行质量门禁。
2. 门禁失败时：
   - 仍写出报告文件；
   - 报告状态为 `invalid`；
   - 不输出“策略优劣结论”。

### 6.4.3 报告格式新增字段

`strategy_comparison.json` 与 `validation_report.md` 增加：

- `quality_gate_passed`
- `quality_gate_reason_codes`
- `data_coverage`
- `effective_sample_count`


## 6.5 StrategyEvaluation 与 Champion 的有效性约束

### 6.5.1 `compute_stability_score` 改造

当前问题：

- 全 NaN 窗口经默认值处理后会得到稳定的非零分（如 27.5）。

改造方案：

1. 区分“无效窗口”和“有效窗口”。
2. 新增 `min_valid_windows`（默认 3）。
3. 若 `valid_windows < min_valid_windows`：
   - `stability_score = NaN`
   - `evaluation_status = invalid`
   - 输出 `invalid_reason = INSUFFICIENT_VALID_WINDOWS`

### 6.5.2 `evaluate_*_candidate` 返回结构扩展

新增字段：

- `evaluation_status`: `valid | invalid`
- `valid_windows`: int
- `total_windows`: int
- `invalid_reasons`: list[str]

### 6.5.3 Champion 选择逻辑改造

在
[champion_strategy_service.py](/Users/fengzhi/Downloads/git/FactorHub/backend/services/champion_strategy_service.py)
中：

1. 只允许 `evaluation_status=valid` 的候选进入排序池。
2. 若有效候选为空：
   - 任务状态应为 `failed`（或 `no_valid_candidate`）；
   - 不写 champion 到 registry；
   - 输出完整失败原因。
3. 去掉“从全部候选降级选一个”的默认行为。

### 6.5.4 Cross-sectional 特殊约束

当 `universe` 无法解析到可回测数据时：

- 直接返回 `invalid`，并给出 `UNIVERSE_DATA_MISSING`。


## 6.6 产物与可审计性

### 6.6.1 每次 run 统一产出 `run_manifest.json`

建议路径：

- `data/reports/<module>/<run_id>/run_manifest.json`

字段示例：

```json
{
  "run_id": "...",
  "module": "temporal_filter_validation",
  "generated_at": "...",
  "git_commit": "...",
  "schema_version": 2,
  "settings_fingerprint": "...",
  "input": {...},
  "data_coverage": {...},
  "quality_gate": {...},
  "result_status": "valid|invalid|failed"
}
```

### 6.6.2 状态机统一

任务状态统一为：

- `pending`
- `running`
- `completed_valid`
- `completed_invalid`
- `failed`

避免“completed 但结果不可用”的语义歧义。


## 7. 配置项设计

新增配置（`backend/core/settings.py`）：

```python
EVAL_MIN_CODE_COVERAGE: float = 0.85
EVAL_MIN_IC_SAMPLES: int = 30
EVAL_MAX_ERROR_RATIO: float = 0.15
EVAL_MIN_VALID_WINDOWS: int = 3
EVAL_STRICT_MODE: bool = True
SCHEMA_AUTO_MIGRATE: bool = True
```

兼容策略：

- 默认开启严格模式；
- 若需短期兼容旧流程，可通过配置关闭 strict（仅限调试环境）。


## 8. 测试设计

## 8.1 单元测试

1. `test_schema_manager_add_formula_type_column`
2. `test_compute_stability_score_returns_invalid_when_all_nan`
3. `test_quality_gate_fail_on_low_code_coverage`
4. `test_champion_selection_rejects_invalid_candidates`

## 8.2 集成测试

1. 构造 30% 数据拉取失败，验证 run 状态为 `completed_invalid`。
2. 构造全 NaN 候选指标，验证 champion 任务失败且不落 registry。
3. expression 变更后重跑，验证旧缓存自动失效。

## 8.3 回归测试

复用现有关键测试：

- [test_temporal_filter_validation_service.py](/Users/fengzhi/Downloads/git/FactorHub/tests/test_temporal_filter_validation_service.py)
- [test_cross_sectional_e2e.py](/Users/fengzhi/Downloads/git/FactorHub/tests/test_cross_sectional_e2e.py)
- [test_search_router.py](/Users/fengzhi/Downloads/git/FactorHub/tests/test_search_router.py)

并新增“无效结果不选 champion”的断言。


## 9. 分阶段落地计划

## Phase 0（当天）

1. 加入运行时 schema 检查，阻止漂移库静默运行。
2. 给 champion 报告追加 `validity` 字段（先不改逻辑，仅观测）。

## Phase 1（1-2 天）

1. 上线 `SchemaManager` + 自动迁移。
2. 数据层结构化错误报告。
3. 评估报告写入 `run_manifest.json`。

## Phase 2（2-3 天）

1. 引入 Validation 质量门禁并启用 strict。
2. `compute_stability_score` 引入有效窗口约束。
3. Champion 逻辑切换为“无有效候选即失败”。

## Phase 3（1-2 天）

1. 扩充测试与 CI。
2. 回放历史任务，验证新旧结果差异并确认预期。
3. 更新文档与运维手册。


## 10. 验收标准（Definition of Done）

满足以下全部条件视为“评估链路修好”：

1. 旧库启动可自动迁移，不再出现 `formula_type` 缺列报错。
2. 当样本覆盖率不足时，任务状态为 `completed_invalid` 或 `failed`，且理由可追溯。
3. 全 NaN 指标不会产生可入选 champion。
4. champion 产物中包含 `evaluation_status=valid` 与完整门禁信息。
5. 每个 run 均有 `run_manifest.json`，可复现实验上下文。
6. 新增测试全部通过，且原有关键回归测试通过。


## 11. 风险与缓解

1. **风险：严格门禁导致可用任务数量下降**  
缓解：先灰度，保留 `strict_mode` 开关，逐步收紧阈值。

2. **风险：历史缓存失效导致短期计算开销上升**  
缓解：分批失效；按 profile 和日期范围逐步重建。

3. **风险：旧调用方依赖“总能返回 champion”语义**  
缓解：API 增加 `result_status`，并在文档明确 invalid/fail 语义。


## 12. 与后续“扩因子库”的关系

本设计完成后，扩因子库（TA-Lib/Alpha360/自定义）才有意义，因为：

1. 能正确判定新增因子是否真的提升了样本外表现；
2. 能避免“因为评估链路失真而误收录无效因子”；
3. 能沉淀可复现的因子准入标准（不是只看单次回测曲线）。

换言之：**先修评估，再扩因子，是必要前置条件**。
