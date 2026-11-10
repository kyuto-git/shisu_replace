# -*- coding: utf-8 -*-
"""Flask UI。

  /              横断検索（集計表を family・指数・オッズ帯・地域・コース条件で絞り込み。
                 表ごとの表示と、全表をまとめて WEDGE などで並べる横断ランキングの2通り。
                 集計表は __tables_meta__ の kind=foragg の行。中身は unified_all の「テーブル名」が同じ行
                 （前の版の DB に1表ずつのテーブル foragg_* が残っていても使わない。前の版の unified_all は
                 そのテーブルを同じ順で詰め直したものなので、表示は同じ）
  /entries       出馬表のレース一覧（日付・場所で絞り込み）
  /entries/race  出馬表（各馬の指数順位・偏差値）
  /entries/key   結合データの前方一致で unified_all を表示
  /entries/hits  条件ヒット馬（絶対軸・軸候補・消し候補・相手候補）
  /entries/wedge レース別 WEDGE 一覧（全馬 × 該当する全キーの WEDGE / adjusted_WEDGE。閾値で足切りしない）
  /ingest        指数の取得（Ω・吉馬・かよちん・keibaAI のスクレイピング、ラップ計算、安井Excel。shisu/ingest/web.py）
  /healthz       DB 接続確認

条件ヒット馬は entries_xjoin_*（build_db が事前計算した照合結果）があればそれを読み、
無ければ同じ照合関数（shisu.matching）でその場で照合する。分類は shisu.classify だけで行う。
DB の率はすべて 0–100（%）。
"""
from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import unicodedata
from contextlib import contextmanager

from flask import Flask, Response, redirect, render_template_string, request, url_for

from shisu import config
from shisu.classify import CATEGORIES, CATEGORY_LABELS, classify, description_lines, sql_condition
from shisu.indicators import INDICATOR_ORDER
from shisu.ingest.web import make_blueprint as make_ingest_blueprint
from shisu import families as fam_registry
from shisu.matching import HIT_COLUMNS, UNIFIED, indicator_bases, match_entry_row, unified_status
from shisu.naming import ENTRIES_ALL, XJOIN_PREFIX, xjoin_table_for
from shisu.odds import ODDS_LABELS, ODDS_MODES, ODDS_RANK
from shisu.wedge import DEFAULT_MAX_YEARS, DEFAULT_MIN_YEAR_COUNT, WEDGE_COLUMNS, WEDGE_FLOAT_COLUMNS

DB_PATH = str(config.db_path())
META = "__tables_meta__"
FACETS = "__facets__"
MAX_TABLES = 100       # 横断検索で一度に表示する集計表の上限（超えたら絞り込みを促す）
ROWS_PER_TABLE = 200
RANK_LIMIT = 500       # 横断ランキングの表示行数
SORTABLE = ("件数", "勝率_val", "連対率_val", "複勝率_val", "2着率_val", "3着率_val", "掲示板率_val",
            "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値",
            "WIN", "EDGE", "WEDGE", "adjusted_WEDGE", "adjusted_WEDGE_件数")
DISPLAY_ORDER = ("結合データ", "件数", "勝率_val", "勝率", "連対率_val", "連対率", "複勝率_val", "複勝率",
                 "2着率_val", "2着率", "3着率_val", "3着率", "掲示板率_val", "掲示板率",
                 "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値", *WEDGE_COLUMNS,
                 "提案勝率", "提案連対率", "提案複勝率", "提案2着率", "提案3着率",
                 "場所", "芝・ダ", "距離", "芝(内・外)")
RANK_COLUMNS = ("結合データ", "件数", "複勝率_val", "連対率_val", "掲示板率_val", "単勝回収値", "複勝回収値",
                "単勝適正回収値", *WEDGE_COLUMNS)
PRESETS = ("none", "abs", "axis", "opp", "keshi")
VIEWS = ("tables", "rank")
TABLE_META_COLUMNS = ("family", "odds_mode", "rank_scheme", "indicator", "region", "テーブル名")
# 下限フィルタ（クエリ引数名, 列）
MIN_FILTERS = (("min_cnt", "件数"), ("min_wedge", "WEDGE"), ("min_adj", "adjusted_WEDGE"))

app = Flask(__name__)


# ========================= 共通 =========================
@contextmanager
def connect():
    """読み取り用の接続（with を抜けたら閉じる）。DB を作ったときの family（__families__）を台帳に入れる。"""
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    try:
        _sync_families(con)
        yield con
    finally:
        con.close()


_FAMILY_SYNC: dict = {}


def _sync_families(con) -> None:
    """DB の __families__ を family の台帳（ラベル・並び・照合に使う family）に反映する（DB が変わったときだけ）。"""
    try:
        stamp = (DB_PATH, os.path.getmtime(DB_PATH))
    except OSError:
        return
    if _FAMILY_SYNC.get("stamp") == stamp:
        return
    try:
        fams = fam_registry.load_from_db(con)
    except (sqlite3.Error, ValueError):
        fams = []
    fam_registry.reset()                 # 前の DB の定義を残さない（同じ名前で部品が変わった family もある）
    if fams:
        fam_registry.set_active(fams, "DB の __families__")
    _FAMILY_SYNC["stamp"] = stamp


