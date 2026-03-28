# 代码去重与精简梳理

更新时间：2026-03-22

## 1. 总体判断

这次梳理后，仓库里的重复逻辑主要不是“同一个小函数拷贝了几次”，而是下面 4 类更重的重复：

1. 路由层重复做业务编排
2. 同一套能力存在多份实现
3. 异步任务 / 报告落盘逻辑各写各的
4. 前端图表和页面壳样式重复拼装

问题最集中的区域不是前端，而是后端 `router -> service -> 文件产物` 这条链路。现在很多 API 路由不仅收参数，还负责取数、查因子、算权重、清洗数值、拼图表、写状态，这会直接导致后续每加一个接口就复制一遍已有逻辑。

## 2. 复杂度热点

当前体量最大的几个服务文件：

| 文件 | 行数 | 判断 |
| --- | ---: | --- |
| `backend/services/factor_service.py` | 1306 | 因子计算、预置因子管理、校验、批量计算混在一起 |
| `backend/services/temporal_filter_validation_service.py` | 1213 | 回测、统计检验、输出报告、序列化全部耦合 |
| `backend/services/temporal_scoring_service.py` | 951 | 打分逻辑和异步任务存储耦合 |
| `backend/services/vectorbt_backtest_service.py` | 833 | 现行主回测引擎 |
| `backend/services/strategy_evaluation_service.py` | 816 | walk-forward 评价、数据加载、结果聚合混合 |
| `backend/services/champion_strategy_service.py` | 739 | 搜索编排、结果筛选、落盘报告混合 |

结论：后续精简不能只做“抽几个工具函数”，而是要先收敛职责边界。

## 3. 主要重复源

### A. 路由层重复做“取数 + 查因子 + 计算 + 清洗 + 返回”

最典型的是：

- `backend/api/routers/portfolio.py:73`
- `backend/api/routers/portfolio.py:416`
- `backend/api/routers/portfolio.py:493`
- `backend/api/routers/backtest.py:57`
- `backend/api/routers/backtest.py:389`
- `backend/api/routers/analysis.py:72`

重复模式基本一致：

1. `data_service.get_stock_data(...)`
2. `FactorRepository(...).get_by_name(...)`
3. `factor_service.calculator.calculate(...)`
4. 针对 `NaN/Inf/numpy` 做本地清洗
5. 再拼自己的返回结构

这类重复的影响最大，因为一旦数据口径、因子查找策略、数值清洗方式变了，至少要改 3 到 6 个接口。

建议收口：

- 抽一个统一的 `factor context builder`
- 统一负责：
  - 股票数据加载
  - 因子定义解析
  - 因子批量计算
  - 因子矩阵 / returns 对齐
  - JSON 可序列化清洗

### B. `portfolio.py` 内部已经出现整段业务逻辑重复

重点位置：

- `backend/api/routers/portfolio.py:73`
- `backend/api/routers/portfolio.py:493`

`optimize_weights` 和 `compare_weight_methods` 中，下面这套逻辑基本写了两遍：

- 获取股票数据
- 获取因子定义
- 计算因子值
- 计算 `returns`
- 按 `equal_weight / ic_weight / ir_weight / max_sharpe / max_return / min_variance` 生成权重
- 再计算组合因子表现

这部分应该直接下沉到 `portfolio_analysis_service`，否则这个路由文件会继续膨胀。

建议拆成：

- `prepare_factor_panel(...)`
- `compute_factor_weights(method, factor_data, returns)`
- `evaluate_weighted_factor(...)`

### C. 回测引擎存在“双实现”

重复文件：

- `backend/services/backtest_service.py:30`
- `backend/services/vectorbt_backtest_service.py:17`

两个文件都实现了：

- `single_factor_backtest`
- `cross_sectional_backtest`
- `multi_factor_backtest`
- `_empty_metrics`

但当前实际被 API 和评价链路使用的是 `vectorbt_backtest_service`，`backtest_service` 在当前 Python 调用图里没有内部引用。

这是一类高风险重复：不是简单冗余，而是“同名能力有两套口径”。后面任何一方修指标、修手续费、修信号逻辑，都可能和另一份脱节。

建议：

1. 先确认 `backtest_service.py` 是否还需要保留
2. 若不需要，删除或归档
3. 若需要，明确它只保留“兼容层 / facade”，内部全部转调 `vectorbt_backtest_service`

### D. 异步任务状态文件和产物写入逻辑重复

重复位置：

- `backend/api/routers/search.py:96`
- `backend/api/routers/search.py:146`
- `backend/services/champion_strategy_service.py:223`
- `backend/services/champion_strategy_service.py:524`
- `backend/services/champion_strategy_service.py:613`
- `backend/services/temporal_scoring_service.py:733`
- `backend/services/temporal_scoring_service.py:779`

现在至少有两套“任务目录 + status json + 结果文件”的实现：

- champion 搜索任务
- temporal scoring 异步任务

而且 `search.py` 路由本身还写了一套 `task_status.json` 的创建和更新逻辑，和 `champion_strategy_service` 内部的任务状态落盘是并行存在的。

建议收口成统一组件：

- `JobStore`：创建任务、更新状态、读取状态
- `ArtifactWriter`：写 JSON / CSV / Markdown / manifest

这样 `search router` 不再负责手写状态文件，只负责提交任务。

### E. `temporal_filter_validation_service` 内部重复较多

重点位置：

- `backend/services/temporal_filter_validation_service.py:148`
- `backend/services/temporal_filter_validation_service.py:211`
- `backend/services/temporal_filter_validation_service.py:698`
- `backend/services/temporal_filter_validation_service.py:733`
- `backend/services/temporal_filter_validation_service.py:997`
- `backend/services/temporal_filter_validation_service.py:1100`

