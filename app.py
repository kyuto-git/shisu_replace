# app.py — レース一覧→「条件ヒット馬」抽出（entries_*_*_hensachi 直参照）＋既存UI維持
from flask import Flask, request, render_template_string, redirect, url_for
import sqlite3
import re
import os

# ========= DB パス（環境変数で上書き可） =========
DB_PATH = os.environ.get("DB_PATH", "/app/racing.db")

# ======== プリセット用の閾値 ========
# ======== 条件ヒット馬用の新しきい値（％表記） ========
AXIS_FV_A       = 80.0   # 複勝率80%以上
AXIS_RV_B       = 60.0   # 連対率60%以上
AXIS_CNT_MIN_C  = 20     # 件数20以上 かつ …
AXIS_FV_C       = 67.0   # … 複勝率67%以上


# ======== 条件ヒット馬：新しきい値（％表記。0–1小数はコード側で自動スケール） ========
# ======== 絶対軸（abs）: 最終版 ========
ABS_CNT = 10
ABS_REN = 80.0
ABS_FRC = 100.0   # 複勝回収値
ABS_TRA = 100.0   # 単勝適正回収値

# ======== 軸候補（axis）: 最終版 ========
AX_CNT = 10
AX_FV_80 = 80.0
AX_RV_60 = 60.0
AX_FV_70 = 70.0
AX_RV_50 = 50.0
AX_TOP5_90 = 90.0
AX_TOP5_80 = 80.0
AX_FRC_100 = 100.0

# ======== 相手候補（opp）: 最終版 ========
OP_CNT = 30
OP_FV_60 = 60.0
OP_RV_50 = 50.0
OP_FV_33 = 33.0
OP_FRC_150 = 150.0
OP_TOP5_70 = 70.0
OP_TOP5_50 = 50.0


# 消し候補
KS_NEW_CNT = 20
KS_NEW_PLC_MAX_ANY = 5.0      # どのモードでも 複勝率 < 5
KS_NEW_PLC_MAX_10U = 20.0     # odds10u のとき 複勝率 < 20
KS_NEW_TOP5_MAX_ANY = 10.0   # ★掲示板率（1~5着）10%未満
KESHI_CNT_MIN   = 20     # 件数20以上 かつ …
KESHI_FV_MAX    = 5.0    # … 複勝率5%未満


# --- 旧名の互換定数（既存コード参照箇所のため残す） ---
AXIS_FUKUSHO_MIN = AXIS_FV_A       # 旧: 複勝しきい値 → 新: 80%
AXIS_RENTAI_MIN  = AXIS_RV_B       # 旧: 連対しきい値 → 新: 60%
KESHI_FUKUSHO_MAX = KESHI_FV_MAX   # 旧: 消しの複勝上限 → 新: 5%


app = Flask(__name__)

# ========================= 共通ユーティリティ =========================
def _norm(s):
    if s is None: return ""
    t = str(s).strip()
    if t in ("None","none","NaN","nan"): return ""
    return t

# 先頭の数値だけを拾うための正規表現
_NUM = re.compile(r'-?\d+(?:\.\d+)?')

def _first_number(s: str) -> float:
    """"18.5% (13.2%)" や "0.85" などから先頭の数値だけを抽出"""
    if s is None:
        return 0.0
    m = _NUM.search(str(s).replace(',', ''))
    return float(m.group()) if m else 0.0

def _tofloat(x):
    """
    '12.3', '12.3%', '18.5% (13.2%)', '0.85', '', None などを安全に float 化
    - % や余計な文字があっても「先頭の数値」を取りにいく
    - ただし 0〜1 を % には変換しない（生の float）
    """
    s = str(x or '').strip()
    if not s or s.lower() in ('none', 'nan'):
        return 0.0
    s = s.replace('%', '')
    return _first_number(s)

def _to_pct(x):
    """
    0〜1 スケールの率（例: 0.78）を 78.0 に拡張。
    それ以外（既に 0〜100 前提）はそのまま返す。
    """
    v = _tofloat(x)
    return v * 100.0 if 0.0 < v <= 1.0 else v

def _get_col(row: dict, *cands, default=""):
    """
    列名候補を順に探して最初に見つかった値を返す。
    rowはdict想定。無い/空はdefault。
    """
    for c in cands:
        if c in row:
            v = row.get(c)
            if v is None:
                continue
            sv = str(v).strip()
            if sv != "":
                return v
    return default


def _pct_from_row(row: dict, *cands, default=0.0):
    """
    rowから%値を取り出して float にする。
    '33.33%（14.00%）' のような形式でも先頭値を拾う。
    """
    v = _get_col(row, *cands, default="")
    if v == "":
        return default
    return _to_pct(v)


def _float_from_row(row: dict, *cands, default=0.0):
    """
    rowから数値(float)を取り出す。
    """
    v = _get_col(row, *cands, default="")
    if v == "":
        return default
    return _tofloat(v)

# 馬名の候補を広げる（馬名Sも拾う）
NAME_CAND = ("馬名","馬名S","馬名1","horse","出走馬名")
def _row_display_name(row):
    for c in NAME_CAND:
        if c in row and _norm(row[c]): return _norm(row[c])
    return ""

# entries_* に枠番グループが無い（または空）ときの簡易フォールバック
def _mk_wakuban_group_fallback(w, heads=None):
    try: w = int(str(w))
    except: return ""
    try: heads = int(str(heads))
    except: heads = None

    if heads is not None:
        if 10 <= heads <= 13:
            return "内枠" if w <= 5 else "外枠"
        elif 14 <= heads <= 16:
            return "内枠" if w <= 4 else "外枠"
        elif 17 <= heads <= 18:
            if w <= 3: return "内枠"
            if 4 <= w <= 6: return "中枠"
            return "外枠"
    # フォールバック（9頭以下/不明）
    if w <= 2: return "内枠"
    if 3 <= w <= 6: return "中枠"
    return "外枠"

def _key_join(*parts):
    out = [str(x) for x in parts if _norm(x)!=""]
    return "×".join(out)

# 指標名のゆらぎ対策
IND_ALIASES = {
    "カツラキ": ["カツラキ"],
    "GoldAirplane": ["GoldAirplane","Gold","Gold指数","Gold_Airplane"],
    "Gold": ["Gold","GoldAirplane","Gold指数"],
    "BC": ["BC","BCindex","BCIndex"],
    "ST指数": ["ST指数","ST"],
    "展開": ["展開","展開力"],
    "想定前半": ["想定前半","想定前半3F","想定前半３F"],
    "想定後半": ["想定後半","想定後半3F","想定後半３F"],
    "タイムM": ["タイムM","マイニング値","タイム指数","TimeM"],
    "対戦M": ["対戦M","対戦型マイニング値","MatchM"],
    "合計値": ["合計値","Total","総合"],
}
def _indicator_candidates(base):
    b = re.sub(r"\s+", "", str(base or "")).strip("_")
    cands = set([b])
    if b in IND_ALIASES:
        cands.update(IND_ALIASES[b])
    return list(cands)

# 偏差値帯の表記ゆらぎを揃える（66-70 / 66~70 / 66〜70 を全部候補に）
def _normalize_band_token(s):
    s = _norm(s)
    if not s: return []
    # 主要3パターンを返す
    dash = re.sub(r"[~〜－–—‐\u2212]", "-", s)
    tilde = re.sub(r"[-〜－–—‐\u2212]", "~", s)
    wave = re.sub(r"[-~－–—‐\u2212]", "〜", s)
    return list(dict.fromkeys([s, dash, tilde, wave]))

def _normalize_rank_variants(s):
    """
    '1', '1.0', '1位', ' 1 位 ' などを同義化して候補リスト化
    """
    s0 = _norm(s)
    if not s0:
        return []
    s0 = s0.replace(" ", "").replace("\u3000", "")
    s_num = re.sub(r'位$', '', s0)
    variants = []
    try:
        i = int(float(s_num))
        variants.extend([str(i), f"{i}位"])
        if s0 not in variants:
            variants.append(s0)
        if re.search(r'\.\d+$', s_num):
            f = str(float(s_num))
            if f not in variants: variants.append(f)
    except:
        base = s_num
        for v in [base, base + "位", s0]:
            v = v.strip()
            if v and v not in variants:
                variants.append(v)
    out = []
    for v in variants:
        if v and v not in out:
            out.append(v)
    return out

def _rank_to_band(rank_val):
    """
    整数順位→保守的な帯
    1-2, 3-5, 6-9, 10~ に丸める
    """
    try:
        r = int(float(str(rank_val)))
    except:
        return ""
    if r <= 2:  return "1~2位"
    if r <= 5:  return "3~5位"
    if r <= 9:  return "6~9位"
    return "10位~"

def _normalize_rank_band_token(s):
    """
    '1~2位' / '1-2位' / '1〜2位' / '1~2' / '1-2' / '1〜2' を相互候補化
    """
    s = _norm(s)
    if not s: return []
    s = re.sub(r"\s+", "", s)
    base = re.sub(r"[~〜－–—‐\u2212]", "~", s)
    base_with_i = base if base.endswith("位") else base + "位"
    out = set()
    for sym in ["~","-","〜"]:
        v = base_with_i.replace("~", sym)
        out.add(v); out.add(v.rstrip("位"))
    return list(out)

def _build_key_candidates(row, base):
    place = _norm(row.get("場所"))
    sd    = _norm(row.get("芝・ダ"))
    dist  = _norm(row.get("距離"))
    inout = _norm(row.get("芝(内・外)")) or _norm(row.get("芝(内外)"))
    wtype = _norm(row.get("重量種別"))
    cls   = _norm(row.get("クラス名"))
    waku  = _norm(row.get("枠番")) or _norm(row.get("枠"))
    wakg  = _norm(row.get("枠番グループ"))
    if not wakg:
        wakg = _mk_wakuban_group_fallback(waku, row.get("頭数"))
    jock  = _norm(row.get("騎手"))

    rank  = _norm(row.get(base + "_順位"))

    # 偏差値帯（66~70 等）
    hensachi_band_raw = _norm(row.get(base + "_偏差値帯"))
    hensachi_band_variants = _normalize_band_token(hensachi_band_raw) if hensachi_band_raw else [""]

    # ★ 追加：順位帯（1~2位 等）— 列が無ければ順位からフォールバック生成
    rank_band_raw = _norm(row.get(base + "_順位帯"))
    if rank_band_raw:
        rank_band_variants = _normalize_rank_band_token(rank_band_raw)
    else:
        rb = _rank_to_band(rank) if rank else ""
        rank_band_variants = _normalize_rank_band_token(rb) if rb else [""]

    # in/out はあり/なし両取り（未設定時は保険で広めに）
    if inout:
        inout_variants = [inout, ""]
    else:
        inout_variants = ["", "内", "外", "内外"]

    keys = {}
    def add_key(tag, *tail):
        arr = keys.setdefault(tag, set())
        for io in inout_variants:
            head = _key_join(place, sd, dist, io)
            arr.add(_key_join(head, *tail))

    # rank（順位）— 従来
    if rank:
        for rv in _normalize_rank_variants(rank):
            add_key("normal",    wtype, cls, rv)
            add_key("rankB",     wakg, rv)
            add_key("rankJ",     jock, rv)
            add_key("rankBJ",    wakg, jock, rv)
            add_key("normalJ",   wtype, cls, jock, rv)

    # ★ 追加：rank-band（順位帯）
    for b in rank_band_variants:
        if not b: continue
        add_key("normal_band",   wtype, cls, b)
        add_key("rankB_band",    wakg, b)
        add_key("rankJ_band",    jock, b)
        add_key("rankBJ_band",   wakg, jock, b)
        add_key("normalJ_band",  wtype, cls, jock, b)

    # hensachi-band（偏差値帯）— 従来
    for b in hensachi_band_variants:
        if not b: continue
        add_key("hensachiA",   wtype, cls, b)
        add_key("hensachiB",   wakg, b)
        add_key("hensachiJ",   jock, b)
        add_key("hensachiBJ",  wakg, jock, b)
        add_key("hensachiAJ",  wtype, cls, jock, b)

    like_prefix = _key_join(place, sd, dist)
    return keys, like_prefix

