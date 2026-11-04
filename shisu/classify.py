"""条件ヒット馬の分類（/entries/hits と / のプリセットで共通）。

値はすべて DB の値をそのまま使う（率は 0–100 の %）。
  fv  = 複勝率_val（推定値: 事後分布の下側10%点。頻度20%以下なら上側90%点）
  rv  = 連対率_val（推定値: 下側10%点）
  top5= 掲示板率_val（頻度）
  frc = 複勝回収値, tra = 単勝適正回収値, cnt = 件数
欠損（None）の比較はすべて「満たさない」と扱う。
判定順は 消し → 絶対軸 → 軸候補 → 相手 で、最初に当てはまった区分を1つだけ返す。

旧版との違い:
  - 絶対軸の連対率は文字列の頻度ではなく 連対率_val（推定値）
  - 0〜1 スケールの自動判定は廃止（DB は常に 0–100。旧版は 0.8% を 80% と誤読し得た）
"""
from __future__ import annotations

from typing import Mapping

CATEGORIES = ("keshi", "abs", "axis", "opp")
CATEGORY_LABELS = {"abs": "絶対軸", "axis": "軸候補", "keshi": "消し候補", "opp": "相手候補"}

# --- 絶対軸 ---
ABS_CNT = 10
ABS_REN = 80.0
ABS_FRC = 100.0
ABS_TRA = 100.0
# --- 軸候補 ---
AX_CNT = 10
AX_FV_80 = 80.0
AX_RV_60 = 60.0
AX_FV_70 = 70.0
AX_RV_50 = 50.0
AX_TOP5_90 = 90.0
AX_TOP5_80 = 80.0
AX_FRC_100 = 100.0
# --- 相手候補 ---
OP_CNT = 30
OP_FV_60 = 60.0
OP_RV_50 = 50.0
OP_FV_33 = 33.0
OP_FRC_150 = 150.0
OP_TOP5_70 = 70.0
OP_TOP5_50 = 50.0
# --- 消し候補 ---
KS_CNT = 20
KS_PLC_MAX_ANY = 5.0       # 全モード: 複勝率 < 5
KS_TOP5_MAX_ANY = 10.0     #           かつ 掲示板率 < 10
KS_PLC_MAX_10U = 20.0      # odds10u: 複勝率 < 20


def _ge(v, t) -> bool:
    return v is not None and v >= t


def _lt(v, t) -> bool:
    return v is not None and v < t


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f  # NaN → None


def classify(rec: Mapping, mode: str) -> str | None:
    """rec: cnt, fv, rv, top5, frc, tra（欠損は None）。mode: オッズ帯（normal/odds10u/...）。"""
    cnt = _num(rec.get("cnt"))
    fv = _num(rec.get("fv"))
    rv = _num(rec.get("rv"))
    top5 = _num(rec.get("top5"))
    frc = _num(rec.get("frc"))
    tra = _num(rec.get("tra"))

    keshi = (
        (_ge(cnt, KS_CNT) and _lt(fv, KS_PLC_MAX_ANY) and _lt(top5, KS_TOP5_MAX_ANY))
        or (mode == "odds10u" and _ge(cnt, KS_CNT) and _lt(fv, KS_PLC_MAX_10U))
    )
    if keshi:
        return "keshi"

    if _ge(cnt, ABS_CNT) and _ge(rv, ABS_REN) and (_ge(frc, ABS_FRC) or _ge(tra, ABS_TRA)):
        return "abs"

    if _ge(cnt, AX_CNT) and (
        (_ge(fv, AX_FV_80) and _ge(top5, AX_TOP5_90))
        or (_ge(rv, AX_RV_60) and _ge(top5, AX_TOP5_90))
        or (_ge(fv, AX_FV_70) and _ge(top5, AX_TOP5_80) and _ge(frc, AX_FRC_100))
        or (_ge(rv, AX_RV_50) and _ge(top5, AX_TOP5_80) and _ge(frc, AX_FRC_100))
    ):
        return "axis"

    if _ge(cnt, OP_CNT) and (
        (_ge(fv, OP_FV_60) and _ge(top5, OP_TOP5_70))
        or (_ge(rv, OP_RV_50) and _ge(top5, OP_TOP5_70))
        or (_ge(fv, OP_FV_33) and _ge(frc, OP_FRC_150) and _ge(top5, OP_TOP5_50))
    ):
        return "opp"
    return None


# ---------------------------------------------------------------------------
# SQL 版（/ 画面のプリセット用）。classify() と同じ結果になることをテストで確認している。
# ---------------------------------------------------------------------------
_COLS = {"cnt": '"件数"', "fv": '"複勝率_val"', "rv": '"連対率_val"', "top5": '"掲示板率_val"',
         "frc": '"複勝回収値"', "tra": '"単勝適正回収値"'}


