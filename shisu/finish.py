"""着順の正規化と的中フラグ。

- 取消・除外: 分母から除外
- 失格・中止: 分母に含め、着順 = 頭数（不明なら18）
- 数字で始まる表記（'3', '3(降)' など）は先頭の数字を着順とする
TARGET の1文字表記（取/除/外/消/失/中/止。★6タブの過去CSVでは 除外=外・取消=消）にも対応する。
"""
from __future__ import annotations

import pandas as pd

from .csvio import norm_series

CANCEL_PATTERN = r"取消|除外|^取$|^除$|^外$|^消$"   # ★6 の過去CSVは 除外=「外」・取消=「消」
DNF_PATTERN = r"失格|中止|^失$|^中$|^止$"


def parse_finish(finish: pd.Series, heads: pd.Series | None = None) -> pd.DataFrame:
    s = norm_series(finish)
    is_cancel = s.str.contains(CANCEL_PATTERN, regex=True, na=False)
    is_dnf = s.str.contains(DNF_PATTERN, regex=True, na=False) & ~is_cancel
    num = pd.to_numeric(s.str.extract(r"^(\d+)", expand=False), errors="coerce")
    if heads is not None:
        h = pd.to_numeric(norm_series(heads), errors="coerce").fillna(18).clip(lower=1, upper=18)
    else:
        h = pd.Series(18.0, index=s.index)
    num = num.mask(is_dnf, h)
    denom = ~is_cancel
    return pd.DataFrame({
        "finish_num": num,
        "denom": denom,
        "win": (num == 1) & denom,
        "second": (num == 2) & denom,
        "third": (num == 3) & denom,
        "place": (num <= 3) & denom,
        "board": (num <= 5) & denom,
    }, index=s.index)