# 追加：LIKE用エスケープ
def _like_escape(s: str) -> str:
    s = str(s or "")
    # ESCAPE '\' 前提：\ / % / _ をエスケープ
    return s.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")

def _query_unified_for_key(conn, fam, base, key, like_prefix):
    """
    1) family一致 + 指標名のゆるい一致 + 結合データ = key（完全一致）
    2) 偏差値帯の記号ゆらぎ置換（- / ~ / 〜）で再試行（完全一致）
    3) LIKE（key + '%'）で末尾ゆるく
    4) どうしても見つからない場合、場所×芝・ダ×距離% の前方一致でフォールバック
    """
    if not key:
        return []

    # fam / cand をエスケープして部分一致に
    fam_esc = _like_escape(fam)
    fam_like = f"%{fam_esc}\\_%"   # ← 「family名 + '_'」を要求（\_ はリテラル '_'）


    sql_base = '''
      SELECT
        "テーブル名","件数",
        "勝率_val","勝率","連対率_val","連対率","複勝率_val","複勝率",
        "掲示板率_val","掲示板率",
        "平均着順","単勝回収値","複勝回収値","単勝適正回収値"
      FROM unified_all
      WHERE "結合データ" = ?
        AND "テーブル名" LIKE ? ESCAPE '\\'
        AND "テーブル名" LIKE ? ESCAPE '\\'
      ORDER BY "複勝率_val" DESC, "連対率_val" DESC
      LIMIT 50
    '''

    for cand in _indicator_candidates(base):
        cand_like = f"%{_like_escape(cand)}%"
        cur = conn.execute(sql_base, [key, fam_like, cand_like])
        rows = [dict(r) for r in cur.fetchall()]
        if rows:
            return rows

    # 2) 記号ゆらぎ置換後の再試行（← fam_like を使用）
    band_re = r"(?:\d{2})\s*[-~〜－–—‐]\s*(?:\d{2})"
    m = re.search(band_re, key)
    if m:
        dash  = re.sub(r"[~〜－–—‐]", "-", key)
        tilde = re.sub(r"[-〜－–—‐]", "~", key)
        wave  = re.sub(r"[-~－–—‐]", "〜", key)
        for k2 in [dash, tilde, wave]:
            for cand in _indicator_candidates(base):
                cand_like = f"%{_like_escape(cand)}%"
                cur = conn.execute(sql_base, [k2, fam_like, cand_like])  # ← 修正
                rows = [dict(r) for r in cur.fetchall()]
                if rows: return rows

    # 3) key + '%' の LIKE
    sql_like = '''
      SELECT
        "テーブル名","件数",
        "勝率_val","勝率","連対率_val","連対率","複勝率_val","複勝率",
        "掲示板率_val","掲示板率",
        "平均着順","単勝回収値","複勝回収値","単勝適正回収値"
      FROM unified_all
      WHERE "結合データ" LIKE ? ESCAPE '\\'
        AND "テーブル名" LIKE ? ESCAPE '\\'
        AND "テーブル名" LIKE ? ESCAPE '\\'
      ORDER BY "複勝率_val" DESC, "連対率_val" DESC
      LIMIT 50
    '''

    for cand in _indicator_candidates(base):
        cand_like = f"%{_like_escape(cand)}%"
        cur = conn.execute(sql_like, [key + "%", fam_like, cand_like])
        rows = [dict(r) for r in cur.fetchall()]
        if rows:
            return rows

    for cand in _indicator_candidates(base):
        cand_like = f"%{_like_escape(cand)}%"
        cur = conn.execute(sql_like, [like_prefix + "%", fam_like, cand_like])
        rows = [dict(r) for r in cur.fetchall()]
        if rows:
            return rows

    return []

def _collect_indicator_bases(rows):
    if not rows: return []
    cols = rows[0].keys()
    bases = []
    for c in cols:
        if c.endswith("_偏差値") and (c[:-4] + "_順位") in cols:
            bases.append(c[:-4])
    return sorted(bases)

def _fetch_race_rows(conn, table, ridn, date_col=None, date_val=None):
    where = []
    params = []
    if date_col and date_val:
        where.append(f'"{date_col}" = ?'); params.append(date_val)

    # 1) レースID(新/馬番無) 一致
    sql1 = f'SELECT * FROM "{table}" WHERE COALESCE("レースID(新)","レースID(新/馬番無)","") = ?'
    if where: sql1 += " AND " + " AND ".join(where)
    sql1 += ' ORDER BY CAST("馬番" AS INT)'
    rows = conn.execute(sql1, [ridn] + params).fetchall()
    if rows: return [dict(r) for r in rows]

    # 2) 「場 R」完全一致
    sql2 = f'SELECT * FROM "{table}" WHERE "場 R" = ?'
    if where: sql2 += " AND " + " AND ".join(where)
    sql2 += ' ORDER BY CAST("馬番" AS INT)'
    rows = conn.execute(sql2, [ridn] + params).fetchall()
    if rows: return [dict(r) for r in rows]

    # 3) スペース（半角/全角）を無視して一致
    ridn_norm = ridn.replace(" ", "").replace("　","")
    sql3 = f'''
      SELECT * FROM "{table}"
      WHERE REPLACE(REPLACE("場 R",' ',''),'　','') = ?
      {'AND ' + ' AND '.join(where) if where else ''}
      ORDER BY CAST("馬番" AS INT)
    '''
    rows = conn.execute(sql3, [ridn_norm] + params).fetchall()
    if rows: return [dict(r) for r in rows]

    # 4) それでも無ければ該当なし（全件フォールバックはしない）
    return []

def _detect_scale(rows):
    vals = []
    for r in rows[:200]:
        for k in ("複勝率_val","連対率_val","勝率_val"):
            v = _tofloat(r.get(k))
            if v:
                vals.append(v)
    if not vals:
        return 1.0
    # 「最大が1以下」だけを 0–1 スケールとみなす
    return 0.01 if max(vals) <= 1.0 else 1.0

# 追加: 共通バリデーション
def _validate_entries_table(conn, table: str) -> str | None:
    """entries_* または entries_xjoin_* の実在テーブルのみ許可。該当なければ None"""
    all_entries = set(get_entries_tables(conn))
    if table in all_entries:
        return table
    # xjoin 派生名も許す（実在チェック）
    xname = _resolve_xjoin_table(table)
    cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?;", (xname,))
    return table if cur.fetchone() else None


def classify(rec, scale, top5_required_for_keshi=True):
    """
    rec: dict 期待キー
      cnt, fv, rv, frc, tra, mode, top5v, has_top5, f(文字列でもOK)
    scale: 率が 0-1 格納のとき 0.01 / 0-100 格納のとき 1.0
    戻り値: "keshi" / "abs" / "axis" / "opp" / None
    """

    # ---- 安全に値を取得（欠損は0扱い）----
    cnt = int(rec.get("cnt") or 0)
    fv  = float(rec.get("fv") or 0.0)
    rv  = float(rec.get("rv") or 0.0)
    frc = float(rec.get("frc") or 0.0)  # 複勝回収値
    tra = float(rec.get("tra") or 0.0)  # 単勝適正回収値
    mode = (rec.get("mode") or "通常")

    has_top5 = bool(rec.get("has_top5", False))
    top5v = float(rec.get("top5v") or 0.0)

    # odds10u 用：複勝率(表示文字列)の先頭数値を % として取る
    # 例 "18.75% (13.57%)" / "0.1875" などでもOK
    f_raw = str(rec.get("f") or "")
    f_pct_10u = _first_number(f_raw)
    if 0.0 < f_pct_10u <= 1.0:
        f_pct_10u *= 100.0

    # ---- スケール適用（%系のみ）----
    ks_any = KS_NEW_PLC_MAX_ANY * scale
    ks_10u = KS_NEW_PLC_MAX_10U * scale

    # (3) 消し候補（あなたの現行xjoinロジックに合わせる）

    # odds10u は fv で判定（推奨：両パス統一）
    is_keshi = (
        (cnt >= KS_NEW_CNT and fv < ks_any
        and ((has_top5 and top5v < KS_NEW_TOP5_MAX_ANY) if top5_required_for_keshi else True))
        or
        (mode == "odds10u" and cnt >= KS_NEW_CNT and fv < ks_10u)
    )
    if is_keshi:
        return "keshi"

    # # (1) 絶対軸（あなたの現行xjoinロジックに合わせる）
    # ren80 = ABS_REN * scale
    # is_abs = (
    #     cnt >= ABS_CNT
    #     and rv >= ren80
    #     and (frc >= ABS_FRC or tra >= ABS_TRA)
    # )

    # (1) 絶対軸（文字列カラムの「連対率」から抽出した値を使用する）
    r_raw = str(rec.get("r") or "")
    r_pct = _first_number(r_raw)
    if 0.0 < r_pct <= 1.0:  # 万が一小数ベースになっていた場合の保険
        r_pct *= 100.0

    is_abs = (
        cnt >= ABS_CNT
        and r_pct >= ABS_REN  # rv >= ren80 を r_pct >= ABS_REN に変更
        and (frc >= ABS_FRC or tra >= ABS_TRA)
    )

    if is_abs:
        return "abs"

    # (2) 軸候補（あなたの現行xjoinロジックに合わせる）
    fv80 = AX_FV_80 * scale
    rv60 = AX_RV_60 * scale
    fv70 = AX_FV_70 * scale
    rv50 = AX_RV_50 * scale

    is_axis = (
        (cnt >= AX_CNT and has_top5 and fv >= fv80 and top5v >= AX_TOP5_90)
        or (cnt >= AX_CNT and has_top5 and rv >= rv60 and top5v >= AX_TOP5_90)
        or (cnt >= AX_CNT and has_top5 and fv >= fv70 and top5v >= AX_TOP5_80 and frc >= AX_FRC_100)
        or (cnt >= AX_CNT and has_top5 and rv >= rv50 and top5v >= AX_TOP5_80 and frc >= AX_FRC_100)
    )
    if is_axis:
        return "axis"

    # (4) 相手候補（あなたの現行xjoinロジックに合わせる）
    opfv60 = OP_FV_60 * scale
    oprv50 = OP_RV_50 * scale
    opfv33 = OP_FV_33 * scale

    is_opp = (
        (cnt >= OP_CNT and has_top5 and fv >= opfv60 and top5v >= OP_TOP5_70)
        or (cnt >= OP_CNT and has_top5 and rv >= oprv50 and top5v >= OP_TOP5_70)
        or (cnt >= OP_CNT and has_top5 and fv >= opfv33 and frc >= OP_FRC_150 and top5v >= OP_TOP5_50)
    )
    if is_opp:
        return "opp"

    return None

