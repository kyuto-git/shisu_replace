"""馬体重の発表後のマイニング確定値の差し替え（calc_entry_hensachi・build_db の --mining-only / --indicators）と、
process_df の列の組み立て（PerformanceWarning を出さない）。"""
import shutil
import sqlite3
import sys
import warnings
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

from shisu.csvio import read_csv_any  # noqa: E402

YEAR = ["--wedge-min-year-count", "2"]
INDICATORS = ("タイムM", "対戦M", "オメガ指数", "Gold")
MERGED = "entries_merged_hensachi.csv"
XJOIN = "entries_xjoin_merged_hensachi"


def entries_frame(confirmed: bool) -> pd.DataFrame:
    """出馬表1本（マイニング値・対戦型マイニング値つき。外部指数の枠 GoldAirplane は TARGET で読み込んでいないので 0）。
    confirmed=True は馬体重の発表後: マイニングの確定値（一部の馬で値と順位が変わる）と馬体重・増減が入る。"""
    e1, _ = synth.entries_frames()
    df = e1.drop(columns=["SAV", "指数順位1", "ZI", "指数順位2"]).copy()
    n = len(df)
    df["GoldAirplane"] = "0"
    df["指数順位1"] = "0"
    time_m = [40 + (i * 11) % 60 for i in range(n)]
    match_m = [45 + (i * 7) % 50 for i in range(n)]
    if confirmed:
        time_m = [v + (9 if i % 5 == 0 else 0) for i, v in enumerate(time_m)]
        match_m = [v - (8 if i % 4 == 1 else 0) for i, v in enumerate(match_m)]
    df["マイニング値"] = [str(v) for v in time_m]
    df["対戦型マイニング値"] = [str(v) for v in match_m]
    for val, rank in (("マイニング値", "マイニング順位"), ("対戦型マイニング値", "対戦型マイニング順位")):
        key = df["レースID(新)"].str.slice(0, 16)
        df[rank] = pd.to_numeric(df[val]).groupby(key).rank(method="min", ascending=False).astype(int).astype(str)
    df["馬体重"] = [str(440 + (i * 6) % 80) for i in range(n)] if confirmed else ""
    df["増減"] = [str((i % 7) * 2 - 6) for i in range(n)] if confirmed else ""
    return df


def write_ext(ext: Path, rids: list[str], shift: int = 0) -> None:
    (ext / "OMEGA").mkdir(parents=True, exist_ok=True)
    (ext / "OMEGA" / "20260531_omega.csv").write_text(
        "レースID(新),ワーク1\n" + "\n".join(f"{r},{40 + (i * 13 + shift) % 37}" for i, r in enumerate(rids[:-3])) + "\n",
        encoding="cp932")
    (ext / "GoldAirplane").mkdir(exist_ok=True)
    lines = []
    for r16 in sorted({r[:16] for r in rids}):
        vals = ",".join(f"{50 + (u * 3) % 40},{(u * 5) % 16 + 1}" for u in range(1, 17))
        lines.append(f"{r16[:10] + r16[14:16]},{vals}")
    (ext / "GoldAirplane" / "gold.csv").write_text("\n".join(lines) + "\n", encoding="cp932")


@pytest.fixture(scope="module")
def monkeypatch_module():
    mp = pytest.MonkeyPatch()
    yield mp
    mp.undo()


@pytest.fixture(scope="module")
def mo(tmp_path_factory, monkeypatch_module):
    """過去データ（4指数）・ext_indices（オメガ・Gold）・出馬表（確定前）で全段を通した状態。"""
    import aggregate_stats_ver2 as agg
    import build_db
    import calc_entry_hensachi as calc
    import replace_csv_ver3
    from shisu import families as F
    b = tmp_path_factory.mktemp("mining")
    monkeypatch_module.setenv("SHISU_BASE_DIR", str(b))
    monkeypatch_module.delenv("DB_PATH", raising=False)
    monkeypatch_module.delenv("SHISU_EXT_DIR", raising=False)
    F.reset()
    synth.make_history(b, indicators=INDICATORS)
    before = entries_frame(False)
    synth.write_cp932(before, b / "entries_in" / "shisu_20260531.csv")
    write_ext(b / "ext_indices", before["レースID(新)"].tolist())
    assert replace_csv_ver3.main(["--clean", "--no-ext"]) == 0
    assert agg.main(["--clean", "--no-compact", *YEAR]) == 0
    assert calc.main(["--merge-only", "--clean"]) == 0
    assert build_db.main(["--fresh"]) == 0
    yield b
    F.reset()


def _work(mo: Path, tmp: Path, frame: pd.DataFrame | None = None) -> dict[str, Path]:
    """テストごとの作業用のコピー（入力・出力・DB）。入力は確定後の出馬表に差し替える。"""
    d = {"in": tmp / "in", "out": tmp / "out", "db": tmp / "racing.db", "ext": mo / "ext_indices"}
    shutil.copytree(mo / "entries_out", d["out"])
    synth.write_cp932(entries_frame(True) if frame is None else frame, d["in"] / "shisu_20260531.csv")
    with sqlite3.connect(mo / "racing.db") as con:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    shutil.copy(mo / "racing.db", d["db"])
    return d


def _calc(d: dict, *args: str) -> int:
    import calc_entry_hensachi as calc
    return calc.main(["--in-dir", str(d["in"]), "--out-dir", str(d["out"]), "--ext-dir", str(d["ext"]), *args])


