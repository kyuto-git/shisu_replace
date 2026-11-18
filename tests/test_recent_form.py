"""近5走の要約条件（近5走同条件・近5走同場）と、その元の履歴（shisu/past_perf_indices.py）。"""
import json

import pandas as pd
import pytest

from shisu import past_perf_indices as PP
from tests import synth2

RID = "レースID(新)"


def hist(rows):
    """(馬ID, 馬名, 日付, 場所, 芝ダ, 距離, 着順) → History。レースID は日付から作る。"""
    recs = []
    for i, (hid, name, date, place, sd, dist, fin) in enumerate(rows):
        recs.append({"馬ID": hid, "馬名": name, "日付": date, RID: f"{date}0501010{i % 9 + 1}01", "場所": place, "芝・ダ": sd,
                     "距離": str(dist), "馬場": "良", "着順": str(fin), "SP": "", "ST": ""})
    return PP.History(pd.DataFrame(recs, columns=PP.HISTORY_COLUMNS))


def target(date="20260601", place="東京", sd="芝", dist=1600, hid="", name="ウマ"):
    return pd.DataFrame([{RID: f"{date}050101010{1}", "日付": date, "馬ID": hid, "馬名": name, "場所": place, "芝・ダ": sd,
                          "距離": float(dist) if dist is not None else float("nan"), "馬場": "", "限定": False}])


def form(history, **kw):
    out = PP.recent_form(target(**kw), history)
    return out["近5走同条件"].iloc[0], out["近5走同場"].iloc[0]


def test_best_finish_among_the_matching_runs_of_the_last_five():
    h = hist([("", "ウマ", "20260501", "東京", "芝", 1600, 4), ("", "ウマ", "20260401", "東京", "芝", 1800, 3),
              ("", "ウマ", "20260301", "中山", "芝", 1400, 2), ("", "ウマ", "20260201", "東京", "ダ", 1600, 1)])
    assert form(h) == ("同条件連対あり", "同場3着あり")           # 同条件 = 芝・1400〜1800 の最高 2着／同場 = 東京・芝の最高 3着
    assert form(h, dist=2000) == ("同条件3着あり", "同場3着あり")  # 1800 だけが距離差200以内
    assert form(h, dist=2100) == ("", "同場3着あり")               # 合う走が無ければ空
    assert form(h, sd="ダ") == ("同条件連対あり", "同場連対あり")
    assert form(h, sd="ダ", dist=1200) == ("", "同場連対あり")
    assert form(h, place="中山") == ("同条件連対あり", "同場連対あり")
    assert form(h, place="京都") == ("同条件連対あり", "")
    h4 = hist([("", "ウマ", "20260501", "東京", "芝", 1600, 4), ("", "ウマ", "20260401", "東京", "芝", 1600, 18)])
    assert form(h4) == ("同条件4着以下", "同場4着以下")
    assert form(hist([("", "ウマ", "20260501", "東京", "芝", 1600, 1)])) == ("同条件連対あり", "同場連対あり")
    assert form(hist([("", "ウマ", "20260501", "東京", "芝", 1600, 2)])) == ("同条件連対あり", "同場連対あり")
    assert form(hist([("", "ウマ", "20260501", "東京", "芝", 1600, 3)])) == ("同条件3着あり", "同場3着あり")
    # 距離差: 200 ちょうどは入り、201 は入らない
    edge = hist([("", "ウマ", "20260501", "中山", "芝", 1800, 1), ("", "ウマ", "20260401", "中山", "芝", 1399, 1)])
    assert form(edge, dist=1600) == ("同条件連対あり", "") and form(edge, dist=1599) == ("同条件連対あり", "")
    assert form(edge, dist=1598) == ("同条件連対あり", "") and form(edge, dist=2001) == ("", "")