def _c(field: str, op: str, value: float) -> str:
    # NULL は「満たさない」に倒す（Python 版の None と同じ）
    return f"IFNULL({_COLS[field]} {op} {float(value)!r}, 0)"


def _and(*xs: str) -> str:
    return "(" + " AND ".join(xs) + ")"


def _or(*xs: str) -> str:
    return "(" + " OR ".join(xs) + ")"


def _sql_raw(category: str, mode: str) -> str:
    if category == "keshi":
        any_mode = _and(_c("cnt", ">=", KS_CNT), _c("fv", "<", KS_PLC_MAX_ANY), _c("top5", "<", KS_TOP5_MAX_ANY))
        if mode == "odds10u":
            return _or(any_mode, _and(_c("cnt", ">=", KS_CNT), _c("fv", "<", KS_PLC_MAX_10U)))
        return any_mode
    if category == "abs":
        return _and(_c("cnt", ">=", ABS_CNT), _c("rv", ">=", ABS_REN),
                    _or(_c("frc", ">=", ABS_FRC), _c("tra", ">=", ABS_TRA)))
    if category == "axis":
        return _and(_c("cnt", ">=", AX_CNT), _or(
            _and(_c("fv", ">=", AX_FV_80), _c("top5", ">=", AX_TOP5_90)),
            _and(_c("rv", ">=", AX_RV_60), _c("top5", ">=", AX_TOP5_90)),
            _and(_c("fv", ">=", AX_FV_70), _c("top5", ">=", AX_TOP5_80), _c("frc", ">=", AX_FRC_100)),
            _and(_c("rv", ">=", AX_RV_50), _c("top5", ">=", AX_TOP5_80), _c("frc", ">=", AX_FRC_100)),
        ))
    if category == "opp":
        return _and(_c("cnt", ">=", OP_CNT), _or(
            _and(_c("fv", ">=", OP_FV_60), _c("top5", ">=", OP_TOP5_70)),
            _and(_c("rv", ">=", OP_RV_50), _c("top5", ">=", OP_TOP5_70)),
            _and(_c("fv", ">=", OP_FV_33), _c("frc", ">=", OP_FRC_150), _c("top5", ">=", OP_TOP5_50)),
        ))
    raise ValueError(category)


def sql_condition(category: str, mode: str) -> str:
    """classify(rec, mode) == category となる行の WHERE 句（判定順の排他も反映）。"""
    order = list(CATEGORIES)
    before = order[:order.index(category)]
    parts = [_sql_raw(category, mode)] + [f"NOT {_sql_raw(c, mode)}" for c in before]
    return _and(*parts)


def description_lines() -> dict[str, list[str]]:
    """画面に表示する条件の説明。"""
    return {
        "abs": [f"件数 ≥ {ABS_CNT} かつ 連対率 ≥ {ABS_REN:g}% かつ（複勝回収値 ≥ {ABS_FRC:g} または 単勝適正回収値 ≥ {ABS_TRA:g}）"],
        "axis": [
            f"件数 ≥ {AX_CNT} かつ 複勝率 ≥ {AX_FV_80:g}% かつ 掲示板率 ≥ {AX_TOP5_90:g}%",
            f"件数 ≥ {AX_CNT} かつ 連対率 ≥ {AX_RV_60:g}% かつ 掲示板率 ≥ {AX_TOP5_90:g}%",
            f"件数 ≥ {AX_CNT} かつ 複勝率 ≥ {AX_FV_70:g}% かつ 掲示板率 ≥ {AX_TOP5_80:g}% かつ 複勝回収値 ≥ {AX_FRC_100:g}",
            f"件数 ≥ {AX_CNT} かつ 連対率 ≥ {AX_RV_50:g}% かつ 掲示板率 ≥ {AX_TOP5_80:g}% かつ 複勝回収値 ≥ {AX_FRC_100:g}",
        ],
        "keshi": [
            f"（全モード）件数 ≥ {KS_CNT} かつ 複勝率 < {KS_PLC_MAX_ANY:g}% かつ 掲示板率 < {KS_TOP5_MAX_ANY:g}%",
            f"（10倍未満）件数 ≥ {KS_CNT} かつ 複勝率 < {KS_PLC_MAX_10U:g}%",
        ],
        "opp": [
            f"件数 ≥ {OP_CNT} かつ 複勝率 ≥ {OP_FV_60:g}% かつ 掲示板率 ≥ {OP_TOP5_70:g}%",
            f"件数 ≥ {OP_CNT} かつ 連対率 ≥ {OP_RV_50:g}% かつ 掲示板率 ≥ {OP_TOP5_70:g}%",
            f"件数 ≥ {OP_CNT} かつ 複勝率 ≥ {OP_FV_33:g}% かつ 複勝回収値 ≥ {OP_FRC_150:g} かつ 掲示板率 ≥ {OP_TOP5_50:g}%",
        ],
    }