# ========================= 既存：横断検索（/） =========================
HTML = """<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8"><title>指数集計 横断検索</title><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,"Noto Sans JP","Hiragino Kaku Gothic ProN",Meiryo,"Apple Color Emoji","Segoe UI Emoji"; margin:0; background:#f6f7f9;}
header{background:#111827; color:#fff; padding:16px 20px; font-size:18px;}
.container{max-width:1200px; margin:24px auto; padding:0 16px;}
.card{background:#fff; border-radius:12px; box-shadow:0 1px 3px rgba(0,0,0,.08); padding:16px; margin-bottom:16px;}
h1{font-size:18px; margin:0 0 12px;}
label{display:block; font-size:12px; color:#374151; margin-bottom:6px;}
select{width:100%; padding:8px; border:1px solid #e5e7eb; border-radius:8px; background:#fff;}
.grid{display:grid; grid-template-columns:repeat(4, 1fr); gap:12px;}
.grid2{display:grid; grid-template-columns:repeat(4, minmax(0, 1fr)); gap:8px; margin-top:8px;}
.cb{display:flex; align-items:center; gap:8px; font-size:13px; color:#111827; background:#f8fafc; border:1px solid #e5e7eb; border-radius:8px; padding:8px;}
.row{display:flex; align-items:center; gap:12px; flex-wrap:wrap;}
button{padding:10px 16px; border:0; background:#2563eb; color:#fff; border-radius:8px; cursor:pointer;}
button:hover{background:#1d4ed8;}
.results h2{font-size:16px; margin:16px 0 6px;}
table{width:100%; border-collapse:collapse; background:#fff; border:1px solid #e5e7eb; border-radius:8px; overflow:hidden;}
th, td{padding:8px 10px; border-bottom:1px solid #f1f5f9; font-size:13px; text-align:left; white-space:nowrap;}
th{background:#f8fafc; color:#334155;}
.muted{color:#6b7280; font-size:12px;}
.tag{display:inline-block; font-size:11px; color:#334155; background:#e5e7eb; padding:2px 6px; border-radius:999px; margin-left:6px;}
a.sort{color:inherit; text-decoration:none;}
.nav{display:flex; gap:12px; align-items:center; margin-bottom:12px;}
.linkbtn{display:inline-block; padding:8px 12px; background:#f1f5f9; border-radius:8px; color:#111827; text-decoration:none;}
.linkbtn:hover{background:#e5e7eb;}
</style>
</head><body>
<header>指数集計 横断検索</header>
<div class="container">
  <div class="nav">
    <a class="linkbtn" href="{{ url_for('entries_index') }}">エントリー一覧（出馬表）</a>
  </div>
  <div class="card">
    <h1>レース条件</h1>
    <form method="get">
      <div class="grid">
        <div><label>場所</label>
          <select name="place">
            <option value="">（すべて）</option>
            {% for v in places %}<option value="{{v}}" {{ 'selected' if v==q_place else '' }}>{{v}}</option>{% endfor %}
          </select>
        </div>
        <div><label>芝・ダ</label>
          <select name="sd">
            <option value="">（すべて）</option>
            {% for v in sds %}<option value="{{v}}" {{ 'selected' if v==q_sd else '' }}>{{v}}</option>{% endfor %}
          </select>
        </div>
        <div><label>距離</label>
          <select name="dist">
            <option value="">（すべて）</option>
            {% for v in dists %}<option value="{{v}}" {{ 'selected' if v==q_dist else '' }}>{{v}}</option>{% endfor %}
          </select>
        </div>
        <div><label>芝(内・外)</label>
          <select name="inout">
            <option value="">（すべて）</option>
            {% for v in inouts %}<option value="{{v}}" {{ 'selected' if v==q_inout else '' }}>{{v}}</option>{% endfor %}
          </select>
        </div>
      </div>

      <div style="margin-top:16px;">
        <label>表示する指数表（複数選択可）</label>
        <div class="grid2">
          {% for key, label in families.items() %}
            <label class="cb"><input type="checkbox" name="family" value="{{key}}" {% if key in q_families %}checked{% endif %}><span>{{label}}</span></label>
          {% endfor %}
        </div>

        <div style="margin-top:12px;">
          <label>指数の種類（複数選択可）</label>
          <div class="grid2">
            {% for ind in indicators %}
              <label class="cb"><input type="checkbox" name="ind" value="{{ind}}" {% if ind in q_inds %}checked{% endif %}><span>{{ind}}</span></label>
            {% endfor %}
          </div>
        </div>

        <div class="row" style="margin-top:8px;">
          <div class="muted">オッズ表示：</div>
          <label class="cb"><input type="radio" name="odds_mode" value="both" {{ 'checked' if q_odds_mode=='both' else '' }}> すべて</label>
          <label class="cb"><input type="radio" name="odds_mode" value="normal" {{ 'checked' if q_odds_mode=='normal' else '' }}> 通常のみ</label>
          <label class="cb"><input type="radio" name="odds_mode" value="odds5p" {{ 'checked' if q_odds_mode=='odds5p' else '' }}> odds5p のみ</label>
          <label class="cb"><input type="radio" name="odds_mode" value="odds10u" {{ 'checked' if q_odds_mode=='odds10u' else '' }}> odds10u のみ</label>
        </div>

        <div class="row" style="margin-top:8px;">
          <div class="muted">オッズ表示：</div>
          <label class="cb"><input type="radio" name="odds_mode" value="both" {{ 'checked' if q_odds_mode=='both' else '' }}> すべて</label>
          <label class="cb"><input type="radio" name="odds_mode" value="normal" {{ 'checked' if q_odds_mode=='normal' else '' }}> 通常のみ</label>
          <label class="cb"><input type="radio" name="odds_mode" value="odds5p" {{ 'checked' if q_odds_mode=='odds5p' else '' }}> odds5p のみ</label>
          <label class="cb"><input type="radio" name="odds_mode" value="odds10_50" {{ 'checked' if q_odds_mode=='odds10_50' else '' }}> 10~50倍のみ</label> <label class="cb"><input type="radio" name="odds_mode" value="odds10u" {{ 'checked' if q_odds_mode=='odds10u' else '' }}> odds10u のみ</label>
        </div>

        <div class="row" style="margin-top:8px;">
          <div class="muted">推奨プリセット：</div>
          <label class="cb"><input type="radio" name="preset" value="none" {{ 'checked' if q_preset=='none' else '' }}> なし</label>
          <label class="cb"><input type="radio" name="preset" value="axis" {{ 'checked' if q_preset=='axis' else '' }}> 軸候補（複勝≥{{axis_min}}% または 連対≥{{rentai_min}}%）</label>
          <label class="cb"><input type="radio" name="preset" value="keshi" {{ 'checked' if q_preset=='keshi' else '' }}> 消し候補（複勝&lt;{{keshi_max}}%）</label>
        </div>
      </div>

      <div style="margin-top:12px;" class="row">
        <button type="submit">検索</button>
        <div class="muted">並び替え：{{ q_sort or '複勝率_val' }} / {{ q_sortdir or 'DESC' }}</div>
      </div>
      <input type="hidden" name="sort" value="{{ q_sort or '複勝率_val' }}">
      <input type="hidden" name="sortdir" value="{{ q_sortdir or 'DESC' }}">
      <input type="hidden" name="submitted" value="1">
    </form>
  </div>

  {% if submitted and results %}
  <div class="results">
    {% for r in results %}
      <h2>{{ r.table_label }} <span class="tag">{{ r.indicator }}</span> {% if r.tag %}<span class="tag">{{ r.tag }}</span>{% endif %}</h2>
      <div class="muted">{{ r.where_desc }}</div>
      <table>
        <thead><tr>
          {% for c in r.columns %}
            {% if c.endswith('_val') %}
              {% set nextdir = 'ASC' if (q_sort==c and q_sortdir=='DESC') else 'DESC' %}
              <th><a class="sort" href="?place={{q_place}}&sd={{q_sd}}&dist={{q_dist}}&inout={{q_inout}}{% for f in q_families %}&family={{f}}{% endfor %}{% for i in q_inds %}&ind={{i}}{% endfor %}&odds_mode={{q_odds_mode}}&preset={{q_preset}}&sort={{c}}&sortdir={{nextdir}}&submitted=1">{{ c }}{% if q_sort==c %} {{ '▼' if q_sortdir=='DESC' else '▲' }}{% endif %}</a></th>
            {% else %}
              <th>{{ c }}</th>
            {% endif %}
          {% endfor %}
        </tr></thead>
        <tbody>
          {% if r.rows %}
            {% for row in r.rows %}<tr>
              {% for c in r.columns %}<td>{{ row.get(c) or '' }}</td>{% endfor %}
            </tr>{% endfor %}
          {% else %}
            <tr><td colspan="{{ r.columns|length }}">該当なし</td></tr>
          {% endif %}
        </tbody>
      </table>
    {% endfor %}
  </div>
  {% elif submitted %}
    <div class="card">該当するテーブルはありませんでした。</div>
  {% else %}
    <div class="card muted">検索条件を設定して「検索」を押してください（初期表示では一覧を描画しません）。</div>
  {% endif %}
</div></body></html>
"""

DISPLAY_ORDER = [
    "結合データ","件数",
    "勝率_val","勝率","連対率_val","連対率","複勝率_val","複勝率","2着率_val","2着率","3着率_val","3着率",
    "掲示板率_val","掲示板率",   # ★ここを追加
    "平均着順","単勝回収値","複勝回収値","単勝適正回収値",
    "提案勝率","提案連対率","提案複勝率","提案2着率","提案3着率",
    "場所","芝・ダ","距離","芝(内・外)","芝(内外)" 
]