def _full(mo: Path, tmp: Path, frame: pd.DataFrame | None = None) -> dict[str, Path]:
    """同じ入力で通常の処理（全指数）をした結果。"""
    d = _work(mo, tmp, frame)
    assert _calc(d, "--merge-only", "--clean") == 0
    return d


def _xjoin_rows(db: Path, where: str = "") -> list[tuple]:
    with sqlite3.connect(db) as con:
        return sorted(con.execute(f'SELECT * FROM "{XJOIN}" {where}').fetchall(), key=repr)


# ---------------------------------------------------------------- calc_entry_hensachi
def test_mining_only_matches_full_run_without_reading_ext(mo, tmp_path, monkeypatch, capsys):
    """--mining-only の結果は、同じ入力で通常の処理をしたときと列の並びも値も同じ。ext_indices は読まない。"""
    from shisu import ext_indices
    v1 = read_csv_any(mo / "entries_out" / MERGED)
    full = read_csv_any(_full(mo, tmp_path / "full")["out"] / MERGED)
    d = _work(mo, tmp_path / "part")

    def boom(*a, **k):
        raise AssertionError("ext_indices を読んではいけない")
    monkeypatch.setattr(ext_indices, "discover", boom)
    monkeypatch.setattr(ext_indices, "load_source", boom)
    capsys.readouterr()
    assert _calc(d, "--mining-only") == 0
    log = capsys.readouterr().out
    part = read_csv_any(d["out"] / MERGED)
    assert list(part.columns) == list(full.columns)
    pd.testing.assert_frame_equal(part, full)
    assert "差し替えました" in log and "タイムM・対戦M" in log
    # 変わったのはマイニングの列と基本の列だけ。ほかの指数（ext_indices から付けた値）は前のまま
    changed = [c for c in v1.columns if not v1[c].equals(part[c])]
    assert {"マイニング値", "タイムM_順位", "タイムM_偏差値", "対戦型マイニング値", "対戦M_順位", "馬体重", "増減"} <= set(changed)
    assert all(c.startswith(("タイムM_", "対戦M_")) or c in ("マイニング値", "マイニング順位", "対戦型マイニング値",
                                                         "対戦型マイニング順位", "馬体重", "増減") for c in changed), changed
    for c in ("オメガ指数", "オメガ指数_順位", "オメガ指数_偏差値", "GoldAirplane", "指数順位1", "Gold_順位", "Gold_偏差値帯"):
        assert v1[c].equals(part[c]) and (part[c] != "").any(), c
    assert (part["GoldAirplane"] != "0").any()             # 入力の 0 で、ext_indices から付けた Gold の値を上書きしない
    assert not list(d["out"].glob("*.part"))


def test_indicators_option_aliases_clean_and_errors(mo, tmp_path, capsys):
    import calc_entry_hensachi as calc
    assert calc.parse_indicators("マイニング値, 対戦型マイニング値，タイムM") == ["タイムM", "対戦M"]
    with pytest.raises(ValueError):
        calc.parse_indicators("タイムM,知らない指数")
    full = read_csv_any(_full(mo, tmp_path / "full")["out"] / MERGED)
    d = _work(mo, tmp_path / "a")
    (d["out"] / "old_hensachi.csv").write_text("x\n1\n", encoding="cp932")
    assert _calc(d, "--indicators", "マイニング値,対戦M", "--clean") == 0        # --clean でも統合ファイルは消さずに読む
    pd.testing.assert_frame_equal(read_csv_any(d["out"] / MERGED), full)
    assert [p.name for p in d["out"].glob("*.csv")] == [MERGED]                  # ほかの古い CSV は消す
    assert _calc(d, "--indicators", "知らない指数") == 2
    assert _calc(d, "--indicators", "") == 2 and _calc(d, "--indicators", " , ") == 2     # 空の指定で全部の計算にしない
    # タイムM だけ: 対戦M は前（確定前）のまま
    e = _work(mo, tmp_path / "b")
    assert _calc(e, "--indicators", "タイムM") == 0
    one = read_csv_any(e["out"] / MERGED)
    v1 = read_csv_any(mo / "entries_out" / MERGED)
    assert one["タイムM_順位"].equals(full["タイムM_順位"]) and one["対戦M_順位"].equals(v1["対戦M_順位"])
    assert not one["対戦M_順位"].equals(full["対戦M_順位"])


def test_indicators_can_refresh_one_ext_indicator(mo, tmp_path):
    """--indicators オメガ指数: その指数だけ ext_indices から読み直す（ほかの外部指数は読まない・前のまま）。"""
    from shisu import ext_indices
    ext = tmp_path / "ext"
    rids = entries_frame(True)["レースID(新)"].tolist()
    write_ext(ext, rids, shift=5)                          # オメガの値が変わった
    d = _work(mo, tmp_path / "part", entries_frame(False))
    d["ext"] = ext
    f = _work(mo, tmp_path / "full", entries_frame(False))
    f["ext"] = ext
    assert _calc(f, "--merge-only", "--clean") == 0
    loaded = []
    real = ext_indices.load_source
    try:
        ext_indices.load_source = lambda src, **k: (loaded.append(src.indicator), real(src, **k))[1]
        assert _calc(d, "--indicators", "オメガ指数") == 0
    finally:
        ext_indices.load_source = real
    assert loaded == ["オメガ指数"]
    part, full = read_csv_any(d["out"] / MERGED), read_csv_any(f["out"] / MERGED)
    pd.testing.assert_frame_equal(part, full)
    assert not part["オメガ指数"].equals(read_csv_any(mo / "entries_out" / MERGED)["オメガ指数"])


