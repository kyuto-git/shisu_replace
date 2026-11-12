"""master ごとの成績表（agg_master_*.csv.gz）・コンパクト化・--resume・並列のまとまり・build_db の直接構築・画面の互換。"""
import importlib
import json
import shutil
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

YEAR = ["--wedge-min-year-count", "2"]


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def mb(tmp_path_factory, monkeypatch_module):
    """合成データの master（3本）と、全部の行（--no-compact）・コンパクト化の2通りの成績表と DB。"""
    import aggregate_stats_ver2 as agg
    import build_db
    import calc_entry_hensachi
    import replace_csv_ver3
    from shisu import families as F
    b = tmp_path_factory.mktemp("aggstore")
    monkeypatch_module.setenv("SHISU_BASE_DIR", str(b))
    monkeypatch_module.delenv("DB_PATH", raising=False)
    F.reset()
    synth.make_history(b)
    synth.make_entries(b)
    assert replace_csv_ver3.main(["--clean"]) == 0
    assert agg.main(["--clean", "--out-dir", str(b / "agg_full"), "--no-compact", *YEAR]) == 0
    assert agg.main(["--clean", "--out-dir", str(b / "agg_csv"), *YEAR]) == 0
    assert calc_entry_hensachi.main(["--merge-one"]) == 0
    assert build_db.main(["--fresh"]) == 0
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(b / "agg_full"),
                          "--db-path", str(b / "full.db")]) == 0
    yield b
    F.reset()


def _shrink_master(path: Path) -> None:
    """master の中身を変える（replace で作り直したのと同じ。最初のレースを除く）。"""
    from shisu.csvio import read_csv_any, write_csv
    m = read_csv_any(path)
    first = m["レースID(新/馬番無)"].iloc[0]
    write_csv(m[m["レースID(新/馬番無)"] != first], path)


def _rows(agg_dir: Path) -> pd.DataFrame:
    from shisu.aggstore import agg_master_files, read_agg_master
    return pd.concat([read_agg_master(p) for p in agg_master_files(agg_dir)], ignore_index=True)


# ---------------------------------------------------------------- 判定のベクトル版
def test_classify_frame_matches_row_classify():
    from shisu.classify import FIELD_COLUMNS, classify, classify_frame, is_rare_key
    rng = np.random.default_rng(7)
    pick = lambda vals: rng.choice(np.array(vals, dtype=float), n)      # noqa: E731（境界の値を多めに混ぜる）
    n = 12000
    keys = ["東京×芝×23-24×定量×G1×3", "中山×芝×2000×内×ハンデ×重賞×1~3位", "準OP・OP×61~65", "芝×芝長×高×1位",
            "ダ×ダ長×前走1~3着×6位~", "東京×ダ×1600×定量×1・2_勝×4", "東京×芝×16-18×馬齢×1勝・OP×2", "騎手G1×1",
            "g1×1", "芝長い×1", "", None, "東京×芝×2400×定量×G1\n", "G1\n", "(G1)×1", "準OP.OP×1"]
    df = pd.DataFrame({
        "件数": pick([5, 6, 9, 10, 11, 12, 14, 15, 19, 20, 29, 30, 31, 100, np.nan]),
        "複勝率_val": pick([0, 4.99, 5, 19.99, 20, 32.99, 33, 34.99, 35, 49.99, 50, 59.99, 60, 69.99, 70, 79.99, 80, 95,
                         np.nan]),
        "連対率_val": pick([0, 49.99, 50, 59.99, 60, 79.99, 80, 90, np.nan]),
        "掲示板率_val": pick([0, 9.99, 10, 49.99, 50, 69.99, 70, 79.99, 80, 89.99, 90, 100, np.nan]),
        "複勝回収値": pick([0, 99.99, 100, 149.99, 150, 199.99, 200, 300, np.nan]),
        "単勝適正回収値": pick([0, 99.99, 100, 149.99, 150, 199.99, 200, np.nan]),
    })
    df["結合データ"] = [keys[i] for i in rng.integers(0, len(keys), n)]
    seen = set()
    for mode in ("normal", "odds10u", "odds10_50", "odds50p"):
        want = [classify({**{k: (None if pd.isna(r[c]) else r[c]) for k, c in FIELD_COLUMNS.items()},
                          "key": r["結合データ"]}, mode) for r in df.to_dict("records")]
        assert list(classify_frame(df, mode)) == want, mode
        assert list(classify_frame(df.rename(columns={"結合データ": "key"}), mode)) == want, mode
        seen |= {(w, is_rare_key(k)) for w, k in zip(want, df["結合データ"])}
        # 結合データの列が無ければ、希少条件ではないものとして判定する
        plain = [classify({k: (None if pd.isna(r[c]) else r[c]) for k, c in FIELD_COLUMNS.items()}, mode)
                 for r in df.to_dict("records")]
        assert list(classify_frame(df.drop(columns=["結合データ"]), mode)) == plain, mode
    assert seen == {(c, r) for c in ("keshi", "abs", "axis", "opp", None) for r in (True, False)}   # 全部の組を試している


