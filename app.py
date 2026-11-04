# -*- coding: utf-8 -*-
"""Flask UI。

  /              横断検索（集計表 foragg_* を family・指数・オッズ帯・地域・コース条件で絞り込み）
  /entries       出馬表のレース一覧（日付・場所で絞り込み）
  /entries/race  出馬表（各馬の指数順位・偏差値）
  /entries/key   結合データの前方一致で unified_all を表示
  /entries/hits  条件ヒット馬（絶対軸・軸候補・消し候補・相手候補）
  /healthz       DB 接続確認

条件ヒット馬は entries_xjoin_*（build_db が事前計算した照合結果）があればそれを読み、
無ければ同じ照合関数（shisu.matching）でその場で照合する。分類は shisu.classify だけで行う。
DB の率はすべて 0–100（%）。
"""
from __future__ import annotations

import os
import re
import sqlite3

from flask import Flask, redirect, render_template_string, request, url_for

from shisu import config
from shisu.classify import CATEGORIES, CATEGORY_LABELS, classify, description_lines, sql_condition
from shisu.indicators import INDICATOR_ORDER
from shisu.keys import ALL_FAMILIES, FAMILY_LABELS
from shisu.matching import UNIFIED, indicator_bases, match_entry_row
from shisu.naming import ENTRIES_ALL, ENTRIES_PREFIX, XJOIN_PREFIX, xjoin_table_for
from shisu.odds import ODDS_LABELS, ODDS_MODES, ODDS_RANK

DB_PATH = str(config.db_path())
META = "__tables_meta__"
FACETS = "__facets__"
MAX_TABLES = 100       # 横断検索で一度に表示する集計表の上限（超えたら絞り込みを促す）
ROWS_PER_TABLE = 200
SORTABLE = ("件数", "勝率_val", "連対率_val", "複勝率_val", "2着率_val", "3着率_val", "掲示板率_val",
            "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値")
DISPLAY_ORDER = ("結合データ", "件数", "勝率_val", "勝率", "連対率_val", "連対率", "複勝率_val", "複勝率",
                 "2着率_val", "2着率", "3着率_val", "3着率", "掲示板率_val", "掲示板率",
                 "平均着順", "単勝回収値", "複勝回収値", "単勝適正回収値",
                 "提案勝率", "提案連対率", "提案複勝率", "提案2着率", "提案3着率",
                 "場所", "芝・ダ", "距離", "芝(内・外)")
PRESETS = ("none",) + tuple(c for c in ("abs", "axis", "opp", "keshi"))

app = Flask(__name__)


# ========================= 共通 =========================
def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con