FAMILY_LABELS_BASE = {
    "normal":       "重量種別×クラス名×指数順位",
    "normal_band":  "重量種別×クラス名×指数順位（band）",
    "normalJ":      "重量種別×クラス名×騎手×指数順位",
    "normalJ_band": "重量種別×クラス名×騎手×指数順位（band）",
    "rankB":        "枠番グループ×指数順位",
    "rankB_band":   "枠番グループ×指数順位（band）",
    "rankJ":        "騎手×指数順位",
    "rankJ_band":   "騎手×指数順位（band）",
    "hensachiA":    "重量種別×クラス名×偏差値帯",
    "hensachiB":    "枠番グループ×偏差値帯",
    "hensachiJ":    "騎手×偏差値帯",
    "rankBJ":       "枠番グループ×騎手×指数順位",
    "rankBJ_band":  "枠番グループ×騎手×指数順位（band）",
    "hensachiBJ":   "枠番グループ×騎手×偏差値帯",
    "hensachiAJ":   "重量種別×クラス名×騎手×偏差値帯",
}

INDICATOR_ORDER = ["カツラキ","Gold","BC","ZI","オメガ指数","かよちん指数","keibaAI指数","吉馬_SP能力値","吉馬_先行力","吉馬_SP信頼","吉馬_SP調整","吉馬_SP最大","吉馬_末脚力","SAV","ST指数","合計値","展開","仕上り","先行","追走","持久","持続","瞬発","想定前半","想定後半","タイムM","対戦M"]

def detect_family_and_flags(tname: str):
    base = tname
    if base.startswith("foragg_"):
        base = base[len("foragg_"):]
    mode = "normal"
    # if base.startswith("odds5p_"):
    #     mode = "odds5p"; base = base[len("odds5p_"):]

    if base.startswith("odds5p_"):
        mode = "odds5p"; base = base[len("odds5p_"):]
    elif base.startswith("odds10_50_"):          # ★追加
        mode = "odds10_50"; base = base[len("odds10_50_"):] # ★追加
    elif base.startswith("odds10u_"):
        mode = "odds10u"; base = base[len("odds10u_"):]
    m = re.match(r'^(?P<fam>[^_]+?)(?:_(?P<band>band))?_', base)
    if m:
        fam = m.group("fam")
        if m.group("band"): fam = f"{fam}_band"
    else:
        fam = base.split("_", 1)[0]
    return fam, mode

# 置換：既存の _mode_label_from_tname を丸ごとこれに
def _mode_label_from_tname(tname: str) -> str:
    s = str(tname or "").lower()
    # どの位置にあっても拾う（foragg_ の有無も不問）
    if re.search(r'(^|[_-])odds10u([_-]|$)', s) or "odds10u" in s:
        return "odds10u"
    # if re.search(r'(^|[_-])odds5p([_-]|$)', s) or "odds5p" in s:
    #     return "odds5p"

    if re.search(r'(^|[_-])odds5p([_-]|$)', s) or "odds5p" in s:
        return "odds5p"
    if re.search(r'(^|[_-])odds10_50([_-]|$)', s) or "odds10_50" in s: # ★追加
        return "odds10_50" # ★追加

    
    return "通常"

def detect_indicator(tname: str):
    if "__" in tname: return tname.split("__")[-1]
    return ""

def scan_families(conn):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'foragg_%'")
    fams = set()
    for (tname,) in cur.fetchall():
        fam, _ = detect_family_and_flags(tname)
        fams.add(fam)
    out = {}
    for fam in sorted(fams):
        out[fam] = FAMILY_LABELS_BASE.get(fam, fam)
    return out

def scan_indicators(conn):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'foragg_%'")
    inds = set()
    for (tname,) in cur.fetchall():
        ind = detect_indicator(tname)
        if ind: inds.add(ind)
    ordered = [i for i in INDICATOR_ORDER if i in inds]
    for i in sorted(inds):
        if i not in ordered: ordered.append(i)
    return ordered

def pretty_table_label(tname: str):
    fam, _ = detect_family_and_flags(tname)
    label = FAMILY_LABELS_BASE.get(fam, fam)
    mode = _mode_label_from_tname(tname)
    # tag = "odds5p" if mode=="odds5p" else ("odds10u" if mode=="odds10u" else "")
    tag = "odds5p" if mode=="odds5p" else ("odds10u" if mode=="odds10u" else ("odds10_50" if mode=="odds10_50" else ""))
    return label + (f"（{tag}）" if tag else ""), tag

def build_where(place, sd, dist, inout):
    where, params = [], []
    if place: where.append('"場所" = ?'); params.append(place)
    if sd:    where.append('"芝・ダ" = ?'); params.append(sd)
    if dist:  where.append('"距離" = ?'); params.append(dist)
    if inout: where.append('COALESCE("芝(内・外)","芝(内外)") = ?'); params.append(inout)
    return (" AND ".join(where), params)

def list_all_tables(conn):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'foragg_%' ORDER BY name;")
    return [r[0] for r in cur.fetchall()]

@app.route("/", methods=["GET"])
def index():
    submitted = request.args.get("submitted", "") == "1"
    place = request.args.get("place", "").strip()
    sd    = request.args.get("sd", "").strip()
    dist  = request.args.get("dist", "").strip()
    inout = request.args.get("inout", "").strip()
    q_families = set(request.args.getlist("family"))
    q_inds     = set(request.args.getlist("ind"))
    sort    = request.args.get("sort", "複勝率_val").strip() or "複勝率_val"
    sortdir = request.args.get("sortdir", "DESC").strip().upper()
    if sortdir not in ("ASC","DESC"): sortdir = "DESC"
    q_odds_mode = request.args.get("odds_mode", "both")
    # if q_odds_mode not in ("both","normal","odds5p","odds10u"): q_odds_mode = "both"
    # if q_odds_mode not in ("both","normal","odds10_50","odds10u"): q_odds_mode = "both"
    if q_odds_mode not in ("both","normal","odds5p","odds10_50","odds10u"): q_odds_mode = "both"
    q_preset = request.args.get("preset", "none")
    if q_preset not in ("none","axis","keshi"): q_preset = "none"

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        def distinct_values(col):
            vals=set()
            for t in list_all_tables(conn):
                for c in (f'"{col}"', f'"{col.replace("内・外","内外")}"'):
                    try:
                        cur=conn.execute(f"SELECT DISTINCT {c} FROM {t} WHERE {c} IS NOT NULL;")
                        vals |= {str(x[0]) for x in cur.fetchall() if x[0] is not None and str(x[0]).strip() != ""}
                        break
                    except: continue
            return sorted(vals)
        places = distinct_values("場所")
        sds    = distinct_values("芝・ダ")
        dists  = distinct_values("距離")
        inouts = distinct_values("芝(内・外)")
        families   = scan_families(conn)
        indicators = scan_indicators(conn)

        results = []
        if submitted:
            base_where_sql, base_params = build_where(place, sd, dist, inout)
            where_desc_parts = [v for v in [place, sd, dist, inout] if v]
            if q_preset == "axis": where_desc_parts.append("プリセット:軸候補")
            elif q_preset == "keshi": where_desc_parts.append("プリセット:消し候補")
            where_desc = " / ".join(where_desc_parts) if where_desc_parts else "条件なし（全件）"

            for t in list_all_tables(conn):
                fam, _ = detect_family_and_flags(t)
                mode = _mode_label_from_tname(t)  # どの位置にあっても拾う方式に統一
                ind = detect_indicator(t)
                if q_families and fam not in q_families: continue
                if q_inds and ind not in q_inds: continue
                if q_odds_mode == "normal" and mode != "通常": continue
                if q_odds_mode == "odds5p" and mode != "odds5p": continue
                if q_odds_mode == "odds10_50" and mode != "odds10_50": continue
                if q_odds_mode == "odds10u" and mode != "odds10u": continue

                cur = conn.execute(f"PRAGMA table_info({t});")
                cols_in_table = [r[1] for r in cur.fetchall()]
                show_cols = [c for c in DISPLAY_ORDER if c in cols_in_table] or cols_in_table[:10]
                cols_sql = ", ".join([f'"{c}"' for c in show_cols])

                extra_where = []
                extra_params = []
                if q_preset == "axis":
                    has_f = "複勝率_val" in cols_in_table
                    has_r = "連対率_val" in cols_in_table
                    if not (has_f or has_r): continue
                    conds = []
                    if has_f: conds.append('"複勝率_val" >= ?'); extra_params.append(AXIS_FUKUSHO_MIN)
                    if has_r: conds.append('"連対率_val" >= ?'); extra_params.append(AXIS_RENTAI_MIN)
                    extra_where.append("(" + " OR ".join(conds) + ")")
                elif q_preset == "keshi":
                    if "複勝率_val" not in cols_in_table: continue
                    extra_where.append('"複勝率_val" < ?'); extra_params.append(KESHI_FUKUSHO_MAX)

                where_sql_parts = []
                if base_where_sql: where_sql_parts.append(base_where_sql)
                if extra_where: where_sql_parts.extend(extra_where)
                where_clause = ("WHERE " + " AND ".join(where_sql_parts)) if where_sql_parts else ""
                order_sql = f'ORDER BY "{sort}" {sortdir}' if (sort in show_cols and sort.endswith("_val")) else ""
                sql = f'SELECT {cols_sql} FROM "{t}" {where_clause} {order_sql} LIMIT 200;'
                params = list(base_params) + extra_params
                cur = conn.execute(sql, params)
                rows = [dict(zip(show_cols, r)) for r in cur.fetchall()]
                if not rows: continue
                label, tag = pretty_table_label(t)
                results.append({
                    "table_label": label, "indicator": ind or "（不明）",
                    "tag": tag, "where_desc": where_desc,
                    "columns": show_cols, "rows": rows
                })

    return render_template_string(HTML,
        places=places, sds=sds, dists=dists, inouts=inouts,
        q_place=place, q_sd=sd, q_dist=dist, q_inout=inout,
        q_sort=sort, q_sortdir=sortdir,
        families=families, indicators=indicators,
        q_families=q_families, q_inds=q_inds,
        q_odds_mode=q_odds_mode, q_preset=q_preset,
        axis_min=AXIS_FUKUSHO_MIN, rentai_min=AXIS_RENTAI_MIN, keshi_max=KESHI_FUKUSHO_MAX,
        submitted=submitted, results=results
    )

# ========================= entries_* ビュー =========================