# ---------------------------------------------------------------- 名前の規約
def test_agg_master_name_and_fast_legacy_parse():
    from shisu import families as F
    from shisu import naming
    src = "主５場（中山、東京、中京、京都、阪神）_吉馬_先行力"
    assert naming.parse_agg_master_name(naming.agg_master_filename(src)) == src
    assert naming.parse_agg_master_name("agg_master_x.csv.gz.part") is None
    assert naming.parse_agg_master_name("foragg_normal_x.csv") is None
    names = F.known_names()

    def slow(stem):                                   # 前の版の当て方（長い名前から順に前方一致）
        s = stem[len("foragg_"):]
        for m in ("odds10_50", "odds10u", "odds50p"):
            if s.startswith(m + "_"):
                s = s[len(m) + 1:]
                break
        if s.startswith("rankband_"):
            s = s[len("rankband_s1_"):]
        return next((f for f in sorted(names, key=len, reverse=True) if s.startswith(f + "_")), None)

    for fam in names:
        for mode in ("normal", "odds10u"):
            for sc in F.get(fam).schemes:
                stem = naming.file_stem(naming.agg_filename(fam, mode, sc, src))
                info = naming.parse_foragg_stem(stem)
                assert info and info["family"] == fam == slow(stem) and info["source"] == src


# ---------------------------------------------------------------- 1ファイルの形
def test_one_file_per_master_with_meta_columns(mb):
    from shisu.aggstore import ROW_META, agg_master_files, read_agg_master
    from shisu.indicators import parse_source_name
    from shisu.naming import agg_filename, table_name_for_foragg
    files = agg_master_files(mb / "agg_csv")
    masters = sorted(p.name for p in (mb / "master_csv").glob("master_*.csv.gz"))
    assert [p.name for p in files] == ["agg_master_" + m[len("master_"):] for m in masters]
    assert not list((mb / "agg_csv").glob("foragg_*.csv")) and not list((mb / "agg_csv").glob("*.part"))
    for p in files:
        df = read_agg_master(p)
        assert list(df.columns[-len(ROW_META):]) == list(ROW_META)
        src = p.name[len("agg_master_"):-len(".csv.gz")]
        region, ind = parse_source_name(src)                # 地域名は NFKC（旧来の成績表と同じ）
        assert set(df["indicator"]) == {ind} and set(df["region"]) == {region}
        want = [table_name_for_foragg(agg_filename(f, m, s, src))
                for f, m, s in zip(df["family"], df["odds_mode"], df["rank_scheme"])]
        assert df["テーブル名"].tolist() == want


# ---------------------------------------------------------------- コンパクト化
def test_compact_keeps_exactly_the_specified_rows(mb):
    """コンパクト化で残る行 = 全部の行のうち、区分（DB と同じ推定値で classify）・WEDGE≧0.70・adjusted_WEDGE≧0.70・
    layer A・件数≧30 のどれかを満たす行（独立に計算して1行も違わない）。"""
    from shisu import families as F
    from shisu.aggstore import prepare_agg_frame
    from shisu.classify import classify
    full, comp = _rows(mb / "agg_full"), _rows(mb / "agg_csv")
    key = ["テーブル名", "結合データ"]
    assert not full.duplicated(key).any() and not comp.duplicated(key).any()
    db = prepare_agg_frame(full)
    layer = {f.name: f.layer for f in F.default_families()}
    want = []
    for r, raw in zip(db.to_dict("records"), full.to_dict("records")):
        cat = classify({"cnt": r["件数"], "fv": r["複勝率_val"], "rv": r["連対率_val"], "top5": r["掲示板率_val"],
                        "frc": r["複勝回収値"], "tra": r["単勝適正回収値"], "key": r["結合データ"]}, r["odds_mode"])
        w, a = r["WEDGE"], r["adjusted_WEDGE"]
        keep = (cat is not None or (w == w and w >= 0.70) or (a == a and a >= 0.70)
                or layer[r["family"]] == "A" or int(r["件数"]) >= 30)
        want.append(keep)
    expect = full[np.array(want)]
    assert 0 < len(comp) < len(full)
    assert set(map(tuple, comp[key].to_numpy())) == set(map(tuple, expect[key].to_numpy()))
    # 残った行の中身は全部の行のときと同じ（並びも同じ）
    pd.testing.assert_frame_equal(comp.reset_index(drop=True), expect.reset_index(drop=True))
    # layer A（初期セットのコース固定）は全部の行が残る
    a_fams = {f for f, ly in layer.items() if ly == "A"}
    assert len(comp[comp["family"].isin(a_fams)]) == len(full[full["family"].isin(a_fams)])


