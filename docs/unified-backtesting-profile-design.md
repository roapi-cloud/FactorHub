# 统一回测页与 Stage4 的技术设计文档

## 1. 背景

当前系统里有两套相互割裂的“策略”定义：

1. 前端回测页
   - 使用页面表单构造 `single_factor` / `multi_factor` 请求
   - 保存在浏览器 `localStorage`
   - 后端入口为 `/api/backtest/single`

2. Stage4 / Champion 搜索
   - 使用 `profile` + `params` 定义策略
   - 产物保存到 `data/pipeline_runs/...`、`data/champions/...`
   - 运行依赖 `profile_id`、`risk_filter`、`rebalance`、`hold_days`、`score_threshold`

两者的主要问题：

- 保存位置不同：前端是 `localStorage`，Stage4 是后端文件
- 数据格式不同：前端是表单快照，Stage4 是 profile/champion
- 运行引擎不同：前端是因子回测接口，Stage4 是 profile 驱动的评分/回测链
- 无法真正“同一策略在页面和离线之间互通”

用户要求：

- 继续使用现有页面，但升级成“双模式统一页”
- 现有页面也支持 `risk_filter`、`rebalance`、`hold_days`、`score_threshold`
- 后端存储统一
- 无论策略来自页面生成还是 Stage4 离线搜索，格式一致、加载方式一致


## 2. 设计目标

### 2.1 目标

- 将“策略定义”统一成同一种后端对象
- 页面手工创建策略与 Stage4 生成策略使用同一格式
- 页面改为双模式：
  - 手工编辑策略
  - 加载 Stage4 / Champion 策略
- 后端统一保存与加载，不再以 `localStorage` 为主存储
- 回测执行统一走 profile 驱动链路
- 保持现有页面布局和结果区尽量复用

### 2.2 非目标

- 本阶段不重做整页视觉结构
- 本阶段不完全删除旧 `/api/backtest/single` 兼容接口
- 本阶段不引入数据库作为策略主存储
- 本阶段不重做 Stage4 搜索算法


## 3. 核心设计结论

### 3.1 统一对象不是旧表单，而是 `Profile Strategy`

统一后的“策略定义”采用 profile 思路，作为系统唯一的可持久化策略格式。

页面生成策略和 Stage4 生成策略都落成同一种对象：

```json
{
  "id": "strategy_profile_xxx",
  "mode": "temporal_pool",
  "description": "说明",
  "source": "ui_manual | stage4_search | champion_import",
  "version": "1.0",
  "factors": [
    {
      "name": "atr_norm",
      "direction": -1,
      "weight": 0.0,
      "role": "risk_filter",
      "threshold": 0.8
    },
    {
      "name": "roc_10",
      "direction": 1,
      "weight": 0.3,
      "role": "signal"
    }
  ],
  "params": {
    "rebalance": "W-FRI",
    "hold_days": 5,
    "top_n": 10,
    "score_threshold": 70,
    "percentile_window": 252
  },
  "ui_defaults": {
    "data_mode": "single",
    "stock_codes": ["002594"],
    "initial_capital": 1000000,
    "commission_rate": 0.0003,
    "slippage": 0.0
  },
  "metadata": {
    "created_at": "2026-03-22T00:00:00Z",
    "updated_at": "2026-03-22T00:00:00Z",
    "tags": ["manual"]
  }
}
```

说明：

- `factors` 与 `params` 是核心运行定义
- `ui_defaults` 是页面加载时的默认值，不参与评分公式
- `metadata` 是展示与管理信息

### 3.2 Champion 不是另一种策略格式，而是“绑定关系”

Stage4 的 champion 不应再作为另一种策略定义格式存在，而应拆成两部分：

1. 统一的 `Profile Strategy`
2. Champion 绑定关系

Champion 绑定只负责表达：

- 某个 `scope_type/scope_key`
- 在某次搜索中
- 选中了哪个 `profile_id`
- 对应的评估指标与报告位置