HTML_ENTRIES_INDEX = """<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8"><title>エントリー一覧（出馬表）</title><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,"Noto Sans JP","Hiragino Kaku Gothic ProN",Meiryo,"Apple Color Emoji","Segoe UI Emoji"; margin:0; background:#f6f7f9;}
header{background:#111827; color:#fff; padding:16px 20px; font-size:18px;}
.container{max-width:1100px; margin:24px auto; padding:0 16px;}
.card{background:#fff; border-radius:12px; box-shadow:0 1px 3px rgba(0,0,0,.08); padding:16px; margin-bottom:16px;}
h1{font-size:18px; margin:0 0 12px;}
label{display:block; font-size:12px; color:#374151; margin-bottom:6px;}
select{width:100%; padding:8px; border:1px solid #e5e7eb; border-radius:8px; background:#fff;}
.grid{display:grid; grid-template-columns:repeat(3, 1fr); gap:12px;}
.row{display:flex; align-items:center; gap:12px; flex-wrap:wrap;}
table{width:100%; border-collapse:collapse; background:#fff; border:1px solid #e5e7eb; border-radius:8px; overflow:hidden;}
th, td{padding:8px 10px; border-bottom:1px solid #f1f5f9; font-size:13px; text-align:left; white-space:nowrap;}
th{background:#f8fafc; color:#334155;}
a{color:#2563eb; text-decoration:none;} a:hover{text-decoration:underline;}
.muted{color:#6b7280; font-size:12px;}
.nav{display:flex; gap:12px; align-items:center; margin-bottom:12px;}
.linkbtn{display:inline-block; padding:8px 12px; background:#f1f5f9; border-radius:8px; color:#111827; text-decoration:none;}
.linkbtn:hover{background:#e5e7eb;}
</style>
</head><body>
<header>エントリー一覧（出馬表）</header>
<div class="container">
  <div class="nav">
    <a class="linkbtn" href="{{ url_for('index') }}">← 横断検索に戻る</a>
  </div>
  <div class="card">
    <h1>テーブル / 開催日 を選択</h1>
    <form method="get">
      <div class="grid">
        <div>
          <label>entries テーブル</label>
          <select name="table" onchange="this.form.submit()">
            {% for t in tables %}<option value="{{t}}" {{ 'selected' if t==q_table else '' }}>{{t}}</option>{% endfor %}
          </select>
        </div>
        <div>
          <label>開催日（存在する場合）</label>
          <select name="date" onchange="this.form.submit()">
            <option value="">（すべて）</option>
            {% for d in dates %}<option value="{{d}}" {{ 'selected' if d==q_date else '' }}>{{d}}</option>{% endfor %}
          </select>
        </div>
        <div>
          <label>場所（任意）</label>
          <select name="place" onchange="this.form.submit()">
            <option value="">（すべて）</option>
            {% for p in places %}<option value="{{p}}" {{ 'selected' if p==q_place else '' }}>{{p}}</option>{% endfor %}
          </select>
        </div>
      </div>
      <input type="hidden" name="submitted" value="1">
    </form>
  </div>

  {% if races %}
  <div class="card">
    <h1>レース一覧</h1>
    <table>
      <thead><tr><th>日付</th><th>場 R</th><th>レース名</th><th>クラス名</th><th>距離</th><th>芝・ダ</th><th>リンク</th><th>条件ヒット馬</th></tr></thead>
      <tbody>
        {% for r in races %}
          <tr>
            <td>{{ r.date or '' }}</td>
            <td>{{ r.jo or '' }}</td>
            <td>{{ r.name or '' }}</td>
            <td>{{ r.cls or '' }}</td>
            <td>{{ r.dist or '' }}</td>
            <td>{{ r.sd or '' }}</td>
            <td><a href="{{ url_for('entries_race', table=q_table, ridn=r.jo,  date=q_date) }}">出馬表</a></td>
            <td><a href="{{ url_for('entries_hits', table=q_table, ridn=r.jo,  date=q_date) }}" target="_blank">条件ヒット馬</a></td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
    <div class="muted">「条件ヒット馬」を押すと、このレースで (1)(2) 条件に当てはまる馬だけを一覧表示します。</div>
  </div>
  {% else %}
    <div class="card muted">条件に一致するレースが見つかりません。</div>
  {% endif %}
</div></body></html>
"""

HTML_ENTRY_RACE = """<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8"><title>出馬表 - {{ table }}</title><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,"Noto Sans JP","Hiragino Kaku Gothic ProN",Meiryo,"Apple Color Emoji","Segoe UI Emoji"; margin:0; background:#f6f7f9;}
header{background:#111827; color:#fff; padding:16px 20px; font-size:18px;}
.container{max-width:1200px; margin:24px auto; padding:0 16px;}
.card{background:#fff; border-radius:12px; box-shadow:0 1px 3px rgba(0,0,0,.08); padding:16px; margin-bottom:16px;}
h1{font-size:18px; margin:0 0 12px;}
table{width:100%; border-collapse:collapse; background:#fff; border:1px solid #e5e7eb; border-radius:8px; overflow:hidden;}
th, td{padding:8px 10px; border-bottom:1px solid #f1f5f9; font-size:13px; text-align:left; white-space:nowrap; vertical-align:top;}
th{background:#f8fafc; color:#334155;}
a{color:#2563eb; text-decoration:none;} a:hover{text-decoration:underline;}
.muted{color:#6b7280; font-size:12px;}
.tag{display:inline-block; font-size:11px; color:#334155; background:#e5e7eb; padding:2px 6px; border-radius:999px; margin-left:6px;}
.nav{display:flex; gap:12px; align-items:center; margin-bottom:12px;}
.linkbtn{display:inline-block; padding:8px 12px; background:#f1f5f9; border-radius:8px; color:#111827; text-decoration:none;}
.linkbtn:hover{background:#e5e7eb;}
.key{font-size:12px;}
</style>
</head><body>
<header>出馬表</header>
<div class="container">
  <div class="nav">
    <a class="linkbtn" href="{{ url_for('entries_index', table=table) }}">← レース一覧に戻る</a>
    <a class="linkbtn" href="{{ url_for('index') }}">横断検索に戻る</a>
  </div>

  <div class="card">
    <h1>{{ race_header }}</h1>
    <div class="muted">{{ sub_header }}</div>
  </div>

  <div class="card">
    <table>
      <thead>
        <tr>
          <th>枠番</th><th>馬番</th><th>騎手</th><th>馬名</th>
          {% for ind in indicators %}<th>{{ ind }}<div class="muted">順位 / 偏差値</div></th>{% endfor %}
          <th>結合データ</th>
        </tr>
      </thead>
      <tbody>
        {% for row in rows %}
          <tr>
            <td>{{ row.get('枠番') or row.get('枠') or '' }}</td>
            <td>{{ row.get('馬番') or '' }}</td>
            <td>{{ row.get('騎手') or '' }}</td>
            <td>{{ row.name_disp }}</td>
            {% for ind in indicators %}
              <td>
                {% set rv = row.get(ind + '_順位') %}{% set hv = row.get(ind + '_偏差値') %}
                <div>{{ rv if rv is not none else '' }}{% if rv %} 位{% endif %}</div>
                <div>{{ hv if hv is not none else '' }}</div>
              </td>
            {% endfor %}
            {% set key = row.get('結合データ') %}
            <td class="key">{% if key %}<a href="{{ url_for('entries_key', key=key) }}" target="_blank">{{ key }}</a>{% else %}（なし）{% endif %}</td>
          </tr>
        {% endfor %}
      </tbody>
    </table>
  </div>
</div></body></html>
"""