def test_compact_mask_uses_rare_minimum_counts_outside_layer_a():
    """コンパクト化は、希少条件（結合データの部品に G1・芝長 など）の行を、下がった最低件数で区分に入るなら残す
    （layer A 以外の family。件数・WEDGE の下限では残らない行で確かめる）。ふつうの結合データの同じ成績は落とす。"""
    from shisu.aggstore import compact_mask, estimate_percent
    from shisu.classify import classify

    def row(key, cnt, fuku, ren, top5, frc=0.0, tra=0.0):
        return {"結合データ": key, "件数": cnt, "複勝率": f"{fuku:.2f}%（{fuku:.2f}%）", "連対率": f"{ren:.2f}%（{ren:.2f}%）",
                "掲示板率_val": top5, "複勝回収値": frc, "単勝適正回収値": tra, "WEDGE": 0.3, "adjusted_WEDGE": None}

    rare, plain = "芝×芝長×高×1位", "芝×芝マイル×高×1位"
    cases = [("normal", 6, 90.0, 85.0, 95.0, 120.0, 0.0, "abs"),          # 絶対軸: 件数 6（ふつうは 10）
             ("normal", 7, 82.0, 55.0, 92.0, 0.0, 0.0, "axis"),           # 軸候補: 件数 6
             ("normal", 15, 62.0, 40.0, 72.0, 0.0, 0.0, "opp"),           # 相手候補: 件数 15（ふつうは 30）
             ("normal", 12, 3.0, 1.0, 8.0, 0.0, 0.0, "keshi"),            # 消し候補: 件数 12（ふつうは 20）
             ("odds10_50", 15, 36.0, 10.0, 55.0, 0.0, 210.0, "axis"),     # 高回収率: 件数 15（ふつうは 30）
             ("odds50p", 15, 36.0, 10.0, 55.0, 0.0, 210.0, None)]         # 高回収率は 50倍以上では使わない
    for mode, cnt, fuku, ren, top5, frc, tra, want in cases:
        out = pd.DataFrame([row(rare, cnt, fuku, ren, top5, frc, tra), row(plain, cnt, fuku, ren, top5, frc, tra)])
        est = {c: float(estimate_percent(out[c]).iloc[0]) for c in ("複勝率", "連対率")}
        rec = {"cnt": cnt, "fv": est["複勝率"], "rv": est["連対率"], "top5": top5, "frc": frc, "tra": tra}
        assert classify({**rec, "key": rare}, mode) == want and classify({**rec, "key": plain}, mode) is None, (mode, want)
        for layer in ("B", "C", "D", "X", ""):
            got = list(compact_mask(out, mode, layer=layer, min_count=30, min_wedge=0.70))
            assert got == [want is not None, False], (mode, want, layer)
        assert list(compact_mask(out, mode, layer="A")) == [True, True]
        # 結合データの列が無い成績表は、希少条件ではないものとして見る
        assert list(compact_mask(out.drop(columns=["結合データ"]), mode, layer="B", min_count=30, min_wedge=0.70)) == [False, False]


def test_compact_keeps_every_category_key_in_db(mb):
    """コンパクト化した DB と全部の行の DB で、条件ヒット馬の4区分になる行・WEDGE の上位は同じ。"""
    from shisu.classify import sql_condition
    from shisu.odds import ODDS_MODES
    q = ('SELECT "テーブル名", "結合データ" FROM unified_all WHERE odds_mode = ? AND {cond}')
    with sqlite3.connect(mb / "racing.db") as c1, sqlite3.connect(mb / "full.db") as c2:
        for cat in ("abs", "axis", "opp", "keshi"):
            for mode in ODDS_MODES:
                sql = q.format(cond=sql_condition(cat, mode))
                assert set(c1.execute(sql, (mode,))) == set(c2.execute(sql, (mode,))), (cat, mode)
        top = 'SELECT "テーブル名", "結合データ" FROM unified_all WHERE "WEDGE" >= 0.70 OR "adjusted_WEDGE" >= 0.70'
        assert set(c1.execute(top)) == set(c2.execute(top))
        n1 = c1.execute("SELECT COUNT(*) FROM unified_all").fetchone()[0]
        n2 = c2.execute("SELECT COUNT(*) FROM unified_all").fetchone()[0]
        assert 0 < n1 < n2


def test_compact_thresholds_are_options(mb, tmp_path):
    import aggregate_stats_ver2 as agg
    loose = tmp_path / "loose"
    assert agg.main(["--out-dir", str(loose), "--compact-min-count", "5", *YEAR]) == 0
    assert len(_rows(loose)) == len(_rows(mb / "agg_full"))          # 件数≧5（全部のグループ）なら全部残る
    tight = tmp_path / "tight"
    assert agg.main(["--out-dir", str(tight), "--compact-min-count", "100000", "--compact-min-wedge", "99", *YEAR]) == 0
    assert len(_rows(tight)) <= len(_rows(mb / "agg_csv"))
    s = json.loads((tight / "agg_settings.json").read_text(encoding="utf-8"))
    assert s["compact"] == {"enabled": True, "min_count": 100000, "min_wedge": 99.0}
    # none はその条件を使わない（区分と layer A だけで残す）。件数・WEDGE の下限を極端にしたときと同じ行
    off = tmp_path / "off"
    assert agg.main(["--out-dir", str(off), "--compact-min-count", "none", "--compact-min-wedge", "none", *YEAR]) == 0
    assert _bytes(off) == _bytes(tight)
    # 0 以下は受け付けない（下げたのに残る行が減る、という逆向きの動きにしない）
    for bad in (["--compact-min-count", "0"], ["--compact-min-wedge", "0"], ["--compact-min-count", "-1"], ["--compact-min-wedge", "x"]):
        with pytest.raises(SystemExit):
            agg.main(["--out-dir", str(tmp_path / "bad"), *bad, *YEAR])