def test_mining_only_falls_back_to_full_run(mo, tmp_path, capsys):
    """統合ファイルが無い・入力に新しい馬がいるときは、通常の処理（全指数）に切り替える。"""
    full = read_csv_any(_full(mo, tmp_path / "full")["out"] / MERGED)
    d = _work(mo, tmp_path / "nomerged")
    (d["out"] / MERGED).unlink()
    capsys.readouterr()
    assert _calc(d, "--mining-only") == 0
    assert "通常の処理" in capsys.readouterr().out
    pd.testing.assert_frame_equal(read_csv_any(d["out"] / MERGED), full)
    # 新しい馬（出馬表が変わった）
    more = entries_frame(True)
    extra = more.iloc[[0]].copy()
    extra["レースID(新)"] = extra["レースID(新)"].str.slice(0, 16) + "17"
    extra["馬番"] = "17"
    more = pd.concat([more, extra], ignore_index=True)
    e = _work(mo, tmp_path / "newhorse", more)
    g = _full(mo, tmp_path / "newhorse_full", more)
    capsys.readouterr()
    assert _calc(e, "--mining-only") == 0
    assert "前の統合ファイルに無い馬が 1 頭" in capsys.readouterr().out
    pd.testing.assert_frame_equal(read_csv_any(e["out"] / MERGED), read_csv_any(g["out"] / MERGED))
    # 入力のレースから馬が消えた（取消の馬を除いて出し直した など）→ そのレースは全指数の順位が変わるので、通常の処理に切り替える
    fewer = entries_frame(True).iloc[:-2]
    h = _work(mo, tmp_path / "fewer", fewer)
    capsys.readouterr()
    assert _calc(h, "--mining-only") == 0
    assert "前の統合ファイルにいた馬が 2 頭消えています" in capsys.readouterr().out
    h2 = _full(mo, tmp_path / "fewer_full", fewer)
    got = read_csv_any(h["out"] / MERGED)
    assert len(got) == len(fewer)
    pd.testing.assert_frame_equal(got, read_csv_any(h2["out"] / MERGED))
    # 入力に無いレース（別の出馬表の分など）の行は前のまま残し、入力にあるレースだけを差し替える
    v1 = read_csv_any(mo / "entries_out" / MERGED)
    two = entries_frame(True).iloc[:32]
    w = _work(mo, tmp_path / "two", two)
    capsys.readouterr()
    assert _calc(w, "--mining-only") == 0
    assert "入力に無いレースの 16 頭の行は前のまま" in capsys.readouterr().out
    got = read_csv_any(w["out"] / MERGED)
    assert len(got) == len(v1) and got.iloc[32:].reset_index(drop=True).equals(v1.iloc[32:].reset_index(drop=True))
    assert got.iloc[:32].reset_index(drop=True).equals(full.iloc[:32].reset_index(drop=True))
    # 入力にマイニングの列が無ければ警告して、その指数は前のまま
    nomining = entries_frame(True).drop(columns=["対戦型マイニング値", "対戦型マイニング順位"])
    k = _work(mo, tmp_path / "nocol", nomining)
    capsys.readouterr()
    assert _calc(k, "--mining-only") == 0
    assert "対戦M の列がありません" in capsys.readouterr().out
    assert read_csv_any(k["out"] / MERGED)["対戦M_順位"].equals(v1["対戦M_順位"])


# ---------------------------------------------------------------- build_db
def test_build_db_mining_only_rematches_only_those_indicators(mo, tmp_path, monkeypatch, capsys):
    """build_db --entries-only --mining-only: 照合結果は base がタイムM・対戦M の行だけを消して作り直す。
    結果は全部の指数を照合し直したときと同じ。ほかの指数の行は触らない（rowid もそのまま）。"""
    import build_db
    from shisu.naming import ENTRIES_PREFIX
    d = _work(mo, tmp_path / "part")
    assert _calc(d, "--mining-only") == 0
    f = _full(mo, tmp_path / "full")
    others = 'WHERE base NOT IN (\'タイムM\', \'対戦M\')'
    with sqlite3.connect(d["db"]) as con:
        before_other = con.execute(f'SELECT rowid, * FROM "{XJOIN}" {others} ORDER BY rowid').fetchall()
        before_mining = _xjoin_rows(d["db"], "WHERE base IN ('タイムM', '対戦M')")
    seen: list[tuple] = []
    real = build_db.iter_hit_rows

    def spy(con, row, bases, *a, **k):
        seen.append(tuple(bases))
        return real(con, row, bases, *a, **k)
    monkeypatch.setattr(build_db, "iter_hit_rows", spy)
    capsys.readouterr()
    assert build_db.main(["--entries-only", "--mining-only", "--entries-dir", str(d["out"]), "--db-path", str(d["db"])]) == 0
    log = capsys.readouterr().out
    assert "タイムM・対戦M の行だけ作り直しました" in log and "mode=entries" in log
    assert seen and set(seen) == {("タイムM", "対戦M")}                    # 照合したのは2指数だけ
    monkeypatch.setattr(build_db, "iter_hit_rows", real)
    assert build_db.main(["--entries-only", "--entries-dir", str(f["out"]), "--db-path", str(f["db"])]) == 0
    assert _xjoin_rows(d["db"]) == _xjoin_rows(f["db"]) and _xjoin_rows(d["db"])
    with sqlite3.connect(d["db"]) as con, sqlite3.connect(f["db"]) as con2:
        after_other = con.execute(f'SELECT rowid, * FROM "{XJOIN}" {others} ORDER BY rowid').fetchall()
        t = ENTRIES_PREFIX + "merged_hensachi"
        assert con.execute(f'SELECT * FROM "{t}"').fetchall() == con2.execute(f'SELECT * FROM "{t}"').fetchall()
        meta = dict(con.execute("SELECT table_name, n_rows FROM __tables_meta__ WHERE kind='xjoin'").fetchall())
        assert meta[XJOIN] == con.execute(f'SELECT COUNT(*) FROM "{XJOIN}"').fetchone()[0]
        bases = {r[0] for r in con.execute(f'SELECT DISTINCT base FROM "{XJOIN}"')}
    assert after_other == before_other and before_other
    assert bases == set(INDICATORS)
    assert _xjoin_rows(d["db"], "WHERE base IN ('タイムM', '対戦M')") != before_mining     # 確定値で照合結果が変わった