def test_only_the_latest_five_runs_within_600_days_before_the_race_day():
    rows = [("", "ウマ", f"2026{m:02d}01", "中山", "ダ", 1200, 9) for m in (5, 4, 3, 2, 1)]      # 直近5走は別の条件
    rows.append(("", "ウマ", "20251201", "東京", "芝", 1600, 1))                                  # 6走前の勝ち
    assert form(hist(rows)) == ("", "")
    assert form(hist(rows[1:])) == ("同条件連対あり", "同場連対あり")                             # 5走以内に入れば数える
    # 当日・未来の走は使わない。600日ちょうど前は入り、601日前は入らない
    d = lambda days: (pd.Timestamp("2026-06-01") - pd.Timedelta(days=days)).strftime("%Y%m%d")
    assert form(hist([("", "ウマ", "20260601", "東京", "芝", 1600, 1), ("", "ウマ", "20260701", "東京", "芝", 1600, 1)])) == ("", "")
    assert form(hist([("", "ウマ", d(600), "東京", "芝", 1600, 1)])) == ("同条件連対あり", "同場連対あり")
    assert form(hist([("", "ウマ", d(601), "東京", "芝", 1600, 1)])) == ("", "")
    assert form(hist([("", "ウマ", d(1), "東京", "芝", 1600, 3)])) == ("同条件3着あり", "同場3着あり")
    # 着順の分からない走は数えない（5走の枠も使わない）
    rows = [("", "ウマ", f"2026{m:02d}01", "中山", "ダ", 1200, "") for m in (5, 4, 3, 2, 1)] + [
        ("", "ウマ", "20251201", "東京", "芝", 1600, 2)]
    assert form(hist(rows)) == ("同条件連対あり", "同場連対あり")
    # 今走の芝・ダ・距離・場所・日付が分からないとき
    one = hist([("", "ウマ", "20260501", "東京", "芝", 1600, 1)])
    assert form(one, sd="") == ("", "") and form(one, dist=None) == ("", "同場連対あり")
    # 今走の芝・ダが分からなければ、芝・ダの空の過去走があっても空（空どうしを「同じ」にしない）
    blank = hist([("", "ウマ", "20260501", "東京", "", 1600, 1)])
    assert form(blank, sd="") == ("", "") and form(blank) == ("", "")
    assert form(one, place="") == ("同条件連対あり", "") and form(one, date="") == ("", "")
    assert form(one, name="ベツ") == ("", "") and form(None) == ("", "")
    assert list(PP.recent_form(target().iloc[0:0], one).columns) == list(PP.RECENT_COLUMNS)


def test_horse_identity_follows_the_history_rules():
    h = hist([("2020100001", "ウマ", "20260501", "東京", "芝", 1600, 1),      # 番号つき
              ("", "ウマ", "20260401", "東京", "芝", 1600, 3),                # 番号の無い同じ馬名の行
              ("2020100002", "ウマ", "20260301", "東京", "芝", 1600, 2)])     # 同じ馬名の別の馬
    assert form(h, hid="2020100001") == ("同条件連対あり", "同場連対あり")
    assert form(h, hid="2020100002") == ("同条件連対あり", "同場連対あり")     # 自分の2着 ＋ 番号なしの3着
    assert form(h, hid="2020100009") == ("同条件3着あり", "同場3着あり")       # 番号で見つからない → 番号なしの同じ馬名の行だけ
    assert form(h, hid="") == ("同条件連対あり", "同場連対あり")               # 番号の無い今走は馬名の全部の走


def base_row(date, r, uma, name, place="東京", code="05", sd="芝", dist=1600, fin="1", heads="12", **more):
    return {RID: f"{date}{code}0101{r:02d}{uma:02d}", "場所": place, "距離": f"{sd}{dist}", "馬場状態": "良", "頭数": heads,
            "馬番": str(uma), "馬名": name, "血統登録番号": "", "着順": fin, **more}


def test_history_keeps_every_real_run_with_place_and_finish():
    raw = pd.DataFrame([
        base_row("20260101", 1, 1, "ア", fin="３"), base_row("20260101", 1, 2, "イ", fin="取消"),
        base_row("20260101", 1, 3, "ウ", fin="中止"), base_row("20260101", 1, 4, "エ", fin="失格", heads=""),
        base_row("20260101", 1, 5, "オ", fin=""), base_row("20260101", 1, 6, "カ", fin="12", sd="障", dist=3000),
        base_row("20260101", 2, 1, "キ", fin="1", place=" 中 山 ", code="06"), base_row("20260101", 2, 2, "", fin="1"),
        base_row("20260101", 2, 3, "ク", fin="除外"),
    ])
    h = PP.history_rows(raw, {"SP": {"202601010501010101": PP.parse_value("55")}, "ST": {}})
    assert list(h.columns) == PP.HISTORY_COLUMNS
    got = {n: (p, f, sp) for n, p, f, sp in zip(h["馬名"], h["場所"], h["着順"], h["SP"])}
    assert got == {"ア": ("東京", "3", "55"),        # 全角の着順・結果指数のある走
                   "ウ": ("東京", "12", ""),         # 中止 = 走ったが着なし → 頭数（4着以下に数える）
                   "エ": ("東京", "18", ""),         # 失格で頭数が分からなければ 18
                   "オ": ("東京", "", ""),           # 着順が空の走（結果指数の履歴には残すが、近5走には数えない）
                   "キ": ("中山", "1", "")}          # 結果指数の無い走も入る。取消・除外・障害・馬名なしは入らない
    hist_ = PP.History(h)
    assert hist_.has_results and hist_.result_rows == {"SP": 1, "ST": 0} and hist_.rows == 5
    assert [r[PP._F_FINISH] for r in hist_.form_runs("", "ウ")] == [12] and hist_.form_runs("", "オ") == []
    assert len(hist_.runs("SP", "", "ア")) == 1 and hist_.runs("SP", "", "キ") == [] and hist_.runs("ST", "", "ア") == []
    assert not PP.History(PP.history_rows(raw, {"SP": {}, "ST": {}})).has_results
    # 着順の列が無い基本CSV: 走は入るが、着順は空（近5走には使えない）
    h2 = PP.history_rows(raw.drop(columns=["着順"]), {"SP": {}, "ST": {}})
    assert len(h2) == 7 and (h2["着順"] == "").all()
    # 前の版の履歴の表（場所・着順の列が無い）を渡しても読める（その列は空として）
    old = h[[c for c in PP.HISTORY_COLUMNS if c not in ("場所", "着順")]]
    assert PP.History(old).form_runs("", "ア") == [] and len(PP.History(old).runs("SP", "", "ア")) == 1


