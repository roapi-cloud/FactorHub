"""离线因子导出示例（简化版）。

目标：
1) 支持外部 CSV 输入（也可用内置样例数据）
2) 生成因子公式 JSON + 代码文件（格式不变）
3) 默认写入 SQLite，便于后续离线加因子
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import List

CURRENT = Path(__file__).resolve()
PROJECT_ROOT = next(
    (parent for parent in CURRENT.parents if (parent / "pyproject.toml").exists()),
    CURRENT.parent,
)
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_dataframe(csv_path: str | None):
    import numpy as np
    import pandas as pd

    if csv_path:
        df = pd.read_csv(csv_path)
        required_cols = {"open", "high", "low", "close", "volume"}
        missing = required_cols - set(df.columns)
        if missing:
            raise ValueError(f"CSV缺少必要列: {sorted(missing)}")
        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.set_index("date")
        return df

    # 内置离线示例数据（可复现）
    np.random.seed(42)
    n = 260
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    base = np.linspace(12, 20, n)
    close = base + np.random.normal(0, 0.5, n)
    open_ = close + np.random.normal(0, 0.2, n)
    high = np.maximum(open_, close) + np.abs(np.random.normal(0, 0.3, n))
    low = np.minimum(open_, close) - np.abs(np.random.normal(0, 0.3, n))
    volume = np.random.randint(100_000, 2_000_000, n)
    return pd.DataFrame(
        {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
        index=idx,
    )


def _build_code_file(output_path: Path, expressions: List[str]) -> None:
    blocks: List[str] = [
        '"""自动生成的因子代码示例文件。\n\n每个函数接收 df(DataFrame) 并返回因子序列。\n"""\n\n',
        "from backend.services.factor_service import factor_service\n",
    ]

    for i, expr in enumerate(expressions, start=1):
        blocks.append(
            f"\n# ===== 因子 {i:02d}: {expr} =====\n"
            f"def calculate_factor_{i:02d}(df):\n"
            f"    \"\"\"计算因子：{expr}\"\"\"\n"
            f"    return factor_service.calculator.calculate(df, {expr!r})\n"
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("".join(blocks), encoding="utf-8")


def _ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS generated_factors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            expression TEXT NOT NULL,
            generation_method TEXT NOT NULL,
            ic_value REAL,
            ir_value REAL,
            turnover_value REAL,
            stability_score REAL,
            validation_score REAL,
            is_valid INTEGER DEFAULT 0,
            is_saved INTEGER DEFAULT 0,
            factor_name TEXT,
            complexity TEXT,
            metadata TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()


def _save_to_sqlite(db_path: Path, payload: dict, generated_code: str, batch_tag: str) -> int:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    _ensure_table(conn)

    inserted_count = 0
    try:
        for i, factor in enumerate(payload["factors"], start=1):
            metadata_payload = {
                "batch_tag": batch_tag,
                "format": "offline_generated_formulas_v1",
                "input": payload["input"],
                "factor": factor,
                "factor_order": i,
                "generated_code": generated_code,
            }

            # 所需字段默认值统一在这里定义，便于后续离线扩展
            row = {
                "expression": factor["expression"],
                "generation_method": "offline_example",
                "ic_value": None,
                "ir_value": None,
                "turnover_value": None,
                "stability_score": None,
                "validation_score": None,
                "is_valid": 0,
                "is_saved": 0,
                "factor_name": None,
                "complexity": factor.get("complexity"),
                "metadata": json.dumps(metadata_payload, ensure_ascii=False),
            }

            conn.execute(
                """
                INSERT INTO generated_factors (
                    expression, generation_method, ic_value, ir_value, turnover_value,
                    stability_score, validation_score, is_valid, is_saved, factor_name,
                    complexity, metadata
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["expression"],
                    row["generation_method"],
                    row["ic_value"],
                    row["ir_value"],
                    row["turnover_value"],
                    row["stability_score"],
                    row["validation_score"],
                    row["is_valid"],
                    row["is_saved"],
                    row["factor_name"],
                    row["complexity"],
                    row["metadata"],
                ),
            )
            inserted_count += 1

        conn.commit()
    finally:
        conn.close()

    return inserted_count


def main() -> None:
    parser = argparse.ArgumentParser(description="离线导出因子公式与代码示例")
    parser.add_argument("--csv", default=None, help="外部OHLCV CSV路径（可选）")
    parser.add_argument("--limit", type=int, default=10, help="导出因子数量")
    parser.add_argument(
        "--base-factors",
        nargs="*",
        default=["close", "open", "high", "low", "volume"],
        help="用于组合的基础因子名",
    )
    parser.add_argument("--no-save-db", action="store_true", help="不写入 SQLite（默认写入）")
    args = parser.parse_args()

    from backend.services.factor_generator_service import factor_generator_service

    df = _load_dataframe(args.csv)
    selected = factor_generator_service.generate_hybrid_factors(
        base_factors=args.base_factors,
        n_factors=max(args.limit, 1),
    )[: args.limit]

    reports_dir = Path("data/reports")
    reports_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    formula_path = reports_dir / f"offline_generated_formulas_{ts}.json"
    code_path = reports_dir / f"offline_generated_code_{ts}.py"

    payload = {
        "created_at": datetime.now().isoformat(),
        "input": {
            "csv": args.csv,
            "rows": int(len(df)),
            "columns": list(df.columns),
            "base_factors": args.base_factors,
            "limit": args.limit,
        },
        "factors": selected,
    }

    formula_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _build_code_file(code_path, [f["expression"] for f in selected])

    inserted = 0
    if not args.no_save_db:
        inserted = _save_to_sqlite(
            db_path=Path("data/db/factorflow.db"),
            payload=payload,
            generated_code=code_path.read_text(encoding="utf-8"),
            batch_tag=ts,
        )

    print("✅ 导出完成")
    print(f"公式文件: {formula_path}")
    print(f"代码文件: {code_path}")
    if not args.no_save_db:
        print(f"SQLite写入: {inserted} 条")


if __name__ == "__main__":
    main()