建议格式：

```json
{
  "scope_type": "single_stock",
  "scope_key": "002594",
  "profile_id": "strategy_profile_xxx",
  "stability_score": 59.07,
  "metrics": {
    "annual_return": 0.51,
    "sharpe": 2.81,
    "max_drawdown": -0.037
  },
  "selected_at": "2026-03-22T03:06:03Z",
  "effective_from": "2026-03-21",
  "report_path": "data/reports/strategy_search/..."
}
```

这样做的原因：

- 策略定义是可复用对象
- champion 是“某次搜索结果中的最佳选择”
- 两者职责不同，不应混成一个长期格式


## 4. 存储设计

### 4.1 建议采用统一的后端文件存储

基于当前仓库的实现风格，优先采用文件存储而非数据库。

原因：

- 现有 Stage4 / Champion 已经大量使用 JSON 文件
- 离线脚本可直接写入
- 前端 API 和离线脚本都能共享同一套目录结构
- 成本低，迁移平滑

### 4.2 目录布局

建议新增统一目录：

```text
data/strategies/
  profiles/
    {profile_id}.json
  champions/
    single_stock/{scope_key}.json
    stock_group/{scope_key}.json
    cross_sectional/{scope_key}.json
```

其中：

- `profiles/` 是唯一的策略定义主存储
- `champions/` 是 scope 到 `profile_id` 的绑定关系

### 4.3 兼容期读取顺序

为了平滑迁移，建议 `FactorProfileRegistryService` 新增统一读取顺序：

1. 运行时 profile
2. `data/strategies/profiles/`
3. 旧配置文件 `config/temporal_pool_profiles.json` / `config/cross_sectional_profiles.json`
4. 旧目录 `data/champions/inline_profiles/`

兼容期写入策略：

- 新创建策略统一写 `data/strategies/profiles/`
- 旧 `inline_profiles` 只读不再新增


## 5. 统一运行模型

### 5.1 页面手工模式和 Stage4 模式共用同一引擎

统一页的两个模式只是“策略来源不同”，不是“策略结构不同”：

1. 手工模式
   - 页面编辑因子、权重、risk_filter、params
   - 保存后生成一个统一的 profile strategy

2. Stage4 / Champion 模式
   - 从 champion 或 profile 列表加载
   - 加载后得到同样的 profile strategy

两者最终回测都调用同一后端入口：

- `profile_id`
- `codes`
- `start_date`
- `end_date`
- `initial_capital`
- `commission_rate`
- `slippage`

### 5.2 统一支持的参数

统一页的策略参数收敛为：

- `signal factors`
- `risk_filter`
- `rebalance`
- `hold_days`
- `top_n`
- `score_threshold`
- `percentile_window`

页面运行参数保留：

- `stock_codes`
- `start_date`
- `end_date`
- `initial_capital`
- `commission_rate`
- `slippage`

### 5.3 参数语义

- `risk_filter`
  - 采用现有 `TemporalScoringService` 语义
  - 过滤命中后，该标的当期得分归零

- `rebalance`
  - 采用现有 `TemporalFilterValidationService.run_temporal_pool_backtest` 语义
  - 支持 `W-FRI`、`2W-FRI`、`M`

- `hold_days`
  - 有值时优先于 `rebalance`
  - 后端已存在该优先级逻辑

- `score_threshold`
  - 回测选股时应用最低分门槛


## 6. API 设计

### 6.1 策略定义 API

新增统一策略接口：

#### `GET /api/strategies`

用途：

- 列出所有可用策略定义
- 支持按 `source`、`mode`、`tag` 过滤

返回示例：

```json
{
  "items": [
    {
      "id": "strategy_profile_xxx",
      "description": "多均线策略",
      "source": "stage4_search",
      "mode": "temporal_pool",
      "updated_at": "2026-03-22T00:00:00Z"
    }
  ]
}
```