def oracle(base: pd.DataFrame, targets: pd.DataFrame | None = None) -> pd.DataFrame:
    """別に書いた計算（1頭ずつ、素直に）。targets（base と同じ列。省略時は base）の各行の 近5走同条件・近5走同場。"""
    df = base.copy()
    df["date"] = pd.to_datetime(df[RID].str[:8])
    df["sd"], df["dist"] = df["距離"].str[0], df["距離"].str[1:].astype(int)
    fin = pd.to_numeric(df["着順"], errors="coerce")
    df["fin"] = fin.where(df["着順"] != "中止", 12)                     # 中止は頭数（12頭立て）
    df = df[df["着順"] != "取消"]
    t = df if targets is None else targets.assign(date=pd.to_datetime(targets[RID].str[:8]), sd=targets["距離"].str[0],
                                                  dist=targets["距離"].str[1:].astype(int))
    if targets is None:
        t = base.assign(date=pd.to_datetime(base[RID].str[:8]), sd=base["距離"].str[0], dist=base["距離"].str[1:].astype(int))
    out = []
    by_horse = {k: g.sort_values(["date", RID], ascending=False) for k, g in df.groupby("血統登録番号")}
    word = lambda best, w: "" if best is None else (f"{w}連対あり" if best <= 2 else f"{w}3着あり" if best == 3 else f"{w}4着以下")
    for _, r in t.iterrows():
        g = by_horse.get(r["血統登録番号"])
        if g is None:
            out.append(("", ""))
            continue
        mine = g[(g["date"] < r["date"]) & (g["date"] >= r["date"] - pd.Timedelta(days=600))].head(5)
        c = mine[(mine["sd"] == r["sd"]) & ((mine["dist"] - r["dist"]).abs() <= 200)]["fin"]
        p = mine[(mine["sd"] == r["sd"]) & (mine["場所"] == r["場所"])]["fin"]
        out.append((word(c.min() if len(c) else None, "同条件"), word(p.min() if len(p) else None, "同場")))
    return pd.DataFrame(out, columns=list(PP.RECENT_COLUMNS), index=t.index)


def test_recent_form_agrees_with_an_independent_reimplementation():
    base, _marks, ent, _names = synth2.site_data()
    history = PP.History(PP.history_rows(base, {"SP": {}, "ST": {}}))
    got = PP.recent_form(PP.target_frame(base), history)
    want = oracle(base)
    for c in PP.RECENT_COLUMNS:
        assert got[c].tolist() == want[c].tolist(), c
    counts = got["近5走同条件"].value_counts()
    assert {"同条件連対あり", "同条件3着あり", "同条件4着以下", ""} <= set(counts.index) and counts.min() > 20
    assert set(got["近5走同場"]) == {"同場連対あり", "同場3着あり", "同場4着以下", ""}
    # 出馬表の形（列名が違う）でも同じ値
    as_base = pd.DataFrame({RID: ent[RID], "距離": ent["芝ダ"] + ent["距離"], "場所": ent["場所"], "血統登録番号": ent["血統登録番号"],
                            "着順": ""})
    want_e = oracle(base, as_base)
    got_e = PP.recent_form(PP.target_frame(ent), history)
    for c in PP.RECENT_COLUMNS:
        assert got_e[c].tolist() == want_e[c].tolist(), c
    assert (got_e["近5走同条件"] != "").sum() >= 8