# ---------------------------------------------------------------- --resume
def _bytes(d: Path) -> list[bytes]:
    return [p.read_bytes() for p in sorted(d.glob("agg_master_*.csv.gz"))]


def test_resume_skips_only_masters_done_with_current_settings(mb, tmp_path, capsys):
    import aggregate_stats_ver2 as agg
    ref = mb / "agg_csv"                                # 既定（コンパクト化）の正解
    out = tmp_path / "agg"
    shutil.copytree(ref, out)
    files = sorted(out.glob("agg_master_*.csv.gz"))
    kept = {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in files[1:]}
    files[0].unlink()
    (out / (files[0].name + ".part")).write_bytes(b"broken")       # 前に止まった実行の書きかけ
    capsys.readouterr()
    assert agg.main(["--out-dir", str(out), "--resume", *YEAR]) == 0
    log = capsys.readouterr().out
    assert "2 本は今の設定で済んでいる" in log and "[1/1]" in log
    assert _bytes(out) == _bytes(ref)                                 # 止まる前と同じ結果
    assert {p.name: (p.read_bytes(), p.stat().st_mtime_ns) for p in files[1:]} == kept   # 済んだ分は触らない
    assert not list(out.glob("*.part"))
    assert agg.main(["--out-dir", str(out), "--resume", "--clean", *YEAR]) == 2


def test_resume_after_interrupted_run_with_other_settings(mb, tmp_path, capsys):
    """設定を変えた実行（--no-compact → 既定）が master 1本目で止まった。前の設定の成績表は、--resume で
    今の設定で作り直す（飛ばして混ぜない。レビューで再現した不具合）。"""
    import aggregate_stats_ver2 as agg
    from shisu.aggstore import stamp_path
    ref, full = mb / "agg_csv", mb / "agg_full"
    mixed = tmp_path / "mixed"
    shutil.copytree(full, mixed)
    first = sorted(ref.glob("agg_master_*.csv.gz"))[0]
    shutil.copy2(first, mixed / first.name)
    shutil.copy2(stamp_path(first), stamp_path(mixed / first.name))
    capsys.readouterr()
    assert agg.main(["--out-dir", str(mixed), "--resume", *YEAR]) == 0
    log = capsys.readouterr().out
    assert "1 本は今の設定で済んでいる" in log and "[2/2]" in log and "compact" in log
    assert _bytes(mixed) == _bytes(ref)