def test_build_db_mining_only_rematches_everything_when_other_things_changed(mo, tmp_path, monkeypatch, capsys):
    """マイニング以外の、照合に関わる値（騎手の乗り替わり・馬の取消）が変わっていたら、--mining-only でも全部の指数を照合し直す。
    2コマンドの結果は、通常の処理（全部やり直し）と同じ DB になる。"""
    import build_db
    cases = {}
    swap = entries_frame(True)
    swap.loc[swap.index[:3], "騎手"] = swap["騎手"].iloc[5]                    # 乗り替わり（3頭）
    cases["jockey"] = swap
    cases["scratched"] = entries_frame(True).drop(index=[1]).reset_index(drop=True)   # 1頭が出馬表から消えた
    for name, frame in cases.items():
        d = _work(mo, tmp_path / name, frame)
        f = _full(mo, tmp_path / (name + "_full"), frame)
        assert _calc(d, "--mining-only") == 0
        pd.testing.assert_frame_equal(read_csv_any(d["out"] / MERGED), read_csv_any(f["out"] / MERGED))
        seen: list[tuple] = []
        real = build_db.iter_hit_rows

        def spy(con, row, bases, *a, _seen=seen, _real=real, **k):
            _seen.append(tuple(bases))
            return _real(con, row, bases, *a, **k)
        monkeypatch.setattr(build_db, "iter_hit_rows", spy)
        capsys.readouterr()
        assert build_db.main(["--entries-only", "--mining-only", "--entries-dir", str(d["out"]), "--db-path", str(d["db"])]) == 0
        log = capsys.readouterr().out
        assert "全部の指数を照合し直します" in log and "行だけ作り直しました" not in log, name
        assert seen and all(set(b) == set(INDICATORS) for b in seen), name       # 全部の指数を照合した
        monkeypatch.setattr(build_db, "iter_hit_rows", real)
        assert build_db.main(["--entries-only", "--entries-dir", str(f["out"]), "--db-path", str(f["db"])]) == 0
        assert _xjoin_rows(d["db"]) == _xjoin_rows(f["db"]) and _xjoin_rows(d["db"]), name
    # 乗り替わった馬の行は、ほかの指数でも新しい騎手になっている
    with sqlite3.connect(tmp_path / "jockey" / "racing.db") as con:
        rid = swap["レースID(新)"].iloc[0]
        got = {r[0] for r in con.execute(f'SELECT DISTINCT "騎手" FROM "{XJOIN}" WHERE "レースID(新)" = ?', (rid,))}
    assert got <= {swap["騎手"].iloc[0]}


def test_build_db_mining_only_rematches_everything_after_unified_rebuild(mo, tmp_path, capsys):
    """成績表（unified_all）を作り直したあと（--mode foragg だけ回したあと）の --mining-only は、全部の指数を照合し直す。
    そのあとは（成績表が変わっていないので）一部の作り直しに戻る。"""
    import build_db
    d = _work(mo, tmp_path / "stale")
    assert _calc(d, "--mining-only") == 0
    args = ["--entries-only", "--mining-only", "--entries-dir", str(d["out"]), "--db-path", str(d["db"])]
    assert build_db.main(["--mode", "foragg", "--agg-dir", str(mo / "agg_csv"), "--db-path", str(d["db"])]) == 0
    capsys.readouterr()
    assert build_db.main(args) == 0
    log = capsys.readouterr().out
    assert "今の成績表（unified_all）より前に作った照合結果" in log and "行だけ作り直しました" not in log
    assert build_db.main(args) == 0
    assert "タイムM・対戦M の行だけ作り直しました" in capsys.readouterr().out
    f = _full(mo, tmp_path / "stale_full")
    assert build_db.main(["--entries-only", "--entries-dir", str(f["out"]), "--db-path", str(f["db"])]) == 0
    assert _xjoin_rows(d["db"]) == _xjoin_rows(f["db"]) and _xjoin_rows(d["db"])