#### `GET /api/strategies/{profile_id}`

用途：

- 获取完整统一策略定义

#### `POST /api/strategies`

用途：

- 新建或保存手工策略

请求体即统一 profile strategy

#### `PUT /api/strategies/{profile_id}`

用途：

- 更新策略

### 6.2 Champion API

保留现有 `/api/search/champions/...` 查询能力。

同时新增或增强：

#### `POST /api/strategies/import-champion`

用途：

- 将 champion 引用规范化成统一 profile strategy
- 若 champion 已指向统一 profile，则直接返回该 profile
- 若是旧格式 champion，则执行一次 materialize

请求示例：

```json
{
  "scope_type": "single_stock",
  "scope_key": "002594"
}
```

### 6.3 回测 API

新增统一回测入口：

#### `POST /api/backtest/profile`

请求示例：

```json
{
  "profile_id": "strategy_profile_xxx",
  "stock_codes": ["002594"],
  "start_date": "2025-01-01",
  "end_date": "2026-03-22",
  "initial_capital": 1000000,
  "commission_rate": 0.0003,
  "slippage": 0.0
}
```

返回示例：

```json
{
  "success": true,
  "data": {
    "profile": {
      "id": "strategy_profile_xxx",
      "description": "多均线策略"
    },
    "metrics": {
      "annual_return": 0.25,
      "sharpe": 1.6,
      "max_drawdown": -0.12,
      "total_return": 0.38
    },
    "equity": {
      "dates": ["2025-01-02"],
      "values": [1000000]
    },
    "drawdown": {
      "dates": ["2025-01-02"],
      "values": [0.0]
    },
    "holdings": [
      {
        "rebalance_date": "2025-01-03",
        "codes": ["002594"]
      }
    ]
  }
}
```

说明：

- 当前 `run_temporal_pool_backtest()` 只返回 metrics
- 需要在服务层扩展一个 richer 版本，返回 `nav_series / drawdown / holdings`

建议新增：

- `run_temporal_pool_backtest_detail(...)`

该接口由新页面使用，旧 `/api/backtest/single` 保持兼容但标记为 legacy


## 7. 前端页面设计

### 7.1 页面结构

继续使用现有 `Backtesting` 页面，但改为三块：

1. 策略来源
   - `手工创建`
   - `加载已保存策略`
   - `加载 Stage4 / Champion`

2. 策略编辑区
   - 无论来源是什么，都落到同一个编辑器

3. 结果区
   - 指标卡片
   - 净值曲线
   - 回撤曲线
   - 持仓/调仓记录

### 7.2 手工创建模式

手工模式不再仅仅是：

- 单因子
- 多因子

而是升级为 profile 编辑器：

- 因子列表表格
  - 因子名
  - `role = signal | risk_filter`
  - `direction`
  - `weight`
  - `threshold`（仅 risk_filter 显示）

- 参数区域
  - `rebalance`
  - `hold_days`
  - `top_n`
  - `score_threshold`
  - `percentile_window`

- 回测区域
  - 股票
  - 日期范围
  - 资金/费率/滑点

### 7.3 加载 Stage4 / Champion 模式

加载模式支持两种入口：

1. 按 scope 加载 champion
   - `single_stock + code`
   - `stock_group + group_hash`
   - `cross_sectional + universe`

2. 直接按 `profile_id` 加载

加载完成后：

- 把统一策略对象灌入同一编辑器
- 用户可以直接回测
- 用户也可以“另存为”新策略

### 7.4 已保存策略

已保存策略列表不再读取 `localStorage`，改为读取后端 `GET /api/strategies`。

列表展示字段建议：

- 名称 / 描述
- `source`
- `mode`
- `scope`（若来自 champion）
- 更新时间
- 关键参数摘要
  - `risk_filter` 数量
  - `rebalance`
  - `hold_days`
  - `score_threshold`


## 8. 后端服务改造点