def q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def table_exists(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def db_status(con) -> str:
    """'ok' / 'empty'（未構築）/ 'old'（旧版の build_db で作った DB）。"""
    u = unified_status(con)
    if u == "old":
        return "old"
    if table_exists(con, META) and "kind" not in table_columns(con, META):
        return "old"
    return "ok" if u == "ok" and table_exists(con, META) else "empty"


DB_WARNINGS = {
    "old": "DB が旧形式です。replace → aggregate → build_db.py --fresh で作り直してください。",
    "empty": "DB にデータがありません。build_db.py を実行してください。",
}


def table_columns(con, name: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({q(name)})")]


def fmt(v, digits: int = 2) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:
            return ""
        return f"{v:.{digits}f}"
    return str(v)


def fmt_cell(col: str, v) -> str:
    """列に合わせた表示（WIN/EDGE/WEDGE は小数3桁）。"""
    return fmt(v, 3 if col in WEDGE_FLOAT_COLUMNS else 2)


def to_int(s, default: int = 1_000_000) -> int:
    m = re.match(r"\d+", str(s or ""))
    return int(m.group()) if m else default


def odds_label(mode: str) -> str:
    return ODDS_LABELS.get(mode or "normal", mode or "")


def family_label(fam: str, scheme: str = "") -> str:
    label = fam_registry.label(fam)
    return f"{label}（{scheme}）" if scheme else label


app.jinja_env.globals.update(fmt=fmt, fmt_cell=fmt_cell, odds_label=odds_label, family_label=family_label,
                             CATEGORY_LABELS=CATEGORY_LABELS)

LAYOUT = """<!doctype html>
<html lang="ja"><head><meta charset="utf-8"><title>{{ title }}</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:system-ui,-apple-system,"Segoe UI","Hiragino Kaku Gothic ProN","Noto Sans JP",Meiryo,sans-serif;margin:0;background:#f6f7f9;color:#111827}
header{background:#111827;color:#fff;padding:14px 20px;font-size:18px}
.container{max-width:1280px;margin:20px auto;padding:0 16px}
.card{background:#fff;border-radius:12px;box-shadow:0 1px 3px rgba(0,0,0,.08);padding:16px;margin-bottom:16px;overflow-x:auto}
h1{font-size:17px;margin:0 0 10px} h2{font-size:15px;margin:14px 0 6px}
label{font-size:12px;color:#374151}
select{width:100%;padding:6px;border:1px solid #e5e7eb;border-radius:8px;background:#fff}
input:not([type]),input[type=text],input[type=number]{padding:6px;border:1px solid #e5e7eb;border-radius:8px;box-sizing:border-box}
input[type=file]{font-size:12px;margin:4px 0}
code{background:#f1f5f9;padding:1px 4px;border-radius:4px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.cbs{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.cb{display:inline-flex;align-items:center;gap:4px;font-size:12px;background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:4px 8px}
button{padding:8px 16px;border:0;background:#2563eb;color:#fff;border-radius:8px;cursor:pointer}
table{border-collapse:collapse;background:#fff;width:100%}
th,td{padding:6px 8px;border-bottom:1px solid #f1f5f9;font-size:12px;text-align:left;white-space:nowrap;vertical-align:top}
th{background:#f8fafc;color:#334155;position:sticky;top:0}
th.num,td.num{text-align:right}
.muted{color:#6b7280;font-size:12px}
.warn{color:#b45309;font-size:13px}
.tag{display:inline-block;font-size:11px;background:#e5e7eb;padding:2px 6px;border-radius:999px;margin-left:4px}
.nav a{display:inline-block;margin-right:8px;padding:6px 10px;background:#e5e7eb;border-radius:8px;color:#111827;text-decoration:none}
a{color:#2563eb;text-decoration:none} a:hover{text-decoration:underline}
.cat-abs h1{color:#7c2d12} .cat-axis h1{color:#1d4ed8} .cat-keshi h1{color:#6b7280} .cat-opp h1{color:#047857}
</style></head><body><header>{{ title }}</header><div class="container">
<div class="nav"><a href="{{ url_for('index') }}">横断検索</a><a href="{{ url_for('entries_index') }}">出馬表</a><a href="{{ url_for('ingest.index') }}">指数の取得</a></div>
{% if db_warning %}<div class="card warn">{{ db_warning }}</div>{% endif %}
{BODY}
</div></body></html>"""


def render(title: str, body: str, **ctx):
    ctx.setdefault("db_warning", "")
    return render_template_string(LAYOUT.replace("{BODY}", body), title=title, **ctx)


app.config["MAX_CONTENT_LENGTH"] = 300 * 1024 * 1024     # /ingest のアップロード上限
app.register_blueprint(make_ingest_blueprint(render))


# ========================= 横断検索（/） =========================
INDEX_BODY = """
<div class="card"><h1>条件</h1>
<form method="get">
  <div class="grid">
    {% for name, label, opts, cur in selects %}
    <div><label>{{ label }}</label><select name="{{ name }}"><option value="">（すべて）</option>
      {% for v in opts %}<option value="{{ v }}" {{ 'selected' if v == cur else '' }}>{{ v }}</option>{% endfor %}
    </select></div>
    {% endfor %}
  </div>
  <div style="margin-top:10px"><label>集計パターン（family）</label><div class="cbs">
    {% for f in families %}<label class="cb"><input type="checkbox" name="family" value="{{ f }}" {{ 'checked' if f in q_families else '' }}>{{ family_label(f) }}</label>{% endfor %}
  </div></div>
  <div style="margin-top:10px"><label>指数</label><div class="cbs">
    {% for i in indicators %}<label class="cb"><input type="checkbox" name="ind" value="{{ i }}" {{ 'checked' if i in q_inds else '' }}>{{ i }}</label>{% endfor %}
  </div></div>
  <div style="margin-top:10px"><label>オッズ帯</label><div class="cbs">
    <label class="cb"><input type="radio" name="odds_mode" value="all" {{ 'checked' if q_odds == 'all' else '' }}>すべて</label>
    {% for m in odds_modes %}<label class="cb"><input type="radio" name="odds_mode" value="{{ m }}" {{ 'checked' if q_odds == m else '' }}>{{ odds_label(m) }}</label>{% endfor %}
  </div></div>
  <div style="margin-top:10px"><label>推奨プリセット（条件ヒット馬と同じ判定）</label><div class="cbs">
    {% for p in presets %}<label class="cb"><input type="radio" name="preset" value="{{ p }}" {{ 'checked' if q_preset == p else '' }}>{{ 'なし' if p == 'none' else CATEGORY_LABELS[p] }}</label>{% endfor %}
  </div></div>
  <div class="grid" style="margin-top:10px">
    <div><label>件数 下限</label><input name="min_cnt" value="{{ q_min.min_cnt }}" inputmode="decimal" placeholder="例 30"></div>
    <div><label>WEDGE 下限</label><input name="min_wedge" value="{{ q_min.min_wedge }}" inputmode="decimal" placeholder="例 1.0"></div>
    <div><label>adjusted_WEDGE 下限</label><input name="min_adj" value="{{ q_min.min_adj }}" inputmode="decimal" placeholder="例 0.8"></div>
    <div><label>並べ替え</label><select name="sort"><option value="" {{ 'selected' if not q_sort_arg else '' }}>（既定: 集計表ごと=複勝率_val／まとめて=adjusted_WEDGE）</option>{% for c in sortable %}<option value="{{ c }}" {{ 'selected' if c == q_sort_arg else '' }}>{{ c }}</option>{% endfor %}</select></div>
    <div><label>順序</label><select name="sortdir"><option value="DESC" {{ 'selected' if q_dir == 'DESC' else '' }}>大きい順</option><option value="ASC" {{ 'selected' if q_dir == 'ASC' else '' }}>小さい順</option></select></div>
  </div>
  <div style="margin-top:10px"><label>表示</label><div class="cbs">
    <label class="cb"><input type="radio" name="view" value="tables" {{ 'checked' if q_view == 'tables' else '' }}>集計表ごと</label>
    <label class="cb"><input type="radio" name="view" value="rank" {{ 'checked' if q_view == 'rank' else '' }}>まとめて（全集計表の横断ランキング・上位{{ rank_limit }}行）</label>
  </div></div>
  {% if bad_inputs %}<div class="warn" style="margin-top:8px">下限の値を数値として読めませんでした（無視しました）: {{ bad_inputs|join('、') }}</div>{% endif %}
  <div style="margin-top:12px"><button type="submit">検索</button>
    <span class="muted">WEDGE = WIN + EDGE（1.00以上=トップ級、0.62前後=平均、0.50未満=下位）。adjusted_WEDGE は年度加重（既定: 件数{{ wedge_min_year }}以上の年すべて・最大{{ wedge_max_years }}年、新しい年ほど重い）。空欄は条件を満たす年が無い</span></div>
  <input type="hidden" name="submitted" value="1">
</form></div>
{% if submitted %}
  {% if q_view == 'rank' %}
    <div class="card"><h2>横断ランキング（{{ q_sort }} {{ '大きい順' if q_dir == 'DESC' else '小さい順' }}、空欄は最後）</h2>
    <div class="muted">{{ rank_rows|length }} 行{% if rank_rows|length >= rank_limit %}（上位 {{ rank_limit }} 行まで）{% endif %}</div>
    <table><thead><tr><th>指数</th><th>パターン</th><th>オッズ帯</th><th>地域</th>
    {% for c in rank_columns %}{% if c in sortable %}<th><a href="{{ sort_links[c] }}">{{ c }}{% if c == q_sort %} {{ '▼' if q_dir == 'DESC' else '▲' }}{% endif %}</a></th>{% else %}<th>{{ c }}</th>{% endif %}{% endfor %}
    </tr></thead><tbody>
    {% for row in rank_rows %}<tr><td>{{ row.indicator }}</td><td>{{ family_label(row.family, row.rank_scheme) }}</td><td>{{ odds_label(row.odds_mode) }}</td><td>{{ row.region }}</td>
      {% for c in rank_columns %}<td{% if c != '結合データ' %} class="num"{% endif %}>{{ fmt_cell(c, row[c]) }}</td>{% endfor %}</tr>
    {% else %}<tr><td colspan="{{ rank_columns|length + 4 }}">該当なし</td></tr>{% endfor %}
    </tbody></table></div>
  {% else %}
  {% if truncated %}<div class="card warn">該当する集計表が {{ n_tables }} 個あります。先頭 {{ max_tables }} 個だけ表示しています。条件を絞るか「まとめて」表示を使ってください。</div>{% endif %}
  {% for r in results %}
    <div class="card"><h2>{{ family_label(r.family, r.rank_scheme) }}<span class="tag">{{ r.indicator }}</span><span class="tag">{{ odds_label(r.odds_mode) }}</span><span class="tag">{{ r.region }}</span></h2>
    <div class="muted">{{ r.table }}</div>
    <table><thead><tr>{% for c in r.columns %}
      {% if c in sortable %}<th><a href="{{ sort_links[c] }}">{{ c }}{% if c == q_sort %} {{ '▼' if q_dir == 'DESC' else '▲' }}{% endif %}</a></th>{% else %}<th>{{ c }}</th>{% endif %}
    {% endfor %}</tr></thead><tbody>
      {% for row in r.rows %}<tr>{% for c in r.columns %}<td>{{ fmt_cell(c, row[c]) }}</td>{% endfor %}</tr>{% endfor %}
    </tbody></table></div>
  {% else %}<div class="card">該当する行はありませんでした。</div>{% endfor %}
  {% endif %}
{% else %}<div class="card muted">条件を選んで「検索」を押してください（初期表示では一覧を描画しません）。</div>{% endif %}
"""


def _facets(con) -> dict[str, list[str]]:
    out = {c: [] for c in ("場所", "芝・ダ", "距離", "芝(内・外)")}
    if table_exists(con, FACETS):
        for r in con.execute(f"SELECT col, val FROM {FACETS} ORDER BY col, val"):
            out.setdefault(r[0], []).append(r[1])
    return out


def _meta_values(con, col: str) -> list[str]:
    if not table_exists(con, META) or col not in table_columns(con, META) or "kind" not in table_columns(con, META):
        return []
    return [r[0] for r in con.execute(
        f"SELECT DISTINCT {q(col)} FROM {META} WHERE kind='foragg' AND {q(col)} IS NOT NULL AND {q(col)} <> ''")]


def _num_arg(name: str) -> tuple[float | None, str, bool]:
    """下限の入力値 → (数値, 入力欄に戻す文字列, 読めなかったか)。全角・カンマ区切りも受ける。"""
    raw = request.args.get(name, "")
    v = unicodedata.normalize("NFKC", raw).strip().replace(",", "")
    if not v:
        return None, "", False
    try:
        f = float(v)
    except ValueError:
        return None, raw.strip(), True
    if f != f or f in (float("inf"), float("-inf")):
        return None, raw.strip(), True
    return f, v, False


def _course_filters(place, sd, dist, inout) -> tuple[list[str], list]:
    w, p = [], []
    for col, val in (("場所", place), ("芝・ダ", sd), ("距離", dist), ("芝(内・外)", inout)):
        if val:
            w.append(f"{q(col)} = ?")
            p.append(val)
    return w, p


def _min_filters(mins: dict) -> tuple[list[str], list, list[str]]:
    """下限フィルタの WHERE 句・値・必要な列。"""
    w, p, cols = [], [], []
    for name, col in MIN_FILTERS:
        if mins.get(name) is not None:
            w.append(f"{q(col)} >= ?")
            p.append(mins[name])
            cols.append(col)
    return w, p, cols


@app.route("/", methods=["GET"])
def index():
    a = request.args
    submitted = a.get("submitted") == "1"
    place, sd, dist, inout, region = (a.get(k, "").strip() for k in ("place", "sd", "dist", "inout", "region"))
    q_families = [f for f in a.getlist("family") if f]
    q_inds = [i for i in a.getlist("ind") if i]
    q_odds = a.get("odds_mode", "all")
    if q_odds not in ("all",) + ODDS_MODES:
        q_odds = "all"
    q_preset = a.get("preset", "none")
    if q_preset not in PRESETS:
        q_preset = "none"
    q_view = a.get("view", "tables")
    if q_view not in VIEWS:
        q_view = "tables"
    default_sort = "adjusted_WEDGE" if q_view == "rank" else "複勝率_val"
    q_sort_arg = a.get("sort", "")
    if q_sort_arg not in SORTABLE:
        q_sort_arg = ""
    q_sort = q_sort_arg or default_sort
    q_dir = "ASC" if a.get("sortdir", "DESC").upper() == "ASC" else "DESC"
    parsed = {name: _num_arg(name) for name, _ in MIN_FILTERS}
    mins = {name: v[0] for name, v in parsed.items()}
    q_min = {name: v[1] for name, v in parsed.items()}
    labels = {"min_cnt": "件数", "min_wedge": "WEDGE", "min_adj": "adjusted_WEDGE"}
    bad_inputs = [f"{labels[n]}「{v[1]}」" for n, v in parsed.items() if v[2]]

    with connect() as con:
        status = db_status(con)
        has_db = status == "ok"
        facets = _facets(con)
        present = set(_meta_values(con, "family"))
        families = [f for f in fam_registry.active_names() if f in present] + sorted(present - set(fam_registry.active_names()))
        inds_all = set(_meta_values(con, "indicator"))
        indicators = [i for i in INDICATOR_ORDER if i in inds_all] + sorted(inds_all - set(INDICATOR_ORDER))
        regions = sorted(_meta_values(con, "region"))
        selects = [("place", "場所", facets.get("場所", []), place), ("sd", "芝・ダ", facets.get("芝・ダ", []), sd),
                   ("dist", "距離", facets.get("距離", []), dist), ("inout", "芝(内・外)", facets.get("芝(内・外)", []), inout),
                   ("region", "地域（元ファイル）", regions, region)]
        results, rank_rows, n_tables, truncated = [], [], 0, False
        if submitted and has_db and q_view == "rank":
            rank_rows = _rank_query(con, q_families, q_inds, q_odds, region, (place, sd, dist, inout),
                                    q_preset, mins, q_sort, q_dir)
        elif submitted and has_db:
            unified_cols = set(table_columns(con, UNIFIED)) - set(TABLE_META_COLUMNS)
            where, params = ["kind='foragg'"], []
            if q_families:
                where.append(f"family IN ({','.join('?' * len(q_families))})")
                params += q_families
            if q_inds:
                where.append(f"indicator IN ({','.join('?' * len(q_inds))})")
                params += q_inds
            if q_odds != "all":
                where.append("odds_mode = ?")
                params.append(q_odds)
            if region:
                where.append("region = ?")
                params.append(region)
            metas = [dict(r) for r in con.execute(
                f"SELECT table_name, family, rank_scheme, indicator, odds_mode, region FROM {META} "
                f"WHERE {' AND '.join(where)}", params)]
            ind_rank = {n: i for i, n in enumerate(INDICATOR_ORDER)}
            fam_rank = {f: i for i, f in enumerate(fam_registry.active_names())}
            metas.sort(key=lambda m: (ind_rank.get(m["indicator"], 999), m["indicator"] or "",
                                      fam_rank.get(m["family"], 999), m["rank_scheme"] or "",
                                      ODDS_RANK.get(m["odds_mode"], 9), m["region"] or ""))
            n_tables = len(metas)
            truncated = n_tables > MAX_TABLES
            mw, mp, mcols = _min_filters(mins)
            for m in metas[:MAX_TABLES]:
                t = m["table_name"]
                # 集計表の中身は unified_all の同じテーブル名の行（並びは同じ列で並べ、同点は成績表の行の順＝rowid）
                cols_in = unified_cols
                if not set(mcols) <= cols_in:      # 下限を指定した列が無い表（古い集計）は出さない
                    continue
                show = [c for c in DISPLAY_ORDER if c in cols_in]
                w, p = ['"テーブル名" = ?'], [t]
                cw, cp = _course_filters(place, sd, dist, inout)
                w += cw
                p += cp
                if q_preset != "none":
                    w.append(sql_condition(q_preset, m["odds_mode"]))
                w += mw
                p += mp
                # 並べ替えに WEDGE などの索引を使わせない（テーブル名の索引で絞ってから並べる）
                order = f"ORDER BY +{q(q_sort)} {q_dir} NULLS LAST, rowid " if q_sort in cols_in else "ORDER BY rowid "
                sql = (f"SELECT {', '.join(q(c) for c in show)} FROM {UNIFIED} "
                       f"WHERE {' AND '.join(w)} {order}LIMIT {ROWS_PER_TABLE}")
                rows = [dict(r) for r in con.execute(sql, p)]
                if not rows:
                    continue
                results.append({"table": t, "family": m["family"], "rank_scheme": m["rank_scheme"],
                                "indicator": m["indicator"], "odds_mode": m["odds_mode"], "region": m["region"],
                                "columns": show, "rows": rows})
        sort_links = {c: _sort_link(c, q_sort, q_dir) for c in SORTABLE}

    return render("指数集計 横断検索", INDEX_BODY, selects=selects, families=families, indicators=indicators,
                  odds_modes=ODDS_MODES, presets=PRESETS, q_families=q_families, q_inds=q_inds,
                  q_odds=q_odds, q_preset=q_preset, q_sort=q_sort, q_sort_arg=q_sort_arg, q_dir=q_dir,
                  q_view=q_view, q_min=q_min, bad_inputs=bad_inputs,
                  submitted=submitted, results=results, rank_rows=rank_rows, rank_columns=rank_columns(q_sort),
                  rank_limit=RANK_LIMIT, sortable=SORTABLE, sort_links=sort_links, n_tables=n_tables,
                  truncated=truncated, max_tables=MAX_TABLES, wedge_min_year=DEFAULT_MIN_YEAR_COUNT, wedge_max_years=DEFAULT_MAX_YEARS,
                  db_warning=DB_WARNINGS.get(status, ""))


def rank_columns(sort: str) -> tuple[str, ...]:
    """横断ランキングの表示列。並べ替えに使った列が表示列に無ければ末尾に足す。"""
    return RANK_COLUMNS if sort in RANK_COLUMNS else RANK_COLUMNS + (sort,)


def _rank_query(con, families, inds, odds_mode, region, course, preset, mins, sort, sortdir) -> list[dict]:
    """unified_all（全集計表の縦結合）を条件で絞り、sort 列で並べた上位 RANK_LIMIT 行。

    WEDGE・adjusted_WEDGE には並べ替え用の索引がある。絞り込みが無い（か、並べ替え列の下限だけの）ときは
    索引を順にたどれば速いが、他の条件で絞ると索引を最後までたどって遅くなる（550万行で約14秒）。
    その場合は `ORDER BY +列` で索引を使わせず、全件を読んで並べる（同約1秒）。
    """
    where, params = _course_filters(*course)
    if families:
        where.append(f"family IN ({','.join('?' * len(families))})")
        params += families
    if inds:
        where.append(f"indicator IN ({','.join('?' * len(inds))})")
        params += inds
    if odds_mode != "all":
        where.append("odds_mode = ?")
        params.append(odds_mode)
    if region:
        where.append("region = ?")
        params.append(region)
    if preset != "none":   # 判定はオッズ帯で変わる（消し候補の10倍未満ルール）ので帯ごとに条件を付ける
        where.append("(" + " OR ".join(f"(odds_mode = '{m}' AND {sql_condition(preset, m)})" for m in ODDS_MODES) + ")")
    only_sort_bound = not where and all(col == sort for name, col in MIN_FILTERS if mins.get(name) is not None)
    mw, mp, _ = _min_filters(mins)
    where += mw
    params += mp
    cols = ", ".join(q(c) for c in ("indicator", "family", "rank_scheme", "odds_mode", "region", "テーブル名")
                     + rank_columns(sort))
    order_col = q(sort) if only_sort_bound else "+" + q(sort)
    sql = (f"SELECT {cols} FROM {UNIFIED} {'WHERE ' + ' AND '.join(where) if where else ''} "
           f"ORDER BY {order_col} {sortdir} NULLS LAST LIMIT {RANK_LIMIT}")
    return [dict(r) for r in con.execute(sql, params)]


QUERY_KEYS = ("place", "sd", "dist", "inout", "region", "family", "ind", "odds_mode", "preset", "view",
              "min_cnt", "min_wedge", "min_adj")


def _sort_link(col: str, cur: str, cur_dir: str) -> str:
    args = {k: request.args.getlist(k) for k in QUERY_KEYS if k in request.args}
    args["sort"] = [col]
    args["sortdir"] = ["ASC" if (col == cur and cur_dir == "DESC") else "DESC"]
    args["submitted"] = ["1"]
    return url_for("index", **args)


# ========================= 出馬表 =========================
def entries_tables(con) -> list[str]:
    if table_exists(con, META) and "kind" in table_columns(con, META):
        names = [r[0] for r in con.execute(f"SELECT table_name FROM {META} WHERE kind='entries' ORDER BY table_name")]
        names = [n for n in names if table_exists(con, n)]
        if names:
            return names
    return [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'entries\\_%' ESCAPE '\\' ORDER BY name")
        if not r[0].startswith(XJOIN_PREFIX) and r[0] != ENTRIES_ALL]


def _valid_entries_table(con, table: str) -> bool:
    return table in entries_tables(con)


ENTRIES_BODY = """
<div class="card"><h1>出馬表を選択</h1>
<form method="get"><div class="grid">
  <div><label>出馬表テーブル</label><select name="table" onchange="this.form.submit()">
    {% for t in tables %}<option value="{{ t }}" {{ 'selected' if t == q_table else '' }}>{{ t }}</option>{% endfor %}</select></div>
  <div><label>日付</label><select name="date" onchange="this.form.submit()">
    {% for d in dates %}<option value="{{ d }}" {{ 'selected' if d == q_date else '' }}>{{ d }}</option>{% endfor %}</select></div>
  <div><label>場所</label><select name="place" onchange="this.form.submit()"><option value="">（すべて）</option>
    {% for p in places %}<option value="{{ p }}" {{ 'selected' if p == q_place else '' }}>{{ p }}</option>{% endfor %}</select></div>
</div></form></div>
{% if races %}
<div class="card"><h1>レース一覧</h1><table>
<thead><tr><th>日付</th><th>場 R</th><th>レース名</th><th>クラス</th><th>芝・ダ</th><th>距離</th><th>頭数</th><th>出馬表</th><th>条件ヒット馬</th><th>WEDGE一覧</th></tr></thead>
<tbody>{% for r in races %}<tr>
  <td>{{ r.date }}</td><td>{{ r.jo }}</td><td>{{ r.name }}</td><td>{{ r.cls }}</td><td>{{ r.sd }}</td><td>{{ r.dist }}</td><td>{{ r.heads }}</td>
  <td><a href="{{ url_for('entries_race', table=q_table, rid=r.rid) }}">出馬表</a></td>
  <td><a href="{{ url_for('entries_hits', table=q_table, rid=r.rid) }}" target="_blank">条件ヒット馬</a></td>
  <td><a href="{{ url_for('entries_wedge', table=q_table, rid=r.rid) }}" target="_blank">WEDGE一覧</a></td>
</tr>{% endfor %}</tbody></table></div>
{% else %}<div class="card muted">レースがありません。</div>{% endif %}
"""


@app.route("/entries", methods=["GET"])
def entries_index():
    with connect() as con:
        tables = entries_tables(con)
        q_table = request.args.get("table", "").strip()
        if q_table not in tables:
            q_table = tables[0] if tables else ""
        dates, places, races, q_date, q_place = [], [], [], "", request.args.get("place", "").strip()
        if q_table:
            dates = [r[0] for r in con.execute(
                f'SELECT DISTINCT "日付" FROM {q(q_table)} WHERE "日付" <> \'\' ORDER BY "日付" DESC')]
            q_date = request.args.get("date", "").strip()
            if q_date not in dates:
                q_date = dates[0] if dates else ""
            cond, params = [], []
            if q_date:
                cond.append('"日付" = ?')
                params.append(q_date)
            places = [r[0] for r in con.execute(
                f'SELECT DISTINCT "場所" FROM {q(q_table)} {"WHERE " + " AND ".join(cond) if cond else ""} ORDER BY 1', params)]
            if q_place:
                cond.append('"場所" = ?')
                params.append(q_place)
            races = [dict(r) for r in con.execute(f"""
                SELECT "レースID(新/馬番無)" AS rid, MIN("日付") AS date, MIN("場 R") AS jo, MIN("レース名") AS name,
                       MIN("クラス名_raw") AS cls, MIN("芝・ダ") AS sd, MIN("距離_raw") AS dist, COUNT(*) AS heads
                FROM {q(q_table)} {"WHERE " + " AND ".join(cond) if cond else ""}
                GROUP BY "レースID(新/馬番無)" ORDER BY "レースID(新/馬番無)" """, params)]
        warning = DB_WARNINGS.get(db_status(con), "")
    return render("出馬表", ENTRIES_BODY, tables=tables, q_table=q_table, dates=dates, q_date=q_date,
                  places=places, q_place=q_place, races=races, db_warning=warning)


def _race_rows(con, table: str, rid: str, legacy_ridn: str = "", date: str = "") -> list[dict]:
    rows = []
    if rid:
        rows = [dict(r) for r in con.execute(
            f'SELECT * FROM {q(table)} WHERE "レースID(新/馬番無)" = ? ORDER BY CAST("馬番" AS INTEGER)', (rid,))]
    if rows or not legacy_ridn:
        return rows
    # 旧リンク（ridn=場 R）互換
    cond, params = ['REPLACE(REPLACE("場 R", \' \', \'\'), \'　\', \'\') = ?'], [legacy_ridn.replace(" ", "").replace("　", "")]
    if date:
        cond.append('"日付" = ?')
        params.append(date)
    return [dict(r) for r in con.execute(
        f'SELECT * FROM {q(table)} WHERE {" AND ".join(cond)} ORDER BY CAST("馬番" AS INTEGER)', params)]


RACE_BODY = """
<div class="card"><h1>{{ head }}</h1><div class="muted">{{ sub }}</div>
<div style="margin-top:6px"><a href="{{ url_for('entries_hits', table=table, rid=rid) }}" target="_blank">条件ヒット馬を見る</a>
 ／ <a href="{{ url_for('entries_wedge', table=table, rid=rid) }}" target="_blank">WEDGE一覧を見る</a></div></div>
<div class="card"><table><thead><tr><th>枠番</th><th>馬番</th><th>騎手</th><th>馬名</th>
{% for b in bases %}<th>{{ b }}<div class="muted">順位 / 偏差値 / 帯</div></th>{% endfor %}<th>結合データ（先頭部分）</th></tr></thead>
<tbody>{% for r in rows %}<tr><td>{{ r['枠番'] }}</td><td>{{ r['馬番'] }}</td><td>{{ r['騎手'] }}</td><td>{{ r['馬名'] }}</td>
{% for b in bases %}<td>{{ r.get(b ~ '_順位') or '' }}{% if r.get(b ~ '_順位') %}位{% endif %} / {{ r.get(b ~ '_偏差値') or '' }} / {{ r.get(b ~ '_偏差値帯') or '' }}</td>{% endfor %}
<td>{% if head_key %}<a href="{{ url_for('entries_key', key=head_key) }}" target="_blank">{{ head_key }}</a>{% endif %}</td></tr>{% endfor %}</tbody></table></div>
"""


def _head_key(r: dict) -> str:
    parts = [r.get("場所") or "", r.get("芝・ダ") or "", r.get("距離") or ""]
    if not all(parts):
        return ""
    if r.get("芝(内・外)"):
        parts.append(r["芝(内・外)"])
    return "×".join(parts)


@app.route("/entries/race", methods=["GET"])
def entries_race():
    table = request.args.get("table", "").strip()
    rid = request.args.get("rid", "").strip()
    legacy = request.args.get("ridn", "").strip()
    with connect() as con:
        if not _valid_entries_table(con, table) or not (rid or legacy):
            return redirect(url_for("entries_index"))
        rows = _race_rows(con, table, rid, legacy, request.args.get("date", "").strip())
        bases = indicator_bases(table_columns(con, table))
    if not rows:
        return render("出馬表", '<div class="card">該当レースがありません。</div>')
    h = rows[0]
    head = f'{h.get("日付", "")} {h.get("場 R", "")} {h.get("レース名", "")}'
    sub = (f'{h.get("場所", "")} / {h.get("芝・ダ", "")} / {h.get("距離_raw", "")}（キー: {h.get("距離", "")}） / '
           f'内外: {h.get("芝(内・外)", "") or "—"} / 重量: {h.get("重量種別", "") or "—"} / クラス: {h.get("クラス名", "")}')
    return render("出馬表", RACE_BODY, head=head, sub=sub, rows=rows, bases=bases, table=table,
                  rid=h.get("レースID(新/馬番無)", rid), head_key=_head_key(h))


KEY_BODY = """
<div class="card"><h1>結合データ: {{ key }}…</h1><div class="muted">前方一致・件数の多い順に最大 {{ limit }} 行</div></div>
<div class="card"><table><thead><tr><th>指数</th><th>パターン</th><th>オッズ帯</th><th>地域</th><th>結合データ</th><th>件数</th>
<th>勝率_val</th><th>連対率_val</th><th>複勝率_val</th><th>掲示板率_val</th><th>平均着順</th><th>単勝回収値</th><th>複勝回収値</th><th>単勝適正回収値</th>
<th>WEDGE</th><th>adjusted_WEDGE</th><th>年数</th></tr></thead>
<tbody>{% for r in rows %}<tr><td>{{ r.indicator }}</td><td>{{ family_label(r.family, r.rank_scheme) }}</td><td>{{ odds_label(r.odds_mode) }}</td><td>{{ r.region }}</td>
<td>{{ r['結合データ'] }}</td><td class="num">{{ r['件数'] }}</td>
{% for c in ['勝率_val','連対率_val','複勝率_val','掲示板率_val','平均着順','単勝回収値','複勝回収値','単勝適正回収値','WEDGE','adjusted_WEDGE','adjusted_WEDGE_年数'] %}<td class="num">{{ fmt_cell(c, r[c]) }}</td>{% endfor %}</tr>
{% else %}<tr><td colspan="17">該当なし</td></tr>{% endfor %}</tbody></table></div>
"""


@app.route("/entries/key", methods=["GET"])
def entries_key():
    key = request.args.get("key", "").strip()
    if not key:
        return redirect(url_for("entries_index"))
    limit = 200
    with connect() as con:
        if not table_exists(con, UNIFIED):
            return render("結合データ", '<div class="card warn">unified_all がありません。</div>')
        lo, hi = key + "×", key + "×\U0010ffff"
        rows = [dict(r) for r in con.execute(
            f'SELECT * FROM {UNIFIED} WHERE "結合データ" = ? OR ("結合データ" >= ? AND "結合データ" < ?) '
            f'ORDER BY "件数" DESC LIMIT ?', (key, lo, hi, limit))]
    return render("結合データ", KEY_BODY, key=key, rows=rows, limit=limit)


# ========================= 条件ヒット馬 =========================
def collect_hits(con, table: str, race_rows: list[dict]) -> tuple[list[dict], str]:
    """1レース分のヒット行と、使った経路（'xjoin' / 'on-the-fly'）。"""
    if not race_rows:
        return [], ""
    rid = race_rows[0].get("レースID(新/馬番無)", "")
    status = unified_status(con)
    if status != "ok":
        return [], f"unified_all {'が旧形式' if status == 'old' else 'なし'}（build_db.py --fresh で作り直してください）"
    if not rid:
        return [], "レースIDなし"
    xname = xjoin_table_for(table)
    note = "on-the-fly"
    if table_exists(con, xname):
        if set(HIT_COLUMNS) <= set(table_columns(con, xname)):
            hits = [dict(r) for r in con.execute(f'SELECT * FROM {q(xname)} WHERE "レースID(新/馬番無)" = ?', (rid,))]
            return hits, "xjoin"
        note = "on-the-fly（照合結果 xjoin が古い形式のため。build_db.py --mode entries で作り直してください）"
    bases = indicator_bases(race_rows[0].keys())
    cache: dict = {}
    hits: list[dict] = []
    for r in race_rows:
        hits.extend(match_entry_row(con, r, bases, table, cache=cache))
    return hits, note


def hit_category(h: dict) -> str | None:
    """ヒット行の区分（abs / axis / keshi / opp / None）。classify と同じ判定。"""
    return classify({"cnt": h.get("件数"), "fv": h.get("複勝率_val"), "rv": h.get("連対率_val"),
                     "top5": h.get("掲示板率_val"), "frc": h.get("複勝回収値"),
                     "tra": h.get("単勝適正回収値")}, h.get("odds_mode") or "normal")


def classify_hits(hits: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    seen: set = set()
    for h in hits:
        ident = (h.get("馬番"), h.get("base"), h.get("family"), h.get("key"), h.get("テーブル名"))
        if ident in seen:
            continue
        seen.add(ident)
        cat = hit_category(h)
        if cat:
            buckets[cat].append(h)

    def fv(h):
        v = h.get("複勝率_val")
        return float(v) if v is not None else 0.0

    for cat, rows in buckets.items():
        sign = 1 if cat == "keshi" else -1
        rows.sort(key=lambda h: (ODDS_RANK.get(h.get("odds_mode") or "normal", 9), to_int(h.get("馬番")), sign * fv(h)))
    return buckets


HITS_BODY = """
<div class="card"><h1>{{ head }}</h1><div class="muted">{{ sub }}</div>
<div class="muted">照合: {{ path }} ／ 率は DB の推定値（複勝率・連対率）と頻度（掲示板率）</div>
<div style="margin-top:6px"><a href="{{ url_for('entries_wedge', table=table, rid=rid) }}" target="_blank">このレースの WEDGE 一覧（閾値なし・全キー）</a></div></div>
<div class="card"><h1>馬ごとの該当数</h1><table><thead><tr><th>馬番</th><th>馬名</th>
{% for c in order %}<th class="num">{{ CATEGORY_LABELS[c] }}</th>{% endfor %}</tr></thead><tbody>
{% for s in summary %}<tr><td>{{ s.uma }}</td><td>{{ s.name }}</td>{% for c in order %}<td class="num">{{ s[c] or '' }}</td>{% endfor %}</tr>{% endfor %}
</tbody></table></div>
{% for c in order %}
<div class="card cat-{{ c }}"><h1>{{ CATEGORY_LABELS[c] }}（{{ buckets[c]|length }}）</h1>
<div class="muted">{% for line in desc[c] %}• {{ line }}<br>{% endfor %}</div>
{% if view[c]|length < buckets[c]|length %}<div class="warn">{{ view[c]|length }} 行だけ表示しています（全 {{ buckets[c]|length }} 行。馬・オッズ帯ごとに偏らないように選んでいます）。全件は <a href="{{ url_for('entries_wedge_csv', table=table, rid=rid, odds_mode='all') }}">WEDGE 一覧の CSV</a>（「区分」列で絞れます）</div>{% endif %}
<table><thead><tr><th>枠</th><th>馬番</th><th>馬名</th><th>騎手</th><th>指数</th><th>パターン</th><th>オッズ帯</th><th>地域</th><th>結合データ</th>
<th>件数</th><th>勝率_val</th><th>連対率_val</th><th>複勝率_val</th><th>掲示板率_val</th><th>勝率</th><th>連対率</th><th>複勝率</th>
<th>平均着順</th><th>単勝回収値</th><th>複勝回収値</th><th>単勝適正回収値</th><th>WEDGE</th><th>adjusted_WEDGE</th><th>年数</th></tr></thead><tbody>
{% for h in view[c] %}<tr><td>{{ h['枠番'] }}</td><td>{{ h['馬番'] }}</td><td>{{ h['馬名'] }}</td><td>{{ h['騎手'] }}</td>
<td>{{ h['base'] }}</td><td>{{ family_label(h['family'], h['rank_scheme']) }}</td><td>{{ odds_label(h['odds_mode']) }}</td><td>{{ h['region'] }}</td><td>{{ h['key'] }}</td>
<td class="num">{{ h['件数'] }}</td>{% for k in ['勝率_val','連対率_val','複勝率_val','掲示板率_val'] %}<td class="num">{{ fmt(h[k]) }}</td>{% endfor %}
<td>{{ h['勝率'] }}</td><td>{{ h['連対率'] }}</td><td>{{ h['複勝率'] }}</td>
{% for k in ['平均着順','単勝回収値','複勝回収値','単勝適正回収値','WEDGE','adjusted_WEDGE','adjusted_WEDGE_年数'] %}<td class="num">{{ fmt_cell(k, h.get(k)) }}</td>{% endfor %}</tr>
{% else %}<tr><td colspan="24">該当なし</td></tr>{% endfor %}</tbody></table></div>
{% endfor %}
"""
HITS_ORDER = ("abs", "axis", "keshi", "opp")
HITS_VIEW_LIMIT = 500        # 区分ごとに画面に出すおよその行数（全件は /entries/wedge.csv。馬ごとの該当数は全件で数える）


def spread_cap(rows: list[dict], limit: int) -> list[dict]:
    """表示を limit 行ほどに絞る。先頭から切ると後ろの馬・オッズ帯が丸ごと消えるので、
    (馬番, オッズ帯) ごとの行数の上限 c をそろえる（行の少ない組は全部出し、余った枠は多い組に回す。並びは元のまま）。"""
    if len(rows) <= limit:
        return rows
    groups: dict[tuple, int] = {}
    for h in rows:
        g = (h.get("馬番"), h.get("odds_mode"))
        groups[g] = groups.get(g, 0) + 1
    sizes = sorted(groups.values())
    per, used_rows = 1, 0           # 合計が limit に収まる最大の c（どの組も最低1行は出す）
    for i, n in enumerate(sizes):
        rest = len(sizes) - i
        c = (limit - used_rows) // rest
        if c < n:
            per = max(per, c)
            break
        used_rows += n
        per = n
    else:
        per = sizes[-1]
    used: dict[tuple, int] = {}
    out = []
    for h in rows:
        g = (h.get("馬番"), h.get("odds_mode"))
        if used.get(g, 0) < per:
            used[g] = used.get(g, 0) + 1
            out.append(h)
    return out


@app.route("/entries/hits", methods=["GET"])
def entries_hits():
    table = request.args.get("table", "").strip()
    rid = request.args.get("rid", "").strip()
    legacy = request.args.get("ridn", "").strip()
    with connect() as con:
        if not _valid_entries_table(con, table) or not (rid or legacy):
            return redirect(url_for("entries_index"))
        race = _race_rows(con, table, rid, legacy, request.args.get("date", "").strip())
        hits, path = collect_hits(con, table, race)
    if not race:
        return render("条件ヒット馬", '<div class="card">該当レースがありません。</div>')
    buckets = classify_hits(hits)
    h = race[0]
    summary = []
    for r in race:
        uma = r.get("馬番", "")
        s = {"uma": uma, "name": r.get("馬名", "")}
        for c in HITS_ORDER:
            s[c] = sum(1 for x in buckets[c] if x.get("馬番") == uma)
        summary.append(s)
    head = f'{h.get("日付", "")} {h.get("場 R", "")} {h.get("レース名", "")}'
    sub = f'{h.get("場所", "")} / {h.get("芝・ダ", "")} / {h.get("距離_raw", "")} / {h.get("クラス名_raw", "")} / {len(race)}頭'
    return render("条件ヒット馬", HITS_BODY, head=head, sub=sub, path=path, buckets=buckets,
                  order=HITS_ORDER, desc=description_lines(), summary=summary, table=table,
                  view={c: spread_cap(buckets[c], HITS_VIEW_LIMIT) for c in HITS_ORDER},
                  rid=h.get("レースID(新/馬番無)", rid))


# ========================= レース別 WEDGE 一覧 =========================
WEDGE_SORTS = ("adjusted_WEDGE", "WEDGE", "WIN", "EDGE", "件数", "adjusted_WEDGE_件数", "馬番")
WEDGE_VIEW_LIMIT = 3000        # 画面に出す行数の上限（CSV ダウンロードは全行）
WEDGE_QUERY_KEYS = ("table", "rid", "ridn", "date", "odds_mode", "ind", "family", "uma", "sort", "dir")
WEDGE_CSV_COLUMNS = ("馬番", "枠番", "馬名", "騎手", "区分", "base", "family", "rank_scheme", "odds_mode", "region", "key",
                     "テーブル名", "件数", "WIN", "EDGE", "WEDGE", "adjusted_WEDGE", "adjusted_WEDGE_年数",
                     "adjusted_WEDGE_件数", "adjusted_WEDGE_最終年", "勝率_val", "連対率_val", "複勝率_val",
                     "掲示板率_val", "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値")


def _num(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if f != f else f


def dedupe_hits(hits: list[dict]) -> list[dict]:
    """同じ馬・指数・family・キー・集計表の重複を除く（classify_hits と同じ同一性）。"""
    out, seen = [], set()
    for h in hits:
        ident = (h.get("馬番"), h.get("base"), h.get("family"), h.get("key"), h.get("テーブル名"))
        if ident not in seen:
            seen.add(ident)
            out.append(h)
    return out


def filter_wedge_hits(hits: list[dict], odds_mode: str = "normal", inds=(), fams=(), uma: str = "") -> list[dict]:
    """画面の選択（オッズ帯・指数・パターン・馬番）だけで絞る。WEDGE の値による足切りはしない。"""
    out = []
    for h in dedupe_hits(hits):
        if odds_mode != "all" and (h.get("odds_mode") or "normal") != odds_mode:
            continue
        if inds and h.get("base") not in inds:
            continue
        if fams and h.get("family") not in fams:
            continue
        if uma and str(h.get("馬番")) != uma:
            continue
        out.append(h)
    return out


def sort_wedge_hits(hits: list[dict], sort: str = "adjusted_WEDGE", desc: bool = True) -> list[dict]:
    """並べ替え。空欄は常に最後。同値は WEDGE → 馬番 → 指数・パターン・キー・集計表の順（照合経路によらず同じ並び）。"""
    def tail(h):
        return (to_int(h.get("馬番")), str(h.get("base") or ""), str(h.get("family") or ""),
                str(h.get("key") or ""), str(h.get("テーブル名") or ""))

    if sort == "馬番":                     # 馬番の若い順に並べ、馬の中は adjusted_WEDGE の大きい順（順序の指定は使わない）
        def key(h):
            a = _num(h.get("adjusted_WEDGE"))
            return (to_int(h.get("馬番")), a is None, -(a or 0)) + tail(h)
        return sorted(hits, key=key)

    def key(h):
        v = _num(h.get(sort))
        w = _num(h.get("WEDGE"))
        return (v is None, -(v or 0) if desc else (v or 0), w is None, -(w or 0)) + tail(h)
    return sorted(hits, key=key)


def wedge_matrix(race: list[dict], hits: list[dict], bases: list[str]) -> list[dict]:
    """馬 × 指数の要約（その馬・指数で該当したキーの adjusted_WEDGE / WEDGE の最大と該当数）。"""
    by: dict[tuple, list[dict]] = {}
    for h in hits:
        by.setdefault((str(h.get("馬番")), h.get("base")), []).append(h)
    rows = []
    for r in race:
        uma = str(r.get("馬番", ""))
        cells, all_hits = {}, []
        for b in bases:
            hs = by.get((uma, b), [])
            all_hits.extend(hs)
            adj = [x for x in (_num(h.get("adjusted_WEDGE")) for h in hs) if x is not None]
            wed = [x for x in (_num(h.get("WEDGE")) for h in hs) if x is not None]
            cells[b] = {"n": len(hs), "adj": max(adj) if adj else None, "wedge": max(wed) if wed else None}
        adj = [x for x in (_num(h.get("adjusted_WEDGE")) for h in all_hits) if x is not None]
        wed = [x for x in (_num(h.get("WEDGE")) for h in all_hits) if x is not None]
        rows.append({"uma": uma, "waku": r.get("枠番", ""), "name": r.get("馬名", ""), "jockey": r.get("騎手", ""),
                     "cells": cells, "n": len(all_hits),
                     "adj_max": max(adj) if adj else None, "adj_mean": sum(adj) / len(adj) if adj else None,
                     "wedge_max": max(wed) if wed else None, "wedge_mean": sum(wed) / len(wed) if wed else None})
    return rows


def wedge_class(v) -> str:
    v = _num(v)
    if v is None:
        return ""
    return "w-top" if v >= 1.0 else "w-good" if v >= 0.8 else "w-avg" if v >= 0.62 else "w-mid" if v >= 0.5 else "w-low"


app.jinja_env.globals.update(wedge_class=wedge_class)

WEDGE_BODY = """
<style>.w-top{background:#bbf7d0}.w-good{background:#dcfce7}.w-avg{background:#f0fdf4}.w-low{background:#f3f4f6;color:#6b7280}
td.cell{text-align:right;min-width:64px} td.cell .sub{color:#6b7280;font-size:11px}</style>
<div class="card"><h1>{{ head }}</h1><div class="muted">{{ sub }}</div>
<div class="muted">照合: {{ path }} ／ 閾値による足切りなし（オッズ帯・指数・パターン・馬番の選択だけで絞る）。
WEDGE = WIN + EDGE（1.00以上=トップ級、0.62前後=平均、0.50未満=下位）。adjusted_WEDGE は年度加重（空欄=条件を満たす年が無い）</div>
<div style="margin-top:6px"><a href="{{ url_for('entries_race', table=table, rid=rid) }}">出馬表</a> ／
<a href="{{ url_for('entries_hits', table=table, rid=rid) }}">条件ヒット馬</a> ／
<a href="{{ csv_url }}">この条件の全行を CSV でダウンロード</a></div></div>
<div class="card"><form method="get"><input type="hidden" name="table" value="{{ table }}"><input type="hidden" name="rid" value="{{ rid }}">
<div class="grid">
  <div><label>オッズ帯（過去成績の区分）</label><select name="odds_mode">
    <option value="all" {{ 'selected' if q_odds == 'all' else '' }}>すべて</option>
    {% for m in odds_modes %}<option value="{{ m }}" {{ 'selected' if m == q_odds else '' }}>{{ odds_label(m) }}</option>{% endfor %}</select></div>
  <div><label>馬番</label><select name="uma"><option value="">（全馬）</option>
    {% for r in race %}<option value="{{ r['馬番'] }}" {{ 'selected' if r['馬番']|string == q_uma else '' }}>{{ r['馬番'] }} {{ r['馬名'] }}</option>{% endfor %}</select></div>
  <div><label>並べ替え</label><select name="sort">{% for c in sorts %}<option value="{{ c }}" {{ 'selected' if c == q_sort else '' }}>{{ c }}</option>{% endfor %}</select></div>
  <div><label>順序</label><select name="dir"><option value="DESC" {{ 'selected' if q_dir == 'DESC' else '' }}>大きい順</option>
    <option value="ASC" {{ 'selected' if q_dir == 'ASC' else '' }}>小さい順</option></select></div>
</div>
<div style="margin-top:8px"><label>指数（未選択=すべて）</label><div class="cbs">{% for b in bases %}
  <label class="cb"><input type="checkbox" name="ind" value="{{ b }}" {{ 'checked' if b in q_inds else '' }}>{{ b }}</label>{% endfor %}</div></div>
<div style="margin-top:8px"><label>パターン（未選択=すべて）</label><div class="cbs">{% for f in families %}
  <label class="cb"><input type="checkbox" name="family" value="{{ f }}" {{ 'checked' if f in q_fams else '' }}>{{ family_label(f) }}</label>{% endfor %}</div></div>
<div style="margin-top:10px"><button type="submit">表示</button></div></form></div>

<div class="card"><h1>馬 × 指数（該当キーの adjusted_WEDGE 最大 / WEDGE 最大・該当数）</h1>
<table><thead><tr><th>枠</th><th>馬番</th><th>馬名</th><th>騎手</th><th>該当数</th><th>adj 最大</th><th>adj 平均</th><th>WEDGE 最大</th><th>WEDGE 平均</th>
{% for b in matrix_bases %}<th>{{ b }}</th>{% endfor %}</tr></thead><tbody>
{% for m in matrix %}<tr><td>{{ m.waku }}</td><td>{{ m.uma }}</td><td>{{ m.name }}</td><td>{{ m.jockey }}</td><td class="num">{{ m.n }}</td>
<td class="cell {{ wedge_class(m.adj_max) }}">{{ fmt(m.adj_max, 3) }}</td><td class="cell">{{ fmt(m.adj_mean, 3) }}</td>
<td class="cell {{ wedge_class(m.wedge_max) }}">{{ fmt(m.wedge_max, 3) }}</td><td class="cell">{{ fmt(m.wedge_mean, 3) }}</td>
{% for b in matrix_bases %}{% set c = m.cells[b] %}<td class="cell {{ wedge_class(c.adj if c.adj is not none else c.wedge) }}">
{% if c.n %}{{ fmt(c.adj, 3) if c.adj is not none else '—' }}<div class="sub">{{ fmt(c.wedge, 3) }}・{{ c.n }}件</div>{% endif %}</td>{% endfor %}</tr>{% endfor %}
</tbody></table></div>

<div class="card"><h1>全該当キー（{{ total }}行{% if total > shown|length %}・画面は先頭 {{ shown|length }} 行。残りは CSV で{% endif %}）</h1>
<table><thead><tr><th>枠</th><th>馬番</th><th>馬名</th><th>騎手</th><th>指数</th><th>パターン</th><th>オッズ帯</th><th>地域</th><th>結合データ</th>
<th>件数</th><th>WIN</th><th>EDGE</th><th>WEDGE</th><th>adjusted_WEDGE</th><th>年数</th><th>年の件数計</th><th>最終年</th>
<th>勝率_val</th><th>連対率_val</th><th>複勝率_val</th><th>掲示板率_val</th><th>単勝回収値</th><th>複勝回収値</th></tr></thead><tbody>
{% for h in shown %}<tr><td>{{ h['枠番'] }}</td><td>{{ h['馬番'] }}</td><td>{{ h['馬名'] }}</td><td>{{ h['騎手'] }}</td>
<td>{{ h['base'] }}</td><td>{{ family_label(h['family'], h['rank_scheme']) }}</td><td>{{ odds_label(h['odds_mode']) }}</td><td>{{ h['region'] }}</td>
<td><a href="{{ url_for('entries_key', key=h['key']) }}" target="_blank">{{ h['key'] }}</a></td><td class="num">{{ h['件数'] }}</td>
{% for k in ['WIN','EDGE','WEDGE','adjusted_WEDGE'] %}<td class="num {{ wedge_class(h.get(k)) if k in ('WEDGE','adjusted_WEDGE') else '' }}">{{ fmt_cell(k, h.get(k)) }}</td>{% endfor %}
{% for k in ['adjusted_WEDGE_年数','adjusted_WEDGE_件数','adjusted_WEDGE_最終年','勝率_val','連対率_val','複勝率_val','掲示板率_val','単勝回収値','複勝回収値'] %}<td class="num">{{ fmt_cell(k, h.get(k)) }}</td>{% endfor %}</tr>
{% else %}<tr><td colspan="23">該当なし（この出馬表の馬の条件に合う集計行がありません）</td></tr>{% endfor %}</tbody></table></div>
"""


def _wedge_request():
    table = request.args.get("table", "").strip()
    rid = request.args.get("rid", "").strip()
    legacy = request.args.get("ridn", "").strip()
    odds_mode = request.args.get("odds_mode", "normal").strip()
    if odds_mode not in ODDS_MODES + ("all",):
        odds_mode = "normal"
    sort = request.args.get("sort", "adjusted_WEDGE").strip()
    if sort not in WEDGE_SORTS:
        sort = "adjusted_WEDGE"
    sdir = "ASC" if request.args.get("dir", "DESC").upper() == "ASC" else "DESC"
    return {"table": table, "rid": rid, "legacy": legacy, "date": request.args.get("date", "").strip(),
            "odds_mode": odds_mode, "inds": [v for v in request.args.getlist("ind") if v],
            "fams": [v for v in request.args.getlist("family") if v],   # 使う family かは DB を読んでから確かめる
            "uma": request.args.get("uma", "").strip(), "sort": sort, "dir": sdir}


def _wedge_data(p: dict):
    with connect() as con:
        if not _valid_entries_table(con, p["table"]) or not (p["rid"] or p["legacy"]):
            return None
        race = _race_rows(con, p["table"], p["rid"], p["legacy"], p["date"])
        hits, path = collect_hits(con, p["table"], race)
        bases = indicator_bases(table_columns(con, p["table"]))
    # connect() が DB の __families__ を台帳に反映したあとで、family の絞り込みを確かめる
    p["fams"] = [v for v in p["fams"] if v in fam_registry.active_names()]
    rank = {n: i for i, n in enumerate(INDICATOR_ORDER)}
    bases = sorted(bases, key=lambda b: (rank.get(b, len(rank)), b))
    rows = sort_wedge_hits(filter_wedge_hits(hits, p["odds_mode"], p["inds"], p["fams"], p["uma"]),
                           p["sort"], p["dir"] == "DESC")
    return race, rows, path, bases


@app.route("/entries/wedge", methods=["GET"])
def entries_wedge():
    p = _wedge_request()
    data = _wedge_data(p)
    if data is None:
        return redirect(url_for("entries_index"))
    race, rows, path, bases = data
    if not race:
        return render("WEDGE一覧", '<div class="card">該当レースがありません。</div>')
    h = race[0]
    rid = h.get("レースID(新/馬番無)", p["rid"])
    matrix_bases = [b for b in bases if not p["inds"] or b in p["inds"]]
    matrix_race = [r for r in race if not p["uma"] or str(r.get("馬番")) == p["uma"]]
    args = {k: v for k, v in request.args.lists() if k in WEDGE_QUERY_KEYS}   # url_for の予約引数（_anchor 等）を通さない
    args.update({"table": [p["table"]], "rid": [rid]})
    head = f'{h.get("日付", "")} {h.get("場 R", "")} {h.get("レース名", "")}　WEDGE 一覧'
    sub = f'{h.get("場所", "")} / {h.get("芝・ダ", "")} / {h.get("距離_raw", "")} / {h.get("クラス名_raw", "")} / {len(race)}頭'
    return render("WEDGE一覧", WEDGE_BODY, head=head, sub=sub, path=path, table=p["table"], rid=rid, race=race,
                  bases=bases, families=fam_registry.active_names(), odds_modes=ODDS_MODES, sorts=WEDGE_SORTS,
                  q_odds=p["odds_mode"], q_inds=p["inds"], q_fams=p["fams"], q_uma=p["uma"], q_sort=p["sort"],
                  q_dir=p["dir"], matrix=wedge_matrix(matrix_race, rows, matrix_bases), matrix_bases=matrix_bases,
                  total=len(rows), shown=rows[:WEDGE_VIEW_LIMIT],
                  csv_url=url_for("entries_wedge_csv", **args))


@app.route("/entries/wedge.csv", methods=["GET"])
def entries_wedge_csv():
    p = _wedge_request()
    data = _wedge_data(p)
    if data is None:
        return redirect(url_for("entries_index"))
    race, rows, _path, _bases = data
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(WEDGE_CSV_COLUMNS)
    for h in rows:
        h = {**h, "区分": CATEGORY_LABELS.get(hit_category(h), "")}      # 条件ヒット馬と同じ判定（該当なしは空）
        w.writerow(["" if h.get(c) is None else h.get(c) for c in WEDGE_CSV_COLUMNS])
    rid = (race[0].get("レースID(新/馬番無)") if race else "") or p["rid"] or "race"
    name = re.sub(r"[^0-9A-Za-z_-]", "_", str(rid))
    return Response(buf.getvalue().encode("utf-8-sig"), mimetype="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="wedge_{name}.csv"'})


@app.route("/healthz")
def healthz():
    try:
        with connect() as con:
            con.execute("SELECT 1")
        return "ok"
    except Exception as e:  # pragma: no cover
        return f"ng: {e}", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=False)