def test_partial_blocker_ignores_target_indicators_and_weight(mo, tmp_path):
    """一部の作り直しで済ませてよいかの判定: 指定した指数の順位・帯と、照合に使わない列（馬体重など）の違いは無視し、
    ほかの指数・騎手・前走の条件・馬の増減・成績表の作り直し・記録が無いことは拾う。"""
    import build_db
    d = _work(mo, tmp_path / "sig")
    t, mining = "entries_merged_hensachi", ["タイムM", "対戦M"]
    with sqlite3.connect(d["db"]) as con:
        rec = build_db.load_xjoin_record(con, XJOIN)
        assert rec is not None and set(rec["bases"]) == set(INDICATORS)        # build_db が照合結果と一緒に残した記録
        assert rec == build_db.xjoin_record(con, t)
        assert build_db.partial_blocker(rec, build_db.xjoin_record(con, t), mining) is None
        assert "記録がありません" in build_db.partial_blocker(None, rec, mining)
        assert "成績表" in build_db.partial_blocker({**rec, "unified": "other"}, rec, mining)
        con.execute(f'UPDATE "{t}" SET "タイムM_順位" = ?, "対戦M_偏差値帯" = ?, "馬体重" = ?', ("9", "40以下", "500"))
        now = build_db.xjoin_record(con, t)
        assert build_db.partial_blocker(rec, now, mining) is None
        assert "ほかの指数（タイムM）" in build_db.partial_blocker(rec, now, ["対戦M"])
        for sql, params, word in (
                (f'UPDATE "{t}" SET "オメガ指数_順位" = ? WHERE "馬番" = ?', ("1", "5"), "ほかの指数（オメガ指数）"),
                (f'UPDATE "{t}" SET "Gold_偏差値帯" = ? WHERE "馬番" = ?', ("71以上", "5"), "ほかの指数（Gold）"),
                (f'UPDATE "{t}" SET "騎手" = ? WHERE "馬番" = ?', ("別の騎手", "5"), "馬・騎手"),
                (f'UPDATE "{t}" SET "前走着順" = ? WHERE "馬番" = ?', ("1", "5"), "馬・騎手"),
                (f'DELETE FROM "{t}" WHERE "馬番" = ?', ("5",), "馬・騎手")):
            con.execute("SAVEPOINT s")
            con.execute(sql, params)
            why = build_db.partial_blocker(rec, build_db.xjoin_record(con, t), mining)
            assert why and word in why, (sql, why)
            con.execute("ROLLBACK TO s")
        assert build_db.partial_blocker(rec, build_db.xjoin_record(con, t), mining) is None
        con.rollback()


def test_build_db_mining_only_after_interrupted_runs(mo, tmp_path, monkeypatch, capsys):
    """途中で止まった実行のあとのやり直しでも、前の照合結果を「今の出馬表のもの」と見誤らない。
    照合結果とその記録は1つのトランザクションで書くので、止まれば前の照合結果と記録に戻り、次の実行は全部の指数を照合し直す。"""
    import build_db
    swap = entries_frame(True)
    swap.loc[swap.index[:5], "騎手"] = "乗替騎手"
    d = _work(mo, tmp_path / "retry", swap)
    f = _full(mo, tmp_path / "retry_full", swap)
    assert build_db.main(["--entries-only", "--entries-dir", str(f["out"]), "--db-path", str(f["db"])]) == 0
    assert _calc(d, "--mining-only") == 0
    args = ["--entries-only", "--mining-only", "--entries-dir", str(d["out"]), "--db-path", str(d["db"])]
    before = _xjoin_rows(d["db"])
    real, calls = build_db.iter_hit_rows, []

    def dies(con, row, bases, *a, **k):
        calls.append(1)
        if len(calls) == 20:
            raise KeyboardInterrupt
        return real(con, row, bases, *a, **k)
    # 1回目: 乗り替わりを見つけて全部の照合を始め、20頭目で止まる
    monkeypatch.setattr(build_db, "iter_hit_rows", dies)
    with pytest.raises(KeyboardInterrupt):
        build_db.main(args)
    import gc
    gc.collect()                                              # 閉じていない接続を片付ける（書きかけは取り消される）
    assert _xjoin_rows(d["db"]) == before                     # 照合結果は前のまま（空にも半端にもならない）
    with sqlite3.connect(d["db"]) as con:
        assert build_db.load_xjoin_record(con, XJOIN) != build_db.xjoin_record(con, "entries_merged_hensachi")
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name = ?", ("idx_" + XJOIN + "_race",)).fetchone()
    # 2回目（同じコマンド）: 出馬表のテーブルはもう新しいが、照合結果の記録が前の出馬表のものなので、全部の指数を照合し直す
    monkeypatch.setattr(build_db, "iter_hit_rows", real)
    capsys.readouterr()
    assert build_db.main(args) == 0
    log = capsys.readouterr().out
    assert "全部の指数を照合し直します" in log and "行だけ作り直しました" not in log
    assert _xjoin_rows(d["db"]) == _xjoin_rows(f["db"]) != before
    # 3回目: もう変わっていないので、一部の作り直しで済む（結果は同じ）
    assert build_db.main(args) == 0
    assert "タイムM・対戦M の行だけ作り直しました" in capsys.readouterr().out
    assert _xjoin_rows(d["db"]) == _xjoin_rows(f["db"])
    # 記録の無い照合結果（前の版で作った DB）は、最初の1回だけ全部の指数を照合し直す
    with sqlite3.connect(d["db"]) as con:
        con.execute("DELETE FROM __build_info__ WHERE key LIKE 'xjoin_of:%'")
    assert build_db.main(args) == 0
    assert "記録がありません" in capsys.readouterr().out
    assert build_db.main(args) == 0
    assert "行だけ作り直しました" in capsys.readouterr().out
    assert _xjoin_rows(d["db"]) == _xjoin_rows(f["db"])


