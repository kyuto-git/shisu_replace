"""条件ヒット馬の分類（/entries/hits と / のプリセットで共通）。

値はすべて DB の値をそのまま使う（率は 0–100 の %）。
  fv  = 複勝率_val（推定値: 事後分布の下側10%点。頻度20%以下なら上側90%点）
  rv  = 連対率_val（推定値: 下側10%点）
  top5= 掲示板率_val（頻度）
  frc = 複勝回収値, tra = 単勝適正回収値, cnt = 件数
  key = 結合データ（希少条件の判定に使う。無ければ希少条件ではないものとして扱う）
欠損（None）の比較はすべて「満たさない」と扱う。
判定順は 消し → 絶対軸 → 軸候補 → 相手 で、最初に当てはまった区分を1つだけ返す。

2026-11-12 に足した規則:
  - 高回収率基準（軸候補）: オッズ帯が 10倍未満・10〜50倍 のときだけ（50倍以上・通常は、100倍超の一発で
    回収値が跳ねるので使わない）
      件数 ≥ 30 かつ 複勝率 ≥ 35% かつ 掲示板率 ≥ 50% かつ（複勝回収値 ≥ 200 または 単勝適正回収値 ≥ 200）
      件数 ≥ 30 かつ 複勝率 ≥ 50% かつ（複勝回収値 ≥ 150 または 単勝適正回収値 ≥ 150）
  - 希少条件（結合データを「×」で分けた部品に G1・重賞・準OP・OP・芝長・ダ長 のどれかがある）は、
    レース数が少なく件数がたまりにくいので、判定に要る最低件数を下げる:
      絶対軸・軸候補 10 → 6 ／ 相手候補 30 → 15 ／ 消し候補 20 → 12 ／ 高回収率基準 30 → 15
classify（1行）・classify_frame（ベクトル）・sql_condition（SQL）は同じ規則（tests/test_core.py・test_aggstore.py で照合）。

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
# --- 軸候補（高回収率基準。HR_MODES のオッズ帯だけ）---
HR_MODES = ("odds10u", "odds10_50")
HR_CNT = 30
HR1_FV = 35.0              # ① 複勝率 ≥ 35 かつ 掲示板率 ≥ 50 かつ（複勝回収値 または 単勝適正回収値 ≥ 200）
HR1_TOP5 = 50.0
HR1_RET = 200.0
HR2_FV = 50.0              # ② 複勝率 ≥ 50 かつ（複勝回収値 または 単勝適正回収値 ≥ 150）
HR2_RET = 150.0
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
# --- 希少条件（結合データの部品にこのどれかがあれば、最低件数を下の値に下げる）---
RARE_TOKENS = ("G1", "重賞", "準OP・OP", "芝長", "ダ長")
RARE_ABS_CNT = 6
RARE_AX_CNT = 6
RARE_OP_CNT = 15
RARE_KS_CNT = 12
RARE_HR_CNT = 15
KEY_SEP = "×"
KEY_COLUMN = "結合データ"


def is_rare_key(key) -> bool:
    """希少条件か: 結合データを「×」で分けた部品に RARE_TOKENS のどれかがある（部品の完全一致）。"""
    if key is None:
        return False
    return any(t in RARE_TOKENS for t in str(key).split(KEY_SEP))


def rule_settings() -> dict:
    """判定規則の値（集計のコンパクト化の設定のハッシュに入れる。値を変えたら成績表を作り直す）。"""
    g = globals()
    nums = {k: g[k] for k in sorted(g) if k.isupper() and isinstance(g[k], (int, float)) and not isinstance(g[k], bool)}
    return {**nums, "HR_MODES": list(HR_MODES), "RARE_TOKENS": list(RARE_TOKENS)}


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
    """rec: cnt, fv, rv, top5, frc, tra（欠損は None）と key（結合データ。無ければ希少条件ではない）。
    mode: オッズ帯（normal/odds10u/...）。"""
    cnt = _num(rec.get("cnt"))
    fv = _num(rec.get("fv"))
    rv = _num(rec.get("rv"))
    top5 = _num(rec.get("top5"))
    frc = _num(rec.get("frc"))
    tra = _num(rec.get("tra"))
    key = rec.get("key")
    rare = is_rare_key(key if key is not None else rec.get(KEY_COLUMN))
    ks_cnt, abs_cnt, ax_cnt, op_cnt, hr_cnt = (
        (RARE_KS_CNT, RARE_ABS_CNT, RARE_AX_CNT, RARE_OP_CNT, RARE_HR_CNT) if rare
        else (KS_CNT, ABS_CNT, AX_CNT, OP_CNT, HR_CNT))

    keshi = (
        (_ge(cnt, ks_cnt) and _lt(fv, KS_PLC_MAX_ANY) and _lt(top5, KS_TOP5_MAX_ANY))
        or (mode == "odds10u" and _ge(cnt, ks_cnt) and _lt(fv, KS_PLC_MAX_10U))
    )
    if keshi:
        return "keshi"

    if _ge(cnt, abs_cnt) and _ge(rv, ABS_REN) and (_ge(frc, ABS_FRC) or _ge(tra, ABS_TRA)):
        return "abs"

    if _ge(cnt, ax_cnt) and (
        (_ge(fv, AX_FV_80) and _ge(top5, AX_TOP5_90))
        or (_ge(rv, AX_RV_60) and _ge(top5, AX_TOP5_90))
        or (_ge(fv, AX_FV_70) and _ge(top5, AX_TOP5_80) and _ge(frc, AX_FRC_100))
        or (_ge(rv, AX_RV_50) and _ge(top5, AX_TOP5_80) and _ge(frc, AX_FRC_100))
    ):
        return "axis"
    if mode in HR_MODES and _ge(cnt, hr_cnt) and (
        (_ge(fv, HR1_FV) and _ge(top5, HR1_TOP5) and (_ge(frc, HR1_RET) or _ge(tra, HR1_RET)))
        or (_ge(fv, HR2_FV) and (_ge(frc, HR2_RET) or _ge(tra, HR2_RET)))
    ):
        return "axis"

    if _ge(cnt, op_cnt) and (
        (_ge(fv, OP_FV_60) and _ge(top5, OP_TOP5_70))
        or (_ge(rv, OP_RV_50) and _ge(top5, OP_TOP5_70))
        or (_ge(fv, OP_FV_33) and _ge(frc, OP_FRC_150) and _ge(top5, OP_TOP5_50))
    ):
        return "opp"
    return None


# DB の列 → classify の値（cnt, fv, rv, top5, frc, tra）
FIELD_COLUMNS = {"cnt": "件数", "fv": "複勝率_val", "rv": "連対率_val", "top5": "掲示板率_val",
                 "frc": "複勝回収値", "tra": "単勝適正回収値"}


def classify_frame(df, mode: str):
    """classify() のベクトル版。df は FIELD_COLUMNS の列を持つ DataFrame（DB と同じ値。5つの率の *_val は推定値）。
    key（無ければ 結合データ = KEY_COLUMN）の列があれば希少条件の最低件数を使う（列が無ければ希少条件ではない。1行版と同じ順）。
    戻り値は区分の文字列（keshi/abs/axis/opp）か None の numpy 配列（object）。判定は classify() と同じ
    （欠損・数にならない値はどの比較も満たさない。tests/test_aggstore.py で1行版と照合している）。"""
    import numpy as np
    import pandas as pd

    def col(field: str):
        c = FIELD_COLUMNS[field]
        if c not in df.columns:
            return np.full(len(df), np.nan)
        return pd.to_numeric(df[c], errors="coerce").to_numpy(dtype=float, na_value=np.nan)

    cnt, fv, rv, top5, frc, tra = (col(f) for f in ("cnt", "fv", "rv", "top5", "frc", "tra"))
    key_col = "key" if "key" in df.columns else KEY_COLUMN if KEY_COLUMN in df.columns else None   # classify() と同じ順
    rare = np.zeros(len(df), dtype=bool)
    if key_col is not None:
        # SQL 版（rare_sql）と同じ見方: 「×」+ 結合データ +「×」に「×」+ 部品 +「×」が入っているか（正規表現は使わない）
        k = df[key_col].astype(object)
        wrapped = KEY_SEP + k.where(k.notna(), "").astype(str) + KEY_SEP
        for t in RARE_TOKENS:
            rare |= wrapped.str.contains(KEY_SEP + t + KEY_SEP, regex=False).to_numpy(dtype=bool)
    ks_cnt = np.where(rare, RARE_KS_CNT, KS_CNT)
    abs_cnt = np.where(rare, RARE_ABS_CNT, ABS_CNT)
    ax_cnt = np.where(rare, RARE_AX_CNT, AX_CNT)
    op_cnt = np.where(rare, RARE_OP_CNT, OP_CNT)
    hr_cnt = np.where(rare, RARE_HR_CNT, HR_CNT)
    with np.errstate(invalid="ignore"):                # NaN との比較は False（= 満たさない）
        keshi = (cnt >= ks_cnt) & (fv < KS_PLC_MAX_ANY) & (top5 < KS_TOP5_MAX_ANY)
        if mode == "odds10u":
            keshi |= (cnt >= ks_cnt) & (fv < KS_PLC_MAX_10U)
        absolute = (cnt >= abs_cnt) & (rv >= ABS_REN) & ((frc >= ABS_FRC) | (tra >= ABS_TRA))
        axis = (cnt >= ax_cnt) & (((fv >= AX_FV_80) & (top5 >= AX_TOP5_90))
                                  | ((rv >= AX_RV_60) & (top5 >= AX_TOP5_90))
                                  | ((fv >= AX_FV_70) & (top5 >= AX_TOP5_80) & (frc >= AX_FRC_100))
                                  | ((rv >= AX_RV_50) & (top5 >= AX_TOP5_80) & (frc >= AX_FRC_100)))
        if mode in HR_MODES:
            axis |= (cnt >= hr_cnt) & (((fv >= HR1_FV) & (top5 >= HR1_TOP5) & ((frc >= HR1_RET) | (tra >= HR1_RET)))
                                       | ((fv >= HR2_FV) & ((frc >= HR2_RET) | (tra >= HR2_RET))))
        opp = (cnt >= op_cnt) & (((fv >= OP_FV_60) & (top5 >= OP_TOP5_70))
                                 | ((rv >= OP_RV_50) & (top5 >= OP_TOP5_70))
                                 | ((fv >= OP_FV_33) & (frc >= OP_FRC_150) & (top5 >= OP_TOP5_50)))
    out = np.full(len(df), None, dtype=object)
    for name, mask in (("opp", opp), ("axis", axis), ("abs", absolute), ("keshi", keshi)):   # 判定順の逆に上書き
        out[mask] = name
    return out


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


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def rare_sql(key_col: str = KEY_COLUMN) -> str:
    """希少条件かどうかの SQL（is_rare_key と同じ: 「×」で分けた部品の完全一致。大文字・小文字も区別する）。"""
    wrapped = f"('{KEY_SEP}' || IFNULL({_q(key_col)}, '') || '{KEY_SEP}')"
    return _or(*(f"instr({wrapped}, '{KEY_SEP}{t}{KEY_SEP}') > 0" for t in RARE_TOKENS))


def _cnt(normal: int, rare: int, key_col: str) -> str:
    """件数の条件（希少条件なら rare 件以上でよい）。文字列の処理は、件数が rare 以上 normal 未満の行でだけ評価される。
    AND の最後に置く（数の条件で落ちる行で文字列の処理をしないため。横断検索は全行を見る）。"""
    return _or(_c("cnt", ">=", normal), _and(_c("cnt", ">=", rare), rare_sql(key_col)))


def _sql_raw(category: str, mode: str, key_col: str = KEY_COLUMN) -> str:
    if category == "keshi":
        cnt = _cnt(KS_CNT, RARE_KS_CNT, key_col)
        any_mode = _and(_c("fv", "<", KS_PLC_MAX_ANY), _c("top5", "<", KS_TOP5_MAX_ANY), cnt)
        if mode == "odds10u":
            return _or(any_mode, _and(_c("fv", "<", KS_PLC_MAX_10U), cnt))
        return any_mode
    if category == "abs":
        return _and(_c("rv", ">=", ABS_REN), _or(_c("frc", ">=", ABS_FRC), _c("tra", ">=", ABS_TRA)),
                    _cnt(ABS_CNT, RARE_ABS_CNT, key_col))
    if category == "axis":
        base = _and(_or(
            _and(_c("fv", ">=", AX_FV_80), _c("top5", ">=", AX_TOP5_90)),
            _and(_c("rv", ">=", AX_RV_60), _c("top5", ">=", AX_TOP5_90)),
            _and(_c("fv", ">=", AX_FV_70), _c("top5", ">=", AX_TOP5_80), _c("frc", ">=", AX_FRC_100)),
            _and(_c("rv", ">=", AX_RV_50), _c("top5", ">=", AX_TOP5_80), _c("frc", ">=", AX_FRC_100)),
        ), _cnt(AX_CNT, RARE_AX_CNT, key_col))
        if mode not in HR_MODES:
            return base
        high = _and(_or(
            _and(_c("fv", ">=", HR1_FV), _c("top5", ">=", HR1_TOP5),
                 _or(_c("frc", ">=", HR1_RET), _c("tra", ">=", HR1_RET))),
            _and(_c("fv", ">=", HR2_FV), _or(_c("frc", ">=", HR2_RET), _c("tra", ">=", HR2_RET))),
        ), _cnt(HR_CNT, RARE_HR_CNT, key_col))
        return _or(base, high)
    if category == "opp":
        return _and(_or(
            _and(_c("fv", ">=", OP_FV_60), _c("top5", ">=", OP_TOP5_70)),
            _and(_c("rv", ">=", OP_RV_50), _c("top5", ">=", OP_TOP5_70)),
            _and(_c("fv", ">=", OP_FV_33), _c("frc", ">=", OP_FRC_150), _c("top5", ">=", OP_TOP5_50)),
        ), _cnt(OP_CNT, RARE_OP_CNT, key_col))
    raise ValueError(category)


def sql_condition(category: str, mode: str, key_col: str = KEY_COLUMN) -> str:
    """classify(rec, mode) == category となる行の WHERE 句（判定順の排他も反映）。
    key_col は結合データの列名（unified_all は 結合データ、照合結果 entries_xjoin_* は key）。"""
    order = list(CATEGORIES)
    before = order[:order.index(category)]
    parts = [_sql_raw(category, mode, key_col)] + [f"NOT {_sql_raw(c, mode, key_col)}" for c in before]
    return _and(*parts)


def description_lines() -> dict[str, list[str]]:
    """画面に表示する条件の説明。"""
    hr = "・".join({"odds10u": "10倍未満", "odds10_50": "10〜50倍"}[m] for m in HR_MODES)
    rare = "・".join(RARE_TOKENS)
    return {
        "abs": [f"件数 ≥ {ABS_CNT} かつ 連対率 ≥ {ABS_REN:g}% かつ（複勝回収値 ≥ {ABS_FRC:g} または 単勝適正回収値 ≥ {ABS_TRA:g}）",
                f"※ 希少条件（結合データに {rare} のどれか）は 件数 ≥ {RARE_ABS_CNT}"],
        "axis": [
            f"件数 ≥ {AX_CNT} かつ 複勝率 ≥ {AX_FV_80:g}% かつ 掲示板率 ≥ {AX_TOP5_90:g}%",
            f"件数 ≥ {AX_CNT} かつ 連対率 ≥ {AX_RV_60:g}% かつ 掲示板率 ≥ {AX_TOP5_90:g}%",
            f"件数 ≥ {AX_CNT} かつ 複勝率 ≥ {AX_FV_70:g}% かつ 掲示板率 ≥ {AX_TOP5_80:g}% かつ 複勝回収値 ≥ {AX_FRC_100:g}",
            f"件数 ≥ {AX_CNT} かつ 連対率 ≥ {AX_RV_50:g}% かつ 掲示板率 ≥ {AX_TOP5_80:g}% かつ 複勝回収値 ≥ {AX_FRC_100:g}",
            f"（高回収率・オッズ帯が {hr} のときだけ）件数 ≥ {HR_CNT} かつ 複勝率 ≥ {HR1_FV:g}% かつ 掲示板率 ≥ {HR1_TOP5:g}% "
            f"かつ（複勝回収値 ≥ {HR1_RET:g} または 単勝適正回収値 ≥ {HR1_RET:g}）",
            f"（高回収率・オッズ帯が {hr} のときだけ）件数 ≥ {HR_CNT} かつ 複勝率 ≥ {HR2_FV:g}% "
            f"かつ（複勝回収値 ≥ {HR2_RET:g} または 単勝適正回収値 ≥ {HR2_RET:g}）",
            f"※ 希少条件（結合データに {rare} のどれか）は 件数 ≥ {RARE_AX_CNT}（高回収率は 件数 ≥ {RARE_HR_CNT}）",
        ],
        "keshi": [
            f"（全モード）件数 ≥ {KS_CNT} かつ 複勝率 < {KS_PLC_MAX_ANY:g}% かつ 掲示板率 < {KS_TOP5_MAX_ANY:g}%",
            f"（10倍未満）件数 ≥ {KS_CNT} かつ 複勝率 < {KS_PLC_MAX_10U:g}%",
            f"※ 希少条件（結合データに {rare} のどれか）は 件数 ≥ {RARE_KS_CNT}",
        ],
        "opp": [
            f"件数 ≥ {OP_CNT} かつ 複勝率 ≥ {OP_FV_60:g}% かつ 掲示板率 ≥ {OP_TOP5_70:g}%",
            f"件数 ≥ {OP_CNT} かつ 連対率 ≥ {OP_RV_50:g}% かつ 掲示板率 ≥ {OP_TOP5_70:g}%",
            f"件数 ≥ {OP_CNT} かつ 複勝率 ≥ {OP_FV_33:g}% かつ 複勝回収値 ≥ {OP_FRC_150:g} かつ 掲示板率 ≥ {OP_TOP5_50:g}%",
            f"※ 希少条件（結合データに {rare} のどれか）は 件数 ≥ {RARE_OP_CNT}",
        ],
    }