HTML_HITS = """<!doctype html>
<html lang="ja"><head>
<meta charset="utf-8"><title>条件ヒット馬 - {{ head }}</title><meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,"Helvetica Neue",Arial,"Noto Sans JP","Hiragino Kaku Gothic ProN",Meiryo,"Apple Color Emoji","Segoe UI Emoji"; margin:0; background:#f6f7f9;}
header{background:#111827; color:#fff; padding:16px 20px; font-size:18px;}
.container{max-width:1200px; margin:24px auto; padding:0 16px;}
.card{background:#fff; border-radius:12px; box-shadow:0 1px 3px rgba(0,0,0,.08); padding:16px; margin-bottom:16px;}
h1{font-size:18px; margin:0 0 12px;}
table{width:100%; border-collapse:collapse; background:#fff; border:1px solid #e5e7eb; border-radius:8px; overflow:hidden;}
th, td{padding:8px 10px; border-bottom:1px solid #f1f5f9; font-size:13px; text-align:left; white-space:nowrap;}
th{background:#f8fafc; color:#334155;}
.muted{color:#6b7280; font-size:12px;}
.linkbtn{display:inline-block; padding:8px 12px; background:#f1f5f9; color:#111827; text-decoration:none;}
.linkbtn:hover{background:#e5e7eb;}
</style>
</head><body>
<header>条件ヒット馬</header>
<div class="container">
  <a class="linkbtn" href="{{ url_for('entries_index') }}">← レース一覧へ</a>

  <div class="card">
    <h1>{{ head }}</h1>
    <div class="muted">{{ sub }}</div>
  </div>

  <div class="card">
    <h1>(1) 絶対軸</h1>
    <div class="muted">
      • 件数 ≥ {{ abs_cnt }} かつ 連対率 ≥ {{ abs_ren }}% かつ（複勝回収値 ≥ {{ abs_frc }} または 単勝適正回収値 ≥ {{ abs_tra }}）
    </div>
    <table>
      <thead>
        <tr>
          <th>枠番</th><th>馬番</th><th>馬名</th><th>騎手</th><th>指標</th><th>パターン</th><th>オッズモード</th><th>結合データ</th>
          <th>件数</th><th>勝率_val</th><th>連対率_val</th><th>複勝率_val</th>
          <th>勝率</th><th>連対率</th><th>複勝率</th><th>掲示板率_val</th><th>掲示板率</th><th>平均着順</th>
          <th>単勝回収値</th><th>複勝回収値</th><th>単勝適正回収値</th>
        </tr>
      </thead>
      <tbody>
        {% if abs_rows %}
          {% for r in abs_rows %}
            <tr>
              <td>{{ r.waku or '' }}</td>
              <td>{{ r.uma or '' }}</td>
              <td>{{ r.name or '' }}</td>
              <td>{{ r.jock or '' }}</td>
              <td>{{ r.base }}</td>
              <td>{{ r.fam }}</td>
              <td>{{ r.mode or '通常' }}</td>
              <td>{{ r.key }}</td>
              <td>{{ r.cnt }}</td>
              <td>{{ r.sv }}</td>
              <td>{{ r.rv }}</td>
              <td>{{ r.fv }}</td>
              <td>{{ r.s }}</td>
              <td>{{ r.r }}</td>
              <td>{{ r.f }}</td>
              <td>{{ r.top5v }}</td><td>{{ r.top5 }}</td> 
              <td>{{ r.avg }}</td>
              <td>{{ r.tr }}</td>
              <td>{{ r.frc }}</td>
              <td>{{ r.tra }}</td>
            </tr>
          {% endfor %}
        {% else %}
          <tr><td colspan="21">該当なし</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h1>(2) 軸候補（以下のいずれか）</h1>
    <div class="muted">
      • 件数 ≥ {{ ax_cnt }} かつ 複勝率 ≥ {{ ax_fv80 }}% かつ 掲示板率 ≥ {{ ax_top5_90 }}% /
      • 件数 ≥ {{ ax_cnt }} かつ 連対率 ≥ {{ ax_rv60 }}% かつ 掲示板率 ≥ {{ ax_top5_90 }}% /
      • 件数 ≥ {{ ax_cnt }} かつ 複勝率 ≥ {{ ax_fv70 }}% かつ 掲示板率 ≥ {{ ax_top5_80 }}% かつ 複勝回収値 ≥ {{ ax_frc_100 }} /
      • 件数 ≥ {{ ax_cnt }} かつ 連対率 ≥ {{ ax_rv50 }}% かつ 掲示板率 ≥ {{ ax_top5_80 }}% かつ 複勝回収値 ≥ {{ ax_frc_100 }}
    </div>
    <table>
      <thead>
        <tr>
          <th>枠番</th><th>馬番</th><th>馬名</th><th>騎手</th><th>指標</th><th>パターン</th><th>オッズモード</th><th>結合データ</th>
          <th>件数</th><th>勝率_val</th><th>連対率_val</th><th>複勝率_val</th>
          <th>勝率</th><th>連対率</th><th>複勝率</th><th>掲示板率_val</th><th>掲示板率</th><th>平均着順</th>
          <th>単勝回収値</th><th>複勝回収値</th><th>単勝適正回収値</th>
        </tr>
      </thead>
      <tbody>
        {% if axis_rows %}
          {% for r in axis_rows %}
            <tr>
              <td>{{ r.waku or '' }}</td>
              <td>{{ r.uma or '' }}</td>
              <td>{{ r.name or '' }}</td>
              <td>{{ r.jock or '' }}</td>
              <td>{{ r.base }}</td>
              <td>{{ r.fam }}</td>
              <td>{{ r.mode or '通常' }}</td>
              <td>{{ r.key }}</td>
              <td>{{ r.cnt }}</td>
              <td>{{ r.sv }}</td>
              <td>{{ r.rv }}</td>
              <td>{{ r.fv }}</td>
              <td>{{ r.s }}</td>
              <td>{{ r.r }}</td>
              <td>{{ r.f }}</td>
              <td>{{ r.top5v }}</td><td>{{ r.top5 }}</td> 
              <td>{{ r.avg }}</td>
              <td>{{ r.tr }}</td>
              <td>{{ r.frc }}</td>
              <td>{{ r.tra }}</td>
            </tr>
          {% endfor %}
        {% else %}
          <tr><td colspan="21">該当なし</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h1>(3) 消し候補</h1>
    <div class="muted">
      • （全モード）件数 ≥ {{ ks_cnt }} かつ 複勝率 &lt; {{ ks_plc_any }}% かつ 掲示板率 &lt; {{ ks_top5 }}%　
      • （odds10u）件数 ≥ {{ ks_cnt }} かつ 複勝率 &lt; {{ ks_plc_10u }}%
    </div>
    <table>
      <thead>
        <tr>
          <th>枠番</th><th>馬番</th><th>馬名</th><th>騎手</th><th>指標</th><th>パターン</th><th>オッズモード</th><th>結合データ</th>
          <th>件数</th><th>勝率_val</th><th>連対率_val</th><th>複勝率_val</th>
          <th>勝率</th><th>連対率</th><th>複勝率</th>
          <th>掲示板率_val</th><th>掲示板率</th>  
          <th>平均着順</th>
          <th>単勝回収値</th><th>複勝回収値</th><th>単勝適正回収値</th>
        </tr>
      </thead>
      <tbody>
        {% if keshi_rows %}
          {% for r in keshi_rows %}
            <tr>
              <td>{{ r.waku or '' }}</td>
              <td>{{ r.uma or '' }}</td>
              <td>{{ r.name or '' }}</td>
              <td>{{ r.jock or '' }}</td>
              <td>{{ r.base }}</td>
              <td>{{ r.fam }}</td>
              <td>{{ r.mode or '通常' }}</td>
              <td>{{ r.key }}</td>
              <td>{{ r.cnt }}</td>
              <td>{{ r.sv }}</td>
              <td>{{ r.rv }}</td>
              <td>{{ r.fv }}</td>
              <td>{{ r.s }}</td>
              <td>{{ r.r }}</td>
              <td>{{ r.f }}</td>
              <td>{{ r.top5v }}</td><td>{{ r.top5 }}</td> 
              <td>{{ r.avg }}</td>
              <td>{{ r.tr }}</td>
              <td>{{ r.frc }}</td>
              <td>{{ r.tra }}</td>
            </tr>
          {% endfor %}
        {% else %}
          <tr><td colspan="21">該当なし</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>

  <div class="card">
    <h1>(4) 相手候補（以下のいずれか）</h1>
    <div class="muted">
      • 件数 ≥ {{ op_cnt }} かつ 複勝率 ≥ {{ op_fv60 }}% かつ 掲示板率 ≥ {{ op_top5_70 }}% /
      • 件数 ≥ {{ op_cnt }} かつ 連対率 ≥ {{ op_rv50 }}% かつ 掲示板率 ≥ {{ op_top5_70 }}% /
      • 件数 ≥ {{ op_cnt }} かつ 複勝率 ≥ {{ op_fv33 }}% かつ 複勝回収値 ≥ {{ op_frc_150 }} かつ 掲示板率 ≥ {{ op_top5_50 }}%
    </div>
    <table>
      <thead>
        <tr>
          <th>枠番</th><th>馬番</th><th>馬名</th><th>騎手</th><th>指標</th><th>パターン</th><th>オッズモード</th><th>結合データ</th>
          <th>件数</th><th>勝率_val</th><th>連対率_val</th><th>複勝率_val</th>
          <th>勝率</th><th>連対率</th><th>複勝率</th>
          <th>掲示板率_val</th><th>掲示板率</th><th>平均着順</th>
          <th>単勝回収値</th><th>複勝回収値</th><th>単勝適正回収値</th>
        </tr>
      </thead>
      <tbody>
        {% if opp_rows %}
          {% for r in opp_rows %}
            <tr>
              <td>{{ r.waku or '' }}</td>
              <td>{{ r.uma or '' }}</td>
              <td>{{ r.name or '' }}</td>
              <td>{{ r.jock or '' }}</td>
              <td>{{ r.base }}</td>
              <td>{{ r.fam }}</td>
              <td>{{ r.mode or '通常' }}</td>
              <td>{{ r.key }}</td>
              <td>{{ r.cnt }}</td>
              <td>{{ r.sv }}</td>
              <td>{{ r.rv }}</td>
              <td>{{ r.fv }}</td>
              <td>{{ r.s }}</td>
              <td>{{ r.r }}</td>
              <td>{{ r.f }}</td>
              <td>{{ r.top5v }}</td><td>{{ r.top5 }}</td> 
              <td>{{ r.avg }}</td>
              <td>{{ r.tr }}</td>
              <td>{{ r.frc }}</td>
              <td>{{ r.tra }}</td>
            </tr>
          {% endfor %}
        {% else %}
          <tr><td colspan="21">該当なし</td></tr>
        {% endif %}
      </tbody>
    </table>
  </div>

</div></body></html>
"""

def get_entries_tables(conn):
    cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'entries_%' ORDER BY name;")
    all_names = [r[0] for r in cur.fetchall()]
    # xjoin は一覧には出さない（必要なら別UIで）
    return [t for t in all_names if not t.startswith("entries_xjoin_")]

def detect_date_column(conn, table):
    candidates = ["日付", "開催日", "RaceDate", "年月日"]
    cur = conn.execute(f"PRAGMA table_info('{table}')")
    cols = [r[1] for r in cur.fetchall()]
    for c in candidates:
        if c in cols: return c
    return None

def distinct_col_values(conn, table, col):
    try:
        cur = conn.execute(f'SELECT DISTINCT "{col}" FROM "{table}" WHERE "{col}" IS NOT NULL AND TRIM("{col}") <> "" ORDER BY 1')
        return [r[0] for r in cur.fetchall()]
    except:
        return []

@app.route("/entries", methods=["GET"])
def entries_index():
    q_table = request.args.get("table", "").strip()
    q_date  = request.args.get("date", "").strip()
    q_place = request.args.get("place", "").strip()

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        tables = get_entries_tables(conn)
        if not tables:
            return render_template_string(HTML_ENTRIES_INDEX, tables=[], q_table="", dates=[], q_date="", places=[], q_place="", races=[])

        if not q_table or q_table not in tables:
            q_table = tables[0]

        date_col = detect_date_column(conn, q_table)
        dates = distinct_col_values(conn, q_table, date_col) if date_col else []

        if date_col and not q_date and dates:
            # def _date_key(d):  # '2025/1/2' → 202512, '2025-01-02' → 20250102
            #     return int(re.sub(r'\D', '', str(d)) or 0)
            def _date_key(d):
                s = str(d)
                m = re.search(r'(\d{4})\D?(\d{1,2})\D?(\d{1,2})', s)
                if m:
                    y, mo, da = m.groups()
                    return int(f"{y}{int(mo):02d}{int(da):02d}")  # YYYYMMDD
                # フォールバック：純数字8桁ならそのまま、そうでなければ0
                ds = re.sub(r'\D', '', s)
                return int(ds) if len(ds) == 8 else 0
            q_date = max(dates, key=_date_key)

        places = distinct_col_values(conn, q_table, "場所")

        where = []
        params = []
        if date_col and q_date:
            where.append(f'"{date_col}" = ?'); params.append(q_date)
        if q_place:
            where.append('"場所" = ?'); params.append(q_place)

        where_sql = ("WHERE " + " AND ".join(where)) if where else ""
        date_expr = f'"{date_col}"' if date_col else "''"

        # 代表行は ROW_NUMBER で決定。グルーピングキーは「場 R」優先（空なら レースID）
        sql = f'''
          WITH base AS (
            SELECT
              COALESCE("場 R","レースID(新)","レースID(新/馬番無)") AS ridn,   -- ★ここを「場 R」優先に
              "場 R" AS jo,
              NULLIF("レース名",'') AS name,
              NULLIF("クラス名",'') AS cls,
              "距離" AS dist,
              "芝・ダ" AS sd,
              {date_expr} AS date,
              ROW_NUMBER() OVER (
                PARTITION BY COALESCE("場 R","レースID(新)","レースID(新/馬番無)")
                ORDER BY CAST("馬番" AS INT)
              ) AS rn
            FROM "{q_table}"
            {where_sql}
          )
          SELECT
            ridn,
            COALESCE(jo, ridn) AS jo,
            name, cls, dist, sd, date
          FROM base
          WHERE rn = 1
          ORDER BY jo
        '''
        cur = conn.execute(sql, params)
        rows = [dict(r) for r in cur.fetchall()]

    return render_template_string(HTML_ENTRIES_INDEX,
                                  tables=tables, q_table=q_table,
                                  dates=dates, q_date=q_date,
                                  places=places, q_place=q_place,
                                  races=rows)