def test_build_db_indicator_options_and_fallback(mo, tmp_path, capsys):
    import build_db
    a = build_db.parse_cli(["--mining-only"])
    assert a.mode == "entries" and a.targets == ["タイムM", "対戦M"] and a.entries_only
    assert build_db.parse_cli(["--indicators", "マイニング値"]).targets == ["タイムM"]
    assert build_db.parse_cli(["--entries-only"]).targets == []
    for bad in (["--mining-only", "--fresh"], ["--mining-only", "--mode", "foragg"], ["--indicators", "知らない指数"],
                ["--mining-only", "--no-xjoin"], ["--indicators", ""], ["--indicators", " , "]):   # 空の指定で全部の作り直しにしない
        with pytest.raises(SystemExit):
            build_db.parse_cli(bad)
    # 照合結果のテーブルが無ければ、全部の指数を照合し直す
    d = _work(mo, tmp_path / "noxjoin")
    assert _calc(d, "--mining-only") == 0
    with sqlite3.connect(d["db"]) as con:
        con.execute(f'DROP TABLE "{XJOIN}"')
    capsys.readouterr()
    assert build_db.main(["--mining-only", "--entries-dir", str(d["out"]), "--db-path", str(d["db"])]) == 0
    assert "全部の指数を照合し直します" in capsys.readouterr().out
    f = _full(mo, tmp_path / "full")
    assert build_db.main(["--entries-only", "--entries-dir", str(f["out"]), "--db-path", str(f["db"])]) == 0
    assert _xjoin_rows(d["db"]) == _xjoin_rows(f["db"])


def test_pages_show_confirmed_values_after_mining_only(mo, tmp_path, monkeypatch):
    """差し替えのあと、画面（出馬表・WEDGE 一覧の CSV・条件ヒット馬）に確定値とその照合結果が出る。
    画面の タイムM・対戦M の行は、全部作り直した DB の画面と同じで、差し替える前の画面とは違う。"""
    import csv
    import importlib
    import io
    import re
    import build_db
    d = _work(mo, tmp_path / "w")
    f = _full(mo, tmp_path / "w_full")
    assert build_db.main(["--entries-only", "--entries-dir", str(f["out"]), "--db-path", str(f["db"])]) == 0
    monkeypatch.setenv("DB_PATH", str(d["db"]))
    import app as app_module
    am = importlib.reload(app_module)
    c = am.app.test_client()
    with sqlite3.connect(d["db"]) as con:
        rid = con.execute('SELECT "レースID(新/馬番無)" FROM entries_merged_hensachi LIMIT 1').fetchone()[0]
    q = {"table": "entries_merged_hensachi", "rid": rid}

    def mining_rows(db):
        am.DB_PATH = str(db)
        r = c.get("/entries/wedge.csv", query_string={**q, "odds_mode": "all"})
        rows = list(csv.DictReader(io.StringIO(r.data.decode("utf-8-sig"))))
        return sorted(tuple(x.items()) for x in rows if x["base"] in ("タイムM", "対戦M")), len(rows)

    def cells(db):                                            # 出馬表の「順位 / 偏差値 / 帯」のセル
        am.DB_PATH = str(db)
        page = c.get("/entries/race", query_string=q).get_data(as_text=True)
        return re.findall(r"<td class=\"[^\"]*\">(\d+位 / \d+ / [^<]+)</td>", page), page

    old_rows, _ = mining_rows(d["db"])
    old_cells, _ = cells(d["db"])
    assert _calc(d, "--mining-only") == 0
    assert build_db.main(["--mining-only", "--entries-dir", str(d["out"]), "--db-path", str(d["db"])]) == 0
    new_rows, n_all = mining_rows(d["db"])
    want_rows, n_want = mining_rows(f["db"])
    assert new_rows == want_rows != old_rows and new_rows and n_all == n_want
    new_cells, page = cells(d["db"])
    assert new_cells == cells(f["db"])[0] != old_cells and new_cells
    # 条件ヒット馬のページも、全部作り直した DB と同じ HTML
    am.DB_PATH = str(d["db"])
    hits = c.get("/entries/hits", query_string=q).get_data(as_text=True)
    am.DB_PATH = str(f["db"])
    assert hits == c.get("/entries/hits", query_string=q).get_data(as_text=True) and "照合: xjoin" in hits