### 8.1 新增 `StrategyProfileRegistryService`

职责：

- 统一读写 `data/strategies/profiles/`
- 列表查询
- 保存、更新、删除 profile
- 向旧格式兼容读取

### 8.2 调整 `FactorProfileRegistryService`

新增统一目录读取能力，作为运行时主入口。

### 8.3 调整 `ChampionStrategyService`

Stage4 产出时：

- 不再只写 `data/champions/inline_profiles`
- 统一写入 `data/strategies/profiles/{profile_id}.json`
- champion 文件只保存绑定信息和评估结果

### 8.4 扩展 `TemporalFilterValidationService`

新增 detail 版回测方法，返回：

- metrics
- equity series
- drawdown series
- holdings by rebalance date
- active flags

### 8.5 新增 `Strategy API Router`

新增后端统一策略接口，供页面加载与保存使用


## 9. 兼容与迁移

### 9.1 旧 Stage4 产物兼容

旧 champion 加载时，若不存在统一 profile 文件，则按以下规则惰性迁移：

- 优先使用 `champion.inline_profile`
- 其次从 `champion.config` 提取 `factors + params`
- 生成统一 profile
- 写入 `data/strategies/profiles/`

### 9.2 旧前端 localStorage 兼容

前端首次进入新页面时可执行一次迁移：

- 读取 `backtest_strategies`
- 将旧单因子/多因子表单转成统一 profile
- 默认补齐：
  - `risk_filter = []`
  - `rebalance = "W-FRI"`
  - `hold_days = 5`
  - `score_threshold = null`
  - `top_n = 1`（单股票）或 `10`（股票池）
  - `percentile_window = 252`

迁移完成后：

- 写入后端统一存储
- 可选择清空本地 `localStorage`

### 9.3 兼容策略

兼容期建议：

- 旧 `/api/backtest/single` 保留
- 新页面只使用统一 profile API
- 旧 localStorage 只读迁移，不再继续写入


## 10. 实施阶段建议

### Phase 1：统一存储与统一回测入口

- 新增 `data/strategies/profiles/`
- 新增 `StrategyProfileRegistryService`
- 新增 `/api/strategies/*`
- 新增 `/api/backtest/profile`
- Stage4 改为写统一 profile

产出：

- 页面和 Stage4 至少能读写同一种策略格式

### Phase 2：前端升级为双模式统一页

- 现有页面新增“策略来源”
- 替换本地保存列表为后端策略列表
- 新增 profile 编辑器
- 新增 champion 加载入口

产出：

- 页面可以手工创建和加载 Stage4 策略

### Phase 3：迁移与清理

- localStorage 一次性迁移
- 旧 inline profile 目录只读
- 评估是否下线 legacy `/api/backtest/single`


## 11. 风险与取舍

### 11.1 主要风险

- 当前页面是一个大组件，前端改动集中，容易变复杂
- profile 回测详情返回结构需要重新定义
- 旧单因子/多因子语义迁移到 profile 后，与旧页面结果可能存在轻微口径差异

### 11.2 设计取舍

本设计选择：

- 不把 Stage4 精简成旧表单
- 不让旧表单继续做系统主策略格式
- 统一到 profile strategy

原因：

- profile 是更强的超集模型
- 能表达 `risk_filter/rebalance/hold_days/score_threshold`
- 可以被页面和离线流程共同生产与消费


## 12. 最终建议

最终建议如下：

- 采用“中改版：现有页面升级成双模式统一页”
- 统一对象选择 `profile strategy`
- 统一存储选择后端文件仓库
- Champion 退化为“绑定关系”，不再承担策略定义职责
- 页面手工创建与 Stage4 离线产出都写入同一 profile 格式

这是当前最平衡的方案：

- 不牺牲 Stage4 的真实策略表达能力
- 不需要整页重做
- 能保证“页面生成”和“离线跑出来”的策略真正互通
