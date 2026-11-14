# -*- coding: utf-8 -*-
"""Flask UI。

  /              横断検索（集計表を family・指数・オッズ帯・地域・コース条件で絞り込み。
                 表ごとの表示と、全表をまとめて WEDGE などで並べる横断ランキングの2通り。
                 集計表は __tables_meta__ の kind=foragg の行。中身は unified_all の「テーブル名」が同じ行
                 （前の版の DB に1表ずつのテーブル foragg_* が残っていても使わない。前の版の unified_all は
                 そのテーブルを同じ順で詰め直したものなので、表示は同じ）
  /entries       出馬表のレース一覧（日付・場所で絞り込み）
  /entries/race  出馬表（各馬の指数順位・偏差値）と指数クロス（複数の指数で同時に上位の馬）
  /entries/key   結合データの前方一致で unified_all を表示
  /entries/hits  条件ヒット馬（絶対軸・軸候補・消し候補・相手候補）と指数クロス好走馬（軸が複数の指数で出ている馬）。
                 オッズ帯別の行と重なる「通常」の行は省き、10〜50倍・50倍以上の軸の行は赤字で出す
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
import json
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
from shisu.matching import HIT_COLUMNS, UNIFIED, HitCache, indicator_bases, match_entry_row, unified_status
from shisu.naming import ENTRIES_ALL, XJOIN_PREFIX, xjoin_table_for
from shisu.odds import ODDS_BREAKS, ODDS_LABELS, ODDS_MODES, ODDS_RANK
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
    _FAMILY_SYNC["odds_note"] = _odds_note(con)
    _FAMILY_SYNC["stamp"] = stamp


def _odds_note(con) -> str:
    """DB の成績表が、今のオッズ帯の境目（odds10u = 2〜10倍未満）で集計されたものかを確かめる。
    違う（前の区切り = 10倍未満で、単勝1倍台も入っている）・分からないなら、画面に出す注意書きを返す。成績表が無ければ ''。"""
    try:
        if not table_exists(con, UNIFIED) or con.execute(f"SELECT 1 FROM {UNIFIED} LIMIT 1").fetchone() is None:
            return ""
        got = None
        if table_exists(con, "__build_info__"):
            r = con.execute("SELECT value FROM __build_info__ WHERE key = 'odds_breaks'").fetchone()
            got = json.loads(r[0]) if r and r[0] else None
    except (sqlite3.Error, ValueError):
        got = None
    if got == list(ODDS_BREAKS):
        return ""
    return (f"この DB の成績表は、オッズ帯の前の区切り（10倍未満。単勝{ODDS_BREAKS[0]:g}倍未満の馬も入る）で集計したものです。"
            f"画面の「{ODDS_LABELS['odds10u']}」の行の数字には、まだ1倍台の馬が入っています"
            "（aggregate_stats_ver2.py → build_db.py --fresh で作り直すと、2倍以上10倍未満だけになります）。")


def odds_note() -> str:
    return _FAMILY_SYNC.get("odds_note", "")


def xjoin_note(con, table: str, path: str) -> str:
    """照合結果（xjoin）を読んだ画面に出す注意書き: その照合結果が、今の成績表（unified_all）より前に作られたものなら返す。
    成績表だけを作り直した（build_db --mode foragg）あとは、条件ヒット馬・WEDGE 一覧の行が前の成績表の数字のまま残る
    （オッズ帯の区切りを変えて集計し直したあとなら、前の区切りの数字）。build_db の記録（__build_info__）で見分ける:
    unified_stamp（成績表を入れ替えるたびに変わる）と、照合結果を作ったときの記録 xjoin_of:{xjoin} の unified。
    記録の仕組みが無い DB（前の版）では分からないので出さない。"""
    if path != "xjoin":
        return ""
    xname = xjoin_table_for(table)
    try:
        if not table_exists(con, "__build_info__"):
            return ""
        info = dict(con.execute("SELECT key, value FROM __build_info__ WHERE key IN (?, ?)",
                                ("unified_stamp", "xjoin_of:" + xname)).fetchall())
        stamp = info.get("unified_stamp")
        if not stamp:
            return ""
        rec = json.loads(info.get("xjoin_of:" + xname) or "null")
    except (sqlite3.Error, ValueError, TypeError):
        return ""
    if isinstance(rec, dict) and rec.get("unified") == stamp:
        return ""
    return ("この出馬表の照合結果（下の行）は、今の成績表より前に作ったものです（成績表だけを作り直したあとなど）。"
            "数字は前の成績表のままです。build_db.py --entries-only を実行すると、今の成績表で照合し直します。")


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
/* 表だけを縦にスクロールする枠（見出しの行を上に固定する。.card は横スクロール用の枠なので、ページのスクロールでは固定されない） */
.scroll{max-height:520px;overflow:auto;border:1px solid #eef2f7;border-radius:8px}
.scroll.tall{max-height:72vh}
.scroll thead th{position:sticky;top:0;z-index:2;background:#f8fafc;box-shadow:0 1px 0 #e5e7eb}
/* 10〜50倍・50倍以上で絶対軸・軸候補に当たった行（狙い目の穴馬） */
tr.hot td{color:#b91c1c;font-weight:700;background:#fef2f2}
.hotnum{color:#b91c1c;font-weight:700}
.kd{display:inline-block;font-size:11px;font-weight:700;color:#fff;background:#4f46e5;padding:2px 7px;border-radius:999px;white-space:nowrap}
.kd.caution{background:#64748b}
tr.kd-dominant td{background:#e0e7ff} tr.kd-caution td{background:#f1f5f9}
.tag.clash{background:#e0e7ff;border:1px solid #4f46e5} .tag .ks{color:#4338ca;font-weight:700}
.kdnum{color:#4338ca;font-weight:700}
td.top3{font-weight:700} td.hen61{background:#ecfdf5} td.top3.hen61{background:#d1fae5}
details.filters>summary{cursor:pointer;font-size:13px;color:#1d4ed8;padding:4px 0}
</style></head><body><header>{{ title }}</header><div class="container">
<div class="nav"><a href="{{ url_for('index') }}">横断検索</a><a href="{{ url_for('entries_index') }}">出馬表</a><a href="{{ url_for('ingest.index') }}">指数の取得</a></div>
{% if db_warning %}<div class="card warn">{{ db_warning }}</div>{% endif %}
{% if odds_note %}<div class="card warn">{{ odds_note }}</div>{% endif %}
{% if xjoin_note %}<div class="card warn">{{ xjoin_note }}</div>{% endif %}
{BODY}
</div></body></html>"""


