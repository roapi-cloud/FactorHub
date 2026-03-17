"""
外部数据进入统一数据层 + 函数式因子生成示例

运行：
    uv run python examples/external_factor_generation_example.py
"""

import pandas as pd
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.services.data_service import DataService
from backend.services.factor_service import FactorCalculator


def build_enriched_dataframe() -> pd.DataFrame:
    """构造包含外部特征的行情数据（示例中用 mock 数据避免依赖外网）"""
    service = DataService()

    # 1) mock 基础行情数据（实际生产由 get_stock_data 从统一数据源获取）
    idx = pd.date_range("2024-01-01", periods=8, freq="D")
    base_df = pd.DataFrame(
        {
            "open": [10.0, 10.2, 10.1, 10.3, 10.4, 10.6, 10.8, 10.9],
            "high": [10.3, 10.4, 10.3, 10.6, 10.7, 10.8, 11.0, 11.2],
            "low": [9.9, 10.0, 9.9, 10.1, 10.2, 10.4, 10.6, 10.7],
            "close": [10.1, 10.3, 10.0, 10.5, 10.6, 10.7, 10.95, 11.1],
            "volume": [100, 120, 110, 130, 125, 140, 150, 145],
        },
        index=idx,
    )

    # monkey patch：让示例稳定可复现
    service.get_stock_data = lambda **kwargs: base_df.copy()

    # 2) 外部特征拉取函数（生产里可替换为 API / DB / 文件）
    def external_fetcher(stock_code: str, start_date: str, end_date: str) -> pd.DataFrame:
        _ = stock_code, start_date, end_date
        return pd.DataFrame(
            {
                "date": idx,
                "sentiment_score": [0.2, 0.1, -0.1, 0.3, 0.15, 0.25, 0.4, 0.35],
                "northbound_netflow": [10, 15, -5, 20, 18, 25, 30, 22],
            }
        )

    # 3) 统一数据层合并（使用你新增的 get_enriched_stock_data）
    enriched_df = service.get_enriched_stock_data(
        stock_code="000001.SZ",
        start_date="2024-01-01",
        end_date="2024-01-08",
        external_fetcher=external_fetcher,
    )

    return enriched_df


def generate_factor_with_function(enriched_df: pd.DataFrame) -> pd.Series:
    """函数式因子示例：融合外部特征 + 价量特征"""
    factor_code = '''def calculate_factor(df):
    momentum_3 = df["close"] / df["close"].shift(3) - 1
    sent_ma = df["sentiment_score"].rolling(3, min_periods=1).mean()
    flow_ma = df["northbound_netflow"].rolling(3, min_periods=1).mean()
    return 0.5 * sent_ma + 0.3 * (flow_ma / 100.0) + 0.2 * momentum_3
'''

    calculator = FactorCalculator()
    factor_series = calculator.calculate(enriched_df, factor_code)
    return factor_series


def main() -> None:
    enriched_df = build_enriched_dataframe()
    factor_series = generate_factor_with_function(enriched_df)

    # 基本校验（模拟测试）
    assert "sentiment_score" in enriched_df.columns
    assert "northbound_netflow" in enriched_df.columns
    assert len(factor_series) == len(enriched_df)
    assert factor_series.notna().sum() >= len(enriched_df) - 3  # 前3天可能因动量窗口产生NaN

    print("[OK] enriched columns:", list(enriched_df.columns))
    print("[OK] factor head:")
    print(factor_series.head(5))
    print("[OK] factor tail:")
    print(factor_series.tail(3))


if __name__ == "__main__":
    main()
