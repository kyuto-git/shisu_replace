"""指数取得（shisu/ingest・shisu/yasui_extractor・/ingest 画面）のテスト。ネットワークには出ない。

各サイトの HTML は、旧スクレイパーの解析コードが前提にしている構造（keibaAI は旧 Readme に残っていた
実ページの断片）をまねた合成のもの。
"""
from __future__ import annotations

import io
import threading
import zipfile
from pathlib import Path

import pandas as pd
import pytest

from shisu.csvio import read_csv_any
from shisu.ingest import IngestRequest, common, ittai, kayochin, keibaai, kichiuma, lap, omega, run_ingest, validate

# ---------------------------------------------------------------- ベースCSV
# 2025-11-01 東京(05) 1R・2R（各3頭）、2025-11-02 中山(06) 11R（2頭）、2025-11-08 小倉(10) 1R（2頭）
BASE_ROWS = [
    ("202511010504010101", "20251101", "1", "1"), ("202511010504010102", "20251101", "2", "2"),
    ("202511010504010103", "20251101", "3", "3"),
    ("202511010504010201", "20251101", "1", "1"), ("202511010504010202", "20251101", "2", "2"),
    ("202511010504010203", "20251101", "3", "3"),
    ("202511020605021101", "20251102", "1", "1"), ("202511020605021102", "20251102", "2", "2"),
    ("202511081003010101", "20251108", "1", "1"), ("202511081003010102", "20251108", "1", "2"),
]


def base_frame() -> pd.DataFrame:
    return pd.DataFrame([{"レースID(新)": r, "日付S": d, "枠番": w, "馬番": u, "ワーク1": ""} for r, d, w, u in BASE_ROWS])