def test_resume_redoes_truncated_files_and_rebuilt_masters(mb, tmp_path, capsys):
    import os
    import aggregate_stats_ver2 as agg
    ref = mb / "agg_csv"
    mdir = tmp_path / "m"
    shutil.copytree(mb / "master_csv", mdir)
    out = tmp_path / "agg"
    shutil.copytree(ref, out)
    files = sorted(out.glob("agg_master_*.csv.gz"))
    files[1].write_bytes(files[1].read_bytes()[: files[1].stat().st_size // 2])     # VM ごと落ちて途中で切れた
    masters = sorted(mdir.glob("master_*.csv.gz"))
    _shrink_master(masters[2])                                                     # replace で master を作り直した
    st = masters[0].stat()
    os.utime(masters[0], ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))           # 更新時刻だけ変わった（コピー・マウント）
    capsys.readouterr()
    assert agg.main(["--in-dir", str(mdir), "--out-dir", str(out), "--resume", *YEAR]) == 0
    assert "1 本は今の設定で済んでいる" in capsys.readouterr().out                 # 中身の同じ masters[0] は飛ばす
    fresh = tmp_path / "fresh"
    assert agg.main(["--in-dir", str(mdir), "--out-dir", str(fresh), *YEAR]) == 0
    assert _bytes(out) == _bytes(fresh)
    assert _bytes(out)[0] == _bytes(ref)[0] and _bytes(out)[2] != _bytes(ref)[2]


def test_all_masters_failing_is_reported_as_failure(mb, tmp_path, monkeypatch, capsys):
    import aggregate_stats_ver2 as agg

    def boom(*a, **k):
        raise MemoryError("simulated")
    monkeypatch.setattr(agg, "aggregate_master", boom)
    capsys.readouterr()
    assert agg.main(["--out-dir", str(tmp_path / "x"), *YEAR]) == 1
    err = capsys.readouterr().err
    assert "すべての master" in err and "--resume" in err and "replace_csv_ver3.py を先に" not in err


# ---------------------------------------------------------------- 並列のまとまり
def test_parallel_batches_recreate_pool_and_match_serial(mb, tmp_path, monkeypatch):
    import concurrent.futures
    import aggregate_stats_ver2 as agg
    created = []

    class Recorder(concurrent.futures.ProcessPoolExecutor):
        def __init__(self, *a, **k):
            created.append(k)
            super().__init__(*a, **k)

    monkeypatch.setattr(agg, "ProcessPoolExecutor", Recorder)
    monkeypatch.setattr(agg, "MASTERS_PER_CHILD", 1)                  # 2本ごとに作り直す（master は3本）
    out = tmp_path / "par"
    assert agg.main(["--out-dir", str(out), "--workers", "2", *YEAR]) == 0
    assert len(created) == 2 and all("max_tasks_per_child" not in k for k in created)
    assert _bytes(out) == _bytes(mb / "agg_csv")
    assert [p.read_bytes() for p in sorted(out.glob("*.done"))] == [p.read_bytes() for p in sorted((mb / "agg_csv").glob("*.done"))]


# ---------------------------------------------------------------- build_db
def _unified(db: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    with sqlite3.connect(db) as con:
        u = pd.read_sql('SELECT * FROM unified_all', con)
        m = pd.read_sql("SELECT table_name, kind, family, odds_mode, rank_scheme, indicator, region, n_rows "
                        "FROM __tables_meta__ WHERE kind='foragg'", con)
    key = ["テーブル名", "結合データ"]
    return u.sort_values(key).reset_index(drop=True), m.sort_values("table_name").reset_index(drop=True)


def test_build_db_reads_legacy_one_file_per_table_the_same(mb, tmp_path):
    """旧来の1表1ファイル（foragg_*.csv）からでも、master ごとの成績表と同じ unified_all・__tables_meta__ になる。"""
    import build_db
    from shisu.aggstore import read_agg_tables
    from shisu.csvio import write_csv
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    for name, t in read_agg_tables(mb / "agg_full").items():
        write_csv(t, legacy / name)
    shutil.copy(mb / "agg_full" / "families.json", legacy / "families.json")
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(legacy), "--db-path", str(tmp_path / "l.db")]) == 0
    u1, m1 = _unified(tmp_path / "l.db")
    u2, m2 = _unified(mb / "full.db")
    pd.testing.assert_frame_equal(u1, u2)
    pd.testing.assert_frame_equal(m1, m2)
    # 同じ master の agg_master_*.csv.gz があれば、旧来のファイルは使わない（二重に入れない）
    both = tmp_path / "both"
    shutil.copytree(legacy, both)
    for p in (mb / "agg_full").glob("agg_master_*.csv.gz"):
        shutil.copy(p, both / p.name)
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(both), "--db-path", str(tmp_path / "b.db")]) == 0
    u3, _ = _unified(tmp_path / "b.db")
    pd.testing.assert_frame_equal(u3, u2)


def test_build_db_skips_truncated_file_and_warns_on_mixed_settings(mb, tmp_path, capsys):
    import build_db
    from shisu.aggstore import stamp_path
    d = tmp_path / "agg"
    shutil.copytree(mb / "agg_csv", d)
    files = sorted(d.glob("agg_master_*.csv.gz"))
    files[0].write_bytes(files[0].read_bytes()[: files[0].stat().st_size // 2])
    capsys.readouterr()
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(d), "--db-path", str(tmp_path / "t.db")]) == 1
    err = capsys.readouterr().err
    assert "読めません" in err and files[0].name in err
    with sqlite3.connect(tmp_path / "t.db") as con:
        inds = {r[0] for r in con.execute("SELECT DISTINCT indicator FROM unified_all")}
        srcs = {r[0] for r in con.execute("SELECT DISTINCT source_file FROM __tables_meta__ WHERE kind='foragg'")}
    assert len(inds) == 2 and files[0].name not in srcs and srcs == {p.name for p in files[1:]}
    # 違う設定（--no-compact）の成績表が混ざっていれば警告する
    m = tmp_path / "mixed"
    shutil.copytree(mb / "agg_csv", m)
    other = sorted((mb / "agg_full").glob("agg_master_*.csv.gz"))[0]
    shutil.copy2(other, m / other.name)
    shutil.copy2(stamp_path(other), stamp_path(m / other.name))
    capsys.readouterr()
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(m), "--db-path", str(tmp_path / "m.db")]) == 0
    assert "違う設定" in capsys.readouterr().err
    capsys.readouterr()
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(mb / "agg_csv"),
                          "--db-path", str(tmp_path / "ok.db")]) == 0
    assert "違う設定" not in capsys.readouterr().err