明确可见的重复包括：

- `nearest_trading_date(...)` 写了两次
- 回测指标统计公式重复出现
- JSON 可序列化转换 `_make_serializable(...)` 是局部函数
- Markdown 报告拼接过长，格式逻辑和数据逻辑耦合

建议拆分：

- `trading_calendar.py`
- `performance_metrics.py`
- `report_writers/temporal_validation_report.py`

### F. 脚本层已经有 `pipeline_common.py`，但复用还不彻底

重复位置：

- `scripts/run_single_stock_factor_research.py:18`
- `scripts/run_single_stock_recent_factor_mining.py:18`
- `scripts/run_single_stock_strategy_search.py:21`
- `scripts/run_factor_library_audit.py:16`
- `scripts/run_single_stock_prediction.py:34`
- `scripts/pipeline_common.py:13`

当前这些脚本已经开始共享：

- `bootstrap_runtime`
- `ensure_output_dir`
- `normalize_stock_code`
- `parse_int_list`
- `write_json / write_csv / write_text`

但仍有明显重复：

- `parse_args()` 结构非常接近
- 输出目录规则相似
- `summary.md` 写法高度相似

这块不需要过度抽象，但可以做一个轻量 `stage_cli.py`：

- 通用参数片段
- 输出目录拼装
- 统一 `result.json/top_factors.csv/summary.md` 产物协议

### G. 前端图表 option 和页面壳样式重复

重点位置：

- `frontend/react-antd/src/pages/Backtesting.tsx:257`
- `frontend/react-antd/src/components/BacktestCharts.tsx:179`
- `frontend/react-antd/src/pages/PortfolioAnalysis.tsx:352`
- `frontend/react-antd/src/pages/FactorDetail.tsx:516`
- `frontend/react-antd/src/pages/FactorDetail.tsx:768`
- `frontend/react-antd/src/pages/Backtesting.css:10`
- `frontend/react-antd/src/pages/FactorManagement.css:10`
- `frontend/react-antd/src/pages/PortfolioAnalysis.css:10`
- `frontend/react-antd/src/pages/FactorMining.css:10`

重复主要体现在：

- `grid / tooltip / areaStyle / 渐变色` 多次手写
- 净值 / 回撤 / 分布 / 时间序列图共用的 option 基座没有抽象
- 多个页面 CSS 复制了同一套背景、网格、header、title 壳子

建议：

- 建一个 `chartOptionFactory.ts`
- 建一个页面级 `page-shell.css` 或 `PageShell` 组件

这部分优先级低于后端，但投入产出比也不错。

### H. 公共基础工具散落，导致小重复不断冒出来

典型位置：

- `backend/services/factor_version_service.py:19`
- `backend/services/factor_import_service.py:20`
- `backend/services/cache_service.py:62`
- `backend/api/routers/portfolio.py:18`
- `backend/api/routers/backtest.py:192`
- `backend/api/routers/analysis.py:10`

重复点包括：

- `_get_db()` 多处重复
- `NaN/Inf/numpy` 清洗逻辑多份实现
- `sys.path.insert(...)` 在 API、脚本、任务里反复出现
- `write_text(json.dumps(...))` 与 `json.loads(read_text())` 到处手写

建议统一出：

- `backend/core/db.py` 里的 session helper / contextmanager
- `backend/core/serialization.py`
- `backend/core/io.py`
- 去掉运行时 `sys.path.insert(...)`，改为稳定包入口

## 4. 疑似可删减模块

按当前 Python 内部引用扫描，下面几个服务看起来没有被主链调用：

- `backend/services/backtest_service.py`
- `backend/services/comprehensive_scoring_service.py`
- `backend/services/factor_import_service.py`
- `backend/services/formula_compiler_service.py`
- `backend/services/visualization_service.py`

这里先标记为“疑似冗余”，不建议直接删；先确认是否有外部脚本、手工调用或未来规划依赖。

## 5. 推荐重构顺序

### 第一阶段：先收口最容易继续复制的地方

1. 抽出统一的 `factor data context` 构建层
2. 抽出统一的数值序列化 / JSON 清洗工具
3. 把 `portfolio.py` 的权重计算下沉到 service

### 第二阶段：统一任务与产物协议

1. 合并 `search.py` / `champion_strategy_service.py` / `temporal_scoring_service.py` 的任务状态管理
2. 统一 JSON / CSV / Markdown / manifest 写入器

### 第三阶段：消灭双实现

1. 确认 `backtest_service.py` 是否下线
2. 清理不再使用的 service 和旧逻辑

### 第四阶段：处理前端重复

1. 抽图表 option factory
2. 抽页面壳样式

## 6. 最值得先改的一刀

如果只先做一刀，我建议从 `backend/api/routers/portfolio.py` 开始。

原因：

- 重复最明显
- 改动边界相对清晰
- 清理后能顺手沉淀出后面 `analysis/backtest` 也能复用的“因子上下文构建层”
- 风险比直接碰 `factor_service.py` 和 `temporal_filter_validation_service.py` 小很多

## 7. 当前结论

仓库现在的核心问题不是“函数太多”，而是“业务编排职责分散且重复实现”。  
真正的精简方向应当是：

- 路由瘦身
- 服务职责单一化
- 任务产物协议统一
- 删除双实现和疑似死代码

后续如果进入实改，建议严格按“先收口、再替换、最后删除旧逻辑”的顺序推进，不要直接大面积重写。
