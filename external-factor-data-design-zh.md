# 外部数据接入因子计算设计文档（函数式因子专版）

## 1. 背景与问题

你当前可以通过函数形式定义因子：

```python
def calculate_factor(df):
    # 返回 pd.Series
    ...
```

系统会在运行时执行该函数代码并传入行情 `df`，因此在技术上函数内部确实可以发 HTTP 请求、读取外部 API，或加载本地文件后参与因子计算。

但是否“应该在函数内部实时拉外部数据”需要从可复现性、性能和稳定性评估。

---

## 2. 两种方案对比

### 方案 A：在 `calculate_factor` 函数内直接拉取外部数据

#### 优点
- 上手快，单个实验验证最快。
- 因子逻辑和外部数据调用写在一起，原型阶段方便。

#### 缺点
- **回测不可复现风险高**：同一时间段二次回测可能得到不同结果（外部接口历史修订、接口限流、请求失败）。
- **性能差**：多股票、多因子回测时会重复请求。
- **稳定性弱**：网络波动/API 限流直接导致因子计算失败。
- **可观测性差**：数据质量问题埋在用户函数中，不易监控。

### 方案 B：外部数据先进入统一数据层，再给因子函数使用（推荐）

#### 优点
- **可复现**：回测与实盘读取同一份已落地特征数据。
- **性能高**：统一缓存/批量拉取，避免重复调用。
- **稳定性高**：失败重试、降级、数据校验都在数据层统一处理。
- **治理清晰**：字段命名、时间对齐、缺失值处理有统一规范。

#### 缺点
- 初期建设成本高于方案 A。

---

## 3. 推荐策略（生产实践）

采用“**分层混合方案**”：

1. **研发阶段**：允许函数内临时拉取（快速验证 alpha）。
2. **准生产/生产阶段**：必须迁移到统一数据层（ETL + 缓存 + 质量校验）。
3. **在线兜底**：若要追求新鲜度，可在数据层增加短 TTL 实时补数，但禁止在用户因子函数中直接请求外网。

---

## 4. 架构设计

```text
外部 API / 文件 / DB
        │
        ▼
ExternalDataProvider（按数据源封装）
        │
        ▼
Feature Store（按 symbol + date 落地，带版本）
        │
        ▼
DataService（统一 join OHLCV + 外部特征）
        │
        ▼
Factor calculate_factor(df)（只消费 df，不直接联网）
```

### 4.1 数据契约（建议）

统一按日频因子特征入库（最简版字段）：

- `symbol`: 股票代码（如 `000001.SZ`）
- `date`: 交易日（`YYYY-MM-DD`）
- `feature_name`: 特征名（如 `news_sentiment`）
- `feature_value`: 数值
- `asof_time`: 数据可得时间（防未来函数）
- `source`: 数据源标识
- `version`: 特征版本号

### 4.2 时间对齐规则

- T 日收盘后才能拿到的数据，只能用于 **T+1** 交易决策。
- 统一在数据层处理 `asof_time <= 决策时点` 的过滤。

### 4.3 缓存规则

- 原始 API 响应缓存（分钟级 TTL）。
- 清洗后特征缓存（小时/天级 TTL）。
- 因子计算结果缓存（按 stock/date/factor hash）。

---

## 5. 示例

## 5.1 不推荐（仅实验阶段）：函数里直接拉 API

```python
def calculate_factor(df):
    import requests
    import pandas as pd

    # 假设外部接口返回 date -> sentiment
    resp = requests.get("https://example.com/api/sentiment", timeout=3)
    resp.raise_for_status()
    payload = resp.json()

    ext = pd.DataFrame(payload)  # columns: date, sentiment
    ext["date"] = pd.to_datetime(ext["date"])
    ext = ext.set_index("date").sort_index()

    merged = df.join(ext[["sentiment"]], how="left")
    merged["sentiment"] = merged["sentiment"].ffill()

    # 例子：情绪 * 20日动量
    momentum_20 = merged["close"] / merged["close"].shift(20) - 1
    return merged["sentiment"] * momentum_20
```

> 该方式容易遇到超时、限流、重跑不一致，不建议上线。

## 5.2 推荐：先把外部数据合并进 `df`，函数只计算

假设进入因子函数时，`df` 已包含列：
- `sentiment_score`
- `northbound_netflow`

```python
def calculate_factor(df):
    # 标准化外部特征
    sent_z = (df["sentiment_score"] - df["sentiment_score"].rolling(60).mean()) / \
             df["sentiment_score"].rolling(60).std()
    flow_z = (df["northbound_netflow"] - df["northbound_netflow"].rolling(60).mean()) / \
             df["northbound_netflow"].rolling(60).std()

    # 基础价量因子
    momentum_20 = df["close"] / df["close"].shift(20) - 1

    # 融合因子
    factor = 0.5 * sent_z + 0.3 * flow_z + 0.2 * momentum_20
    return factor
```

该方式中，函数保持“纯计算”，最利于回测复现与线上稳定运行。

---

## 6. 落地实施清单

1. 新建 `ExternalDataProvider` 接口（按源拆实现）。
2. 增加定时任务：批量拉取、重试、幂等写入。
3. DataService 增加 `get_enriched_stock_data()`，统一合并外部特征。
4. 因子执行前强制数据校验：缺失率、极值、时间对齐。
5. 指标监控：拉取成功率、延迟、缺失率、特征漂移。
6. 逐步把“函数内联网”的因子迁移为“函数纯计算”的标准模式。

---

## 7. 最终建议（简版）

- **能不能在函数里拉数据？** 能。
- **应不应该长期这样做？** 不建议。
- **更好的方式？** 外部数据先进入统一数据层，函数只负责计算。
- **什么时候允许函数直连 API？** 仅限研究原型阶段，且尽快迁移。