@app.route("/entries/race", methods=["GET"])
def entries_race():
    table = request.args.get("table","").strip()
    ridn  = request.args.get("ridn","").strip()
    q_date = request.args.get("date","").strip()
    if not table or not ridn: return redirect(url_for('entries_index'))

    # /entries/race の先頭付近
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if not _validate_entries_table(conn, table):
            return redirect(url_for('entries_index'))

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        date_col = detect_date_column(conn, table)
        rows = _fetch_race_rows(conn, table, ridn, date_col=date_col, date_val=q_date)
        if not rows:
            return render_template_string(HTML_ENTRY_RACE, table=table, race_header="該当レースなし", sub_header="", indicators=[], rows=[])

        # 表示名を付けておく（馬名S対応）
        for r in rows:
            r["name_disp"] = _row_display_name(r)

        head = rows[0]
        rid_disp = _norm(head.get("場 R")) or _norm(head.get("レースID(新)")) or _norm(head.get("レースID(新/馬番無)"))
        race_header = f'{rid_disp} / {_norm(head.get("レース名"))}'
        sub_header  = f'{_norm(head.get("場所"))} / {_norm(head.get("芝・ダ"))} / {_norm(head.get("距離"))} / {_norm(head.get("芝(内・外)")) or _norm(head.get("芝(内外)"))}'
        indicators = _collect_indicator_bases(rows)

    return render_template_string(HTML_ENTRY_RACE,
                                  table=table,
                                  race_header=race_header,
                                  sub_header=sub_header,
                                  indicators=indicators,
                                  rows=rows)

HTML_KEY_PAGE = """<!doctype html><meta charset="utf-8"><title>キー詳細</title>
<style>body{font-family:system-ui; margin:20px;} table{border-collapse:collapse} th,td{border:1px solid #ddd; padding:6px 8px;}</style>
<h1>結合データ：{{ key }}</h1>
<table>
<thead>
<tr>
  <th>テーブル名</th><th>件数</th>
  <th>勝率_val</th><th>勝率</th>
  <th>連対率_val</th><th>連対率</th>
  <th>複勝率_val</th><th>複勝率</th>
  <th>掲示板率_val</th><th>掲示板率</th>
  <th>平均着順</th>
  <th>単勝回収値</th><th>複勝回収値</th><th>単勝適正回収値</th>
</tr>
</thead>
<tbody>
{% for r in rows %}
<tr>
  <td>{{ r["テーブル名"] }}</td><td>{{ r["件数"] }}</td>
  <td>{{ r["勝率_val"] }}</td><td>{{ r["勝率"] }}</td>
  <td>{{ r["連対率_val"] }}</td><td>{{ r["連対率"] }}</td>
  <td>{{ r["複勝率_val"] }}</td><td>{{ r["複勝率"] }}</td>
  <td>{{ r.get("掲示板率_val") }}</td><td>{{ r.get("掲示板率") }}</td>
  <td>{{ r["平均着順"] }}</td>
  <td>{{ r["単勝回収値"] }}</td><td>{{ r["複勝回収値"] }}</td><td>{{ r["単勝適正回収値"] }}</td>
</tr>
{% endfor %}
{% if not rows %}<tr><td colspan="14">該当なし</td></tr>{% endif %}
</tbody></table>
"""

@app.route("/entries/key", methods=["GET"])
def entries_key():
    key = request.args.get("key","").strip()
    if not key: return redirect(url_for('entries_index'))

    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        sql = '''
          SELECT "テーブル名","件数","勝率_val","勝率","連対率_val","連対率","複勝率_val","複勝率",
                 "掲示板率_val","掲示板率",
                 "単勝回収値","複勝回収値","単勝適正回収値","平均着順"
          FROM unified_all
          WHERE "結合データ" = ?
          ORDER BY "複勝率_val" DESC, "連対率_val" DESC
          LIMIT 200
        '''
        cur = conn.execute(sql, [key])
        rows = [dict(r) for r in cur.fetchall()]

    return render_template_string(HTML_KEY_PAGE, key=key, rows=rows)

def _resolve_xjoin_table(table: str) -> str:
    # すでに xjoin テーブルならそのまま返す
    if table.startswith("entries_xjoin_"):
        return table
    # 通常の entries_* なら xjoin 名に変換
    if table.startswith("entries_"):
        return "entries_xjoin_" + table[len("entries_"):]
    # 念のためのフォールバック
    return "entries_xjoin_" + table

def _mode_rank(mode: str) -> int:
    # 表示優先度：通常(0) > odds10u(1) > odds5p(2)
    # return {"通常": 0, "odds10u": 1, "odds5p": 2}.get(mode or "", 3)
    return {"通常": 0, "odds10u": 1, "odds5p": 2, "odds10_50": 3}.get(mode or "", 4)