def test_build_db_drops_old_table_level_foragg_and_keeps_db_on_failure(mb, tmp_path, monkeypatch, capsys):
    import build_db
    p = tmp_path / "old.db"
    shutil.copy(mb / "racing.db", p)
    with sqlite3.connect(p) as con:                      # 前の版の build_db が作った1表ずつのテーブル
        t = con.execute("SELECT table_name FROM __tables_meta__ WHERE kind='foragg' LIMIT 1").fetchone()[0]
        con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM unified_all WHERE "テーブル名" = ?', (t,))
        con.execute('CREATE TABLE "unified_all__build" (x)')             # 前に止まった作り直しの残り
        before = con.execute("SELECT COUNT(*) FROM unified_all").fetchone()[0]
    # 読める成績表が無ければ DB は変えない
    empty = tmp_path / "empty"
    empty.mkdir()
    assert build_db.main(["--mode", "foragg", "--agg-dir", str(empty), "--db-path", str(p)]) == 2
    with sqlite3.connect(p) as con:
        assert con.execute("SELECT COUNT(*) FROM unified_all").fetchone()[0] == before
    assert build_db.main(["--mode", "foragg", "--agg-dir", str(mb / "agg_csv"), "--db-path", str(p)]) == 0
    with sqlite3.connect(p) as con:
        names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert t not in names and "unified_all__build" not in names
        assert con.execute("SELECT COUNT(*) FROM __tables_meta__ WHERE table_name = ?", (t,)).fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM unified_all").fetchone()[0] == before
    u1, m1 = _unified(p)
    u2, m2 = _unified(mb / "racing.db")
    pd.testing.assert_frame_equal(u1, u2)
    pd.testing.assert_frame_equal(m1, m2)
    # 多すぎるときは消さずに残して --fresh を勧める（DROP は本数の2乗で遅い。画面は使わない）
    with sqlite3.connect(p) as con:
        con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM unified_all WHERE "テーブル名" = ?', (t,))
    monkeypatch.setattr(build_db, "DROP_TABLE_LIMIT", 0)
    capsys.readouterr()
    assert build_db.main(["--mode", "foragg", "--agg-dir", str(mb / "agg_csv"), "--db-path", str(p)]) == 0
    assert "--fresh" in capsys.readouterr().err
    with sqlite3.connect(p) as con:
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()


def test_facets_from_stream_match_distinct(mb):
    with sqlite3.connect(mb / "racing.db") as con:
        got = set(con.execute("SELECT col, val FROM __facets__"))
        want = set()
        for c in ("場所", "芝・ダ", "距離", "芝(内・外)"):
            want |= {(c, r[0]) for r in con.execute(
                f'SELECT DISTINCT "{c}" FROM unified_all WHERE "{c}" IS NOT NULL AND "{c}" <> \'\'')}
    assert got == want and got


# ---------------------------------------------------------------- 画面（集計表ごと）
def test_tables_view_same_from_unified_all_and_old_tables(mb, tmp_path, monkeypatch):
    """1表ずつのテーブルが無くても、「集計表ごと」の表示は前の版の DB（1表ずつのテーブルあり）と同じ。
    古い中身の1表ずつのテーブルが残っていても（多すぎて消さなかったとき）、表示は unified_all のとおり。"""
    old, stale = tmp_path / "old.db", tmp_path / "stale.db"
    shutil.copy(mb / "racing.db", old)
    with sqlite3.connect(old) as con:
        show = [r[1] for r in con.execute('PRAGMA table_info("unified_all")')
                if r[1] not in ("family", "odds_mode", "rank_scheme", "indicator", "region", "テーブル名")]
        cols = ", ".join(f'"{c}"' for c in show)
        for (t,) in con.execute("SELECT table_name FROM __tables_meta__ WHERE kind='foragg'").fetchall():
            con.execute(f'CREATE TABLE "{t}" AS SELECT {cols} FROM unified_all WHERE "テーブル名" = ? ORDER BY rowid', (t,))
        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")          # WAL の中身を本体に（ファイルをコピーするので）
    shutil.copy(old, stale)
    with sqlite3.connect(stale) as con:
        for (t,) in con.execute("SELECT table_name FROM __tables_meta__ WHERE kind='foragg'").fetchall():
            con.execute(f'UPDATE "{t}" SET "件数" = 99999, "複勝率" = \'stale\'')
        con.commit()
    bodies = {}
    import app as app_module
    queries = [
        {},
        {"sort": "WEDGE", "sortdir": "DESC"},
        {"sort": "件数", "sortdir": "ASC", "odds_mode": "odds10u"},
        {"preset": "keshi"},
        {"preset": "opp", "family": "normal"},
        {"place": "東京", "sd": "ダ", "min_cnt": "10"},
        {"ind": "SAV", "sort": "adjusted_WEDGE", "min_adj": "0.5"},
    ]
    for name, db in (("old", old), ("stale", stale), ("new", mb / "racing.db")):
        monkeypatch.setenv("DB_PATH", str(db))
        am = importlib.reload(app_module)
        c = am.app.test_client()
        bodies[name] = [c.get("/", query_string={"submitted": "1", "view": "tables", **qs}).get_data(as_text=True)
                        for qs in queries]
    for qs, a, b, c in zip(queries, bodies["old"], bodies["stale"], bodies["new"]):
        assert a == b == c, qs
    assert any("<td>" in b for b in bodies["new"]) and not any("99999" in b for b in bodies["new"])