def test_history_is_built_without_result_files_and_keeps_results_when_they_vanish(tmp_path):
    base, _marks, _ent, _names = synth2.site_data(days=20)
    csv_dir, res, master = tmp_path / "csv_files", tmp_path / "result_indices", tmp_path / "master_csv"
    csv_dir.mkdir()
    base.to_csv(csv_dir / "全場_タイムM.csv", index=False, encoding="cp932")
    logs = []
    h = PP.get_history(csv_dir, res, master, logs.append)
    assert h is not None and not h.has_results and h.rows > 600
    assert any("結果指数（result_indices）のファイルが無いので、着順だけの履歴を作ります" in m for m in logs)
    meta = json.loads((master / PP.HISTORY_META).read_text(encoding="utf-8"))
    assert meta["version"] == PP.HISTORY_VERSION == 4 and meta["rows"] == h.rows
    logs.clear()
    assert PP.get_history(csv_dir, res, master, logs.append).rows == h.rows
    assert any(f"（{h.rows} 走）を使います" in m for m in logs)                        # 2回目は読むだけ
    assert not any("作り直します" in m or "作ります" in m or "前に作った" in m for m in logs)
    # 結果指数が無いまま基本CSVが変わったら、作り直す（出馬表側の build_db・calc が新しい走を拾う）
    last_race = base[RID].str[:16] == base[RID].str[:16].max()
    base[~last_race].to_csv(csv_dir / "全場_タイムM.csv", index=False, encoding="cp932")
    logs.clear()
    short = PP.get_history(csv_dir, res, master, logs.append)
    assert short.rows < h.rows and any("変わったので作り直します" in m for m in logs)
    base.to_csv(csv_dir / "全場_タイムM.csv", index=False, encoding="cp932")
    assert PP.get_history(csv_dir, res, master).rows == h.rows
    # 結果指数を足すと作り直す（結果指数のある走が入る）
    res.mkdir()
    rid = base[RID].iloc[5]
    (res / "SP_a.csv").write_text(f"{rid},61\n", encoding="utf-8")
    logs.clear()
    h2 = PP.get_history(csv_dir, res, master, logs.append)
    assert h2.has_results and h2.result_rows["SP"] == 1 and any("作り直します" in m for m in logs)
    # 結果指数のファイルが無くなっても、前に作った履歴に結果指数があればそれを使う（8指数を落とさない）
    (res / "SP_a.csv").unlink()
    logs.clear()
    h3 = PP.get_history(csv_dir, res, master, logs.append)
    assert h3.has_results and any("結果指数のファイルが無いので、前に作った" in m for m in logs)
    # その状態で基本CSVが変わったら: 走は基本CSVから作り直し、結果指数の値は前の履歴から引き継ぐ（古い走のままにしない）
    base[~last_race].to_csv(csv_dir / "全場_タイムM.csv", index=False, encoding="cp932")
    logs.clear()
    h3b = PP.get_history(csv_dir, res, master, logs.append)
    assert h3b.rows == short.rows and h3b.has_results and h3b.result_rows["SP"] == 1
    assert any("前に作った履歴から引き継ぎます" in m for m in logs)
    assert PP.load_history_cache(master)[0].set_index(RID).loc[rid, "SP"] == "61"
    logs.clear()                                                    # 同じ基本CSVなら、次は読むだけ
    assert PP.get_history(csv_dir, res, master, logs.append).rows == short.rows
    assert any("前に作った" in m and "を使います" in m for m in logs) and not any("引き継ぎます" in m for m in logs)
    # 行数が印と合わない（途中で切れた）履歴も、そのまま使わず作り直す
    meta_path = master / PP.HISTORY_META
    meta_path.write_text(json.dumps({**json.loads(meta_path.read_text(encoding="utf-8")), "rows": 3}), encoding="utf-8")
    logs.clear()
    assert PP.get_history(csv_dir, res, master, logs.append).has_results and any("引き継ぎます" in m for m in logs)
    assert json.loads(meta_path.read_text(encoding="utf-8"))["rows"] == short.rows
    base.to_csv(csv_dir / "全場_タイムM.csv", index=False, encoding="cp932")
    assert PP.get_history(csv_dir, res, master).rows == h.rows
    # replace（rebuild=True）は、今ある元から作り直す（結果指数なしの履歴になる）
    h4 = PP.get_history(csv_dir, res, master, rebuild=True)
    assert not h4.has_results and h4.rows == h.rows
    # 基本CSVも履歴も無い場所は None
    assert PP.get_history(tmp_path / "x", tmp_path / "y", tmp_path / "z") is None
    # 前の版（version 2）の履歴は使わず、作り直す
    (master / PP.HISTORY_META).write_text(json.dumps({"version": 2, "signature": "x", "rows": 1}), encoding="utf-8")
    assert PP.load_history_cache(master) is None and PP.get_history(csv_dir, res, master).rows == h.rows