@app.route("/entries/hits", methods=["GET"])
def entries_hits():
    table = request.args.get("table","").strip()
    ridn  = request.args.get("ridn","").strip()
    q_date = request.args.get("date","").strip()
    if not table or not ridn: return redirect(url_for('entries_index'))

    fam_list = [
        "normal","normal_band",
        "rankB","rankB_band",
        "rankJ","rankJ_band",
        "rankBJ","rankBJ_band",
        "normalJ","normalJ_band",
        "hensachiA","hensachiB","hensachiJ","hensachiBJ","hensachiAJ"
    ]

    abs_rows, axis_rows, keshi_rows, opp_rows = [], [], [], []
    seen_abs, seen_axis, seen_keshi, seen_opp = set(), set(), set(), set()

    def _add_row(cat, rec, keytuple):
        if cat == "abs":
            if keytuple not in seen_abs: abs_rows.append(rec); seen_abs.add(keytuple)
        elif cat == "keshi":
            if keytuple not in seen_keshi: keshi_rows.append(rec); seen_keshi.add(keytuple)
        elif cat == "axis":
            if keytuple not in seen_axis: axis_rows.append(rec); seen_axis.add(keytuple)
        elif cat == "opp":
            if keytuple not in seen_opp:  opp_rows.append(rec);  seen_opp.add(keytuple)

    def _scale_by_frac_mode(fv, rv, sv):
        m = max(fv, rv, sv)
        if m == 0.0:
            return 1.0  # 判定不能は 1.0 に倒す（安全側）
        return 0.01 if m <= 1.0 else 1.0

    # /entries/hits の先頭付近
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        if not _validate_entries_table(conn, table):
            return redirect(url_for('entries_index'))
            
    # ---- xjoin 高速パス ----
    xname = _resolve_xjoin_table(table)
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        cur = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?;", (xname,))
        if cur.fetchone():
            date_col = detect_date_column(conn, xname)

            where_core = '( "場 R" = ? OR COALESCE("レースID(新)","レースID(新/馬番無)","") = ? )'
            params = [ridn, ridn]
            if date_col and q_date:
                where_core += f' AND "{date_col}" = ?'
                params.append(q_date)

            rows = conn.execute(f'SELECT * FROM "{xname}" WHERE {where_core}', params).fetchall()
            if not rows:
                ridn_norm = ridn.replace(" ", "").replace("　", "")
                where_alt = 'REPLACE(REPLACE("場 R"," ",""),"　","") = ?'
                params_alt = [ridn_norm]
                if date_col and q_date:
                    where_alt += f' AND "{date_col}" = ?'
                    params_alt.append(q_date)
                rows = conn.execute(f'SELECT * FROM "{xname}" WHERE {where_alt}', params_alt).fetchall()
            if not rows and ridn.endswith('R'):
                where_r = '"場 R" = ?'
                params_r = [ridn[:-1]]
                if date_col and q_date:
                    where_r += f' AND "{date_col}" = ?'
                    params_r.append(q_date)
                rows = conn.execute(f'SELECT * FROM "{xname}" WHERE {where_r}', params_r).fetchall()

            rows = [dict(r) for r in rows]

            # しきい値スケール（最初の行で判定）
            scale = _detect_scale(rows) if rows else 1.0

            for r in rows:
                uma = str(r.get("馬番") or "")
                if not uma: continue

                waku = _norm(r.get("枠番") or r.get("枠"))
                name = _row_display_name(r)
                jock = _norm(r.get("騎手"))
                base = _norm(r.get("base"))
                fam  = _norm(r.get("family"))
                key  = _norm(r.get("key"))
                tname = _norm(r.get("テーブル名"))
                mode_raw = _norm(r.get("オッズモード") or r.get("odds_mode") or r.get("mode"))
                if mode_raw:
                    m = mode_raw.lower()
                    # mode = "odds10u" if "odds10u" in m else ("odds5p" if "odds5p" in m else "通常")
                    mode = "odds10u" if "odds10u" in m else ("odds5p" if "odds5p" in m else ("odds10_50" if "odds10_50" in m else "通常"))
                else:
                    mode = _mode_label_from_tname(tname)

                sv = _tofloat(r.get("勝率_val"))
                rv = _tofloat(r.get("連対率_val"))
                fv = _tofloat(r.get("複勝率_val"))
                cnt = int(r.get("件数") or 0)
                s   = _norm(r.get("勝率"))
                rr  = _norm(r.get("連対率"))
                f   = _norm(r.get("複勝率"))
                avg = _tofloat(r.get("平均着順"))
                tr  = _tofloat(r.get("単勝回収値"))
                frc = _tofloat(r.get("複勝回収値"))
                tra = _tofloat(r.get("単勝適正回収値"))

                raw_top5 = r.get("掲示板率_val") or r.get("掲示板率")
                top5v = _to_pct(raw_top5)                      # 0〜1 も 0〜100 も吸収
                has_top5 = bool(str(raw_top5 or '').strip())  # 空欄は False

                rec = {
                    "waku": waku, "uma": uma, "name": name, "jock": jock,
                    "base": base, "fam": fam, "key": key,
                    "mode": mode,
                    "sv": sv, "rv": rv, "fv": fv, "cnt": cnt,
                    "s": s, "r": rr, "f": f,
                    "avg": avg, "tr": tr, "frc": frc, "tra": tra,
                    "tan": tr, "fuku": frc, "tan_adj": tra,
                    "top5v": top5v,                                         # ★ 表示用
                    # "top5": _norm(r.get("掲示板率")) or (f"{top5v:.1f}%" if top5v else ""),  # ★ 表示用
                    "top5": (f"{top5v:.1f}%" if has_top5 else ""),
                }
                idtuple = (uma, base, fam, key, mode, tname)

                # --- 分類は classify() に集約 ---
                cat = classify({
                    "cnt": cnt, "fv": fv, "rv": rv, "frc": frc, "tra": tra,
                    "mode": mode,
                    "top5v": top5v, "has_top5": has_top5,
                    "f": f,  # 文字列（"18.75% (..)" 等）
                    "r": rr,
                }, scale)

                if cat:
                    _add_row(cat, rec, idtuple)
                    # keshi/abs/axis は排他なので次へ
                    if cat in ("keshi", "abs", "axis"):
                        continue
                # opp は排他にしないなら continue しない（今の挙動踏襲）

            def _to_int(s):
                m = re.match(r'\d+', str(s))
                return int(m.group()) if m else 1_000_000

            abs_rows.sort( key=lambda x: (_mode_rank(x["mode"]), _to_int(x["uma"]), -x["fv"]))
            axis_rows.sort(key=lambda x: (_mode_rank(x["mode"]), _to_int(x["uma"]), -x["fv"]))
            keshi_rows.sort(key=lambda x: (_mode_rank(x["mode"]), _to_int(x["uma"]), x["fv"]))
            opp_rows.sort( key=lambda x: (_mode_rank(x["mode"]), _to_int(x["uma"]), -x["fv"]))

            head = f'{(rows[0].get("場 R") if rows else ridn)} / （xjoin利用）'
            sub  = ''

            return render_template_string(HTML_HITS,
                head=head, sub=sub,
                abs_rows=abs_rows, axis_rows=axis_rows, keshi_rows=keshi_rows, opp_rows=opp_rows,

                # (1) 絶対軸（表示用）
                abs_cnt=int(ABS_CNT), abs_ren=float(ABS_REN), abs_frc=float(ABS_FRC), abs_tra=float(ABS_TRA),

                # (2) 軸候補（表示用）
                ax_cnt=int(AX_CNT),
                ax_fv80=float(AX_FV_80), ax_rv60=float(AX_RV_60),
                ax_fv70=float(AX_FV_70), ax_rv50=float(AX_RV_50),
                ax_top5_90=float(AX_TOP5_90), ax_top5_80=float(AX_TOP5_80),
                ax_frc_100=float(AX_FRC_100),

                # (3) 消し（表示用）
                ks_cnt=int(KS_NEW_CNT), ks_plc_any=float(KS_NEW_PLC_MAX_ANY), ks_plc_10u=float(KS_NEW_PLC_MAX_10U),
                ks_top5=float(KS_NEW_TOP5_MAX_ANY),

                # (4) 相手（表示用）
                op_cnt=int(OP_CNT),
                op_fv60=float(OP_FV_60), op_rv50=float(OP_RV_50), op_fv33=float(OP_FV_33),
                op_frc_150=float(OP_FRC_150),
                op_top5_70=float(OP_TOP5_70), op_top5_50=float(OP_TOP5_50),
            )

    # ---- 通常パス（xjoinが無い場合：従来ロジック＋新条件）----
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        date_col = detect_date_column(conn, table)
        rows = _fetch_race_rows(conn, table, ridn, date_col=date_col, date_val=q_date)
        if not rows:
            return render_template_string(HTML_HITS,
                head="該当レースなし", sub="",
                abs_rows=abs_rows, axis_rows=axis_rows, keshi_rows=keshi_rows, opp_rows=opp_rows,

                # (1) 絶対軸（表示用）
                abs_cnt=int(ABS_CNT), abs_ren=float(ABS_REN), abs_frc=float(ABS_FRC), abs_tra=float(ABS_TRA),

                # (2) 軸候補（表示用）
                ax_cnt=int(AX_CNT),
                ax_fv80=float(AX_FV_80), ax_rv60=float(AX_RV_60),
                ax_fv70=float(AX_FV_70), ax_rv50=float(AX_RV_50),
                ax_top5_90=float(AX_TOP5_90), ax_top5_80=float(AX_TOP5_80),
                ax_frc_100=float(AX_FRC_100),

                # (3) 消し（表示用）
                ks_cnt=int(KS_NEW_CNT), ks_plc_any=float(KS_NEW_PLC_MAX_ANY), ks_plc_10u=float(KS_NEW_PLC_MAX_10U),
                ks_top5=float(KS_NEW_TOP5_MAX_ANY),

                # (4) 相手候補（表示用）
                op_cnt=int(OP_CNT),
                op_fv60=float(OP_FV_60), op_rv50=float(OP_RV_50), op_fv33=float(OP_FV_33),
                op_frc_150=float(OP_FRC_150),
                op_top5_70=float(OP_TOP5_70), op_top5_50=float(OP_TOP5_50),
            )

        head = f'{_norm(rows[0].get("場 R"))} / {_norm(rows[0].get("レース名"))}'
        sub  = f'{_norm(rows[0].get("場所"))} / {_norm(rows[0].get("芝・ダ"))} / {_norm(rows[0].get("距離"))} / {_norm(rows[0].get("芝(内・外)")) or _norm(rows[0].get("芝(内外)"))}'
        bases = _collect_indicator_bases(rows)

        # しきい値スケール（先頭行で判定）
        # tmp_fv = _tofloat(rows[0].get(bases[0] + "_偏差値")) if bases else 0.0  # 値が無い場合は後で判定
        scale = 1.0  # entries_*のみでは率の0-1/0-100判定が難しいため 1.0 固定（unified参照時に判定されます）

        for r in rows:
            name = _row_display_name(r)
            waku = _norm(r.get("枠番")) or _norm(r.get("枠"))
            uma  = _norm(r.get("馬番"))
            jock = _norm(r.get("騎手"))
            if not uma: continue

            for base in bases:
                key_map, like_prefix = _build_key_candidates(r, base)
                for fam in fam_list:
                    for key in key_map.get(fam, []):
                        hits = _query_unified_for_key(conn, fam, base, key, like_prefix)
                        for h in hits:
                            sv = _tofloat(h.get("勝率_val"))
                            rv = _tofloat(h.get("連対率_val"))
                            fv = _tofloat(h.get("複勝率_val"))
                            avg = _tofloat(h.get("平均着順"))
                            tr  = _tofloat(h.get("単勝回収値"))
                            frc = _tofloat(h.get("複勝回収値"))
                            tra = _tofloat(h.get("単勝適正回収値"))
                            tname = _norm(h.get("テーブル名"))
                            mode = _mode_label_from_tname(tname)

                            raw_top5 = h.get("掲示板率_val") or h.get("掲示板率")
                            top5v = _to_pct(raw_top5)
                            has_top5 = bool(str(raw_top5 or '').strip())

                            rec = {
                                "waku": waku, "uma": uma, "name": name, "jock": jock,
                                "base": base, "fam": fam, "key": key,
                                "mode": mode,
                                "sv": sv, "rv": rv, "fv": fv,
                                "cnt": int(h.get("件数") or 0),
                                "s": _norm(h.get("勝率")),
                                "r": _norm(h.get("連対率")),
                                "f": _norm(h.get("複勝率")),
                                "avg": avg, "tr": tr, "frc": frc, "tra": tra,
                                "tan": tr, "fuku": frc, "tan_adj": tra,
                                "top5v": top5v,                                         # ★ 表示用
                                # "top5": _norm(h.get("掲示板率")) or (f"{top5v:.1f}%" if top5v else ""),  # ★ 表示用
                                "top5": (f"{top5v:.1f}%" if has_top5 else ""),
                            }
                            idtuple = (uma, base, fam, key, mode)

                            # # 率スケール（unified_allの値で判定）
                            scale2 = _scale_by_frac_mode(rec["fv"], rec["rv"], rec["sv"])


                            # --- 分類は classify() に集約 ---
                            cat = classify({
                                "cnt": rec["cnt"], "fv": rec["fv"], "rv": rec["rv"], "frc": rec["frc"], "tra": rec["tra"],
                                "mode": mode,
                                "top5v": top5v, "has_top5": has_top5,
                                "f": rec.get("f", ""),
                                "r": rec.get("r", ""),
                            }, scale2)

                            if cat:
                                _add_row(cat, rec, idtuple)
                                if cat in ("keshi", "abs", "axis"):
                                    continue


    def _to_int(s):
        m = re.match(r'\d+', str(s))
        return int(m.group()) if m else 1_000_000

    abs_rows.sort( key=lambda x: (_mode_rank(x["mode"]), _to_int(x["uma"]), -x["fv"]))
    axis_rows.sort(key=lambda x: (_mode_rank(x["mode"]), _to_int(x["uma"]), -x["fv"]))
    keshi_rows.sort(key=lambda x: (_mode_rank(x["mode"]), _to_int(x["uma"]), x["fv"]))
    opp_rows.sort( key=lambda x: (_mode_rank(x["mode"]), _to_int(x["uma"]), -x["fv"]))

    return render_template_string(HTML_HITS,
        head=head, sub=sub,
        abs_rows=abs_rows, axis_rows=axis_rows, keshi_rows=keshi_rows, opp_rows=opp_rows,

        # (1) 絶対軸（表示用）
        abs_cnt=int(ABS_CNT), abs_ren=float(ABS_REN), abs_frc=float(ABS_FRC), abs_tra=float(ABS_TRA),

        # (2) 軸候補（表示用）
        ax_cnt=int(AX_CNT),
        ax_fv80=float(AX_FV_80), ax_rv60=float(AX_RV_60),
        ax_fv70=float(AX_FV_70), ax_rv50=float(AX_RV_50),
        ax_top5_90=float(AX_TOP5_90), ax_top5_80=float(AX_TOP5_80),
        ax_frc_100=float(AX_FRC_100),

        # (3) 消し（表示用）
        ks_cnt=int(KS_NEW_CNT), ks_plc_any=float(KS_NEW_PLC_MAX_ANY), ks_plc_10u=float(KS_NEW_PLC_MAX_10U),
        ks_top5=float(KS_NEW_TOP5_MAX_ANY),

        # (4) 相手（表示用）
        op_cnt=int(OP_CNT),
        op_fv60=float(OP_FV_60), op_rv50=float(OP_RV_50), op_fv33=float(OP_FV_33),
        op_frc_150=float(OP_FRC_150),
        op_top5_70=float(OP_TOP5_70), op_top5_50=float(OP_TOP5_50),
    )

@app.route("/healthz")
def healthz():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("SELECT 1")
        return "ok"
    except Exception as e:
        return f"ng: {e}", 500

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8000"))
    app.run(host="0.0.0.0", port=port, debug=False)