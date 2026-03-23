"""Qlib 因子提取服务。

直接调用 qlib 官方 Handler（Alpha158/Alpha360 等）提取因子，
不通过额外 Router 规划层。
"""
from __future__ import annotations

import shutil
from pathlib import Path
from tempfile import mkdtemp
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from backend.services.data_service import data_service


class QlibFactorService:
    """Qlib 因子提取服务。"""

    SUPPORTED_FACTOR_SETS = {
        "Alpha158": 158,
        "Alpha360": 360,
        "Alpha158vwap": 158,
        "Alpha360vwap": 360,
    }

    def _import_qlib(self):
        """延迟导入 qlib，缺失时给出明确安装提示。"""
        try:
            import qlib
            from qlib.utils import init_instance_by_config

            return qlib, init_instance_by_config
        except ImportError as exc:
            raise ImportError(
                "未安装 qlib。请先安装依赖：pip install pyqlib"
            ) from exc

    def _build_qlib_dataset(
        self,
        stock_codes: List[str],
        start_date: str,
        end_date: str,
        output_dir: Path,
    ) -> Path:
        """把当前系统 OHLCV 数据转成 qlib 本地数据目录。"""
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        fields = ["open", "high", "low", "close", "vwap", "volume"]
        raw = data_service.get_multiple_stocks_data(stock_codes, start_date, end_date)
        if not raw:
            raise ValueError("未获取到任何股票数据")

        records: list[pd.DataFrame] = []
        normalized_codes = [str(code).replace('.SH', '').replace('.SZ', '').zfill(6) for code in stock_codes]

        for src_code, code in zip(stock_codes, normalized_codes):
            df = raw.get(src_code)
            if df is None or df.empty:
                continue

            item = df.copy()
            item = item.reset_index().rename(columns={"index": "date"})
            if "date" not in item.columns:
                item = item.rename(columns={item.columns[0]: "date"})
            item["symbol"] = code

            if "vwap" not in item.columns:
                item["vwap"] = np.where(
                    item[["high", "low", "close"]].notna().all(axis=1),
                    (item["high"] + item["low"] + item["close"]) / 3,
                    np.nan,
                )

            for field in fields:
                if field not in item.columns:
                    item[field] = np.nan

            records.append(item[["date", "symbol", *fields]])

        if not records:
            raise ValueError("股票数据为空，无法构建 qlib 数据集")

        data = pd.concat(records, ignore_index=True)
        data["date"] = pd.to_datetime(data["date"])
        data = data.dropna(subset=["date", "symbol"]).sort_values(["symbol", "date"])

        cal_dir = output_dir / "calendars"
        ins_dir = output_dir / "instruments"
        feat_root = output_dir / "features"
        cal_dir.mkdir(exist_ok=True)
        ins_dir.mkdir(exist_ok=True)
        feat_root.mkdir(exist_ok=True)

        all_days = pd.DatetimeIndex(sorted(data["date"].dt.normalize().unique()))
        calendar_pos = {d: idx for idx, d in enumerate(all_days)}

        with (cal_dir / "day.txt").open("w", encoding="utf-8") as f:
            for d in all_days:
                f.write(f"{d.date()}\n")

        with (ins_dir / "all.txt").open("w", encoding="utf-8") as f:
            for sym, g in data.groupby("symbol"):
                f.write(f"{sym}\t{g['date'].min().date()}\t{g['date'].max().date()}\n")

        for sym, g in data.groupby("symbol"):
            sym_dir = feat_root / str(sym).lower()
            sym_dir.mkdir(exist_ok=True)
            g = g.drop_duplicates("date").set_index("date").sort_index()

            start_dt = pd.Timestamp(g.index.min()).normalize()
            end_dt = pd.Timestamp(g.index.max()).normalize()
            day_slice = all_days[(all_days >= start_dt) & (all_days <= end_dt)]
            g = g.reindex(day_slice)
            start_index = calendar_pos[start_dt]

            for field in fields:
                arr = g[field].to_numpy(dtype="float32")
                out = np.hstack([np.array([start_index], dtype="float32"), arr])
                with (sym_dir / f"{field}.day.bin").open("wb") as f:
                    out.tofile(f)

        (output_dir / ".version").write_text("1", encoding="utf-8")
        return output_dir

    def generate_qlib_factors(
        self,
        stock_codes: List[str],
        start_date: str,
        end_date: str,
        factor_set: str = "Alpha158",
        factor_names: Optional[List[str]] = None,
    ) -> pd.DataFrame:
        """直接调用 qlib 提取因子。"""
        if factor_set not in self.SUPPORTED_FACTOR_SETS:
            raise ValueError(f"不支持的 factor_set: {factor_set}")

        qlib, init_instance_by_config = self._import_qlib()

        tmp_dir = Path(mkdtemp(prefix="qlib_data_"))
        try:
            self._build_qlib_dataset(stock_codes, start_date, end_date, tmp_dir)
            qlib.init(provider_uri=str(tmp_dir), region="cn")

            handler_conf = {
                "class": factor_set,
                "module_path": "qlib.contrib.data.handler",
                "kwargs": {
                    "start_time": start_date,
                    "end_time": end_date,
                    "fit_start_time": start_date,
                    "fit_end_time": end_date,
                    "instruments": "all",
                },
            }
            dataset_conf = {
                "class": "DatasetH",
                "module_path": "qlib.data.dataset",
                "kwargs": {
                    "handler": handler_conf,
                    "segments": {"train": [start_date, end_date]},
                },
            }

            dataset = init_instance_by_config(dataset_conf)
            df = dataset.prepare("train", col_set="feature")

            if isinstance(df.columns, pd.MultiIndex):
                factor_df = df.loc[:, ("feature", slice(None))].copy()
                factor_df.columns = [c[1] for c in factor_df.columns]
            else:
                factor_df = df.copy()

            if factor_names:
                miss = [name for name in factor_names if name not in factor_df.columns]
                if miss:
                    raise ValueError(f"请求因子不存在: {miss[:5]}")
                factor_df = factor_df[factor_names]

            dates = pd.to_datetime(factor_df.index.get_level_values(0))
            codes = [str(code).replace('.XSHG', '').replace('.XSHE', '').zfill(6) for code in factor_df.index.get_level_values(1)]
            factor_df.index = pd.MultiIndex.from_arrays([dates, codes], names=["date", "stock_code"])
            return factor_df.sort_index()
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)


qlib_factor_service = QlibFactorService()