# ---------------------------------------------------------------- 照合（xjoin）のメモリ（2026-11-14）
def test_hit_cache_is_bounded_by_keys_and_rows(monkeypatch):
    """キャッシュは「入れるたび」に上限を見る（件数と、持っている行数の両方）。ふつうの dict を渡されても件数の上限は守る。"""
    from shisu import matching as M
    c = M.HitCache(max_keys=3, max_rows=5)
    seen = []
    for i, n in enumerate([1, 1, 1, 1, 4, 1, 1, 0, 0, 0, 9, 0]):
        c.put(("k", i), [("r",)] * n)
        seen.append((len(c), c.rows))
        assert len(c) <= 3 and (c.rows <= 5 or len(c) == 1)            # 上限より大きい1件だけは、それだけを持つ
        assert c.rows == sum(len(v) for v in c.values())
    assert seen[3] == (1, 1) and seen[4] == (2, 5) and seen[5] == (1, 1) and seen[10] == (1, 9) and seen[11] == (1, 0)
    c.clear()
    assert len(c) == 0 and c.rows == 0
    monkeypatch.setattr(M, "CACHE_MAX_KEYS", 4)
    plain: dict = {}
    for i in range(20):
        M._cache_put(plain, ("k", i), [])
        assert len(plain) <= 4
    assert M.HitCache().max_keys == 20_000 and M.HitCache().max_rows == 50_000    # 既定（引数の既定は定義時の値）


