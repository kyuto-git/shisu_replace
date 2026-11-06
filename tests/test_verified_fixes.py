"""検証ワークフロー（2026-11-06）で再現した不具合の再発防止。

過去側と出馬表側のキーのずれ（重量種別の推定・障害・前走開催・牝馬限定）、指数の値の扱い（除外・取消の符号、
走らなかった馬の 0、偏差値の向き、ZI の順位、外部指数の値の無い馬）、family の台帳の受け渡し（予約名・定義の
置き換え・明示したファイルが無い・スキームの絞り込み・古い成績表・画面の同期）と screen_families の引数・終了コード。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

from shisu import families as F  # noqa: E402
from shisu.conditions import STD_COLUMNS, _prev_rid16_from_kaisai, derive, standardize_history  # noqa: E402
from shisu.finish import parse_finish  # noqa: E402
from shisu.hensachi import compute_indicator  # noqa: E402
from shisu.indicators import is_discontinued, spec_for  # noqa: E402


# ---------------------------------------------------------------- 着順の符号・走らなかった馬
def test_target_cancel_codes():
    """★6タブの過去CSVの 除外=「外」・取消=「消」は分母に入れない（外枠の「外」などとは別。着順の列だけの話）。"""
    f = parse_finish(pd.Series(["外", "消", "止", "1", "中止", "取消"]))
    assert f["denom"].tolist() == [False, False, True, True, True, False]


def _race(values, ranks, finishes, rid="2026102404040304"):
    rows = []
    for i, (v, r, fin) in enumerate(zip(values, ranks, finishes), 1):
        rows.append({"レースID(新)": f"{rid}{i:02d}", "場所": "新潟", "芝・ダ": "ダ", "距離": "1200", "頭数": "12",
                     "枠番": str(min(8, (i + 1) // 2)), "馬番": str(i), "騎手": f"騎手{i}", "クラス名": "1勝",
                     "重量種別": "定量", "指数": v, "指数順位": r, "着順": fin, "単勝オッズ": "5.0"})
    return pd.DataFrame(rows)


def test_cancelled_horse_is_left_out_of_hensachi(tmp_path):
    """取消の馬（TARGET は 指数 0・指数順位 0）は偏差値・順位の計算に入れない。入れると 0 が平均を動かし、
    小さいほど良い指数では 0 が最高値になって 71以上 に入っていた。"""
    import replace_csv_ver3 as r
    vals = ["72", "60", "55", "50", "48", "45", "40", "38", "35", "30", "25", "0"]
    ranks = [str(i) for i in range(1, 12)] + ["0"]
    fins = [str(i) for i in range(1, 12)] + ["消"]
    df = _race(vals, ranks, fins)
    synth.write_cp932(df, tmp_path / "csv" / "新潟_タイムM.csv")
    msg = r.process_file(str(tmp_path / "csv" / "新潟_タイムM.csv"), str(tmp_path / "m"), None)
    assert "取消・除外 1 頭" in msg
    m = pd.read_csv(tmp_path / "m" / "master_新潟_タイムM.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)
    gone = m[m["着順"] == "消"].iloc[0]
    assert gone["指数有効"] == "0" and gone["偏差値"] == "" and gone["順位"] == ""
    ran = m[m["着順"] != "消"]
    expect = compute_indicator(df["指数"][:11], pd.Series(["a"] * 11), spec_for("タイムM"), given_rank=df["指数順位"][:11])
    assert ran["偏差値"].astype(int).tolist() == expect["偏差値"].astype(int).tolist()
    # 1位（値が最大）の馬が最も高い偏差値（TARGET の順位と同じ向き）
    assert ran.loc[ran["順位"] == "1", "偏差値"].astype(int).iloc[0] == ran["偏差値"].astype(int).max()


def test_hensachi_follows_target_rank_direction_on_both_sides():
    """偏差値の向きは TARGET の順位に合わせる（1位が最大値なら大きいほど上、最小値なら小さいほど上）。
    過去側（replace）と出馬表側（calc_entry_hensachi）で同じ。順位が無ければ台帳の向き。"""
    import calc_entry_hensachi as c
    v = pd.Series(["72.4", "50", "22.7", "40"])
    k = pd.Series(["a"] * 4)
    hi = compute_indicator(v, k, spec_for("タイムM"), given_rank=pd.Series(["1", "2", "4", "3"]))
    assert hi.loc[0, "偏差値"] == hi["偏差値"].max() and hi.attrs["direction"] == "rank" and not hi.attrs["reverse"]
    lo = compute_indicator(v, k, spec_for("タイムM"), given_rank=pd.Series(["4", "2", "1", "3"]))
    assert lo.loc[2, "偏差値"] == lo["偏差値"].max() and lo.attrs["reverse"]
    none = compute_indicator(v, k, spec_for("タイムM"))          # タイムM の台帳の既定は「大きいほど上」
    assert none.attrs["direction"] == "spec" and none.loc[0, "順位"] == 1
    rev = compute_indicator(v, k, spec_for("展開"))               # 展開は「小さいほど上」
    assert rev.attrs["direction"] == "spec" and rev.loc[2, "順位"] == 1
    ent = pd.DataFrame({"レースID(新)": [f"20260531050212010{i}" for i in range(1, 5)], "馬番": ["1", "2", "3", "4"],
                        "マイニング値": v, "マイニング順位": ["1", "2", "4", "3"]})
    out = c.process_df(ent, "t", verbose=False)
    assert out["タイムM_偏差値"].astype(int).tolist() == hi["偏差値"].astype(int).tolist()


def test_zi_rank_puts_zero_last_like_target():
    """ZI の順位を値から作るとき（ext_indices の形式D）、0 の馬は最下位（同順位）。平均で埋めた値では順位を付けない。"""
    z = pd.Series(["99", "105", "84", "90", "0", "92", "0"])
    r = compute_indicator(z, pd.Series(["a"] * 7), spec_for("ZI"))
    assert r["順位"].tolist() == [2, 1, 5, 4, 6, 3, 6]
    assert r.loc[4, "偏差値"] == 50                                   # 偏差値は従来どおり平均で埋める


def test_ext_missing_values_become_zero_like_target(tmp_path):
    """外部指数のファイルに無い馬は 0（TARGET が出馬表・従来の過去CSVで 0 を出すのと同じ）。欠損2頭でレースが消えない。"""
    import replace_csv_ver3 as r
    vals = [str(200 - i) for i in range(12)]
    df = _race(vals, [str(i) for i in range(1, 13)], [str(i) for i in range(1, 13)])
    synth.write_cp932(df, tmp_path / "csv" / "新潟_タイムM.csv")
    ext = tmp_path / "ext" / "OMEGA"
    ext.mkdir(parents=True)
    lines = [f"{rid},{80 + i}" for i, rid in enumerate(df["レースID(新)"]) if i >= 2]     # 馬番1・2 は値なし
    (ext / "20261024_omega.csv").write_text("\n".join(lines), encoding="utf-8")
    assert r.main(["--csv-dir", str(tmp_path / "csv"), "--ext-dir", str(tmp_path / "ext"),
                   "--master-dir", str(tmp_path / "m")]) == 0
    m = pd.read_csv(tmp_path / "m" / "master_新潟_オメガ指数.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)
    assert m.loc[m["馬番"].isin(["1", "2"]), "指数"].tolist() == ["0", "0"]
    assert (m["指数有効"] == "1").all()
    assert set(m.loc[m["馬番"].isin(["1", "2"]), "順位"]) == {"11"}


# ---------------------------------------------------------------- 出馬表側
def test_entries_jump_race_builds_no_keys():
    """出馬表の障害レース（芝ダ は「ダ」、今芝ダ が「障」・レース名が 障害…）はどの family のキーも作らない。"""
    import build_db
    from shisu.matching import is_jump_row, match_entry_row
    raw = pd.DataFrame([
        {"レースID(新)": "202611010804110401", "場所": "京都", "芝ダ": "ダ", "今芝ダ": "障", "距離": "2910",
         "レース名": "障害未勝利", "馬番": "1", "騎手": "騎手A", "前着順": "2着"},
        {"レースID(新)": "202611010804110501", "場所": "京都", "芝ダ": "ダ", "今芝ダ": "", "距離": "1800",
         "レース名": "未勝利", "馬番": "1", "騎手": "騎手A", "前着順": "2着"},
        {"レースID(新)": "202611010804110601", "場所": "京都", "芝ダ": "芝", "距離": "3170", "トラックコード(JV)": "54",
         "レース名": "京都ジャンプＳ", "馬番": "1", "騎手": "騎手A"},
    ])
    out = build_db.normalize_entries_df(raw)
    assert out["芝・ダ"].tolist() == ["障", "ダ", "障"]
    assert [is_jump_row(r) for r in out.to_dict("records")] == [True, False, True]
    con = sqlite3.connect(":memory:")       # unified_all が無くても、障害の行は問い合わせる前に返る
    assert match_entry_row(con, out.iloc[0].to_dict(), ["SAV"], "t") == []


def test_prev_kaisai_without_trailing_r():
    """TARGET の「前走開催」は固定幅なので、日が2桁だと末尾の R が切れる。それでも前走のレースIDを作る。"""
    assert _prev_rid16_from_kaisai("260712 1函10 11") == "2026071202011011"
    assert _prev_rid16_from_kaisai("261101 4京11 11") == "2026110108041111"
    assert _prev_rid16_from_kaisai("260816 2新8  1R") == "2026081604020801"
    assert _prev_rid16_from_kaisai("260725 英国") == "" and _prev_rid16_from_kaisai("初") == ""


def test_history_mare_rule_without_sex_limit_column():
    """過去側に 性別限定 の列が無いときも、出馬表側と同じ規則（レース名に牝・全馬が牝）。何も分からなければ空。"""
    raw = pd.DataFrame({"レース名": ["一般", "一般", "牝馬S", "牝馬S", "一般", "一般"],
                        "性別": ["牝", "牝", "牡", "牝", "牝", "牡"]})
    norm = pd.DataFrame({c: [""] * 6 for c in ("日付", "場所", "芝・ダ", "距離_raw", "芝(内・外)")})
    norm["レースID(新/馬番無)"] = ["r1", "r1", "r2", "r2", "r3", "r3"]
    std = standardize_history(raw, norm)
    assert std["牝馬限定"].tolist() == ["牝馬限定", "牝馬限定", "牝馬限定", "牝馬限定", "混合", "混合"]
    empty = standardize_history(pd.DataFrame(index=range(2)), norm.iloc[:2])
    assert empty["牝馬限定"].tolist() == ["", ""]
    assert set(derive(pd.DataFrame([{c: "" for c in STD_COLUMNS}]))["牝馬限定"]) == {""}


def test_discontinued_names_with_parenthetical_alias():
    assert is_discontinued("結果分析スピード指数(ittai)") and is_discontinued("結果分析スピード指数（一隊）")
    assert is_discontinued("カツラキ") and not is_discontinued("オメガ指数(Ω指数)") and not is_discontinued("SAV")


# ---------------------------------------------------------------- family の台帳
def test_reserved_family_names_are_rejected():
    for name in ("odds10u_x", "odds50p_x", "odds10_50_x", "odds5p_x", "rankband_s1_x", "Odds10u_x"):
        with pytest.raises(ValueError):
            F.Family.from_dict({"name": name, "parts": ["騎手"], "tail": "RBAND"})
    with pytest.raises(ValueError):                          # スキームを持たない family に schemes は書けない
        F.Family.from_dict({"name": "X_a", "parts": ["騎手"], "tail": "HBAND", "schemes": ["s1"]})
    assert F.Family.from_dict({"name": "X_b", "parts": ["騎手", "前走着順"], "tail": "HBAND", "schemes": ["s2"]}).schemes == ("s2",)


def test_set_active_replaces_definitions():
    """selected_families.json・DB の定義が正。初期セットと同じ名前で部品が違っても置き換えられる（画面が 500 にならない）。"""
    try:
        F.set_active([F.Family("normal", ("騎手",), "RANK")], "test")
        assert F.get("normal").parts == ("騎手",)
        F.set_active([F.Family("normal", ("COURSE", "騎手"), "RANK")], "test")
        assert F.get("normal").parts == ("COURSE", "騎手")
    finally:
        F.reset()
    assert F.get("normal").parts == ("COURSE", "重量種別", "クラス名")


def test_explicit_missing_families_file_is_an_error(tmp_path, monkeypatch):
    import aggregate_stats_ver2
    m = tmp_path / "m"
    m.mkdir()
    (m / "master_x_SAV.csv.gz").write_bytes(b"")
    assert aggregate_stats_ver2.main(["--in-dir", str(m), "--out-dir", str(tmp_path / "a"),
                                      "--families", str(tmp_path / "nope.json")]) == 2
    monkeypatch.setenv("SHISU_FAMILIES_FILE", str(tmp_path / "nope2.json"))
    with pytest.raises(FileNotFoundError):
        F.configure()
    monkeypatch.delenv("SHISU_FAMILIES_FILE")
    monkeypatch.setenv("SHISU_BASE_DIR", str(tmp_path))
    assert len(F.configure()) == 83                           # 既定の場所に無いだけなら初期セット
    F.reset()


@pytest.fixture(scope="module")
def mini(tmp_path_factory):
    """合成データ（SAV だけ）の master と、family を絞った集計・DB。"""
    import replace_csv_ver3
    b = tmp_path_factory.mktemp("vf")
    synth.make_history(b, indicators=("SAV",))
    synth.make_entries(b)
    assert replace_csv_ver3.main(["--csv-dir", str(b / "csv_files"), "--master-dir", str(b / "master_csv"),
                                  "--clean", "--no-ext"]) == 0
    fams = [{"name": "normal", "parts": ["COURSE", "重量種別", "クラス名"], "tail": "RANK"},
            {"name": "X_jk_band", "parts": ["騎手"], "tail": "RBAND"},
            {"name": "X_sd_pfin_hen", "parts": ["芝・ダ", "前走着順"], "tail": "HBAND"}]
    (b / "fams.json").write_text(json.dumps({"families": fams}, ensure_ascii=False), encoding="utf-8")
    return b


def _agg(b: Path, out: str, *extra) -> list[str]:
    import aggregate_stats_ver2
    assert aggregate_stats_ver2.main(["--in-dir", str(b / "master_csv"), "--out-dir", str(b / out),
                                      "--families", str(b / "fams.json"), "--wedge-min-year-count", "0", *extra]) == 0
    return sorted(p.name for p in (b / out).glob("foragg_*.csv"))


def test_rankband_schemes_env_removes_old_files_and_limits_families_json(mini, monkeypatch):
    full = _agg(mini, "agg1", "--clean")
    assert any("rankband_s2_X_jk_band" in n for n in full) and any("rankband_s2_X_sd_pfin_hen" in n for n in full)
    monkeypatch.setenv("AGG_RANKBAND_SCHEMES", "s1")
    only = _agg(mini, "agg1")                                 # --clean なしでも前の s2 は消える
    assert not any("rankband_s2_" in n for n in only) and any("rankband_s1_X_jk_band" in n for n in only)
    fj = json.loads((mini / "agg1" / "families.json").read_text(encoding="utf-8"))
    assert {f["name"]: f.get("schemes") for f in fj["families"]} == {"normal": None, "X_jk_band": ["s1"],
                                                                     "X_sd_pfin_hen": ["s1"]}


def test_ref_columns_only_for_families_that_fix_them(mini):
    _agg(mini, "agg2", "--clean")
    a = mini / "agg2"
    course = pd.read_csv(a / "foragg_normal_主５場（中山、東京、中京、京都、阪神）_SAV.csv", encoding="cp932", dtype=str,
                         keep_default_na=False)
    assert (course["場所"] != "").all() and (course["距離"] != "").all()
    jk = pd.read_csv(next(a.glob("foragg_rankband_s1_X_jk_band_*.csv")), encoding="cp932", dtype=str, keep_default_na=False)
    assert (jk[["場所", "芝・ダ", "距離", "芝(内・外)"]] == "").all().all()       # 騎手だけの family はコースを持たない
    sd = pd.read_csv(next(a.glob("foragg_rankband_s1_X_sd_pfin_hen_*.csv")), encoding="cp932", dtype=str,
                     keep_default_na=False)
    assert (sd["芝・ダ"] != "").all() and (sd["場所"] == "").all()


def test_build_db_skips_tables_of_other_family_sets(mini, tmp_path):
    """agg_csv に前の実行の別の family の成績表が残っていても、families.json に無いものは取り込まない。"""
    import build_db
    a = mini / "agg3"
    _agg(mini, "agg3", "--clean")
    stale = a / "foragg_rankJ_主５場（中山、東京、中京、京都、阪神）_SAV.csv"
    stale.write_bytes((a / "foragg_normal_主５場（中山、東京、中京、京都、阪神）_SAV.csv").read_bytes())
    db = tmp_path / "x.db"
    F.reset()
    assert build_db.main(["--mode", "foragg", "--agg-dir", str(a), "--db-path", str(db), "--fresh"]) == 0
    with sqlite3.connect(db) as con:
        fams = {r[0] for r in con.execute("SELECT DISTINCT family FROM unified_all")}
    assert fams <= {"normal", "X_jk_band", "X_sd_pfin_hen"} and "rankJ" not in fams
    F.reset()


def test_app_survives_db_rebuilt_with_changed_family_definition(mini, monkeypatch):
    """画面を動かしたまま、同じ名前で部品の違う family の DB に作り直しても 500 にならない。wedge の family 絞り込みも初回から効く。"""
    import importlib
    import build_db
    import calc_entry_hensachi
    monkeypatch.setenv("SHISU_BASE_DIR", str(mini))
    db = mini / "racing.db"
    monkeypatch.setenv("DB_PATH", str(db))
    _agg(mini, "agg_csv", "--clean")
    assert calc_entry_hensachi.main(["--merge-only", "--clean"]) == 0
    F.reset()
    assert build_db.main(["--fresh"]) == 0
    import app as app_module
    am = importlib.reload(app_module)
    c = am.app.test_client()
    with sqlite3.connect(db) as con:
        rid = con.execute('SELECT "レースID(新/馬番無)" FROM entries_merged_hensachi LIMIT 1').fetchone()[0]
    F.reset()                                               # 別のリクエストが初期セットに戻していても
    q = {"table": "entries_merged_hensachi", "rid": rid, "odds_mode": "all", "family": "X_jk_band"}
    body = c.get("/entries/wedge.csv", query_string=q).get_data(as_text=True)
    lines = [ln for ln in body.splitlines()[1:] if ln]
    assert lines and all(",X_jk_band," in ln for ln in lines)
    assert c.get("/?submitted=1&view=all").status_code == 200
    # 同じ名前 X_jk_band を別の部品で作り直す
    fams = json.loads((mini / "fams.json").read_text(encoding="utf-8"))
    fams["families"][1]["parts"] = ["枠番グループ"]
    (mini / "fams.json").write_text(json.dumps(fams, ensure_ascii=False), encoding="utf-8")
    _agg(mini, "agg_csv", "--clean")
    F.reset()
    assert build_db.main(["--fresh"]) == 0
    assert c.get("/?submitted=1&view=all").status_code == 200
    assert c.get("/entries/hits", query_string={"table": "entries_merged_hensachi", "rid": rid}).status_code == 200
    assert F.get("X_jk_band").parts == ("枠番グループ",)
    F.reset()


# ---------------------------------------------------------------- screen_families
def test_screen_arguments_are_normalized_and_failures_exit_1(mini, tmp_path):
    import screen_families
    rep = tmp_path / "r.csv"
    sel = tmp_path / "s.json"
    base = ["--master-dir", str(mini / "master_csv"), "--space", "initial", "--max-candidates", "3",
            "--report", str(rep), "--wedge-min-year-count", "0"]
    assert screen_families.main(base + ["--indicators", "ＳＡＶ", "--regions", "主５場"]) == 0     # 全角でも当たる
    bad = tmp_path / "mbad"
    bad.mkdir()
    (bad / "master_主５場（中山、東京、中京、京都、阪神）_SAV.csv.gz").write_bytes(
        (mini / "master_csv" / "master_主５場（中山、東京、中京、京都、阪神）_SAV.csv.gz").read_bytes())
    (bad / "master_札幌_SAV.csv.gz").write_bytes(b"not a gzip")
    args = base[2:] + ["--master-dir", str(bad), "--export-selected", str(sel)]
    assert screen_families.main(args) == 1 and not sel.exists()        # 1本でも失敗したら選抜は書かない
    assert rep.exists()


# ---------------------------------------------------------------- 2回目の検証の指摘
def test_entries_weight_change_blank_for_first_starters():
    """前走の無い馬（前斤量・斤量差が空）は 斤量増減 が空（NaN から '-0.5~-1.5' が出ていた）。"""
    import build_db
    raw = pd.DataFrame([
        {"レースID(新)": "202611010504110101", "場所": "東京", "芝ダ": "芝", "距離": "1600", "馬番": "1", "斤量": "56",
         "前斤量": "", "斤量差": "", "前走開催": "初"},
        {"レースID(新)": "202611010504110102", "場所": "東京", "芝ダ": "芝", "距離": "1600", "馬番": "2", "斤量": "57",
         "前斤量": "", "斤量差": "+1.0", "前走開催": "261018 4東7  3R"},
        {"レースID(新)": "202611010504110103", "場所": "東京", "芝ダ": "芝", "距離": "1600", "馬番": "3", "斤量": "55",
         "前斤量": "57", "斤量差": "-2.0", "前走開催": "261018 4東7  3R"},
    ])
    out = build_db.normalize_entries_df(raw)
    assert out["斤量増減"].tolist() == ["", "+0.5~+1.5", "-2kg以下"]
    from shisu.conditions import _num, weight_change
    assert _num("nan") is None and _num("inf") is None and weight_change("56", "nan") == ""


def test_timem_without_ranks_agrees_with_ranked_side(tmp_path):
    """過去側に 指数順位 が無く、出馬表側に マイニング順位 がある場合も、タイムM の順位・偏差値が両側で同じ。"""
    import calc_entry_hensachi as c
    import replace_csv_ver3 as r
    vals = [f"{96.9 - i * 1.4:.1f}" for i in range(12)]
    df = _race(vals, [""] * 12, [str(i) for i in range(1, 13)]).drop(columns=["指数順位"])
    synth.write_cp932(df, tmp_path / "csv" / "新潟_タイムM.csv")
    r.process_file(str(tmp_path / "csv" / "新潟_タイムM.csv"), str(tmp_path / "m"), None)
    m = pd.read_csv(tmp_path / "m" / "master_新潟_タイムM.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)
    ent = pd.DataFrame({"レースID(新)": df["レースID(新)"], "馬番": df["馬番"], "マイニング値": vals,
                        "マイニング順位": [str(i) for i in range(1, 13)]})
    e = c.process_df(ent, "t", verbose=False)
    assert m["順位"].tolist() == e["タイムM_順位"].tolist() == [str(i) for i in range(1, 13)]
    assert m["偏差値"].tolist() == e["タイムM_偏差値"].tolist()


def test_time_indicators_treat_zero_as_missing():
    """想定前半・想定後半（3Fの秒数）の 0 は欠損。外部指数の無い馬の 0 が最高値（71以上）にならない。"""
    v = pd.Series(["34.5", "34.8", "35.0", "35.3", "35.6", "36.0", "0"])
    r = compute_indicator(v, pd.Series(["a"] * 7), spec_for("想定前半"))
    assert pd.isna(r.loc[6, "偏差値"]) and pd.isna(r.loc[6, "順位"])
    assert r.loc[0, "順位"] == 1 and r.loc[0, "偏差値帯"] in ("61~65", "66~70", "71以上")


def test_ranked_ext_formats_put_absent_horses_last(tmp_path):
    """形式B（StridePlus）でファイルに無い馬は値 0・レースの最下位（TARGET が出馬表で付ける順位と同じ）。"""
    import replace_csv_ver3 as r
    df = _race([str(200 - i) for i in range(12)], [str(i) for i in range(1, 13)], [str(i) for i in range(1, 13)])
    synth.write_cp932(df, tmp_path / "csv" / "新潟_タイムM.csv")
    sp = tmp_path / "ext" / "StridePlus"
    sp.mkdir(parents=True)
    from shisu.ext_indices import old_id_from_new
    lines = [f"{old_id_from_new(rid)},{60 - i},{i + 1}" for i, rid in enumerate(df["レースID(新)"]) if i < 10]
    (sp / "sav_2026.csv").write_text("\n".join(lines), encoding="utf-8")
    assert r.main(["--csv-dir", str(tmp_path / "csv"), "--ext-dir", str(tmp_path / "ext"),
                   "--master-dir", str(tmp_path / "m")]) == 0
    m = pd.read_csv(tmp_path / "m" / "master_新潟_SAV.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)
    tail = m[m["馬番"].isin(["11", "12"])]
    assert tail["指数"].tolist() == ["0", "0"] and tail["順位"].tolist() == ["11", "11"]


def _app_db(mini, monkeypatch):
    import importlib
    import build_db
    import calc_entry_hensachi
    monkeypatch.setenv("SHISU_BASE_DIR", str(mini))
    monkeypatch.setenv("DB_PATH", str(mini / "racing.db"))
    fams = [{"name": "normal", "parts": ["COURSE", "重量種別", "クラス名"], "tail": "RANK"},
            {"name": "X_jk_band", "parts": ["騎手"], "tail": "RBAND"},
            {"name": "X_sd_pfin_hen", "parts": ["芝・ダ", "前走着順"], "tail": "HBAND"}]
    (mini / "fams.json").write_text(json.dumps({"families": fams}, ensure_ascii=False), encoding="utf-8")
    _agg(mini, "agg_csv", "--clean")
    assert calc_entry_hensachi.main(["--merge-only", "--clean"]) == 0
    F.reset()
    assert build_db.main(["--fresh"]) == 0
    import app as app_module
    return importlib.reload(app_module)


def test_hits_page_caps_rows_per_category(mini, monkeypatch):
    am = _app_db(mini, monkeypatch)
    monkeypatch.setattr(am, "HITS_VIEW_LIMIT", 2)
    c = am.app.test_client()
    with sqlite3.connect(mini / "racing.db") as con:
        rid = con.execute('SELECT "レースID(新/馬番無)" FROM entries_merged_hensachi LIMIT 1').fetchone()[0]
    with am.connect() as con:
        race = am._race_rows(con, "entries_merged_hensachi", rid)
        hits, _ = am.collect_hits(con, "entries_merged_hensachi", race)
    buckets = am.classify_hits(hits)
    big = [k for k, v in buckets.items() if len(v) > 2]
    assert big, "合成データで2行を超える区分が要る"
    page = c.get("/entries/hits", query_string={"table": "entries_merged_hensachi", "rid": rid}).get_data(as_text=True)
    assert "先頭 2 行だけ表示しています" in page
    body = page.split("馬ごとの該当数", 1)[1]
    for k in big:                                         # 見出しの件数は全件のまま
        assert f"（{len(buckets[k])}）" in body
    F.reset()


def test_build_db_missing_families_file_is_reported_before_fresh(tmp_path, monkeypatch):
    import build_db
    db = tmp_path / "keep.db"
    sqlite3.connect(db).execute("CREATE TABLE t (x)").connection.commit()
    agg = tmp_path / "agg"
    agg.mkdir()
    monkeypatch.setenv("SHISU_FAMILIES_FILE", str(tmp_path / "nope.json"))
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(agg), "--db-path", str(db)]) == 2
    assert db.exists() and sqlite3.connect(db).execute("SELECT name FROM sqlite_master").fetchall() == [("t",)]
    F.reset()