def q(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def table_exists(con, name: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


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


def to_int(s, default: int = 1_000_000) -> int:
    m = re.match(r"\d+", str(s or ""))
    return int(m.group()) if m else default


def odds_label(mode: str) -> str:
    return ODDS_LABELS.get(mode or "normal", mode or "")


def family_label(fam: str, scheme: str = "") -> str:
    label = FAMILY_LABELS.get(fam, fam)
    return f"{label}（{scheme}）" if scheme else label


app.jinja_env.globals.update(fmt=fmt, odds_label=odds_label, family_label=family_label,
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
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px}
.cbs{display:flex;flex-wrap:wrap;gap:6px;margin-top:4px}
.cb{display:inline-flex;align-items:center;gap:4px;font-size:12px;background:#f8fafc;border:1px solid #e5e7eb;border-radius:8px;padding:4px 8px}
button{padding:8px 16px;border:0;background:#2563eb;color:#fff;border-radius:8px;cursor:pointer}
table{border-collapse:collapse;background:#fff;width:100%}
th,td{padding:6px 8px;border-bottom:1px solid #f1f5f9;font-size:12px;text-align:left;white-space:nowrap;vertical-align:top}
th{background:#f8fafc;color:#334155;position:sticky;top:0}
td.num{text-align:right}
.muted{color:#6b7280;font-size:12px}
.warn{color:#b45309;font-size:13px}
.tag{display:inline-block;font-size:11px;background:#e5e7eb;padding:2px 6px;border-radius:999px;margin-left:4px}
.nav a{display:inline-block;margin-right:8px;padding:6px 10px;background:#e5e7eb;border-radius:8px;color:#111827;text-decoration:none}
a{color:#2563eb;text-decoration:none} a:hover{text-decoration:underline}
.cat-abs h1{color:#7c2d12} .cat-axis h1{color:#1d4ed8} .cat-keshi h1{color:#6b7280} .cat-opp h1{color:#047857}
</style></head><body><header>{{ title }}</header><div class="container">
<div class="nav"><a href="{{ url_for('index') }}">横断検索</a><a href="{{ url_for('entries_index') }}">出馬表</a></div>
{BODY}
</div></body></html>"""


def render(title: str, body: str, **ctx):
    return render_template_string(LAYOUT.replace("{BODY}", body), title=title, **ctx)


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
  <div style="margin-top:12px"><button type="submit">検索</button>
    <span class="muted">並び替え: {{ q_sort }} {{ q_dir }}（列見出しの *_val などをクリック）</span></div>
  <input type="hidden" name="sort" value="{{ q_sort }}"><input type="hidden" name="sortdir" value="{{ q_dir }}">
  <input type="hidden" name="submitted" value="1">
</form></div>
{% if not has_db %}<div class="card warn">DB にテーブルがありません。build_db.py を実行してください。</div>{% endif %}
{% if submitted %}
  {% if truncated %}<div class="card warn">該当する集計表が {{ n_tables }} 個あります。先頭 {{ max_tables }} 個だけ表示しています。条件を絞ってください。</div>{% endif %}
  {% for r in results %}
    <div class="card"><h2>{{ family_label(r.family, r.rank_scheme) }}<span class="tag">{{ r.indicator }}</span><span class="tag">{{ odds_label(r.odds_mode) }}</span><span class="tag">{{ r.region }}</span></h2>
    <div class="muted">{{ r.table }}</div>
    <table><thead><tr>{% for c in r.columns %}
      {% if c in sortable %}<th><a href="{{ r.sort_links[c] }}">{{ c }}{% if c == q_sort %} {{ '▼' if q_dir == 'DESC' else '▲' }}{% endif %}</a></th>{% else %}<th>{{ c }}</th>{% endif %}
    {% endfor %}</tr></thead><tbody>
      {% for row in r.rows %}<tr>{% for c in r.columns %}<td>{{ fmt(row[c]) }}</td>{% endfor %}</tr>{% endfor %}
    </tbody></table></div>
  {% else %}<div class="card">該当する行はありませんでした。</div>{% endfor %}
{% else %}<div class="card muted">条件を選んで「検索」を押してください（初期表示では一覧を描画しません）。</div>{% endif %}
"""


def _facets(con) -> dict[str, list[str]]:
    out = {c: [] for c in ("場所", "芝・ダ", "距離", "芝(内・外)")}
    if table_exists(con, FACETS):
        for r in con.execute(f"SELECT col, val FROM {FACETS} ORDER BY col, val"):
            out.setdefault(r[0], []).append(r[1])
    return out


def _meta_values(con, col: str) -> list[str]:
    if not table_exists(con, META):
        return []
    return [r[0] for r in con.execute(
        f"SELECT DISTINCT {q(col)} FROM {META} WHERE kind='foragg' AND {q(col)} IS NOT NULL AND {q(col)} <> ''")]


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
    q_sort = a.get("sort", "複勝率_val")
    if q_sort not in SORTABLE:
        q_sort = "複勝率_val"
    q_dir = "ASC" if a.get("sortdir", "DESC").upper() == "ASC" else "DESC"

    with connect() as con:
        has_db = table_exists(con, META) and table_exists(con, UNIFIED)
        facets = _facets(con)
        families = [f for f in ALL_FAMILIES if f in set(_meta_values(con, "family"))]
        inds_all = set(_meta_values(con, "indicator"))
        indicators = [i for i in INDICATOR_ORDER if i in inds_all] + sorted(inds_all - set(INDICATOR_ORDER))
        regions = sorted(_meta_values(con, "region"))
        selects = [("place", "場所", facets.get("場所", []), place), ("sd", "芝・ダ", facets.get("芝・ダ", []), sd),
                   ("dist", "距離", facets.get("距離", []), dist), ("inout", "芝(内・外)", facets.get("芝(内・外)", []), inout),
                   ("region", "地域（元ファイル）", regions, region)]
        results, n_tables, truncated = [], 0, False
        if submitted and has_db:
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
            metas = [dict(r) for r in con.execute(f"SELECT * FROM {META} WHERE {' AND '.join(where)}", params)]
            ind_rank = {n: i for i, n in enumerate(INDICATOR_ORDER)}
            fam_rank = {f: i for i, f in enumerate(ALL_FAMILIES)}
            metas.sort(key=lambda m: (ind_rank.get(m["indicator"], 999), m["indicator"] or "",
                                      fam_rank.get(m["family"], 999), m["rank_scheme"] or "",
                                      ODDS_RANK.get(m["odds_mode"], 9), m["region"] or ""))
            n_tables = len(metas)
            truncated = n_tables > MAX_TABLES
            for m in metas[:MAX_TABLES]:
                t = m["table_name"]
                if not table_exists(con, t):
                    continue
                cols_in = set(table_columns(con, t))
                show = [c for c in DISPLAY_ORDER if c in cols_in]
                w, p = [], []
                for col, val in (("場所", place), ("芝・ダ", sd), ("距離", dist), ("芝(内・外)", inout)):
                    if val:
                        w.append(f"{q(col)} = ?")
                        p.append(val)
                if q_preset != "none":
                    w.append(sql_condition(q_preset, m["odds_mode"]))
                sql = (f"SELECT {', '.join(q(c) for c in show)} FROM {q(t)} "
                       f"{'WHERE ' + ' AND '.join(w) if w else ''} "
                       f"ORDER BY {q(q_sort)} {q_dir} NULLS LAST LIMIT {ROWS_PER_TABLE}"
                       if q_sort in cols_in else
                       f"SELECT {', '.join(q(c) for c in show)} FROM {q(t)} "
                       f"{'WHERE ' + ' AND '.join(w) if w else ''} LIMIT {ROWS_PER_TABLE}")
                rows = [dict(r) for r in con.execute(sql, p)]
                if not rows:
                    continue
                links = {c: _sort_link(c, q_sort, q_dir) for c in show if c in SORTABLE}
                results.append({"table": t, "family": m["family"], "rank_scheme": m["rank_scheme"],
                                "indicator": m["indicator"], "odds_mode": m["odds_mode"], "region": m["region"],
                                "columns": show, "rows": rows, "sort_links": links})

    return render("指数集計 横断検索", INDEX_BODY, selects=selects, families=families, indicators=indicators,
                  odds_modes=ODDS_MODES, presets=PRESETS, q_families=q_families, q_inds=q_inds,
                  q_odds=q_odds, q_preset=q_preset, q_sort=q_sort, q_dir=q_dir, submitted=submitted,
                  results=results, sortable=SORTABLE, has_db=has_db, n_tables=n_tables,
                  truncated=truncated, max_tables=MAX_TABLES)


def _sort_link(col: str, cur: str, cur_dir: str) -> str:
    args = request.args.to_dict(flat=False)
    args["sort"] = [col]
    args["sortdir"] = ["ASC" if (col == cur and cur_dir == "DESC") else "DESC"]
    args["submitted"] = ["1"]
    return url_for("index", **args)


# ========================= 出馬表 =========================
def entries_tables(con) -> list[str]:
    if table_exists(con, META):
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
<thead><tr><th>日付</th><th>場 R</th><th>レース名</th><th>クラス</th><th>芝・ダ</th><th>距離</th><th>頭数</th><th>出馬表</th><th>条件ヒット馬</th></tr></thead>
<tbody>{% for r in races %}<tr>
  <td>{{ r.date }}</td><td>{{ r.jo }}</td><td>{{ r.name }}</td><td>{{ r.cls }}</td><td>{{ r.sd }}</td><td>{{ r.dist }}</td><td>{{ r.heads }}</td>
  <td><a href="{{ url_for('entries_race', table=q_table, rid=r.rid) }}">出馬表</a></td>
  <td><a href="{{ url_for('entries_hits', table=q_table, rid=r.rid) }}" target="_blank">条件ヒット馬</a></td>
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
    return render("出馬表", ENTRIES_BODY, tables=tables, q_table=q_table, dates=dates, q_date=q_date,
                  places=places, q_place=q_place, races=races)


def _race_rows(con, table: str, rid: str, legacy_ridn: str = "", date: str = "") -> list[dict]:
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
<div style="margin-top:6px"><a href="{{ url_for('entries_hits', table=table, rid=rid) }}" target="_blank">条件ヒット馬を見る</a></div></div>
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
<th>勝率_val</th><th>連対率_val</th><th>複勝率_val</th><th>掲示板率_val</th><th>平均着順</th><th>単勝回収値</th><th>複勝回収値</th><th>単勝適正回収値</th></tr></thead>
<tbody>{% for r in rows %}<tr><td>{{ r.indicator }}</td><td>{{ family_label(r.family, r.rank_scheme) }}</td><td>{{ odds_label(r.odds_mode) }}</td><td>{{ r.region }}</td>
<td>{{ r['結合データ'] }}</td><td class="num">{{ r['件数'] }}</td>
{% for c in ['勝率_val','連対率_val','複勝率_val','掲示板率_val','平均着順','単勝回収値','複勝回収値','単勝適正回収値'] %}<td class="num">{{ fmt(r[c]) }}</td>{% endfor %}</tr>
{% else %}<tr><td colspan="14">該当なし</td></tr>{% endfor %}</tbody></table></div>
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
    xname = xjoin_table_for(table)
    if table_exists(con, xname):
        hits = [dict(r) for r in con.execute(f'SELECT * FROM {q(xname)} WHERE "レースID(新/馬番無)" = ?', (rid,))]
        return hits, "xjoin"
    if not table_exists(con, UNIFIED):
        return [], "unified_all なし"
    bases = indicator_bases(race_rows[0].keys())
    cache: dict = {}
    hits: list[dict] = []
    for r in race_rows:
        hits.extend(match_entry_row(con, r, bases, table, cache=cache))
    return hits, "on-the-fly"


def classify_hits(hits: list[dict]) -> dict[str, list[dict]]:
    buckets: dict[str, list[dict]] = {c: [] for c in CATEGORIES}
    seen: set = set()
    for h in hits:
        ident = (h.get("馬番"), h.get("base"), h.get("family"), h.get("key"), h.get("テーブル名"))
        if ident in seen:
            continue
        seen.add(ident)
        cat = classify({"cnt": h.get("件数"), "fv": h.get("複勝率_val"), "rv": h.get("連対率_val"),
                        "top5": h.get("掲示板率_val"), "frc": h.get("複勝回収値"),
                        "tra": h.get("単勝適正回収値")}, h.get("odds_mode") or "normal")
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
<div class="muted">照合: {{ path }} ／ 率は DB の推定値（複勝率・連対率）と頻度（掲示板率）</div></div>
<div class="card"><h1>馬ごとの該当数</h1><table><thead><tr><th>馬番</th><th>馬名</th>
{% for c in order %}<th>{{ CATEGORY_LABELS[c] }}</th>{% endfor %}</tr></thead><tbody>
{% for s in summary %}<tr><td>{{ s.uma }}</td><td>{{ s.name }}</td>{% for c in order %}<td class="num">{{ s[c] or '' }}</td>{% endfor %}</tr>{% endfor %}
</tbody></table></div>
{% for c in order %}
<div class="card cat-{{ c }}"><h1>{{ CATEGORY_LABELS[c] }}（{{ buckets[c]|length }}）</h1>
<div class="muted">{% for line in desc[c] %}• {{ line }}<br>{% endfor %}</div>
<table><thead><tr><th>枠</th><th>馬番</th><th>馬名</th><th>騎手</th><th>指数</th><th>パターン</th><th>オッズ帯</th><th>地域</th><th>結合データ</th>
<th>件数</th><th>勝率_val</th><th>連対率_val</th><th>複勝率_val</th><th>掲示板率_val</th><th>勝率</th><th>連対率</th><th>複勝率</th>
<th>平均着順</th><th>単勝回収値</th><th>複勝回収値</th><th>単勝適正回収値</th></tr></thead><tbody>
{% for h in buckets[c] %}<tr><td>{{ h['枠番'] }}</td><td>{{ h['馬番'] }}</td><td>{{ h['馬名'] }}</td><td>{{ h['騎手'] }}</td>
<td>{{ h['base'] }}</td><td>{{ family_label(h['family'], h['rank_scheme']) }}</td><td>{{ odds_label(h['odds_mode']) }}</td><td>{{ h['region'] }}</td><td>{{ h['key'] }}</td>
<td class="num">{{ h['件数'] }}</td>{% for k in ['勝率_val','連対率_val','複勝率_val','掲示板率_val'] %}<td class="num">{{ fmt(h[k]) }}</td>{% endfor %}
<td>{{ h['勝率'] }}</td><td>{{ h['連対率'] }}</td><td>{{ h['複勝率'] }}</td>
{% for k in ['平均着順','単勝回収値','複勝回収値','単勝適正回収値'] %}<td class="num">{{ fmt(h[k]) }}</td>{% endfor %}</tr>
{% else %}<tr><td colspan="21">該当なし</td></tr>{% endfor %}</tbody></table></div>
{% endfor %}
"""
HITS_ORDER = ("abs", "axis", "keshi", "opp")


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
                  order=HITS_ORDER, desc=description_lines(), summary=summary)


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