# ---------------------------------------------------------------- 検証で再現した不具合の再発防止（2026-11-10）
def test_failed_master_read_leaves_no_old_agg_file(mb, tmp_path, capsys):
    """master が読めない（途中で切れた等）とき、その master の前の成績表と印を残さない（build_db が黙って使わない）。"""
    import aggregate_stats_ver2 as agg
    import build_db
    from shisu.aggstore import stamp_path
    mdir, out = tmp_path / "m", tmp_path / "agg"
    shutil.copytree(mb / "master_csv", mdir)
    shutil.copytree(mb / "agg_csv", out)
    bad = sorted(mdir.glob("master_*.csv.gz"))[0]
    bad.write_bytes(bad.read_bytes()[: bad.stat().st_size // 3])
    old = out / ("agg_master_" + bad.name[len("master_"):])
    assert old.exists() and stamp_path(old).exists()
    assert agg.main(["--in-dir", str(mdir), "--out-dir", str(out), *YEAR]) == 1
    assert not old.exists() and not stamp_path(old).exists()
    capsys.readouterr()
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(out), "--db-path", str(tmp_path / "x.db")]) == 0
    with sqlite3.connect(tmp_path / "x.db") as con:
        assert con.execute("SELECT COUNT(DISTINCT indicator) FROM unified_all").fetchone()[0] == 2


def test_master_rebuilt_during_aggregation_gets_no_stamp(mb, tmp_path, monkeypatch, capsys):
    import aggregate_stats_ver2 as agg
    import build_db
    from shisu.aggstore import stamp_path
    mdir, out = tmp_path / "m", tmp_path / "agg"
    shutil.copytree(mb / "master_csv", mdir)
    target = sorted(mdir.glob("master_*.csv.gz"))[1]
    real = agg.read_csv_any

    def read_then_rebuild(path, *a, **k):              # 読んだあとに replace が master を作り直した
        df = real(path, *a, **k)
        if Path(path).name == target.name:
            _shrink_master(target)
        return df
    monkeypatch.setattr(agg, "read_csv_any", read_then_rebuild)
    capsys.readouterr()
    assert agg.main(["--in-dir", str(mdir), "--out-dir", str(out), *YEAR]) == 0
    log = capsys.readouterr().out
    assert "作り直されたので書き終わりの印を書きません" in log
    assert "年度窓の終わり: " in log or "年度窓の終わり（master の最終年）" in log    # 年度の窓のまとめから落ちない
    done = out / ("agg_master_" + target.name[len("master_"):])
    assert done.exists() and not stamp_path(done).exists()
    # 印の無い成績表があれば build_db が警告する（全部の成績表に印が無くても）
    capsys.readouterr()
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(out), "--db-path", str(tmp_path / "x.db")]) == 0
    assert "印" in capsys.readouterr().err
    only = tmp_path / "only"
    only.mkdir()
    shutil.copy(done, only / done.name)
    shutil.copy(out / "families.json", only / "families.json")
    capsys.readouterr()
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(only), "--db-path", str(tmp_path / "y.db")]) == 0
    assert "印" in capsys.readouterr().err
    monkeypatch.setattr(agg, "read_csv_any", real)
    capsys.readouterr()
    assert agg.main(["--in-dir", str(mdir), "--out-dir", str(out), "--resume", *YEAR]) == 0
    assert "2 本は今の設定で済んでいる" in capsys.readouterr().out and stamp_path(done).exists()


def test_wedge_window_summary_keeps_masters_with_a_note(capsys):
    import aggregate_stats_ver2 as agg
    agg._print_wedge_coverage(["[OK] a.csv.gz: 1 tables groups=1(adj 1) kept=1/1 / 年度窓の終わり=2025 / [WARN] 印を書きません",
                               "[OK] b.csv.gz: 1 tables groups=1(adj 1) kept=1/1 / 年度窓の終わり=2024"])
    err = capsys.readouterr().err
    assert "2024: 1本" in err and "2025: 1本" in err


def test_classify_thresholds_are_part_of_settings(monkeypatch):
    import aggregate_stats_ver2 as agg
    from shisu import classify
    from shisu.aggstore import settings_hash
    on, off = agg.compact_options(True), agg.compact_options(False)
    h_on, h_off = (settings_hash(agg.settings_of([], set(), {}, c)) for c in (on, off))
    monkeypatch.setattr(classify, "ABS_REN", 79.0)
    assert settings_hash(agg.settings_of([], set(), {}, on)) != h_on        # コンパクト化で残す行が変わる
    assert settings_hash(agg.settings_of([], set(), {}, off)) == h_off      # 全部の行を書くなら関係ない
    from shisu import odds, wedge
    monkeypatch.setattr(odds, "ODDS_BREAKS", (8.0, 50.0))                 # オッズ帯の境目
    h2 = settings_hash(agg.settings_of([], set(), {}, off))
    assert h2 != h_off
    monkeypatch.setattr(wedge, "EDGE_TABLE", wedge.EDGE_TABLE[:-1])       # EDGE の表
    assert settings_hash(agg.settings_of([], set(), {}, off)) != h2


