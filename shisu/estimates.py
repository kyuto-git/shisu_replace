"""率のベイズ推定（aggregate_stats_ver2.py で使用）。

既定（AGG_PROP_MODE=lower）: 事前 Beta(0.5, 0.5)（Jeffreys）の事後分布の下側 q 点（q=0.10）。
複勝率だけは、観測頻度が PLC_SWITCH_THRESHOLD_PCT 以下なら上側 q 点（q=0.90）を使う。
AGG_PROP_MODE=mean なら事後平均 (k+α)/(n+α+β)。
scipy があれば厳密な Beta 分位点、無ければ正規近似（[0,1] にクリップ）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

try:
    from scipy.stats import beta as _beta
except Exception:  # pragma: no cover - scipy 無し環境用
    _beta = None


def beta_quantile(k: pd.Series, n: pd.Series, q: float, a0: float = 0.5, b0: float = 0.5) -> pd.Series:
    k = k.astype(float)
    n = n.astype(float)
    a = k + a0
    b = n - k + b0
    if _beta is not None:
        return pd.Series(_beta.ppf(q, a, b), index=n.index)
    from statistics import NormalDist
    mean = a / (a + b)
    var = (a * b) / (((a + b) ** 2) * (a + b + 1.0))
    z = NormalDist().inv_cdf(q)
    return pd.Series(np.clip(mean + z * np.sqrt(var), 0.0, 1.0), index=n.index)


def posterior_mean(k: pd.Series, n: pd.Series, a0: float = 0.5, b0: float = 0.5) -> pd.Series:
    k = k.astype(float)
    n = n.astype(float)
    return ((k + a0) / (n + a0 + b0)).where(n > 0)


def has_scipy() -> bool:
    return _beta is not None