def render(title: str, body: str, **ctx):
    ctx.setdefault("db_warning", "")
    ctx.setdefault("odds_note", odds_note() if ctx.pop("show_odds_note", False) else "")
    ctx.setdefault("xjoin_note", "")
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

    return render("指数集計 横断検索", INDEX_BODY, show_odds_note=True, selects=selects, families=families, indicators=indicators,
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
    if preset != "none":   # 判定はオッズ帯で変わる（消し候補の2〜10倍未満ルール）ので帯ごとに条件を付ける
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


CROSS_RANK_MAX = 3          # 指数クロス: 順位がこの位以内なら「上位」
CROSS_HEN_MIN = 61.0        # 指数クロス: 偏差値がこれ以上なら「上位」
CROSS_CRITERIA = {"either": f"順位{CROSS_RANK_MAX}位以内 または 偏差値{CROSS_HEN_MIN:g}以上", "rank": f"順位{CROSS_RANK_MAX}位以内",
                  "hen": f"偏差値{CROSS_HEN_MIN:g}以上", "both": f"順位{CROSS_RANK_MAX}位以内 かつ 偏差値{CROSS_HEN_MIN:g}以上"}
CROSS_MIN_CHOICES = (1, 2, 3, 4, 5, 6, 8, 10)


def order_bases(bases) -> list[str]:
    """指数の並び（台帳の順。台帳に無いものは名前順で後ろ）。"""
    rank = {n: i for i, n in enumerate(INDICATOR_ORDER)}
    return sorted(bases, key=lambda b: (rank.get(b, len(rank)), b))


def min_choices(xmin: int) -> list[int]:
    """「何指数以上」の選択肢。いま効いている値が選択肢に無い（URL を手で書いた）ときも選べるように足す
    （足さないとどれも選ばれず、フォームを送り直したときに先頭の 1 に変わってしまう）。"""
    return sorted(set(CROSS_MIN_CHOICES) | {xmin})


def cross_request() -> dict:
    """指数クロスの絞り込み（出馬表・条件ヒット馬で共通の引数。xmin=何指数以上, xa/xb=この2指数の両方, xonly=該当馬だけ）。"""
    crit = request.args.get("xc", "either").strip()
    try:
        xmin = int(request.args.get("xmin", "2"))
    except ValueError:
        xmin = 2
    return {"crit": crit if crit in CROSS_CRITERIA else "either", "xmin": min(max(xmin, 1), 99),
            "a": request.args.get("xa", "").strip(), "b": request.args.get("xb", "").strip(),
            "only": request.args.get("xonly", "") == "1"}


def cross_rows(rows: list[dict], bases: list[str], crit: str = "either", a: str = "", b: str = "",
               xmin: int = 2) -> list[dict]:
    """出馬表の馬ごとに、上位（crit: 順位3位以内・偏差値61以上）になっている指数を数える。
    hit = 上位の指数が xmin 個以上で、a・b を指定したときはその両方が上位。"""
    out = []
    for r in rows:
        tops = []
        for base in bases:
            rank = to_int(r.get(f"{base}_順位"), 0) or None
            hen = _num(r.get(f"{base}_偏差値"))
            by_rank = rank is not None and rank <= CROSS_RANK_MAX
            by_hen = hen is not None and hen >= CROSS_HEN_MIN
            ok = {"rank": by_rank, "hen": by_hen, "both": by_rank and by_hen}.get(crit, by_rank or by_hen)
            if ok:
                tops.append({"base": base, "rank": rank, "hen": hen})
        names = {t["base"] for t in tops}
        pair = (not a or a in names) and (not b or b in names)
        out.append({"uma": str(r.get("馬番", "")), "waku": r.get("枠番", ""), "name": r.get("馬名", ""),
                    "jockey": r.get("騎手", ""), "tops": tops, "n": len(tops), "hit": len(tops) >= xmin and pair})
    return out


CROSS_FORM = """
<form method="get" style="margin-top:6px">{% for k, v in cross_hidden %}<input type="hidden" name="{{ k }}" value="{{ v }}">{% endfor %}
<div class="grid">
  {% if cross_crit is not none %}<div><label>上位の基準</label><select name="xc">{% for k, lab in cross_criteria.items() %}
    <option value="{{ k }}" {{ 'selected' if k == cross.crit else '' }}>{{ lab }}</option>{% endfor %}</select></div>{% endif %}
  <div><label>何指数以上で同時に</label><select name="xmin">{% for n in cross_min_choices %}
    <option value="{{ n }}" {{ 'selected' if n == cross.xmin else '' }}>{{ n }} 指数以上</option>{% endfor %}</select></div>
  <div><label>指数A（A・B の両方）</label><select name="xa"><option value="">（指定なし）</option>
    {% for b in cross_bases %}<option value="{{ b }}" {{ 'selected' if b == cross.a else '' }}>{{ b }}</option>{% endfor %}</select></div>
  <div><label>指数B</label><select name="xb"><option value="">（指定なし）</option>
    {% for b in cross_bases %}<option value="{{ b }}" {{ 'selected' if b == cross.b else '' }}>{{ b }}</option>{% endfor %}</select></div>
  <div><label>&nbsp;</label><label class="cb"><input type="checkbox" name="xonly" value="1" {{ 'checked' if cross.only else '' }}>該当馬だけを下の表に出す</label></div>
  <div><label>&nbsp;</label><button type="submit">絞り込む</button></div>
</div>
{% if cut_low is defined %}<div style="margin-top:8px"><input type="hidden" name="cut_low_10u" value="0">
<label class="cb"><input type="checkbox" name="cut_low_10u" value="1" {{ 'checked' if cut_low else '' }}>2〜10倍未満（odds10u）の軸・相手は回収値{{ cut_min }}以上のみ（過剰人気カット）</label></div>{% endif %}
</form>
"""

RACE_BODY = """
<div class="card"><h1>{{ head }}</h1><div class="muted">{{ sub }}</div>
<div style="margin-top:6px"><a href="{{ url_for('entries_hits', table=table, rid=rid) }}" target="_blank">条件ヒット馬を見る</a>
 ／ <a href="{{ url_for('entries_wedge', table=table, rid=rid) }}" target="_blank">WEDGE一覧を見る</a></div></div>
<div class="card"><h1>指数クロス（複数の指数で同時に上位の馬）：{{ cross_hits|length }}頭</h1>
<div class="muted">上位 = {{ cross_criteria[cross.crit] }}。{{ cross.xmin }} 指数以上で同時に上位{% if cross.a or cross.b %}・{{ [cross.a, cross.b]|select|join(' と ') }} が上位{% endif %} の馬</div>
""" + CROSS_FORM + """
<div class="scroll" style="margin-top:8px"><table><thead><tr><th>枠番</th><th>馬番</th><th>馬名</th><th>騎手</th><th class="num">上位の指数数</th><th>上位の指数（順位 / 偏差値）</th></tr></thead><tbody>
{% for x in cross_hits %}<tr><td>{{ x.waku }}</td><td>{{ x.uma }}</td><td>{{ x.name }}</td><td>{{ x.jockey }}</td><td class="num"><b>{{ x.n }}</b></td>
<td style="white-space:normal">{% for t in x.tops %}<span class="tag">{{ t.base }} {{ t.rank if t.rank else '—' }}位 / {{ fmt(t.hen, 0) if t.hen is not none else '—' }}</span>{% endfor %}</td></tr>
{% else %}<tr><td colspan="6">該当する馬はいません（基準・指数数を変えてみてください）</td></tr>{% endfor %}</tbody></table></div></div>
<div class="card"><div class="muted">太字 = 順位{{ rank_max }}位以内、緑の背景 = 偏差値{{ hen_min }}以上{% if cross.only %}（指数クロスの該当馬だけを表示中）{% endif %}</div>
<div class="scroll tall"><table><thead><tr><th>枠番</th><th>馬番</th><th>騎手</th><th>馬名</th><th class="num">上位数</th>
{% for b in bases %}<th>{{ b }}<div class="muted">順位 / 偏差値 / 帯</div></th>{% endfor %}<th>結合データ（先頭部分）</th></tr></thead>
<tbody>{% for r in rows %}{% set x = cross_by[r['馬番']|string] %}<tr><td>{{ r['枠番'] }}</td><td>{{ r['馬番'] }}</td><td>{{ r['騎手'] }}</td><td>{{ r['馬名'] }}</td>
<td class="num">{% if x.hit %}<b class="hotnum">{{ x.n }}</b>{% else %}{{ x.n or '' }}{% endif %}</td>
{% for b in bases %}<td class="{{ cell_class(r, b) }}">{{ r.get(b ~ '_順位') or '' }}{% if r.get(b ~ '_順位') %}位{% endif %} / {{ r.get(b ~ '_偏差値') or '' }} / {{ r.get(b ~ '_偏差値帯') or '' }}</td>{% endfor %}
<td>{% if head_key %}<a href="{{ url_for('entries_key', key=head_key) }}" target="_blank">{{ head_key }}</a>{% endif %}</td></tr>{% endfor %}</tbody></table></div></div>
"""


def _head_key(r: dict) -> str:
    parts = [r.get("場所") or "", r.get("芝・ダ") or "", r.get("距離") or ""]
    if not all(parts):
        return ""
    if r.get("芝(内・外)"):
        parts.append(r["芝(内・外)"])
    return "×".join(parts)


def cell_class(r: dict, base: str) -> str:
    """出馬表のセルの強調（順位3位以内は太字、偏差値61以上は背景）。"""
    rank = to_int(r.get(f"{base}_順位"), 0)
    hen = _num(r.get(f"{base}_偏差値"))
    return " ".join(c for c, ok in (("top3", 1 <= rank <= CROSS_RANK_MAX), ("hen61", hen is not None and hen >= CROSS_HEN_MIN)) if ok)


@app.route("/entries/race", methods=["GET"])
def entries_race():
    table = request.args.get("table", "").strip()
    rid = request.args.get("rid", "").strip()
    legacy = request.args.get("ridn", "").strip()
    with connect() as con:
        if not _valid_entries_table(con, table) or not (rid or legacy):
            return redirect(url_for("entries_index"))
        rows = _race_rows(con, table, rid, legacy, request.args.get("date", "").strip())
        bases = order_bases(indicator_bases(table_columns(con, table)))
    if not rows:
        return render("出馬表", '<div class="card">該当レースがありません。</div>')
    h = rows[0]
    rid = h.get("レースID(新/馬番無)", rid)
    head = f'{h.get("日付", "")} {h.get("場 R", "")} {h.get("レース名", "")}'
    sub = (f'{h.get("場所", "")} / {h.get("芝・ダ", "")} / {h.get("距離_raw", "")}（キー: {h.get("距離", "")}） / '
           f'内外: {h.get("芝(内・外)", "") or "—"} / 重量: {h.get("重量種別", "") or "—"} / クラス: {h.get("クラス名", "")}')
    cross = cross_request()
    cross["a"], cross["b"] = (v if v in bases else "" for v in (cross["a"], cross["b"]))
    xs = cross_rows(rows, bases, cross["crit"], cross["a"], cross["b"], cross["xmin"])
    hits = sorted((x for x in xs if x["hit"]), key=lambda x: (-x["n"], to_int(x["uma"])))
    hit_umas = {x["uma"] for x in hits}
    shown = [r for r in rows if not cross["only"] or str(r.get("馬番", "")) in hit_umas]
    return render("出馬表", RACE_BODY, head=head, sub=sub, rows=shown, bases=bases, table=table, rid=rid,
                  head_key=_head_key(h), cross=cross, cross_hits=hits, cross_by={x["uma"]: x for x in xs},
                  cross_bases=bases, cross_criteria=CROSS_CRITERIA, cross_crit=cross["crit"],
                  cross_min_choices=min_choices(cross["xmin"]), cross_hidden=[("table", table), ("rid", rid)],
                  cell_class=cell_class, rank_max=CROSS_RANK_MAX, hen_min=f"{CROSS_HEN_MIN:g}")


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
    return render("結合データ", KEY_BODY, key=key, rows=rows, limit=limit, show_odds_note=True)


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
    cache = HitCache()                      # 上限つき（同じレースの馬どうしの引き直しを省く）
    hits: list[dict] = []
    for r in race_rows:
        hits.extend(match_entry_row(con, r, bases, table, cache=cache))
    return hits, note


def hit_category(h: dict) -> str | None:
    """ヒット行の区分（abs / axis / keshi / opp / None）。classify と同じ判定。"""
    return classify({"cnt": h.get("件数"), "fv": h.get("複勝率_val"), "rv": h.get("連対率_val"),
                     "top5": h.get("掲示板率_val"), "frc": h.get("複勝回収値"),
                     "tra": h.get("単勝適正回収値"), "key": h.get("key")}, h.get("odds_mode") or "normal")


# 条件ヒット馬の並び・表示の優先度（オッズ帯。通常は、オッズ帯を分けた行と中身が重なりやすいので最後）
HITS_ODDS_RANK = {"odds10u": 0, "odds10_50": 1, "odds50p": 2, "normal": 3}
REDUNDANT_NORMAL_DIFF = 3              # 通常とオッズ帯別の件数の差がこれ以内なら、通常の行は同じ馬の集まりなので省く
HOT_MODES = ("odds10_50", "odds50p")   # このオッズ帯で絶対軸・軸候補に当たった行は、狙い目の穴馬として強調する
HOT_CATEGORIES = ("abs", "axis")


# 人気馬（2〜10倍未満）の見分け
FAV_MODE = "odds10u"                   # 2〜10倍未満のオッズ帯
CUT_CATEGORIES = ("axis", "opp")       # 過剰人気カットの対象の区分（絶対軸はもともと回収値100以上が条件・消しは対象外）
CUT_MIN_RETURN = 100.0                 # 複勝回収値・単勝適正回収値のどちらもこれ未満なら、過剰人気（当たっても増えない）として除く
KESHI_DOMINANT_RATIO = 1.0             # 消し(2〜10倍未満)の行数 ≥ 軸の行数 × これ → 「消優勢」
KESHI_CAUTION_RATIO = 0.5              # 消し(2〜10倍未満)の行数 ≥ 軸の行数 × これ → 「要注意」
DANGER_LABELS = {"dominant": "⚠️消優勢", "caution": "要注意"}


def is_hot(h: dict, cat: str | None) -> bool:
    return cat in HOT_CATEGORIES and (h.get("odds_mode") or "normal") in HOT_MODES


def is_low_return_favorite(h: dict, cat: str | None) -> bool:
    """過剰人気カットの対象か: 2〜10倍未満（odds10u）の軸候補・相手候補で、複勝回収値も単勝適正回収値も CUT_MIN_RETURN 未満
    （値が無いものは「100以上」ではないので対象。classify の「欠損は満たさない」と同じ見方）。"""
    return cat in CUT_CATEGORIES and (h.get("odds_mode") or "normal") == FAV_MODE and is_low_return(h)


def is_low_return(h: dict) -> bool:
    """複勝回収値も単勝適正回収値も CUT_MIN_RETURN 未満か（値の無いものは「100以上」ではないので未満の側）。"""
    return not any(v is not None and v >= CUT_MIN_RETURN for v in (_num(h.get("複勝回収値")), _num(h.get("単勝適正回収値"))))


def danger_level(axis_rows: int, keshi_fav: int) -> str:
    """軸の行数（絶対軸 + 軸候補）と 消し(2〜10倍未満)の行数の比べ方。
    'dominant'（消優勢）= 消しが1行以上あり、軸の行数以上 ／ 'caution'（要注意）= 軸があり、消しが軸の半分以上 ／ ''。"""
    if keshi_fav > 0 and keshi_fav >= axis_rows * KESHI_DOMINANT_RATIO:
        return "dominant"
    if axis_rows > 0 and keshi_fav > 0 and keshi_fav >= axis_rows * KESHI_CAUTION_RATIO:
        return "caution"
    return ""


def horse_stats(race: list[dict], buckets: dict[str, list[dict]]) -> dict[str, dict]:
    """馬番 → 区分ごとの行数・穴の軸の行数・消し(2〜10倍未満)の行数と指数ごとの内訳・消優勢の判定。
    buckets は classify_hits の結果（冗長な通常・過剰人気カットを除いたあと）。"""
    out = {str(r.get("馬番", "")): {**{c: 0 for c in CATEGORIES}, "hot": 0, "keshi_fav": 0, "keshi_fav_by": {}}
           for r in race}
    for cat, items in buckets.items():
        for h in items:
            s = out.get(str(h.get("馬番")))
            if s is None:
                continue
            s[cat] += 1
            if is_hot(h, cat):
                s["hot"] += 1
            if cat == "keshi" and (h.get("odds_mode") or "normal") == FAV_MODE:
                s["keshi_fav"] += 1
                b = h.get("base")
                s["keshi_fav_by"][b] = s["keshi_fav_by"].get(b, 0) + 1
    for s in out.values():
        s["axis_rows"] = s["abs"] + s["axis"]
        s["keshi_fav_inds"] = len(s["keshi_fav_by"])
        s["danger"] = danger_level(s["axis_rows"], s["keshi_fav"])
    return out


def drop_redundant_normal(rows: list[tuple[dict, str]], cut_low: bool = False
                          ) -> tuple[list[tuple[dict, str]], int, dict[str, int]]:
    """(ヒット行, 区分) の並びから、冗長な「通常」の行を除く。cut_low=True なら過剰人気の行（is_low_return_favorite）も除く。
    戻り値: (残した並び, 除いた「通常」の行数, 区分ごとの過剰人気カットの行数)。

    同じ馬・指数・パターン（スキーム・地域も）・結合データで、通常（normal）とオッズ帯別（2〜10倍未満・10〜50倍・50倍以上の
    どれか）が同じ区分に当たり、件数の差が REDUNDANT_NORMAL_DIFF 件以内なら、通常の行はオッズ帯別の行とほぼ同じ馬の
    集まりなので除く（件数が離れていれば、通常の行はほかのオッズ帯の馬も含む別の情報なので残す）。

    過剰人気カットとの組み合わせ: 冗長かどうかはカットの前の行で決める。つまり、カットした2〜10倍未満の行とほぼ同じ中身の
    「通常」の行は、カット ON でも出さない（その行自身の回収値が100以上でも。件数差3以内なので、その上振れは10倍以上の馬
    1〜3頭ぶんでしかなく、残すと同じ過剰人気の条件が「通常」として出直してしまう）。"""
    def ident(h: dict, cat: str) -> tuple:
        return (h.get("馬番"), h.get("base"), h.get("family"), h.get("rank_scheme") or "", h.get("region") or "",
                h.get("key"), cat)

    subs: dict[tuple, list[float]] = {}             # オッズ帯別の行の件数（カットされる行も、冗長かどうかの相手には数える）
    for h, cat in rows:
        if (h.get("odds_mode") or "normal") != "normal":
            n = _num(h.get("件数"))
            if n is not None:
                subs.setdefault(ident(h, cat), []).append(n)
    out, dropped, cut = [], 0, {c: 0 for c in CUT_CATEGORIES}
    for h, cat in rows:
        if cut_low and is_low_return_favorite(h, cat):
            cut[cat] += 1
            continue
        if (h.get("odds_mode") or "normal") == "normal":
            n = _num(h.get("件数"))
            if n is not None and any(abs(n - m) <= REDUNDANT_NORMAL_DIFF for m in subs.get(ident(h, cat), ())):
                dropped += 1
                continue
        out.append((h, cat))
    return out, dropped, cut


def classify_hits(hits: list[dict], info: dict | None = None, cut_low: bool = False) -> dict[str, list[dict]]:
    """ヒット行を区分ごとに分ける（重複と、冗長な「通常」の行を除く）。info を渡すと、除いた行数を info["dropped"] に入れる。
    cut_low=True なら、過剰人気の行（2〜10倍未満の軸候補・相手候補で回収値100未満）も除き、区分ごとの行数を info["cut"] に入れる。
    カットした2〜10倍未満の行とほぼ同じ中身の「通常」の行（冗長な通常）は、カット ON でも出さない（drop_redundant_normal）。
    並びは オッズ帯（2〜10倍未満 → 10〜50倍 → 50倍以上 → 通常）→ 馬番 → 複勝率（消しは昇順）→ 指数・パターン・キー・集計表
    （同点の並びも決めるので、表示の上限で出る行が、照合結果の行の順に左右されない）。"""
    rows = [(h, cat) for h in dedupe_hits(hits) for cat in (hit_category(h),) if cat]
    rows, dropped, cut = drop_redundant_normal(rows, cut_low)
    if info is not None:
        info["dropped"] = dropped
        info["cut"] = cut
    buckets: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    for h, cat in rows:
        buckets[cat].append(h)

    def fv(h):
        v = h.get("複勝率_val")
        return float(v) if v is not None else 0.0

    ind_rank = {n: i for i, n in enumerate(INDICATOR_ORDER)}

    def tie(h):      # 同点の並びを照合の経路（xjoin の行の順・その場照合）に依らず同じにする: 指数（台帳の順）→ パターン → キー → 集計表
        b = str(h.get("base") or "")
        return (ind_rank.get(b, len(ind_rank)), b, str(h.get("family") or ""), str(h.get("rank_scheme") or ""),
                str(h.get("key") or ""), str(h.get("テーブル名") or ""))

    for cat, items in buckets.items():
        sign = 1 if cat == "keshi" else -1
        items.sort(key=lambda h: (HITS_ODDS_RANK.get(h.get("odds_mode") or "normal", 9), to_int(h.get("馬番")), sign * fv(h)) + tie(h))
    return buckets


HITS_BODY = """
<div class="card"><h1>{{ head }}</h1><div class="muted">{{ sub }}</div>
<div class="muted">照合: {{ path }} ／ 率は DB の推定値（複勝率・連対率）と頻度（掲示板率）
{% if dropped %} ／ オッズ帯別の行と同じ区分・件数の差{{ diff }}件以内の「通常」の行 {{ dropped }} 行は、重なるので省いています{% endif %}</div>
{% if cut_low %}<div class="warn" style="margin-top:4px">過剰人気カット中: 2〜10倍未満の軸候補・相手候補のうち、複勝回収値も単勝適正回収値も {{ cut_min }} 未満の行を除いています（軸候補 {{ cut.axis }} 行・相手候補 {{ cut.opp }} 行。下の表・該当数・指数クロスはすべて除いたあとの数）。
<a href="{{ cut_toggle }}">カットせずに全部表示する</a></div>
{% else %}<div class="muted" style="margin-top:4px">過剰人気カットは切ってあります（2〜10倍未満の軸・相手を回収値に関係なく全部表示中）。<a href="{{ cut_toggle }}">回収値{{ cut_min }}以上だけにする</a></div>{% endif %}
<div style="margin-top:6px"><a href="{{ url_for('entries_race', table=table, rid=rid) }}">出馬表（指数クロス）</a> ／
<a href="{{ url_for('entries_wedge', table=table, rid=rid) }}" target="_blank">このレースの WEDGE 一覧（閾値なし・全キー）</a></div></div>
<div class="card"><h1>指数クロス好走馬（絶対軸・軸候補が複数の指数で同時に出ている馬）：{{ cross_hits|length }}頭</h1>
<div class="muted">絶対軸・軸候補の行がある指数を馬ごとに数えます。{{ cross.xmin }} 指数以上{% if cross.a or cross.b %}・{{ [cross.a, cross.b]|select|join(' と ') }} の両方で軸{% endif %} の馬。
<span class="hotnum">赤字</span> = 10〜50倍・50倍以上のオッズ帯で軸に当たった指数（狙い目の穴）<br>
<span class="kd">{{ DANGER_LABELS.dominant }}</span> = 消し(2〜10倍未満)の行数が、軸の行数（絶対軸＋軸候補）以上 ／
<span class="kd caution">{{ DANGER_LABELS.caution }}</span> = 消し(2〜10倍未満)の行数が、軸の行数の半分以上 ／
<span class="tag clash">指数 絶対軸 / 軸候補 <span class="ks">(消n)</span></span> = 同じ指数で、軸と 消し(2〜10倍未満) の両方が出ている（n = 消しの行数）</div>
""" + CROSS_FORM + """
<div class="scroll" style="margin-top:8px"><table><thead><tr><th>馬番</th><th>馬名</th><th>騎手</th><th>判定</th><th class="num">軸の指数数</th><th class="num">うち穴</th>
<th class="num">軸の行数</th><th class="num">消し(2〜10倍未満)</th><th class="num">消し(2〜10倍未満)の指数数</th><th>軸に当たった指数（絶対軸 / 軸候補 の行数。(消n) = 同じ指数の 消し(2〜10倍未満)）</th></tr></thead><tbody>
{% for x in cross_hits %}<tr{% if x.danger %} class="kd-{{ x.danger }}"{% endif %}><td>{{ x.uma }}</td><td>{{ x.name }}</td><td>{{ x.jockey }}</td>
<td>{% if x.danger %}<span class="kd{{ ' caution' if x.danger == 'caution' else '' }}">{{ DANGER_LABELS[x.danger] }}</span>{% endif %}</td>
<td class="num"><b>{{ x.n }}</b></td><td class="num hotnum">{{ x.n_hot or '' }}</td><td class="num">{{ x.axis_rows }}</td>
<td class="num kdnum">{{ x.keshi_fav or '' }}</td><td class="num kdnum">{{ x.keshi_fav_inds or '' }}</td>
<td style="white-space:normal">{% for t in x.inds %}<span class="tag{{ ' hotnum' if t.hot else '' }}{{ ' clash' if t.keshi_fav else '' }}">{{ t.base }} {{ t.abs }} / {{ t.axis }}{% if t.keshi_fav %} <span class="ks">(消{{ t.keshi_fav }})</span>{% endif %}</span>{% endfor %}</td></tr>
{% else %}<tr><td colspan="10">該当する馬はいません（指数数を下げてみてください）</td></tr>{% endfor %}</tbody></table></div></div>
<div class="card"><h1>馬ごとの該当数{% if n_dominant or n_caution %}（{{ DANGER_LABELS.dominant }} {{ n_dominant }}頭・{{ DANGER_LABELS.caution }} {{ n_caution }}頭）{% endif %}</h1><div class="scroll"><table><thead><tr><th>馬番</th><th>馬名</th><th>判定</th>
{% for c in order %}<th class="num">{{ CATEGORY_LABELS[c] }}</th>{% endfor %}<th class="num">穴の軸（10倍以上）</th><th class="num">軸の指数数</th>
<th class="num">消し(2〜10倍未満)</th><th class="num">消し(2〜10倍未満)の指数数</th></tr></thead><tbody>
{% for s in summary %}<tr{% if s.danger %} class="kd-{{ s.danger }}"{% endif %}><td>{{ s.uma }}</td><td>{{ s.name }}</td>
<td>{% if s.danger %}<span class="kd{{ ' caution' if s.danger == 'caution' else '' }}">{{ DANGER_LABELS[s.danger] }}</span>{% endif %}</td>
{% for c in order %}<td class="num">{{ s[c] or '' }}</td>{% endfor %}
<td class="num hotnum">{{ s.hot or '' }}</td><td class="num">{% if s.cross %}<b>{{ s.n }}</b>{% else %}{{ s.n or '' }}{% endif %}</td>
<td class="num kdnum">{{ s.keshi_fav or '' }}</td><td class="num kdnum">{{ s.keshi_fav_inds or '' }}</td></tr>{% endfor %}
</tbody></table></div></div>
{% for c in order %}
<div class="card cat-{{ c }}"><h1>{{ CATEGORY_LABELS[c] }}（{{ buckets[c]|length }}{% if cross.only %}・指数クロスの該当馬だけ{% endif %}）</h1>
<div class="muted">{% for line in desc[c] %}• {{ line }}<br>{% endfor %}</div>
{% if cut_low and cut.get(c) %}<div class="muted">過剰人気カットで、2〜10倍未満・回収値{{ cut_min }}未満の {{ cut[c] }} 行を除いています{% if cross.only %}（全馬の合計）{% endif %}</div>{% endif %}
{% if view[c]|length < buckets[c]|length %}<div class="warn">{{ view[c]|length }} 行だけ表示しています（全 {{ buckets[c]|length }} 行。馬・オッズ帯ごとに偏らないように選び、オッズ帯別の行を通常の行より先に出しています）。全件は <a href="{{ url_for('entries_wedge_csv', table=table, rid=rid, odds_mode='all') }}">WEDGE 一覧の CSV</a>（「区分」列で絞れます）</div>{% endif %}
<div class="scroll"><table><thead><tr><th>枠</th><th>馬番</th><th>馬名</th><th>騎手</th><th>指数</th><th>パターン</th><th>オッズ帯</th><th>地域</th><th>結合データ</th>
<th>件数</th><th>勝率_val</th><th>連対率_val</th><th>複勝率_val</th><th>掲示板率_val</th><th>勝率</th><th>連対率</th><th>複勝率</th>
<th>平均着順</th><th>単勝回収値</th><th>複勝回収値</th><th>単勝適正回収値</th><th>WEDGE</th><th>adjusted_WEDGE</th><th>年数</th></tr></thead><tbody>
{% for h in view[c] %}{% set dg = danger_by.get(h['馬番']|string) %}<tr{% if is_hot(h, c) %} class="hot"{% endif %}><td>{{ h['枠番'] }}</td><td>{{ h['馬番'] }}</td><td>{{ h['馬名'] }}{% if dg %} <span class="kd{{ ' caution' if dg == 'caution' else '' }}">{{ DANGER_LABELS[dg] }}</span>{% endif %}</td><td>{{ h['騎手'] }}</td>
<td>{{ h['base'] }}</td><td>{{ family_label(h['family'], h['rank_scheme']) }}</td><td>{{ odds_label(h['odds_mode']) }}</td><td>{{ h['region'] }}</td><td>{{ h['key'] }}</td>
<td class="num">{{ h['件数'] }}</td>{% for k in ['勝率_val','連対率_val','複勝率_val','掲示板率_val'] %}<td class="num">{{ fmt(h[k]) }}</td>{% endfor %}
<td>{{ h['勝率'] }}</td><td>{{ h['連対率'] }}</td><td>{{ h['複勝率'] }}</td>
{% for k in ['平均着順','単勝回収値','複勝回収値','単勝適正回収値','WEDGE','adjusted_WEDGE','adjusted_WEDGE_年数'] %}<td class="num">{{ fmt_cell(k, h.get(k)) }}</td>{% endfor %}</tr>
{% else %}<tr><td colspan="24">該当なし</td></tr>{% endfor %}</tbody></table></div></div>
{% endfor %}
"""
HITS_ORDER = ("abs", "axis", "keshi", "opp")
HITS_VIEW_LIMIT = 500        # 区分ごとに画面に出すおよその行数（全件は /entries/wedge.csv。馬ごとの該当数は全件で数える）


def _even_cap(sizes: list[int], limit: int) -> int:
    """組ごとの行数 sizes を、合計が limit に収まるように同じ上限 c で切るときの最大の c（どの組も最低1行は出す）。"""
    sizes = sorted(sizes)
    per, used = 1, 0
    for i, n in enumerate(sizes):
        c = (limit - used) // (len(sizes) - i)
        if c < n:
            return max(per, c)
        used += n
        per = n
    return sizes[-1] if sizes else 0


def spread_cap(rows: list[dict], limit: int) -> list[dict]:
    """表示を limit 行ほどに絞る。先頭から切ると後ろの馬・オッズ帯が丸ごと消えるので、
    (馬番, オッズ帯) ごとの行数の上限をそろえる（行の少ない組は全部出し、余った枠は多い組に回す。並びは元のまま）。
    枠はオッズ帯別（2〜10倍未満・10〜50倍・50倍以上）の組に先に配り、通常の組は残りの枠（どの組も最低1行は出す）。"""
    if len(rows) <= limit:
        return rows
    groups: dict[tuple, int] = {}
    for h in rows:
        g = (h.get("馬番"), h.get("odds_mode") or "normal")
        groups[g] = groups.get(g, 0) + 1
    sub = {g: n for g, n in groups.items() if g[1] != "normal"}
    normal = {g: n for g, n in groups.items() if g[1] == "normal"}
    cap_sub = _even_cap(list(sub.values()), max(limit - len(normal), len(sub))) if sub else 0
    used_sub = sum(min(n, cap_sub) for n in sub.values())
    cap_normal = _even_cap(list(normal.values()), max(limit - used_sub, len(normal))) if normal else 0
    used: dict[tuple, int] = {}
    out = []
    for h in rows:
        g = (h.get("馬番"), h.get("odds_mode") or "normal")
        if used.get(g, 0) < (cap_normal if g[1] == "normal" else cap_sub):
            used[g] = used.get(g, 0) + 1
            out.append(h)
    return out


def axis_cross(race: list[dict], buckets: dict[str, list[dict]], a: str = "", b: str = "", xmin: int = 2) -> list[dict]:
    """馬ごとに、絶対軸・軸候補の行がある指数を数える（指数クロス好走馬）。
    hit = その指数が xmin 個以上で、a・b を指定したときはその両方で軸に当たっている。
    inds の各指数には、同じ馬・同じ指数の 消し(2〜10倍未満) の行数 keshi_fav も付ける（軸と消しが同じ指数で出ている＝矛盾）。
    馬ごとの行数・消優勢の判定（horse_stats）も一緒に返す。"""
    per: dict[str, dict[str, dict]] = {}
    for cat in HOT_CATEGORIES:
        for h in buckets.get(cat, ()):
            d = per.setdefault(str(h.get("馬番")), {}).setdefault(h.get("base"), {"abs": 0, "axis": 0, "hot": False})
            d[cat] += 1
            d["hot"] = d["hot"] or is_hot(h, cat)
    stats = horse_stats(race, buckets)
    out = []
    for r in race:
        uma = str(r.get("馬番", ""))
        st = stats[uma]
        inds = [{"base": bname, **per.get(uma, {})[bname], "keshi_fav": st["keshi_fav_by"].get(bname, 0)}
                for bname in order_bases(per.get(uma, {}))]
        names = {t["base"] for t in inds}
        pair = (not a or a in names) and (not b or b in names)
        out.append({"uma": uma, "name": r.get("馬名", ""), "jockey": r.get("騎手", ""), "inds": inds, "n": len(inds),
                    "n_hot": sum(1 for t in inds if t["hot"]), "hit": len(inds) >= xmin and pair,
                    "n_clash": sum(1 for t in inds if t["keshi_fav"]), **{k: st[k] for k in
                    ("abs", "axis", "keshi", "opp", "hot", "axis_rows", "keshi_fav", "keshi_fav_inds", "danger")}})
    return out


app.jinja_env.globals.update(is_hot=is_hot, DANGER_LABELS=DANGER_LABELS)


def cut_low_request() -> bool:
    """過剰人気カット（cut_low_10u）の指定。既定は ON。フォームは hidden の 0 とチェックボックスの 1 を送るので、
    1 が1つでもあれば ON、引数があって 1 が無ければ OFF（チェックを外して送った・リンクの cut_low_10u=0）。"""
    vals = request.args.getlist("cut_low_10u")
    return True if not vals else any(v.strip() == "1" for v in vals)


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
        stale = xjoin_note(con, table, path)
        bases = order_bases(indicator_bases(table_columns(con, table)))
    if not race:
        return render("条件ヒット馬", '<div class="card">該当レースがありません。</div>')
    cut_low = cut_low_request()
    info: dict = {}
    buckets = classify_hits(hits, info, cut_low=cut_low)
    h = race[0]
    rid = h.get("レースID(新/馬番無)", rid)
    cross = cross_request()
    cross["a"], cross["b"] = (v if v in bases else "" for v in (cross["a"], cross["b"]))
    xs = axis_cross(race, buckets, cross["a"], cross["b"], cross["xmin"])
    cross_hits = sorted((x for x in xs if x["hit"]), key=lambda x: (-x["n"], -x["n_hot"], to_int(x["uma"])))
    hit_umas = {x["uma"] for x in cross_hits}
    summary = [{**x, "cross": x["uma"] in hit_umas} for x in xs]       # 馬ごとの該当数（指数クロスと同じ集計）
    danger_by = {x["uma"]: x["danger"] for x in xs if x["danger"]}
    shown = {c: [x for x in buckets[c] if not cross["only"] or str(x.get("馬番")) in hit_umas] for c in HITS_ORDER}
    head = f'{h.get("日付", "")} {h.get("場 R", "")} {h.get("レース名", "")}'
    sub = f'{h.get("場所", "")} / {h.get("芝・ダ", "")} / {h.get("距離_raw", "")} / {h.get("クラス名_raw", "")} / {len(race)}頭'
    keep = {"table": table, "rid": rid, "xmin": str(cross["xmin"]), "xa": cross["a"], "xb": cross["b"],
            "xonly": "1" if cross["only"] else ""}
    toggle = url_for("entries_hits", **{k: v for k, v in keep.items() if v}, cut_low_10u="0" if cut_low else "1")
    return render("条件ヒット馬", HITS_BODY, show_odds_note=True, xjoin_note=stale, head=head, sub=sub, path=path, buckets=shown,
                  order=HITS_ORDER, desc=description_lines(), summary=summary, table=table,
                  view={c: spread_cap(shown[c], HITS_VIEW_LIMIT) for c in HITS_ORDER}, rid=rid,
                  dropped=info.get("dropped", 0), diff=REDUNDANT_NORMAL_DIFF, cross=cross, cross_hits=cross_hits,
                  cross_bases=bases, cross_criteria=CROSS_CRITERIA, cross_crit=None,
                  cross_min_choices=min_choices(cross["xmin"]), cross_hidden=[("table", table), ("rid", rid)],
                  cut_low=cut_low, cut=info.get("cut", {}), cut_min=f"{CUT_MIN_RETURN:g}", cut_toggle=toggle,
                  danger_by=danger_by, n_dominant=sum(1 for x in xs if x["danger"] == "dominant"),
                  n_caution=sum(1 for x in xs if x["danger"] == "caution"))


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
<details class="filters"><summary>照合: {{ path }} ／ 閾値による足切りなし ／ WEDGE の見方を開く</summary>
<div class="muted">オッズ帯・指数・パターン・馬番の選択だけで絞ります（WEDGE の値では足切りしません）。
WEDGE = WIN + EDGE（1.00以上=トップ級、0.62前後=平均、0.50未満=下位）。adjusted_WEDGE は年度加重（空欄=条件を満たす年が無い）</div></details>
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
  <div><label>&nbsp;</label><button type="submit">表示</button></div>
</div>
<details class="filters" style="margin-top:6px"><summary>指数・パターンの絞り込みを開く（指数 {{ q_inds|length or 'すべて' }}{{ ' 個を選択中' if q_inds else '' }} ／ パターン {{ q_fams|length or 'すべて' }}{{ ' 個を選択中' if q_fams else '' }}）</summary>
<div style="margin-top:8px"><label>指数（未選択=すべて。全 {{ bases|length }}）</label><div class="cbs">{% for b in bases %}
  <label class="cb"><input type="checkbox" name="ind" value="{{ b }}" {{ 'checked' if b in q_inds else '' }}>{{ b }}</label>{% endfor %}</div></div>
<div style="margin-top:8px"><label>パターン（未選択=すべて。全 {{ families|length }}）</label><div class="cbs">{% for f in families %}
  <label class="cb"><input type="checkbox" name="family" value="{{ f }}" {{ 'checked' if f in q_fams else '' }}>{{ family_label(f) }}</label>{% endfor %}</div></div>
<div style="margin-top:10px"><button type="submit">この絞り込みで表示</button>
{% if q_inds or q_fams %} <a href="{{ clear_url }}" style="margin-left:10px">指数・パターンの選択を外す</a>{% endif %}</div></details></form></div>

<div class="card"><h1>馬 × 指数（該当キーの adjusted_WEDGE 最大 / WEDGE 最大・該当数）</h1>
<div class="scroll tall"><table><thead><tr><th>枠</th><th>馬番</th><th>馬名</th><th>騎手</th><th>該当数</th><th>adj 最大</th><th>adj 平均</th><th>WEDGE 最大</th><th>WEDGE 平均</th>
{% for b in matrix_bases %}<th>{{ b }}</th>{% endfor %}</tr></thead><tbody>
{% for m in matrix %}<tr><td>{{ m.waku }}</td><td>{{ m.uma }}</td><td>{{ m.name }}</td><td>{{ m.jockey }}</td><td class="num">{{ m.n }}</td>
<td class="cell {{ wedge_class(m.adj_max) }}">{{ fmt(m.adj_max, 3) }}</td><td class="cell">{{ fmt(m.adj_mean, 3) }}</td>
<td class="cell {{ wedge_class(m.wedge_max) }}">{{ fmt(m.wedge_max, 3) }}</td><td class="cell">{{ fmt(m.wedge_mean, 3) }}</td>
{% for b in matrix_bases %}{% set c = m.cells[b] %}<td class="cell {{ wedge_class(c.adj if c.adj is not none else c.wedge) }}">
{% if c.n %}{{ fmt(c.adj, 3) if c.adj is not none else '—' }}<div class="sub">{{ fmt(c.wedge, 3) }}・{{ c.n }}件</div>{% endif %}</td>{% endfor %}</tr>{% endfor %}
</tbody></table></div></div>

<div class="card"><h1>全該当キー（{{ total }}行{% if total > shown|length %}・画面は先頭 {{ shown|length }} 行。残りは CSV で{% endif %}）</h1>
<div class="scroll tall"><table><thead><tr><th>枠</th><th>馬番</th><th>馬名</th><th>騎手</th><th>指数</th><th>パターン</th><th>オッズ帯</th><th>地域</th><th>結合データ</th>
<th>件数</th><th>WIN</th><th>EDGE</th><th>WEDGE</th><th>adjusted_WEDGE</th><th>年数</th><th>年の件数計</th><th>最終年</th>
<th>勝率_val</th><th>連対率_val</th><th>複勝率_val</th><th>掲示板率_val</th><th>単勝回収値</th><th>複勝回収値</th></tr></thead><tbody>
{% for h in shown %}<tr><td>{{ h['枠番'] }}</td><td>{{ h['馬番'] }}</td><td>{{ h['馬名'] }}</td><td>{{ h['騎手'] }}</td>
<td>{{ h['base'] }}</td><td>{{ family_label(h['family'], h['rank_scheme']) }}</td><td>{{ odds_label(h['odds_mode']) }}</td><td>{{ h['region'] }}</td>
<td><a href="{{ url_for('entries_key', key=h['key']) }}" target="_blank">{{ h['key'] }}</a></td><td class="num">{{ h['件数'] }}</td>
{% for k in ['WIN','EDGE','WEDGE','adjusted_WEDGE'] %}<td class="num {{ wedge_class(h.get(k)) if k in ('WEDGE','adjusted_WEDGE') else '' }}">{{ fmt_cell(k, h.get(k)) }}</td>{% endfor %}
{% for k in ['adjusted_WEDGE_年数','adjusted_WEDGE_件数','adjusted_WEDGE_最終年','勝率_val','連対率_val','複勝率_val','掲示板率_val','単勝回収値','複勝回収値'] %}<td class="num">{{ fmt_cell(k, h.get(k)) }}</td>{% endfor %}</tr>
{% else %}<tr><td colspan="23">該当なし（この出馬表の馬の条件に合う集計行がありません）</td></tr>{% endfor %}</tbody></table></div></div>
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
        p["xjoin_note"] = xjoin_note(con, p["table"], path)
        bases = indicator_bases(table_columns(con, p["table"]))
    # connect() が DB の __families__ を台帳に反映したあとで、family の絞り込みを確かめる
    p["fams"] = [v for v in p["fams"] if v in fam_registry.active_names()]
    bases = order_bases(bases)
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
    return render("WEDGE一覧", WEDGE_BODY, show_odds_note=True, xjoin_note=p.get("xjoin_note", ""), head=head, sub=sub, path=path, table=p["table"], rid=rid, race=race,
                  bases=bases, families=fam_registry.active_names(), odds_modes=ODDS_MODES, sorts=WEDGE_SORTS,
                  q_odds=p["odds_mode"], q_inds=p["inds"], q_fams=p["fams"], q_uma=p["uma"], q_sort=p["sort"],
                  q_dir=p["dir"], matrix=wedge_matrix(matrix_race, rows, matrix_bases), matrix_bases=matrix_bases,
                  total=len(rows), shown=rows[:WEDGE_VIEW_LIMIT],
                  csv_url=url_for("entries_wedge_csv", **args),
                  clear_url=url_for("entries_wedge", **{k: v for k, v in args.items() if k not in ("ind", "family")}))


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