def test_build_db_reports_unreadable_files_and_does_not_substitute_legacy(mb, tmp_path, capsys):
    import build_db
    from shisu.aggstore import read_agg_tables
    from shisu.csvio import write_csv
    d = tmp_path / "agg"
    shutil.copytree(mb / "agg_csv", d)
    files = sorted(d.glob("agg_master_*.csv.gz"))
    # 同じ master の旧来の成績表（前の版の残り）
    src0 = files[0].name[len("agg_master_"):-len(".csv.gz")]
    for name, t in read_agg_tables(mb / "agg_full").items():
        if name.endswith(f"_{src0}.csv"):
            write_csv(t, d / name)
    files[0].write_bytes(files[0].read_bytes()[:100])             # 先頭から切れた
    files[1].write_bytes(b"this is not gzip\n")                   # gzip でない
    capsys.readouterr()
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(d), "--db-path", str(tmp_path / "x.db")]) == 1
    err = capsys.readouterr().err
    assert err.count("読めません") >= 2 and files[0].name in err and files[1].name in err
    with sqlite3.connect(tmp_path / "x.db") as con:
        srcs = {r[0] for r in con.execute("SELECT DISTINCT source_file FROM __tables_meta__ WHERE kind='foragg'")}
    assert srcs == {files[2].name}                               # 旧来のファイルで代わりに埋めない


def test_build_db_warns_on_legacy_mix_and_rebuilt_master(mb, tmp_path, capsys):
    import os
    import aggregate_stats_ver2 as agg
    import build_db
    from shisu.aggstore import read_agg_tables
    from shisu.csvio import write_csv
    d = tmp_path / "agg"
    shutil.copytree(mb / "agg_csv", d)
    first = sorted(d.glob("agg_master_*.csv.gz"))[0]
    src0 = first.name[len("agg_master_"):-len(".csv.gz")]
    for name, t in read_agg_tables(mb / "agg_csv").items():        # その master は旧来の形だけにする
        if name.endswith(f"_{src0}.csv"):
            write_csv(t, d / name)
    first.unlink()
    (d / (first.name + ".done")).unlink()
    capsys.readouterr()
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(d), "--db-path", str(tmp_path / "a.db")]) == 0
    assert "旧来の成績表" in capsys.readouterr().err
    # master を作り直したら警告する（集計が読んだ場所の master と中身で比べる。更新時刻だけの違いは警告しない）
    mdir, out = tmp_path / "m", tmp_path / "out"
    shutil.copytree(mb / "master_csv", mdir)
    assert agg.main(["--in-dir", str(mdir), "--out-dir", str(out), *YEAR]) == 0
    masters = sorted(mdir.glob("master_*.csv.gz"))
    st = masters[0].stat()
    os.utime(masters[0], ns=(st.st_atime_ns, st.st_mtime_ns + 10**9))
    capsys.readouterr()
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(out), "--db-path", str(tmp_path / "b.db")]) == 0
    assert "作り直されています" not in capsys.readouterr().err
    _shrink_master(masters[1])
    capsys.readouterr()
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(out), "--db-path", str(tmp_path / "c.db")]) == 0
    assert "master が作り直されています" in capsys.readouterr().err


def test_zero_row_master_does_not_bring_back_legacy_files(mb, tmp_path):
    import json as _json
    import build_db
    from shisu.aggstore import read_agg_tables, stamp_path
    from shisu.csvio import write_csv
    d = tmp_path / "agg"
    shutil.copytree(mb / "agg_csv", d)
    first = sorted(d.glob("agg_master_*.csv.gz"))[0]
    src0 = first.name[len("agg_master_"):-len(".csv.gz")]
    for name, t in read_agg_tables(mb / "agg_full").items():       # 前の版の古い成績表
        if name.endswith(f"_{src0}.csv"):
            write_csv(t, d / name)
    st = _json.loads(stamp_path(first).read_text(encoding="utf-8"))
    first.unlink()                                                  # 今の版で集計したら0行だった
    stamp_path(first).write_text(_json.dumps({**st, "rows": 0, "bytes": 0}), encoding="utf-8")
    assert build_db.main(["--fresh", "--mode", "foragg", "--agg-dir", str(d), "--db-path", str(tmp_path / "z.db")]) == 0
    with sqlite3.connect(tmp_path / "z.db") as con:
        assert con.execute("SELECT COUNT(DISTINCT indicator) FROM unified_all").fetchone()[0] == 2


def test_prune_keeps_table_list_when_old_tables_are_left(mb, tmp_path, monkeypatch):
    import build_db
    p = tmp_path / "p.db"
    shutil.copy(mb / "racing.db", p)
    with sqlite3.connect(p) as con:
        t = con.execute("SELECT table_name FROM __tables_meta__ WHERE kind='foragg' LIMIT 1").fetchone()[0]
        con.execute(f'CREATE TABLE "{t}" AS SELECT * FROM unified_all WHERE "テーブル名" = ?', (t,))
        n = con.execute("SELECT COUNT(*) FROM __tables_meta__ WHERE kind='foragg'").fetchone()[0]
    monkeypatch.setattr(build_db, "DROP_TABLE_LIMIT", 0)
    assert build_db.main(["--mode", "both", "--prune", "--agg-dir", str(mb / "agg_csv"), "--db-path", str(p)]) == 0
    with sqlite3.connect(p) as con:
        assert con.execute("SELECT COUNT(*) FROM __tables_meta__ WHERE kind='foragg'").fetchone()[0] == n
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", (t,)).fetchone()