def write_base(path: Path, df: pd.DataFrame | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((base_frame() if df is None else df).to_csv(index=False).encode("cp932"))
    return path


class Resp:
    def __init__(self, status=200, content=b"", encoding="utf-8"):
        self.status_code = status
        self.content = content
        self.encoding = encoding
        self.apparent_encoding = encoding
        self.text = content.decode(encoding, errors="replace")


class FakeSession:
    """URL → Resp（または例外）。呼ばれた URL を記録する。"""

    def __init__(self, pages: dict):
        self.pages = pages
        self.calls: list[str] = []

    def get(self, url, headers=None, timeout=None):
        self.calls.append(url)
        v = self.pages.get(url)
        if v is None:
            return Resp(404)
        if isinstance(v, Exception):
            raise v
        if isinstance(v, Resp):
            return v
        return Resp(200, v.encode("utf-8"))


def fast_fetcher(pages, **kw):
    s = FakeSession(pages)
    return common.Fetcher(interval=kw.pop("interval", 2.0), session=s, sleep=lambda _: None, log=lambda m: None,
                          **kw), s


def out_csv(path: Path) -> pd.DataFrame:
    return read_csv_any(path)


# ---------------------------------------------------------------- common
def test_rid_helpers():
    assert common.clean_rid("202511010504010101.0") == "202511010504010101"
    assert common.clean_rid("２０２５１１０１０５０４０１０１０１") == "202511010504010101"
    rid = "202511010504010203"
    assert common.rid12(rid) == "202511010502"
    assert common.key14(rid) == "20251101050203"
    assert common.bid16(rid) == "2025110105040102"
    assert common.key14("2025") == "" and common.int_text("01") == "1" and common.int_text("x") == ""
    assert common.ymd_text("2025/11/1") == "20251101" and common.ymd_text("20251101.0") == "20251101"


def test_read_base_and_write_outputs(tmp_path):
    df = base_frame()
    df.loc[0, "日付S"] = ""                                    # 日付S が空 → レースIDの先頭8桁
    df.loc[1, "レースID(新)"] = "123"                          # 18桁でない → 照合されない
    a = write_base(tmp_path / "in" / "a.csv", df)
    b = write_base(tmp_path / "in" / "b.csv", base_frame().iloc[:3])   # a と重なる行
    logs = []
    base = common.read_base([a, b], logs.append)
    assert len(base) == len(BASE_ROWS) + 3 and base.loc[0, "日付S"] == "20251101"
    assert any("18桁でない行が 1 行" in m for m in logs)
    vals = base["レースID(新)"].map(lambda r: "55.5" if r.endswith("01") else "")
    res = common.write_outputs(base, vals, tmp_path / "out", "x", logs.append)
    merged = out_csv(tmp_path / "out" / "a_merged.csv")
    assert len(merged) == len(BASE_ROWS) and "_source_file" not in merged.columns     # 行は増えない
    day = out_csv(tmp_path / "out" / "20251101_x.csv")
    assert list(day.columns) == ["レースID(新)", "ワーク1"]
    assert not day["レースID(新)"].duplicated().any()               # 2本のベースが重なっても1行
    assert day.set_index("レースID(新)").loc["202511010504010101", "ワーク1"] == "55.5"
    assert {p.name for p in res.files} >= {"a_merged.csv", "b_merged.csv", "20251101_x.csv", "20251102_x.csv"}
    assert (tmp_path / "out" / "20251101_x.csv").read_bytes().decode("cp932")


def test_decode_prefers_declared_charset():
    text = "<th>指数</th>髙"
    sj = text.encode("cp932")
    assert common._decode(Resp(200, sj, "Shift_JIS"), "utf-8") == text            # 宣言（Shift_JIS 系 → cp932）
    assert common._decode(Resp(200, text.encode("utf-8"), "UTF-8"), "cp932") == text   # サイトが UTF-8 に変わっても読める
    r = Resp(200, sj, "cp932")
    r.encoding = "ISO-8859-1"                                                        # charset なし → fallback
    assert common._decode(r, "cp932") == text


def test_read_base_same_name_files_stay_separate(tmp_path):
    a = write_base(tmp_path / "dayA" / "base.csv", base_frame().iloc[:2])
    b = write_base(tmp_path / "dayB" / "base.csv", base_frame().iloc[6:8])
    base = common.read_base([a, b], lambda m: None)
    assert base["_source_file"].tolist() == ["base.csv"] * 2 + ["base_2.csv"] * 2
    common.write_outputs(base, pd.Series("", index=base.index), tmp_path / "o", "x", lambda m: None)
    assert len(out_csv(tmp_path / "o" / "base_merged.csv")) == 2 and len(out_csv(tmp_path / "o" / "base_2_merged.csv")) == 2


def test_lap_duplicate_rows_across_files(tmp_path):
    a, b = tmp_path / "a.csv", tmp_path / "b.csv"
    a.write_bytes(lap_rows().iloc[:2].to_csv(index=False).encode("cp932"))
    b.write_bytes(lap_rows().iloc[1:2].to_csv(index=False).encode("cp932"))
    logs = []
    lap.run([a, b], tmp_path / "o", logs.append, today="20261105")
    assert len(out_csv(tmp_path / "o" / "2F" / "20261012_2F.csv")) == 2 and any("同じ レースID(新)" in m for m in logs)


def test_fetcher_interval_retry_404_and_cancel():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    s = FakeSession({"u1": "a", "u2": "b", "boom": RuntimeError("x"), "five": Resp(500)})
    f = common.Fetcher(interval=2.0, session=s, sleep=sleep, clock=lambda: now[0], log=lambda m: None, retries=2)
    assert f.get("u1") == "a" and slept == []                  # 1回目は待たない
    assert f.get("u2") == "b" and sum(slept) == pytest.approx(2.0)
    slept.clear()
    assert f.get("missing") is None and s.calls.count("missing") == 1      # 404 はやり直さない
    assert f.get("five") is None and s.calls.count("five") == 3            # 5xx は2回やり直す
    assert f.get("boom") is None and s.calls.count("boom") == 3
    assert sum(slept) >= 2.0 * 3                                         # やり直しは待ちを伸ばす
    stop = threading.Event()
    stop.set()
    f2 = common.Fetcher(interval=0, session=s, sleep=sleep, log=lambda m: None, stop=stop)
    with pytest.raises(common.Cancelled):
        f2.get("u1")


# ---------------------------------------------------------------- ① Ω指数
def omega_html(rows):
    body = "".join(f"<tr><td>{w}</td><td>{u}</td><td>{n}</td><td>牡3</td><td>{v}</td></tr>" for w, u, n, v in rows)
    return ("<html><body><table><tr><th>x</th></tr><tr><td>1</td></tr></table>"
            "<table><thead><tr><th>枠</th><th>馬番</th><th>馬名</th><th>性齢</th><th>Ω指数</th></tr></thead>"
            f"<tbody>{body}</tbody></table></body></html>")


def test_omega_parse_and_run(tmp_path):
    html = omega_html([(1, 1, "アア", "98.5"), (2, 2, "イイ", "-"), (3, 3, "ウウ", "101")])
    assert omega.parse(html) == [{"枠": "1", "馬番": "1", "馬名": "アア", "値": "98.5"},
                                 {"枠": "3", "馬番": "3", "馬名": "ウウ", "値": "101"}]
    base = common.read_base([write_base(tmp_path / "base.csv")], lambda m: None)
    pages = {omega.race_url("202511010501"): omega_html([(1, 1, "a", "90"), (2, 2, "b", "80"), (3, 3, "c", "70"),
                                                         (3, 3, "c", "70")]),       # 同じ馬が2回 → 行は増えない
             omega.race_url("202511010502"): omega_html([(1, 1, "a", "60"), (9, 2, "b", "50")]),   # 枠違い → 照合しない
             omega.race_url("202511020611"): omega_html([(1, 1, "a", "40")])}
    f, s = fast_fetcher(pages)
    res = omega.run(base, f, tmp_path / "omega", lambda m: None)
    assert s.calls == [omega.race_url(r) for r in ("202511010501", "202511010502", "202511020611", "202511081001")]
    day = out_csv(tmp_path / "omega" / "20251101_omega.csv").set_index("レースID(新)")["ワーク1"]
    assert day.to_dict() == {"202511010504010101": "90", "202511010504010102": "80", "202511010504010103": "70",
                             "202511010504010201": "60", "202511010504010202": "", "202511010504010203": ""}
    assert res.matched == 5 and len(out_csv(tmp_path / "omega" / "base_merged.csv")) == len(BASE_ROWS)


# ---------------------------------------------------------------- ② 吉馬
def kichiuma_html(rows, wrap=True):
    head = ('<tr><th>枠</th><th>馬</th><th>競　走　馬　名</th><th>SP能力値</th><th>先行力</th>'
            '<th colspan="2">SP信頼</th><th colspan="2">SP調整</th><th colspan="2">SP最大</th><th colspan="2">末脚力</th></tr>')
    body = ""
    for u, name, vals in rows:
        a, b, c, d, e, f = vals
        body += (f"<tr><td>1</td><td>{u}</td><td>{name}</td><td>{a}</td><td>{b}</td>"
                 f"<td>A</td><td>{c}</td><td>B</td><td>{d}</td><td>C</td><td>{e}</td><td>D</td><td>{f}</td></tr>")
    table = f"<table>{head}{body}</table>"
    return f"<html><body><table><tr><td>dummy</td></tr></table>{'<div id=fp_form>' + table + '</div>' if wrap else table}</body></html>"


def test_kichiuma_url_and_parse():
    assert kichiuma.race_url("202601050601010110") == \
        "https://kichiuma.net/php/search.php?race_id=202601050176&date=2026%2F1%2F5&no=1&id=76&p=fp"
    assert "id=80" in kichiuma.race_url("202601171001020905") and "no=9" in kichiuma.race_url("202601171001020905")
    rows = kichiuma.parse(kichiuma_html([(1, "アア", ("55", "48", "60", "-3", "71", "35.1")),
                                         (2, "イイ", ("50", "", "58", "2", "66", "34.0"))]))
    assert rows[0] == {"馬番": "1", "馬名": "アア", "sp_ability": "55", "senkou": "48", "sp_trust": "60",
                       "sp_control": "-3", "sp_max": "71", "sueashi": "35.1"}
    assert rows[1]["senkou"] == ""
    assert kichiuma.parse(kichiuma_html([(1, "ア", ("1",) * 6)], wrap=False))[0]["sp_max"] == "1"   # fp_form 無し
    assert kichiuma.parse("<table><tr><th>馬</th></tr><tr><td>1</td></tr></table>") == []


def test_kichiuma_run(tmp_path):
    base = common.read_base([write_base(tmp_path / "base.csv")], lambda m: None)
    pages = {kichiuma.race_url("202511010504010101"): kichiuma_html([(1, "a", ("1", "2", "3", "4", "5", "6")),
                                                                     (3, "c", ("7", "8", "9", "10", "11", "12"))])}
    f, s = fast_fetcher(pages)
    kichiuma.run(base, f, tmp_path / "k", lambda m: None)
    assert len(s.calls) == 4 and s.calls[0] == kichiuma.race_url("202511010504010101")   # 1レース1回
    for key, want in (("sp_ability", ("1", "", "7")), ("sueashi", ("6", "", "12"))):
        df = out_csv(tmp_path / "k" / f"kichiuma_{key}" / f"20251101_kichiuma_{key}.csv")
        assert tuple(df["ワーク1"].iloc[:3]) == want
    assert (tmp_path / "k" / "kichiuma_senkou" / "base_merged.csv").exists()


# ---------------------------------------------------------------- ③ かよちん
def kayochin_html(races):
    rows = ""
    for r, pairs in races.items():
        circ = "".join(f"<td>{chr(0x2460 + h - 1) if h else ''}</td>" for h, _ in pairs)
        vals = "".join(f"<td>{v}</td>" for _, v in pairs)
        rows += f"<tr><td>{r}R</td>{circ}</tr><tr><td>指</td>{vals}</tr><tr><td>印</td><td>◎</td></tr>"
    return f"<html><body><table>{rows}</table></body></html>"


def test_kayochin_key_parse_and_run(tmp_path):
    assert kayochin.page_key("202511020605021101") == "251102nakayama"
    assert kayochin.page_key("202511020705021101") == "251102cyuukyo"
    assert kayochin.page_key("202511020805021101") == "251102kyouto"
    data = kayochin.parse(kayochin_html({1: [(1, "52"), (2, ""), (3, "47")], 12: [(10, "-3")]}))
    assert data == {1: [(1, 52), (3, 47)], 12: [(10, -3)]}               # 空欄の位置で対がずれない
    html = kayochin_html({11: [(1, "50")]}).replace("<td>11R</td>", "<td>11R 15:25 芝1600m</td>")
    assert kayochin.parse(html) == {11: [(1, 50)]}                        # 旧版と同じ前方一致
    base = common.read_base([write_base(tmp_path / "base.csv")], lambda m: None)
    pages = {kayochin.page_url("251101tokyo"): kayochin_html({1: [(1, "52"), (2, "48"), (3, "47")], 2: [(3, "60")]})}
    f, s = fast_fetcher(pages)
    kayochin.run(base, f, tmp_path / "ka", lambda m: None)
    assert s.calls == [kayochin.page_url(k) for k in ("251101tokyo", "251102nakayama", "251108kokura")]
    day = out_csv(tmp_path / "ka" / "20251101_kayochin.csv")["ワーク1"].tolist()
    assert day == ["52", "48", "47", "", "", "60"]


# ---------------------------------------------------------------- ④ keibaAI
KEIBAAI_B = ('<html><head><meta name="description" content="【高回収率】過去10年統計データで分析！2025年11月1日東京競馬場 2R2歳未勝利">'
             '<title>x</title></head><body><table class="race_table"><thead><tr><th>馬番</th><th>馬名</th><th>人気</th>'
             '<th class="predict_head"><strong>AI予想</strong></th></tr></thead><tbody>'
             '<tr><td><p class="umaban_wrap waku_1">3</p></td><td>a</td><td>1</td><td><p class="predict_wrap predict_1">'
             '<span class="mark">◎</span><span class="predict">74.9</span></p></td></tr>'
             '<tr><td><p class="umaban_wrap waku_1">1</p></td><td>b</td><td>2</td><td><p class="predict_wrap predict_2">'
             '<span class="mark">○</span><span class="predict">73.5</span></p></td></tr></tbody></table></body></html>')
KEIBAAI_A = ('<html><head><title>「2歳未勝利」の無料AI競馬予想 2025年11月2日 中山競馬場 11R</title></head><body>'
             '<table><tr><th>馬番</th><th>馬名</th><th>AI 予想</th></tr>'
             '<tr><td>1</td><td>a</td><td><div class="mark">◎</div><div class="predict">61.0</div></td></tr>'
             '<tr><td>2</td><td>b</td><td><div class="predict">58.2</div></td></tr></table></body></html>')


def test_keibaai_parse_patterns():
    b = keibaai.parse(KEIBAAI_B)
    assert b == {"date": "20251101", "place": "05", "race": "02", "rows": [("03", "74.9"), ("01", "73.5")]}
    a = keibaai.parse(KEIBAAI_A)
    assert a == {"date": "20251102", "place": "06", "race": "11", "rows": [("01", "61.0"), ("02", "58.2")]}
    assert keibaai.parse("<html><title>no info</title></html>") is None


def test_keibaai_run(tmp_path):
    base = common.read_base([write_base(tmp_path / "base.csv")], lambda m: None)
    pages = {keibaai.page_url(100): KEIBAAI_B, keibaai.page_url(101): KEIBAAI_A, keibaai.page_url(103): KEIBAAI_B}
    f, s = fast_fetcher(pages)
    keibaai.run(base, f, tmp_path / "ai", 100, 103, lambda m: None)
    assert s.calls == [keibaai.page_url(i) for i in range(100, 104)]
    d1 = out_csv(tmp_path / "ai" / "20251101_keibaAI.csv").set_index("レースID(新)")["ワーク1"]
    assert d1["202511010504010203"] == "74.9" and d1["202511010504010201"] == "73.5" and d1["202511010504010101"] == ""
    assert not d1.index.duplicated().any()
    d2 = out_csv(tmp_path / "ai" / "20251102_keibaAI.csv")["ワーク1"].tolist()
    assert d2 == ["61.0", "58.2"]
    with pytest.raises(ValueError):
        keibaai.run(base, f, tmp_path / "ai", 5, 4)


# ---------------------------------------------------------------- ⑤ 一隊
def ittai_bytes(rows):
    body = "".join(f'<tr><td>{i}</td><td><span class="badge">{u}</span></td><td>x</td><td><a href="#">{n}</a></td>'
                   f'<td>..</td><td><strong>{v}</strong></td></tr>' for i, (u, n, v) in enumerate(rows, 1))
    html = ('<html><head><meta charset="shift_jis"></head><body><table><tr><th>着</th><th>馬番</th><th>印</th><th>馬名</th>'
            f'<th>タイム</th><th>指数</th></tr>{body}</table></body></html>')
    return html.encode("cp932")


def test_ittai_parse_and_run(tmp_path):
    raw = ittai_bytes([(1, "髙ラージギャラリー", "66.0"), (12, "ウマ", "58")])
    assert ittai.parse(raw.decode("cp932")) == {"01": ("髙ラージギャラリー", "66.0"), "12": ("ウマ", "58")}
    base = common.read_base([write_base(tmp_path / "base.csv")], lambda m: None)
    pages = {ittai.race_url("2025110105040101"): Resp(200, ittai_bytes([(1, "a", "66.0"), (2, "b", "1,050")]), "cp932")}
    f, s = fast_fetcher(pages)
    ittai.run(base, f, tmp_path / "it", lambda m: None)
    assert len(s.calls) == 4
    day = out_csv(tmp_path / "it" / "20251101_ittai.csv")["ワーク1"].tolist()
    assert day[:3] == ["66.0", "1050", ""]


# ---------------------------------------------------------------- ⑥ ラップ
LAP_COLUMNS = ["レースID(新)", "日付S", "場所", "芝・ダ", "距離", "コース区分", "馬場状態", "クラス名", "年齢限定", "着順",
               "着差", "補正", "レース印２", "1角", "2角", "3角", "4角", "人気", "単勝オッズ", "ワーク1"]


def lap_rows():
    laps7 = " 7.1-11.0-11.7-12.0-12.2-12.3-12.4"
    rows = [
        # rid, 日付, 芝ダ, 距離, クラス, 年齢, 着順, 着差, 補正, 印2, ラップ
        ("202610120504051211", "20261012", "ダ", "1300", "1勝", "３上", "１", "-0.0", " 92", "-0.7", laps7),
        ("202610120504051209", "20261012", "ダ", "1300", "1勝", "３上", "２", "0.3", " 92", "不能", laps7),
        ("202610120504051213", "20261012", "ダ", "1300", "1勝", "３上", "消", "0.3", " 89", "-0.7", laps7),
        ("202610120504051214", "20261012", "ダ", "1300", "1勝", "３上", "止", "0.3", " 89", "-0.7", laps7),
        ("202610120504051215", "20261012", "ダ", "1300", "1勝", "３上", "外", "", " 89", "-0.7", laps7),
        ("202610120504051216", "20261012", "ダ", "1300", "1勝", "３上", "3", "abc", " 89", "-0.7", laps7),
        ("202610130504051101", "20261013", "芝", "1000", "Ｇ３", "２歳", "1", "0.0", "90", "±0.0", "11.9-10.3-10.8-11.0-11.5"),
        ("202610130504051102", "20261013", "ダ", "1150", "ｵｰﾌﾟﾝ", "３歳", "3", "0.5", "85", "", "12.0-10.8-11.2-11.9-12.1-12.3"),
    ]
    out = []
    for rid, d, sd, dist, cls, age, fin, diff, hosei, mark, laps in rows:
        out.append(dict(zip(LAP_COLUMNS, [rid, d, "東京", sd, dist, "", "良", cls, age, fin, diff, hosei, mark,
                                          "", "", "", "", "1", "3.5", laps])))
    return pd.DataFrame(out, columns=LAP_COLUMNS)


def test_lap_formulas(tmp_path):
    p = tmp_path / "lap.csv"
    p.write_bytes(lap_rows().to_csv(index=False).encode("cp932"))
    res = lap.run([p], tmp_path / "lap", lambda m: None, today="20261105")
    assert res.total == 8 and res.matched == 4           # 消・止・外・着差が数でない行を除く
    get = lambda folder, day: out_csv(tmp_path / "lap" / folder / f"{day}_{folder}.csv")
    f2 = get("2F", "20261012")
    assert list(f2.columns) == ["レースID(新)", "計算結果"]
    assert f2["計算結果"].tolist() == ["24.7", "25.0"]                    # 12.3+12.4 / +0.3
    assert get("1F", "20261012")["計算結果"].tolist() == ["12.5", "12.5"]  # 12.4 + 1勝0.1、3上は補正なし
    # 4F: 末尾4F=48.9。1着: 48.9 - (-0.7/2) = 49.25→round=49.2（Python の round）。ダ1300 の基準 49.3
    #     → 48 - round(49.3-49.2, 1) = 47.9 / 2着: 48.9+0.3=49.2, 不能 +3.5 = 52.7 → 48-(49.3-52.7)=51.4
    assert get("4F", "20261012")["計算結果"].tolist() == ["47.9", "51.4"]
    # 33rap: 末尾6本の前3本（11.0+11.7+12.0=34.7）− 末尾3本（12.2+12.3+12.4=36.9）= -2.2
    assert get("33rap", "20261012")["計算結果"].tolist() == ["-2.2", "-2.2"]
    # テン2F: 7.1+11.0=18.1、1300m は ×4/3 = 24.1333→24.1、1F は 12.0666→12.1
    r2 = get("rabbit_2F", "20261012")
    assert list(r2.columns) == ["レースID(新)", "逃げ馬2F指数"] and r2["逃げ馬2F指数"].tolist() == ["24.1", "24.1"]
    assert get("rabbit_1F", "20261012")["逃げ馬1F指数"].tolist() == ["12.1", "12.1"]
    # 1000m（ラップ5本）: 33rap = (10.3+10.8) - (11.0+11.5) = -1.4。1F: 11.5 + G3 0.5 - 2歳 0.2 = 11.8
    #   4F: 10.3+10.8+11.0+11.5=43.6、±0.0 → 43.6、芝1000 基準43.8 → 48-(43.8-43.6)=47.8
    # 1150m（ラップ6本）: 33rap = round((12.0+10.8+11.2)*6/5,1)=40.8 - (11.9+12.1+12.3=36.3) = 4.5
    #   1F: 3着・補正85 → 12.3 + オープン0.4 - 3歳0.1 = 12.6。4F: 印2 が空欄 → 空欄（旧版と同じ）
    #   テン2F: (12.0+10.8)*8/7 = 26.057→26.1、1F 13.0285→13.0
    assert get("33rap", "20261013")["計算結果"].tolist() == ["-1.4", "4.5"]
    assert get("1F", "20261013")["計算結果"].tolist() == ["11.8", "12.6"]
    assert get("4F", "20261013")["計算結果"].tolist() == ["47.8", ""]
    assert get("rabbit_2F", "20261013")["逃げ馬2F指数"].tolist() == ["22.2", "26.1"]
    pace = out_csv(tmp_path / "lap" / "pace" / "20261105_pace_of_racecoment.csv")
    assert pace["計算結果"].iloc[0] == "逃げ馬2F：24.1、逃げ馬1F：12.1" and len(pace) == 4


def test_lap_bad_laps_are_blank_not_crash(tmp_path):
    df = lap_rows().iloc[:2].copy()
    df.loc[0, "ワーク1"] = "7.1-abc-11.7"
    p = tmp_path / "lap.csv"
    p.write_bytes(df.to_csv(index=False).encode("cp932"))
    logs = []
    lap.run([p], tmp_path / "o", logs.append, today="20261105")
    assert out_csv(tmp_path / "o" / "2F" / "20261012_2F.csv")["計算結果"].tolist() == ["", "25.0"]
    assert any("読めない行: 1" in m for m in logs)


# ---------------------------------------------------------------- 安井 Excel
def make_yasui_xlsx(path: Path, sheets: dict[str, list[list]]) -> Path:
    import openpyxl
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    header2 = [None, "レース情報", None, None, "基本情報", None, None, None, None, None, None, None, "ステータス"]
    header3 = [None, "場", "レース", "条件", "枠番", "馬番", "状態", "展開", "能力", "ランク", None, "馬名",
               "スピード", None, "スタミナ", None, "パワー", None, "根性", None, "賢さ", None, "体力", "やる気",
               "芝", "ダート"]
    for name, rows in sheets.items():
        ws = wb.create_sheet(name)
        ws.append([None])
        ws.append(header2)
        ws.append(header3)
        for r in rows:
            ws.append(r)
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


def yrow(place, race, waku, uma, rank, name, sp, st, pw, ko, ka, tai, yaru):
    return [None, place, race, "３歳未勝利　ダート1200ｍ", str(waku), f"{uma:02d}", "◎", "", "", rank, "F+", name,
            sp, "E", st, "G", pw, "D", ko, "E", ka, "E", tai, yaru, "A", "G"]


def test_split_status_and_dates():
    from shisu.yasui_extractor import file_year_month, sheet_date, split_status
    assert split_status("48⑥") == ("48", 6)
    assert split_status("-17⑫") == ("-17", 12)
    assert split_status("⑯") == ("", 16)
    assert split_status("１０３⑱") == ("103", 18)
    assert split_status(34) == ("34", None) and split_status(None) == ("", None) and split_status("") == ("", None)
    assert split_status("  5⑳ ") == ("5", 20)
    nfd = "ステータス新バージョン2025_11月.xlsx".replace("バ", "バ")
    assert file_year_month(nfd) == (2025, 11)
    assert file_year_month("f99e1aad-_____________2026_1_.xlsx") == (2026, 1)
    assert sheet_date("1101", 2025, 11) == ("20251101", "")
    assert sheet_date("0104", 2025, 12) == ("20260104", "")          # 年をまたぐ
    assert sheet_date("1228", 2026, 1) == ("20251228", "")
    assert sheet_date("0301", 2026, 2) == ("20260301", "")           # 翌月の日曜はそのまま
    d, note = sheet_date("1029", 2025, 11)                            # 水曜 → 11/29（土）と読み替え
    assert d == "20251129" and "読み替え" in note
    assert sheet_date("1124", 2025, 11) == ("20251124", "")          # 月曜の祝日開催
    assert sheet_date("abc", 2025, 11)[0] == "" and sheet_date("1101", None, None)[0] == ""


def test_yasui_extract_and_target_outputs(tmp_path):
    from shisu import yasui_extractor as y
    xlsx = make_yasui_xlsx(tmp_path / "x" / "ステータス新バージョン2025_11月.xlsx", {
        "1101": [yrow("東京", 1, 1, 1, 34, "アア", "48⑥", "-17⑫", "57⑧", "50⑥", "40⑨", 4, 0),
                 yrow("東京", 1, 2, 2, None, "イイ", "⑯", "3⑭", "42⑨", "41⑫", "⑬", 5, 2),
                 yrow("東京", 2, 1, 1, 39, "ウウ", "54④", "79①", "68③", "52④", "78①", 4, 5),
                 [None, None, None, None, None, None]],                       # 空行は飛ばす
        "1102": [yrow("中山", 11, 1, 1, 40, "エエ", "60①", "1⑨", "60⑦", "54③", "50③", 5, 1)],
        "1029": [yrow("京都", 1, 1, 1, 41, "オオ", "70①", "2①", "3①", "4①", "5①", 3, 3)],   # 11/29 と読み替え
    })
    base = base_frame()
    base.loc[len(base)] = ["202511290801010101", "20251129", "1", "1", ""]
    base["馬名S"] = ""
    base.loc[0, "馬名S"] = "アア"
    b = common.read_base([write_base(tmp_path / "base.csv", base)], lambda m: None)
    logs = []
    res = y.run([xlsx], b, tmp_path / "yasui", logs.append, entries_dir=tmp_path / "entries_in")
    ex = out_csv(tmp_path / "yasui" / "yasui_extract.csv")
    assert len(ex) == 5 and res.matched == 5
    r0 = ex.iloc[0]
    assert (r0["安井_スピード"], r0["安井_スピード_Excel順位"], r0["安井_スタミナ"], r0["安井_スタミナ_Excel順位"]) == \
        ("48", "6", "-17", "12")
    assert ex.iloc[1]["安井_ランク"] == "" and ex.iloc[1]["安井_スピード"] == "" and ex.iloc[1]["安井_スピード_Excel順位"] == "16"
    assert ex.iloc[4]["日付"] == "20251129" and ex.iloc[4]["レースID(新)"] == "202511290801010101"
    assert any("読み替え" in m for m in logs)
    sp = out_csv(tmp_path / "yasui" / "yasui_speed" / "20251101_yasui_speed.csv").set_index("レースID(新)")["ワーク1"]
    assert sp["202511010504010101"] == "48" and sp["202511010504010102"] == "" and sp["202511010504010201"] == "54"
    assert out_csv(tmp_path / "yasui" / "yasui_yaruki" / "20251102_yasui_yaruki.csv")["ワーク1"].tolist() == ["1", ""]
    assert (tmp_path / "yasui" / "yasui_rank" / "base_merged.csv").exists()
    ent = out_csv(tmp_path / "entries_in" / "yasui_20251101.csv")
    assert list(ent.columns) == ["レースID(新)", "安井_馬名", "安井_ランク", "安井_スピード", "安井_スタミナ", "安井_パワー",
                                 "安井_根性", "安井_賢さ", "安井_体力", "安井_やる気"]           # 丸数字の順位は入れない
    # ベースのその日の全馬（Excel に無い 1R 3番・2R 2番・3番も空欄で入る。過去側の欠損の数え方とそろえる）
    assert ent["レースID(新)"].tolist() == [r for r, d, _w, _u in BASE_ROWS if d == "20251101"]
    assert ent.set_index("レースID(新)").loc["202511010504010103", "安井_スピード"] == ""
    assert (tmp_path / "yasui" / "entries_in" / "yasui_20251101.csv").exists()


def test_yasui_without_base_writes_extract_only(tmp_path):
    from shisu import yasui_extractor as y
    xlsx = make_yasui_xlsx(tmp_path / "ステータス新バージョン2025_11月.xlsx",
                           {"1101": [yrow("東京", 1, 1, 1, 34, "アア", "48⑥", "1①", "1①", "1①", "1①", 4, 0)]})
    res = y.run([xlsx], None, tmp_path / "o", lambda m: None)
    assert [p.name for p in res.files] == ["yasui_extract.csv"]


def test_yasui_entries_file_feeds_calc_entry_hensachi(tmp_path):
    """出馬表用CSVは calc_entry_hensachi で TARGET の出馬表と レースID(新) で横に結合され、安井_* の偏差値・順位が付く。
    順位は値から計算する（過去側は TARGET に取り込んだ値から順位を出すので、Excel の丸数字は使わない）。"""
    import calc_entry_hensachi as c
    from shisu import yasui_extractor as y
    rows = [yrow("東京", 1, (u + 1) // 2, u, 30 + u, f"ウマ{u}", f"{100 - u * 5}{chr(0x2460 + u - 1)}", "1①", "1①",
                 "1①", "1①", 4, 0) for u in range(1, 11)]
    rows[2][12] = "99③"                     # 値は1番大きいが丸数字は3位 → 値から計算した 1位 を使う
    rows[4][12] = "70⑤"
    rows[5][12] = "70⑥"                     # 同値は同順位（丸数字は分けている）
    xlsx = make_yasui_xlsx(tmp_path / "ステータス新バージョン2025_11月.xlsx", {"1101": rows})
    base = pd.DataFrame([{"レースID(新)": f"2025110105040101{u:02d}", "日付S": "20251101", "枠番": str((u + 1) // 2),
                          "馬番": str(u), "ワーク1": ""} for u in range(1, 11)])
    b = common.read_base([write_base(tmp_path / "base.csv", base)], lambda m: None)
    y.run([xlsx], b, tmp_path / "o", lambda m: None)
    yas = read_csv_any(tmp_path / "o" / "entries_in" / "yasui_20251101.csv")
    assert not [col for col in yas.columns if col.endswith("順位")]
    main = pd.DataFrame([{"レースID(新)": f"2025110105040101{u:02d}", "場 R": "東1", "場所": "東京", "馬番": str(u),
                          "馬名S": f"ウマ{u}", "カツラキ": str(50 + u)} for u in range(1, 11)])
    out = c.merge_frames([c.process_df(main, "main", verbose=False), c.process_df(yas, "yasui", verbose=False)])
    assert {"安井_スピード_偏差値", "安井_スピード_順位", "安井_ランク_順位帯_s1", "カツラキ_順位"} <= set(out.columns)
    sp_rank = out.set_index("馬番")["安井_スピード_順位"].astype(str)
    assert sp_rank["3"] == "1" and sp_rank["1"] == "2"
    assert sp_rank["5"] == sp_rank["6"]
    # 過去側（replace）と同じ関数・同じ値なら同じ順位になる
    from shisu.hensachi import compute_indicator
    from shisu.indicators import spec_for
    hist = compute_indicator(yas["安井_スピード"], pd.Series("r", index=yas.index), spec_for("安井_スピード"),
                             pd.Series(False, index=yas.index), None)
    assert [str(int(v)) for v in hist["順位"]] == [sp_rank[str(u)] for u in range(1, 11)]
    assert out.set_index("馬番")["安井_ランク_順位"].astype(str)["10"] == "1"


def test_yasui_indicators_in_registry():
    from shisu.indicators import canonical_indicator, is_known_indicator, parse_source_name, spec_for
    for n in ("安井_ランク", "安井_スピード", "安井_スタミナ", "安井_パワー", "安井_根性", "安井_賢さ", "安井_体力", "安井_やる気"):
        assert is_known_indicator(n) and canonical_indicator(n) == n and not spec_for(n).reverse
    assert parse_source_name("主５場（中山、東京、中京、京都、阪神）_安井_スピード")[1] == "安井_スピード"
    assert not is_known_indicator("安井_スピード順位")


# ---------------------------------------------------------------- まとめて実行・画面
def test_validate_messages():
    assert validate(IngestRequest(sources=[]))
    errs = validate(IngestRequest(sources=["omega", "keibaai", "lap", "nope"], keibaai_start=5, keibaai_end=3))
    text = " ".join(errs)
    assert "ベースCSV" in text and "終了ID" in text and "ラップCSV" in text and "知らない取得元" in text
    assert IngestRequest(sources=["omega"], intervals={"omega": 0.1}).interval_for("omega") == 1.0
    assert IngestRequest(sources=["omega"]).interval_for("omega") == 3.0


def test_run_ingest_continues_after_one_source_fails(tmp_path):
    base = write_base(tmp_path / "base.csv")
    pages = {omega.race_url("202511010501"): omega_html([(1, 1, "a", "90")]),
             kayochin.page_url("251101tokyo"): RuntimeError("down")}
    session = FakeSession(pages)
    logs = []
    req = IngestRequest(sources=["kayochin", "omega", "lap"], base_paths=[base], lap_paths=[tmp_path / "nope.csv"])
    res = run_ingest(req, tmp_path / "out", logs.append, session=session, sleep=lambda _: None)
    assert isinstance(res["kayochin"], common.Outputs)                    # 取れなくても出力は作る（空欄）
    assert isinstance(res["omega"], common.Outputs) and res["omega"].matched == 1
    assert isinstance(res["lap"], str) and res["lap"].startswith("ERROR")  # ラップCSVが無い → この取得元だけ失敗
    assert (tmp_path / "out" / "omega" / "20251101_omega.csv").exists()


def test_ingest_web_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("SHISU_BASE_DIR", str(tmp_path))
    import app as am
    from shisu.ingest import web
    pages = {omega.race_url("202511010501"): omega_html([(1, 1, "a", "90"), (2, 2, "b", "80")])}
    monkeypatch.setattr(web, "SESSION_FACTORY", lambda: FakeSession(pages))
    monkeypatch.setattr(web, "SLEEP", lambda _: None)
    monkeypatch.setattr(web, "JOBS", {})
    c = am.app.test_client()
    page = c.get("/ingest").get_data(as_text=True)
    assert "Ω指数" in page and "keibaAI 開始ID" in page
    # 入力エラー（ベースCSVなし）
    r = c.post("/ingest/run", data={"src": ["omega"]})
    assert r.status_code == 400 and "ベースCSV" in r.get_data(as_text=True)
    # ingest_in/lap/ に置いたファイルを選ぶ + ベースCSVと安井Excelをアップロード
    lap_dir = tmp_path / "ingest_in" / "lap"
    lap_dir.mkdir(parents=True)
    (lap_dir / "lap.csv").write_bytes(lap_rows().to_csv(index=False).encode("cp932"))
    xlsx = make_yasui_xlsx(tmp_path / "tmp" / "ステータス新バージョン2025_11月.xlsx",
                           {"1101": [yrow("東京", 1, 1, 1, 34, "アア", "48⑥", "1①", "1①", "1①", "1①", 4, 0)]})
    data = {"src": ["omega", "lap", "yasui"], "lap_pick": ["lap.csv"], "interval_omega": "0",
            "base_files": [(io.BytesIO(base_frame().to_csv(index=False).encode("cp932")), "../ベース.csv")],
            "yasui_files": [(io.BytesIO(xlsx.read_bytes()), "ステータス新バージョン2025_11月.xlsx")]}
    r = c.post("/ingest/run", data=data, content_type="multipart/form-data")
    assert r.status_code == 302
    run_id = r.headers["Location"].rstrip("/").split("/")[-1]
    job = web.JOBS[run_id]
    job.thread.join(timeout=30)
    assert job.status == "done", list(job.lines)
    up = tmp_path / "ingest_in" / "uploads" / run_id
    assert (up / "base" / "ベース.csv").exists() and (up / "yasui" / "ステータス新バージョン2025_11月.xlsx").exists()
    page = c.get(f"/ingest/job/{run_id}").get_data(as_text=True)
    assert "完了" in page and "omega/20251101_omega.csv" in page and "lap/2F/20261012_2F.csv" in page
    assert "yasui/yasui_speed/20251101_yasui_speed.csv" in page
    r = c.get(f"/ingest/files/{run_id}/omega/20251101_omega.csv")
    assert r.status_code == 200 and r.data.decode("cp932").startswith("レースID(新),ワーク1")
    z = zipfile.ZipFile(io.BytesIO(c.get(f"/ingest/zip/{run_id}").data))
    assert f"{run_id}/omega/20251101_omega.csv" in z.namelist()
    assert "Ω指数" in c.get(f"/ingest/job/{run_id}/log.txt").get_data(as_text=True)
    # 不正なパス・ID
    assert c.get(f"/ingest/files/{run_id}/../../etc/passwd").status_code == 404
    assert c.get("/ingest/job/..%2F..").status_code == 404
    assert c.get("/ingest/job/20990101_000000").status_code == 404
    # 一覧に出る
    assert run_id in c.get("/ingest").get_data(as_text=True)


def test_ingest_web_cancel(tmp_path, monkeypatch):
    monkeypatch.setenv("SHISU_BASE_DIR", str(tmp_path))
    import app as am
    from shisu.ingest import web
    gate = threading.Event()

    class SlowSession(FakeSession):
        def get(self, url, headers=None, timeout=None):
            gate.wait(5)
            return super().get(url, headers, timeout)

    monkeypatch.setattr(web, "SESSION_FACTORY", lambda: SlowSession({}))
    monkeypatch.setattr(web, "SLEEP", lambda _: None)
    monkeypatch.setattr(web, "JOBS", {})
    c = am.app.test_client()
    data = lambda: {"src": ["omega"],
                    "base_files": [(io.BytesIO(base_frame().to_csv(index=False).encode("cp932")), "b.csv")]}
    r = c.post("/ingest/run", data=data(), content_type="multipart/form-data")
    run_id = r.headers["Location"].rstrip("/").split("/")[-1]
    assert c.post("/ingest/run", data=data(), content_type="multipart/form-data").status_code == 409   # 同時に1件
    assert "中止" in c.get(f"/ingest/job/{run_id}").get_data(as_text=True)
    c.post(f"/ingest/job/{run_id}/cancel")
    gate.set()
    job = web.JOBS[run_id]
    job.thread.join(timeout=30)
    assert job.status == "cancelled"


def _post(c, data, **kw):
    return c.post("/ingest/run", data=data, content_type="multipart/form-data", **kw)


def _base_upload(name="b.csv"):
    return [(io.BytesIO(base_frame().to_csv(index=False).encode("cp932")), name)]


@pytest.fixture
def web_app(tmp_path, monkeypatch):
    monkeypatch.setenv("SHISU_BASE_DIR", str(tmp_path))
    import app as am
    from shisu.ingest import web
    monkeypatch.setattr(web, "SESSION_FACTORY", lambda: FakeSession({}))
    monkeypatch.setattr(web, "SLEEP", lambda _: None)
    monkeypatch.setattr(web, "JOBS", {})
    monkeypatch.setattr(web, "_RESERVED", set())
    return am.app.test_client(), web


def test_ingest_web_rejects_cross_site_posts(web_app):
    c, web = web_app
    data = lambda: {"src": ["omega"], "base_files": _base_upload()}
    assert _post(c, data(), headers={"Origin": "http://evil.example"}).status_code == 403
    assert _post(c, data(), headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    assert _post(c, data(), headers={"Origin": "null"}).status_code == 403
    assert not web.JOBS
    r = _post(c, data(), headers={"Origin": "http://localhost", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 302


def test_ingest_web_validation_error_removes_uploads(web_app, tmp_path):
    c, web = web_app
    r = _post(c, {"src": ["lap"], "base_files": _base_upload()})        # ラップCSV が無い → 400
    assert r.status_code == 400
    up = tmp_path / "ingest_in" / "uploads"
    assert not up.exists() or not any(up.iterdir())
    out = tmp_path / "ingest_out"
    assert not out.exists() or not any(out.iterdir())
    assert not web._RESERVED and _post(c, {"src": ["omega"], "base_files": _base_upload()}).status_code == 302


def test_ingest_web_same_name_uploads_are_kept_apart(web_app, tmp_path):
    c, web = web_app
    a = lap_rows().iloc[:1].to_csv(index=False).encode("cp932")
    b = lap_rows().iloc[6:7].to_csv(index=False).encode("cp932")
    r = _post(c, {"src": ["lap"], "lap_files": [(io.BytesIO(a), "dayA/lap.csv"), (io.BytesIO(b), "dayB/lap.csv")]})
    run_id = r.headers["Location"].rstrip("/").split("/")[-1]
    web.JOBS[run_id].thread.join(timeout=30)
    up = tmp_path / "ingest_in" / "uploads" / run_id / "lap"
    assert sorted(p.name for p in up.iterdir()) == ["lap.csv", "lap_2.csv"]
    out = tmp_path / "ingest_out" / run_id / "lap" / "2F"
    assert sorted(p.name for p in out.iterdir()) == ["20261012_2F.csv", "20261013_2F.csv"]
    assert len(out_csv(out / "20261012_2F.csv")) == 1


def test_ingest_web_double_submit_runs_one_job(web_app, monkeypatch):
    """受付の確認と予約が同じロックの中なので、同時に2回押しても2件目は 409（ID も重ならない）。"""
    c, web = web_app
    import datetime as _dt
    fixed = _dt.datetime(2026, 11, 5, 3, 49, 7)

    class FixedDT(_dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed
    monkeypatch.setattr(web._dt, "datetime", FixedDT)
    gate = threading.Event()
    real_accept_save = web.unique_path

    def slow_unique_path(dest, taken):
        gate.wait(5)                                    # 1件目がアップロードを保存している間に2件目を送る
        return real_accept_save(dest, taken)
    monkeypatch.setattr(web, "unique_path", slow_unique_path)
    results = []

    def submit():
        cl = web_app[0].application.test_client()
        results.append(_post(cl, {"src": ["omega"], "base_files": _base_upload()}).status_code)
    t1 = threading.Thread(target=submit)
    t1.start()
    for _ in range(100):
        if web._RESERVED:
            break
        threading.Event().wait(0.01)
    assert web._RESERVED
    results.append(_post(c, {"src": ["omega"], "base_files": _base_upload()}).status_code)
    gate.set()
    t1.join(10)
    assert sorted(results) == [302, 409]
    assert len(web.JOBS) == 1


def test_job_finishes_even_if_log_file_fails(tmp_path, monkeypatch):
    from shisu.ingest import web
    monkeypatch.setattr(web, "SESSION_FACTORY", lambda: FakeSession({}))
    monkeypatch.setattr(web, "SLEEP", lambda _: None)
    req = IngestRequest(sources=["omega"], base_paths=[write_base(tmp_path / "b.csv")])
    job = web.Job("20261105_000000", tmp_path / "out", req)
    import shutil
    shutil.rmtree(tmp_path / "out")                       # 実行中に出力フォルダが消えた
    (tmp_path / "out").write_text("not a dir")            # しかも同じ名前のファイルがある → ログも出力も書けない
    job.run()
    assert job.status == "error" and job.finished is not None
    assert any("ログファイルに書けません" in line for line in job.lines)
