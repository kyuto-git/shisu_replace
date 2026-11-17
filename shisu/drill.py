"""条件（結合データ）に当たった過去の馬の逆引き（/entries/drill）。

成績表（agg_csv・unified_all）の1行は、master の馬を family のキーとオッズ帯で束ねたもの。ここではその逆に、
master_csv/master_{地域}_{指数名}.csv.gz から、キーとオッズ帯が同じ馬の行を取り出す。キーは集計と同じ
shisu.families.build_keys、オッズ帯は shisu.odds.odds_mask、重複の除き方と分母（取消・除外を除く）も集計と同じなので、
同じ master・同じ設定で作った成績表の「件数」と、取り出した行の数（分母に入る行）は一致する。

取り出した行から、TARGET に貼るレースIDの並びと、項目ごとの分布のまとめ（大多数が収まる「コア」と、そこから外れた
単独1件・凡走の集まり）を作る。master は 5万行ずつ読むので、10年分の master でもメモリは増えない。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import config
from . import families as fam_registry
from .conditions import CONDITION_COLUMNS
from .csvio import read_csv_any
from .finish import parse_finish
from .indicators import CONDITION_ONLY, parse_source_name
from .naming import base_masters, file_stem
from .odds import ODDS_MODES, odds_mask

MASTER_PREFIX = "master_"
RID = "レースID(新)"
RID16 = "レースID(新/馬番無)"
CHUNK_ROWS = 50_000
GOOD_MAX = 3                     # 好走 = 3着以内（凡走 = 4着以下・失格・中止）

FINISH = "_着順の数"             # 取り出した行に足す列
DENOM = "_分母"
GOOD = "_好走"
RESULT = "区分"                  # 好走 / 凡走 / 集計外（取消・除外）

CORE_TARGET = 0.80               # コア = 件数の多い値から足して、この割合に届くまで（単独1件の値は入れない）
SINGLE_MIN_N = 5                 # 全体がこの件数以上のときだけ「単独1件」を外れ値として印を付ける
BAD_MIN_COUNT = 2                # 凡走の集まり: この件数以上で、
BAD_RATE_RATIO = 0.5             #   好走率が全体の好走率 × これ 以下、
BAD_MIN_EXPECTED = 1.0           #   かつ 全体の好走率どおりなら好走がこの数以上あるはずの値（件数 × 全体の好走率）
MAX_CHIPS = 18                   # 1項目に出す値の数（多い項目は件数の多い順にここまで）
ODDS_BINS = ((2.0, "2.0倍未満"), (3.0, "2.0〜2.9倍"), (5.0, "3.0〜4.9倍"), (10.0, "5.0〜9.9倍"),
             (20.0, "10〜19.9倍"), (50.0, "20〜49.9倍"), (float("inf"), "50倍以上"))
# 分布をまとめる項目（表示名, master の列）。単勝オッズは帯にする。22条件はそのままの列名
SUMMARY_ITEMS = (("単勝オッズ", "単勝オッズ"), ("枠番", "枠番"), ("馬番", "馬番"), ("頭数", "頭数"), ("枠番グループ", "枠番グループ"),
                 ("場所", "場所"), ("芝・ダ", "芝・ダ"), ("距離", "距離_raw"), ("芝(内・外)", "芝(内・外)"),
                 ("クラス名", "クラス名"), ("重量種別", "重量種別"), ("騎手", "騎手"), ("年", "year")) + tuple(
                     (c, c) for c in CONDITION_COLUMNS)
# 明細の表に出す列（master にあるものだけ）
DETAIL_COLUMNS = ("日付", "場所", RID, "馬番", "馬名", "騎手", "単勝オッズ", "着順", "指数", "順位", "偏差値",
                  "枠番", "頭数", "芝・ダ", "距離_raw", "クラス名", "重量種別") + tuple(CONDITION_COLUMNS)


class DrillError(Exception):
    """逆引きできない（引数が合わない・master が読めない）。"""


def master_files(master_dir=None) -> list[Path]:
    d = Path(master_dir) if master_dir is not None else config.master_dir()
    if not d.is_dir():
        return []
    return sorted(p for p in d.iterdir() if p.is_file() and p.name.startswith(MASTER_PREFIX)
                  and p.name.lower().endswith((".csv.gz", ".csv")))


def find_master(region: str, indicator: str, master_dir=None) -> Path | None:
    """(地域, 指数) の master。ファイル名は集計と同じ読み方（parse_source_name）で比べる（引数からパスを組み立てない）。"""
    want = (str(region or ""), str(indicator or ""))
    if want[1] == CONDITION_ONLY:        # 条件のみの family: 集計と同じ基準の master（地域ごとに タイムM → 対戦M → 先頭）
        return base_masters(master_files(master_dir)).get(want[0])
    for p in master_files(master_dir):
        if parse_source_name(file_stem(p)[len(MASTER_PREFIX):]) == want:
            return p
    return None


def check_args(fam, scheme: str, mode: str) -> None:
    if mode not in ODDS_MODES:
        raise DrillError(f"オッズ帯 {mode!r} はありません")
    if fam is None:
        raise DrillError("このパターン（family）は、今の DB の定義にありません")
    if fam.uses_schemes and scheme not in ("s1", "s2"):
        raise DrillError(f"{fam.name} は順位帯のパターンなので、スキーム（s1/s2）が要ります")
    if not fam.uses_schemes and scheme:
        raise DrillError(f"{fam.name} はスキームを持たないパターンです")


def extract(path, fam, scheme: str, mode: str, key: str) -> pd.DataFrame:
    """master から、family・スキームのキーが key と同じで、オッズ帯 mode に入る馬の行（指数有効=1。
    指数を使わない family（条件のみ）は集計と同じく全部の行）。
    重複は集計と同じく レースID × 馬番（無ければ馬名）で除く。FINISH・DENOM・GOOD・RESULT の列を足す。新しい日付が上。"""
    fam = fam_registry.get(fam) if isinstance(fam, str) else fam
    check_args(fam, scheme, mode)
    key = str(key)
    path = Path(path)

    def scan(frames) -> tuple[list[pd.DataFrame], list[str] | None]:
        parts, columns = [], None
        for chunk in frames:
            columns = list(chunk.columns) if columns is None else columns
            if RID not in chunk.columns or "着順" not in chunk.columns:
                raise DrillError(f"{path.name}: 列 {RID}・着順 がありません（master ではない可能性）")
            if "指数有効" in chunk.columns and not fam.no_index:
                chunk = chunk[chunk["指数有効"].astype(str) == "1"]
            if chunk.empty:
                continue
            keys = fam_registry.build_keys(chunk, fam, scheme)
            idx = keys.index[(keys == key).to_numpy()]
            if not len(idx):
                continue
            sub = chunk.loc[idx]
            sub = sub[odds_mask(sub["単勝オッズ"], mode)] if "単勝オッズ" in sub.columns else (
                sub if mode == "normal" else sub.iloc[0:0])
            if len(sub):
                parts.append(sub)
        return parts, columns

    try:
        try:
            parts, columns = scan(pd.read_csv(path, encoding="cp932", dtype=str, keep_default_na=False,
                                              chunksize=CHUNK_ROWS))
        except UnicodeDecodeError:                     # cp932 でない master（手で作り直したもの）は、まとめて読み直す
            parts, columns = scan([read_csv_any(path)])
    except (OSError, EOFError, pd.errors.ParserError, pd.errors.EmptyDataError) as e:   # 空のファイル（書きかけで止まった master）も
        raise DrillError(f"{path.name} を読めません（{type(e).__name__}: {e}）") from e
    df = pd.concat(parts, ignore_index=True) if parts else pd.DataFrame(columns=columns or [RID, "着順"])
    horse = next((c for c in ("馬番", "馬名") if c in df.columns and (df[c] != "").any()), None)
    if horse or (len(df) and df[RID].astype(str).str.fullmatch(r"\d{18}").all()):
        df = df.drop_duplicates(subset=[RID] + ([horse] if horse else []), keep="first")
    fl = parse_finish(df["着順"], df["頭数"] if "頭数" in df.columns else None) if len(df) else pd.DataFrame(
        {"finish_num": [], "denom": [], "place": []})
    df = df.assign(**{FINISH: fl["finish_num"].to_numpy(), DENOM: fl["denom"].to_numpy(dtype=bool),
                      GOOD: (fl["place"].to_numpy(dtype=bool) if len(df) else [])})
    df[RESULT] = ["集計外" if not d else ("好走" if g else "凡走") for d, g in zip(df[DENOM], df[GOOD])]
    if RID16 not in df.columns:
        df[RID16] = df[RID].astype(str).str.slice(0, 16)
    order = df["日付"] if "日付" in df.columns else df[RID].astype(str).str.slice(0, 8)
    return df.assign(_o=order.astype(str)).sort_values(["_o", RID], ascending=[False, True], kind="stable").drop(
        columns="_o").reset_index(drop=True)


def counted(df: pd.DataFrame) -> pd.DataFrame:
    """集計の分母に入る行（取消・除外を除く）。"""
    return df[df[DENOM].astype(bool)] if len(df) else df


def id_lists(df: pd.DataFrame) -> dict[str, dict[str, list[str]]]:
    """TARGET に貼るレースIDの並び。{'all'|'good'|'bad': {'horse': 18桁（馬単位）, 'race': 16桁（レース単位・重複なし）}}。
    分母に入る行だけ（取消・除外は入れない）。新しい日付が上。"""
    d = counted(df)
    out = {}
    for name, sub in (("all", d), ("good", d[d[GOOD].astype(bool)] if len(d) else d),
                      ("bad", d[~d[GOOD].astype(bool)] if len(d) else d)):
        horse = [str(v) for v in sub[RID]] if len(sub) else []
        race = list(dict.fromkeys(str(v) for v in sub[RID16])) if len(sub) else []
        out[name] = {"horse": horse, "race": race}
    return out


def odds_bin(v) -> str:
    try:
        o = float(str(v).strip())
    except (TypeError, ValueError):
        return ""
    if o != o or o <= 0:
        return ""
    return next((label for top, label in ODDS_BINS if o < top), ODDS_BINS[-1][1])    # inf も最後の帯


def _as_number(v: str):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def summarize_item(name: str, values: list[str], good: list[bool], order: list[str] | None = None) -> dict | None:
    """1項目の分布のまとめ。values = 分母に入る行の値（空は「値なし」として数えるだけ）、good = 好走かどうか。
    order を渡すとその並び（オッズの帯）、無ければ、全部の値が数なら小さい順、そうでなければ件数の多い順。

    コア（大多数が収まる範囲。CORE_TARGET = 80% 以上を覆う）:
      並びのある項目（数・オッズの帯）= 並びの上でつながった範囲のうち、80% 以上を覆ういちばん狭いもの
      並びの無い項目（騎手・脚質など）= 単独1件の値を除き、件数の多い値から足して 80% に届くまで（届かなければ2件以上の値の全部。
                                        全部が単独1件なら全部）
    危険候補:
      single = コアの外の単独1件の値（飛び地。全体が SINGLE_MIN_N 件以上のとき）
      bad    = 凡走の集まり（BAD_MIN_COUNT 件以上・好走率が全体の BAD_RATE_RATIO 倍以下・全体の好走率どおりなら
               好走が BAD_MIN_EXPECTED 以上あるはずの値。コアの中の値にも付く）"""
    groups: dict[str, list[int]] = {}
    n_empty = 0
    for v, g in zip(values, good):
        v = "" if v is None else str(v).strip()
        if not v:
            n_empty += 1
            continue
        c = groups.setdefault(v, [0, 0])
        c[0] += 1
        c[1] += 1 if g else 0
    n = sum(c[0] for c in groups.values())
    if not n:
        return None
    total_good = sum(c[1] for c in groups.values())
    rate = total_good / n
    nums = {v: _as_number(v) for v in groups}
    ordered = order is not None or all(x is not None for x in nums.values())
    if order is not None:
        pos = {v: i for i, v in enumerate(order)}
        natural = sorted(groups, key=lambda v: (pos.get(v, len(pos)), v))
    elif ordered:
        natural = sorted(groups, key=lambda v: (nums[v], v))
    else:
        natural = sorted(groups, key=lambda v: (-groups[v][0], v))
    rank = {v: i for i, v in enumerate(natural)}
    if ordered:
        best = None                                   # (値の数, -件数, 始まり) がいちばん小さい範囲
        for i in range(len(natural)):
            tot = 0
            for j in range(i, len(natural)):
                tot += groups[natural[j]][0]
                if tot / n >= CORE_TARGET:
                    cand = (j - i, -tot, i, j)
                    best = cand if best is None or cand < best else best
                    break
        core = natural[best[2]:best[3] + 1]
    else:
        by_count = sorted(groups, key=lambda v: (-groups[v][0], rank[v]))
        multi = [v for v in by_count if groups[v][0] >= 2]
        core, cum = [], 0
        for v in multi:
            core.append(v)
            cum += groups[v][0]
            if cum / n >= CORE_TARGET:
                break
        if not multi:                                 # 全部が単独1件なら、どれも外れ値とは言えない
            core = list(by_count)
    core_set = set(core)
    items = []
    for v in natural:
        c, g = groups[v]
        flag = ""
        if c == 1 and v not in core_set and n >= SINGLE_MIN_N:
            flag = "single"
        elif (c >= BAD_MIN_COUNT and rate > 0 and (g / c) <= rate * BAD_RATE_RATIO
              and c * rate >= BAD_MIN_EXPECTED):
            flag = "bad"
        items.append({"value": v, "n": c, "good": g, "bad": c - g, "rate": g / c, "core": v in core_set, "flag": flag})
    core_items = [it for it in items if it["core"]]
    if ordered:
        core_text = core_items[0]["value"] if len(core_items) == 1 else f"{core_items[0]['value']} 〜 {core_items[-1]['value']}"
    else:
        core_text = "・".join(it["value"] for it in core_items[:8]) + (f" ほか{len(core_items) - 8}種" if len(core_items) > 8 else "")
    dangers = [it for it in items if it["flag"]]
    shown = items if len(items) <= MAX_CHIPS else sorted(
        sorted(items, key=lambda it: (-it["n"], rank[it["value"]]))[:MAX_CHIPS], key=lambda it: rank[it["value"]])
    return {"name": name, "n": n, "n_empty": n_empty, "kinds": len(groups), "rate": rate, "ordered": ordered,
            "core": core_items, "core_cover": sum(it["n"] for it in core_items) / n, "core_text": core_text,
            "dangers": dangers, "items": shown, "hidden": len(items) - len(shown)}


def summarize(df: pd.DataFrame) -> list[dict]:
    """項目ごとの分布のまとめ（分母に入る行で）。値が1つも無い項目は出さない。"""
    d = counted(df)
    if not len(d):
        return []
    good = [bool(g) for g in d[GOOD]]
    out = []
    for name, col in SUMMARY_ITEMS:
        if col not in d.columns:
            continue
        if col == "単勝オッズ":
            item = summarize_item(name, [odds_bin(v) for v in d[col]], good, [label for _, label in ODDS_BINS])
        else:
            item = summarize_item(name, [str(v) for v in d[col]], good)
        if item is not None:
            out.append(item)
    return out


def overview(df: pd.DataFrame) -> dict:
    """件数のまとめ: 取り出した行・分母に入る行・好走・凡走・集計外（取消・除外）。"""
    d = counted(df)
    good = int(d[GOOD].astype(bool).sum()) if len(d) else 0
    return {"rows": len(df), "n": len(d), "good": good, "bad": len(d) - good, "excluded": len(df) - len(d),
            "rate": (good / len(d)) if len(d) else None}