@pytest.mark.parametrize("workers", [1])
def test_replace_writes_the_recent_form_columns(tmp_path, monkeypatch, capsys, workers):
    monkeypatch.setenv("SHISU_BASE_DIR", str(tmp_path))
    for k in ("RESULT_INDICES_DIR", "RACE_MARKS_DIR", "SHISU_EXT_DIR"):
        monkeypatch.delenv(k, raising=False)
    base, _marks, _ent, _names = synth2.write_site(tmp_path, days=24, marks_as="none")
    import replace_csv_ver3
    assert replace_csv_ver3.main(["--clean"]) == 0
    m = pd.read_csv(tmp_path / "master_csv" / "master_全場_タイムM.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)
    want = oracle(base).loc[base.index[base[RID].isin(m[RID])]]
    assert m[RID].tolist() == base.loc[want.index, RID].tolist()
    for c in PP.RECENT_COLUMNS:
        assert m[c].tolist() == want[c].tolist(), c
    assert (m["近5走同条件"] != "").mean() > 0.5
    assert (tmp_path / "master_csv" / PP.HISTORY_FILE).exists()
    # cp932 でも UTF-8 でも読めないバイトが1つあるだけの基本CSV: master と同じに、その文字だけ置き換えて履歴を作る
    good = (tmp_path / "csv_files" / "全場_タイムM.csv").read_bytes()
    name = "ホース1,".encode("cp932")
    assert good.count(name) > 3
    (tmp_path / "csv_files" / "全場_タイムM.csv").write_bytes(good.replace(name, "ホース1".encode("cp932") + b"\x81\xff,", 1))
    capsys.readouterr()
    assert replace_csv_ver3.main([]) == 0
    out = capsys.readouterr().out
    assert "読めない文字があります" in out and "[ERROR]" not in out
    m_bad = pd.read_csv(tmp_path / "master_csv" / "master_全場_タイムM.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)
    assert (m_bad["近5走同条件"] != "").mean() > 0.5 and (m_bad["近5走同条件"] != m["近5走同条件"]).sum() <= 12
    (tmp_path / "csv_files" / "全場_タイムM.csv").write_bytes(good)
    # replace は履歴を必ず作り直す: 結果指数のファイルが無くなったら、前の履歴の結果指数を引き継がない
    res = tmp_path / "result_indices"
    res.mkdir()
    (res / "sp_a.csv").write_text("".join(f"{r},{50 + i % 30}\n" for i, r in enumerate(base[RID])), encoding="utf-8")
    assert replace_csv_ver3.main([]) == 0
    assert PP.History(PP.load_history_cache(tmp_path / "master_csv")[0]).has_results
    assert (tmp_path / "master_csv" / "master_全場_過去SP_3走平均.csv.gz").exists()
    (res / "sp_a.csv").unlink()
    assert PP.get_history(log=lambda m: None).has_results                    # 出馬表側（作り直さない）は前の値を使う
    capsys.readouterr()
    assert replace_csv_ver3.main([]) == 0                                    # --clean なし（前の履歴が残っている）でも作り直す
    assert not PP.History(PP.load_history_cache(tmp_path / "master_csv")[0]).has_results
    assert "過去SP_3走平均" not in capsys.readouterr().out                   # 8指数の master は作らない
    # 履歴を作れないとき（読めない基本CSV）: エラーを出して近5走は空・前の履歴を残さない・ほかの master は作る
    (tmp_path / "csv_files" / "全場_対戦M.csv").write_bytes((tmp_path / "csv_files" / "全場_タイムM.csv").read_bytes())
    (tmp_path / "csv_files" / "全場_タイムM.csv").write_bytes(b"")
    assert replace_csv_ver3.main([]) == 1
    assert not (tmp_path / "master_csv" / PP.HISTORY_FILE).exists()
    m2 = pd.read_csv(tmp_path / "master_csv" / "master_全場_対戦M.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)
    assert len(m2) == len(m) and (m2["近5走同条件"] == "").all() and (m2["近5走同場"] == "").all()