def test_iter_hit_rows_equals_match_entry_row_and_cache_stays_bounded(mo):
    """1行ずつ返す iter_hit_rows は match_entry_row（dict のリスト）と同じ中身・同じ順。キャッシュをどれだけ小さくしても
    結果は変わらず、1頭を回している途中でも上限を超えない（family・指数が多いと1頭で数十万回の照合になる）。"""
    from shisu import families as F
    from shisu import matching as M
    con = sqlite3.connect(mo / "racing.db")
    con.row_factory = None
    F.set_active(F.load_from_db(con), "db")
    cur = con.execute("SELECT * FROM entries_merged_hensachi")
    names = [d[0] for d in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    bases = M.indicator_bases(names)
    peak = {"keys": 0, "rows": 0, "puts": 0}

    class Watch(M.HitCache):
        def put(self, key, hits):
            super().put(key, hits)
            peak["keys"], peak["rows"], peak["puts"] = max(peak["keys"], len(self)), max(peak["rows"], self.rows), peak["puts"] + 1

    small, big = Watch(max_keys=7, max_rows=11), M.HitCache()
    total = 0
    for row in rows:
        ref = M.match_entry_row(con, row, bases, "t")                    # キャッシュなし（この1頭の中だけ）
        got = list(M.iter_hit_rows(con, row, bases, "t", cache=small))
        assert [dict(zip(M.HIT_COLUMNS, t)) for t in got] == ref
        assert list(M.iter_hit_rows(con, row, bases, "t", cache=big)) == got
        assert all(isinstance(t, tuple) and len(t) == len(M.HIT_COLUMNS) for t in got)
        total += len(got)
    assert total > 1000 and peak["puts"] > 1000
    assert peak["keys"] <= 7 and peak["rows"] <= max(11, M.DEFAULT_LIMIT)   # 上限を超えるのは、1件でそれより大きいものだけ
    assert len(big) <= M.CACHE_MAX_KEYS and big.rows <= M.CACHE_MAX_ROWS
    # query_unified（dict）は今までどおり
    key, fam, base = next(iter(big))
    assert M.query_unified(con, key, fam, base) == [dict(zip(M.META_COLUMNS + M.STAT_COLUMNS, r)) for r in big[(key, fam, base)]]
    con.close()
    F.reset()


def test_xjoin_is_streamed_in_batches_with_a_bounded_cache(mo, tmp_path, monkeypatch, capsys):
    """build_db の xjoin: ヒット行を XJOIN_BATCH 行ごとに INSERT し（1頭ぶんをまとめて持たない）、キャッシュは照合1回ごとに
    上限を見る。上限・まとまりを極端に小さくしても、結果は行の順まで同じ。出馬表を読んだあと・照合の前に gc.collect() を呼ぶ。"""
    import build_db
    from shisu import matching as M
    d = _work(mo, tmp_path / "a")
    f = _work(mo, tmp_path / "b")
    args = ["--entries-only", "--entries-dir"]
    assert build_db.main([*args, str(f["out"]), "--db-path", str(f["db"])]) == 0          # 既定の大きさ
    monkeypatch.setattr(build_db, "XJOIN_BATCH", 7)
    monkeypatch.setattr(build_db, "XJOIN_CACHE_LIMIT", 3)
    monkeypatch.setattr(build_db, "XJOIN_CACHE_ROWS", 5)
    events: list[str] = []
    sizes: list[int] = []
    real_gc, real_iter, real_put = build_db.gc.collect, build_db.iter_hit_rows, M.HitCache.put
    real_load = build_db.load_entries_tables

    def put(self, key, hits):
        real_put(self, key, hits)
        sizes.append(len(self))
        assert len(self) <= 3

    def it(con, row, bases, table, families, cache):
        events.append("match")
        assert isinstance(cache, M.HitCache) and (cache.max_keys, cache.max_rows) == (3, 5)
        return real_iter(con, row, bases, table, families, cache)

    monkeypatch.setattr(build_db.gc, "collect", lambda *a: (events.append("gc"), real_gc(*a))[1])
    monkeypatch.setattr(build_db, "load_entries_tables", lambda *a, **k: (events.append("load"), real_load(*a, **k))[1])
    monkeypatch.setattr(build_db, "iter_hit_rows", it)
    monkeypatch.setattr(M.HitCache, "put", put)
    assert build_db.main([*args, str(d["out"]), "--db-path", str(d["db"])]) == 0
    # 出馬表を読む → gc.collect() → 最初の照合、の順（読む前の gc では、正規化に使った DataFrame を手放せない）
    first = events.index("match")
    last_load = max(i for i, e in enumerate(events[:first]) if e == "load")
    assert "gc" in events[last_load + 1:first] and sizes and max(sizes) <= 3
    q = f'SELECT * FROM "{XJOIN}" ORDER BY rowid'
    with sqlite3.connect(d["db"]) as c1, sqlite3.connect(f["db"]) as c2:
        a, b = c1.execute(q).fetchall(), c2.execute(q).fetchall()
        assert a == b and len(a) > 1000                                    # 行の順まで同じ
        assert c1.execute("SELECT 1 FROM sqlite_master WHERE name = ?", ("idx_" + XJOIN + "_race",)).fetchone()
    # insert_hits が1回に INSERT する行数は XJOIN_BATCH まで（偽の接続で数える）
    monkeypatch.setattr(M.HitCache, "put", real_put)
    monkeypatch.setattr(build_db, "iter_hit_rows", real_iter)
    con = sqlite3.connect(d["db"])
    cur = con.execute("SELECT * FROM entries_merged_hensachi")
    names = [x[0] for x in cur.description]
    rows = [dict(zip(names, r)) for r in cur.fetchall()]
    batches: list[int] = []
    flow = {"yielded": 0, "inserted": 0, "pending": 0}

    class Counting:
        def execute(self, *a):
            return con.execute(*a)

        def executemany(self, sql, batch):
            batches.append(len(batch))
            flow["inserted"] += len(batch)
            assert sql.startswith("INSERT INTO")

    def counted(*a, **k):
        # 受け取ったのにまだ INSERT していない行は、いつも XJOIN_BATCH 行まで（1頭ぶん・全部をためてから切り分けて入れても通らない）
        for t in real_iter(*a, **k):
            flow["yielded"] += 1
            flow["pending"] = max(flow["pending"], flow["yielded"] - flow["inserted"])
            assert flow["yielded"] - flow["inserted"] <= 7
            yield t

    monkeypatch.setattr(build_db, "iter_hit_rows", counted)
    from shisu import families as F
    F.set_active(F.load_from_db(con), "db")
    n = build_db.insert_hits(Counting(), XJOIN, rows, M.indicator_bases(names), "entries_merged_hensachi", F.active_names())
    assert n == sum(batches) == len(a) and max(batches) == 7 and batches.count(7) == len(a) // 7
    assert flow["yielded"] == flow["inserted"] == len(a) and flow["pending"] == 7
    assert build_db.insert_hits(Counting(), XJOIN, rows, [], "entries_merged_hensachi", F.active_names()) == 0   # 指数なし
    con.close()
    F.reset()


# ---------------------------------------------------------------- 列の組み立て
def test_process_df_builds_columns_without_fragmentation_warning():
    """指数が30以上あっても PerformanceWarning（DataFrame is highly fragmented）を出さない。派生列は指数列のすぐ右。"""
    import calc_entry_hensachi as calc
    from shisu.indicators import canonical_indicator, is_known_indicator
    names = ["Gold", "BC", "ZI", "吉馬_SP能力値", "吉馬_先行力", "吉馬_SP信頼", "吉馬_SP調整", "吉馬_SP最大", "吉馬_末脚力",
             "展開", "先行", "追走", "持久", "持続", "瞬発", "想定前半", "想定後半", "タイムM", "対戦M", "安井_ランク",
             "安井_スピード", "安井_スタミナ", "安井_パワー", "安井_根性", "安井_賢さ", "安井_体力", "安井_やる気",
             "オメガ指数", "かよちん指数", "keibaAI指数", "SAV", "ST指数", "合計値", "仕上り"]
    assert all(is_known_indicator(n) and canonical_indicator(n) == n for n in names) and len(names) >= 33
    e1, _ = synth.entries_frames()
    df = e1.drop(columns=["SAV", "指数順位1", "ZI", "指数順位2"]).copy()
    cols = {n: [str(30 + (i * (k + 3)) % 70) for i in range(len(df))] for k, n in enumerate(names)}
    df = pd.concat([df, pd.DataFrame(cols, index=df.index)], axis=1)
    with warnings.catch_warnings():
        warnings.simplefilter("error", pd.errors.PerformanceWarning)
        out = calc.process_df(df, "wide", verbose=False)
        again = calc.process_df(out, "wide", verbose=False)      # 派生列のある入力をもう一度通しても同じ
    assert list(out.columns) == list(again.columns) and out.equals(again)
    cols_out = list(out.columns)
    for n in names:
        i = cols_out.index(n)
        assert cols_out[i + 1:i + 6] == [f"{n}_偏差値", f"{n}_偏差値帯", f"{n}_順位", f"{n}_順位帯_s1", f"{n}_順位帯_s2"], n
    assert len(cols_out) == len(df.columns) + 5 * len(names) and (out["Gold_順位"] != "").all()
    # 一部の指数だけ: ほかの指数の派生列は作らない
    some = calc.process_df(df, "wide", verbose=False, only={"タイムM", "対戦M"})
    assert [c for c in some.columns if c not in df.columns] == [
        f"{n}{s}" for n in ("タイムM", "対戦M") for s in ("_偏差値", "_偏差値帯", "_順位", "_順位帯_s1", "_順位帯_s2")]
