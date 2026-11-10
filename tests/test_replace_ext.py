"""★6タブの基本CSV（タイムM・対戦M）＋ ext_indices/ の外部指数 → replace_csv_ver3.py の master。

  - 基本CSVは 202610.csv の形（芝・ダ 列が無く 距離 が「ダ1200」、単勝配当・複勝配当が無いこともある）
  - 外部指数の4パターン（A: 18桁 / B: 旧10桁＋順位 / C: 12桁×(値,順位) / D: 旧8桁×値）を馬に結び付ける
  - csv_files に同じ地域×指数のファイルがあればそちらを使う（後方互換）。廃止した指数は読まない
  - 障害レース（距離が「障…」・トラックコード51以上）は除く
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

REGION = "東京"
HEADS = 10
# (R, 距離, トラックコード, 日)。日=10 は旧IDで「A」。4R・5R は障害（距離の「障」・トラックコード）で除く
RACES = [(11, "芝1600", "11", 10), (12, "ダ1400", "23", 10), (1, "芝2000", "11", 3), (4, "障3000", "54", 3),
         (5, "芝2000", "52", 3)]
DATE = "20261025"


def rid18(r: int, day: int, uma: int) -> str:
    return f"{DATE}0504{day:02d}{r:02d}{uma:02d}"


def old10(r: int, day: int, uma: int) -> str:
    return f"0526{4}{'0123456789ABC'[day]}{r:02d}{uma:02d}"


def omega_value(r, uma):
    return 100 + r * 3 + uma


def st_value(r, uma):
    return 40 + (uma * 7) % 13


def gold_value(r, uma):
    return 50 + (uma * 5) % 11


def bc_value(r, uma):
    return round(40 + uma * 1.37 + r * 0.1, 2)


def zi_value(r, uma):
    return 0 if uma == 3 else 80 + uma * 2


def base_frame(index_col: str = "指数", with_payout: bool = False) -> pd.DataFrame:
    rows = []
    for r, dist, track, day in RACES:
        for uma in range(1, HEADS + 1):
            old = old10(r, day, uma)
            rows.append({
                "日付S": DATE, "開催": "4東10", "馬名": f"ウマ{r}_{uma}", "場所": REGION, "Ｒ": str(r),
                "クラス名": "1勝", "騎手": synth.JOCKEYS[uma % len(synth.JOCKEYS)], "斤量": " 56 ", "頭数": str(HEADS),
                "枠番": str(min(8, (uma + 1) // 2)), "馬番": str(uma), "単勝オッズ": f"{2.0 + uma:.1f}",
                "着順": "１" if uma == 1 else str(uma), "距離": dist, "芝(内・外)": " ", "所属": "(美)",
                "年齢限定": "３上", "性別限定": "", "重量種別": "定量", index_col: str(200 - uma * 3),
                "指数順位": str(uma), "レースID(新)": rid18(r, day, uma),
                # 先頭の0が落ちて9桁になった旧ID（TARGET の CSV を表計算ソフトで開いて保存したとき）も混ぜる
                "レースID(旧)": old[1:] if uma % 2 else old, "トラックコード(JV)": track, "間隔": "3",
                "休み明け～戦目": "2", "前走場所": "中山", "前芝・ダ": "芝", "前距離": "1600", "前走着順": "３",
                "前走人気": "2", "前走上り3F順": "1", "前走脚質": "先行", "前走レース印３": " Ｈ ", "前走斤量": " 56 ",
                "前走レースID(新)": f"2026092806040801{uma:02d}",
            })
            if with_payout:
                rows[-1]["単勝配当"] = "300" if uma == 1 else "0"
                rows[-1]["複勝配当"] = "120" if uma <= 3 else "0"
    return pd.DataFrame(rows)


def write_ext(root: Path) -> None:
    """ext_indices/ に4パターンの外部指数CSVと、使わないファイルを置く。"""
    races = [(r, day) for r, _d, _t, day in RACES]
    # A: 18桁 + 値（見出しあり、cp932）
    a = ["レースID(新),ワーク1"] + [f"{rid18(r, d, u)},{omega_value(r, u)}" for r, d in races for u in range(1, HEADS + 1)]
    (root / "OMEGA").mkdir(parents=True)
    (root / "OMEGA" / f"{DATE}_omega.csv").write_bytes("\r\n".join(a).encode("cp932"))
    # B: 旧10桁 + 値 + 順位（見出しなし）。st_se_ は廃止（成績ST指数）
    sp = root / "StridePlus"
    sp.mkdir()
    b = [f"{old10(r, d, u)},{st_value(r, u)},{u}" for r, d in races for u in range(1, HEADS + 1)]
    (sp / "st_2026.csv").write_text("\n".join(b), encoding="utf-8")
    (sp / "st_se_2026.csv").write_text("\n".join(f"{old10(r, d, u)},1,1" for r, d in races for u in range(1, 11)),
                                       encoding="utf-8")
    # B の SAV もあるが、csv_files に 東京_SAV.csv があるのでそちらを使う
    (sp / "sav_2026.csv").write_text("\n".join(f"{old10(r, d, u)},999,1" for r, d in races for u in range(1, 11)),
                                     encoding="utf-8")
    # C: 12桁（日付+場所+R）+ 馬番1から (値, 順位) の組
    (root / "GoldAirplane").mkdir()
    c = []
    for r, _d in races:
        cells = [f"{DATE}05{r:02d}"]
        vals = [gold_value(r, u) for u in range(1, HEADS + 1)]
        order = sorted(range(HEADS), key=lambda i: -vals[i])
        rank = {i: k for k, i in enumerate(order, 1)}
        for u in range(HEADS):
            cells += [str(vals[u]), str(rank[u])]
        c.append(",".join(cells))
    (root / "GoldAirplane" / "gold_2026.csv").write_text("\n".join(c), encoding="utf-8")
    # D: 旧8桁（BC は先頭に RX）+ 馬番1から値
    (root / "BCindex").mkdir()
    (root / "BCindex" / "BC2026.csv").write_text(
        "\n".join(f"RX{old10(r, d, 1)[:8]}," + ",".join(f"{bc_value(r, u)} " for u in range(1, HEADS + 1)) + ", ,"
                  for r, d in races), encoding="utf-8")
    (root / "ZI_DATA").mkdir()
    (root / "ZI_DATA" / "ZI2026.CSV").write_text(
        "\n".join(f"{old10(r, d, 1)[:8]}," + ",".join(str(zi_value(r, u)) for u in range(1, HEADS + 1))
                  for r, d in races), encoding="utf-8")
    # 廃止した指数
    (root / "katsuraki").mkdir()
    (root / "katsuraki" / "x.csv").write_text(f"{rid18(11, 10, 1)},5", encoding="utf-8")
    (root / "HPRTV5C").mkdir()
    (root / "HPRTV5C" / "x.csv").write_text(f"{rid18(11, 10, 1)},5", encoding="utf-8")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    import replace_csv_ver3
    b = tmp_path_factory.mktemp("ext")
    csv_dir, ext, master = b / "csv_files", b / "ext_indices", b / "master_csv"
    synth.write_cp932(base_frame(), csv_dir / f"{REGION}_タイムM.csv")
    mining = base_frame()
    mining["指数"] = [str(50 + i % 7) for i in range(len(mining))]
    synth.write_cp932(mining, csv_dir / f"{REGION}_対戦M.csv")
    sav = base_frame(with_payout=True)
    sav["指数"] = [str(60 + i % 9) for i in range(len(sav))]
    synth.write_cp932(sav, csv_dir / f"{REGION}_SAV.csv")
    synth.write_cp932(base_frame(), csv_dir / f"{REGION}_カツラキ.csv")
    write_ext(ext)
    import contextlib
    import io
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert replace_csv_ver3.main(["--csv-dir", str(csv_dir), "--ext-dir", str(ext), "--master-dir", str(master),
                                      "--clean"]) == 0
    return b, out.getvalue()


def master(b: Path, ind: str) -> pd.DataFrame:
    return pd.read_csv(b / "master_csv" / f"master_{REGION}_{ind}.csv.gz", encoding="cp932", dtype=str,
                       keep_default_na=False)


def test_masters_for_base_and_ext(run):
    b, log = run
    names = sorted(p.name for p in (b / "master_csv").glob("master_*.csv.gz"))
    expect = {f"master_{REGION}_{i}.csv.gz" for i in ("タイムM", "対戦M", "SAV", "オメガ指数", "ST指数", "Gold", "BC", "ZI")}
    assert set(names) == expect, names
    assert "カツラキ は廃止した指数" in log
    assert "東京_SAV: csv_files のファイルを使います" in log
    assert not any("katsuraki" in n.lower() or "HPRTV5C" in n or "成績" in n for n in names)


def test_distance_split_and_jump_exclusion(run):
    b, _ = run
    m = master(b, "タイムM")
    assert len(m) == 3 * HEADS                                     # 障害の2レースを除く
    assert set(zip(m["芝・ダ"], m["距離"])) == {("芝", "16-18"), ("ダ", "13-14"), ("芝", "2000")}
    assert set(m["距離_raw"]) == {"1600", "1400", "2000"}
    assert m["着順"].isin([str(i) for i in range(1, HEADS + 1)]).all()       # 全角「１」も 1
    # 単勝配当・複勝配当の列が無くてもエラーにならない（空のまま）
    assert set(m["単勝配当"]) == {""} and set(m["複勝配当"]) == {""}
    assert set(master(b, "SAV")["単勝配当"]) == {"300", "0"}


def _by_horse(m: pd.DataFrame, col: str) -> dict[tuple[int, int], str]:
    return {(int(rid[14:16]), int(rid[16:18])): v for rid, v in zip(m["レースID(新)"], m[col])}


@pytest.mark.parametrize("ind,func", [("オメガ指数", omega_value), ("ST指数", st_value), ("Gold", gold_value),
                                      ("BC", bc_value), ("ZI", zi_value)])
def test_ext_values_attached_to_the_right_horse(run, ind, func):
    b, log = run
    m = master(b, ind)
    assert len(m) == 3 * HEADS
    got = _by_horse(m, "指数")
    for (r, uma), v in got.items():
        assert float(v) == pytest.approx(float(func(r, uma))), (ind, r, uma)
    assert f"ext_indices/{ind}" in log and f"外部指数が付いた馬 {len(RACES) * HEADS}/{len(RACES) * HEADS}" in log


def test_ext_ranks_from_file_or_values(run):
    """B（StridePlus）と C（Gold）はファイルの順位、A と D は値から計算（基本CSVの タイムM の順位は使わない）。"""
    b, _ = run
    st = master(b, "ST指数")
    assert (st["順位"] == st["馬番"]).all()                           # ファイルの3列目（= 馬番にしてある）
    gold = master(b, "Gold")
    for _, g in gold.groupby("レースID(新/馬番無)"):
        vals = g["指数"].astype(float)
        assert g.loc[vals.idxmax(), "順位"] == "1"
    om = master(b, "オメガ指数")
    for _, g in om.groupby("レースID(新/馬番無)"):
        assert g.sort_values("馬番", key=lambda s: s.astype(int))["順位"].tolist() == [str(i) for i in range(10, 0, -1)]
    zi = master(b, "ZI")                                              # ZI の 0 は同レースの非ゼロ平均で埋める
    assert (zi.loc[zi["馬番"] == "3", "指数有効"] == "1").all()


def test_csv_file_precedes_ext(run):
    b, _ = run
    sav = master(b, "SAV")
    assert not (sav["指数"] == "999").any()                         # ext の sav_2026.csv（全部 999）は使わない


def test_master_aggregates_without_payouts(run, tmp_path):
    import aggregate_stats_ver2
    b, _ = run
    out = tmp_path / "agg"
    assert aggregate_stats_ver2.main(["--in-dir", str(b / "master_csv"), "--out-dir", str(out), "--clean",
                                      "--wedge-min-year-count", "0"]) == 0
    from shisu.aggstore import read_agg_tables
    files = [t for n, t in sorted(read_agg_tables(out).items()) if n.endswith(f"_{REGION}_オメガ指数.csv")]
    assert files
    t = pd.concat(files)
    for c in ("単勝回収値", "複勝回収値", "件数"):
        t[c] = pd.to_numeric(t[c])
    assert (t["単勝回収値"] == 0).all() and (t["複勝回収値"] == 0).all() and (t["件数"] >= 5).all()


def test_no_ext_flag_and_missing_base(tmp_path):
    """--no-ext なら ext_indices を見ない。基本CSV（タイムM・対戦M）が無ければ警告して ext を使わない。"""
    import contextlib
    import io
    import replace_csv_ver3
    csv_dir, ext = tmp_path / "csv", tmp_path / "ext"
    synth.write_cp932(base_frame(), csv_dir / f"{REGION}_SAV.csv")
    write_ext(ext)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert replace_csv_ver3.main(["--csv-dir", str(csv_dir), "--ext-dir", str(ext),
                                      "--master-dir", str(tmp_path / "m1")]) == 0
    assert "結び付ける基本CSV" in out.getvalue()
    assert [p.name for p in (tmp_path / "m1").glob("master_*.csv.gz")] == [f"master_{REGION}_SAV.csv.gz"]
    synth.write_cp932(base_frame(), csv_dir / f"{REGION}_タイムM.csv")
    assert replace_csv_ver3.main(["--csv-dir", str(csv_dir), "--ext-dir", str(ext), "--no-ext",
                                  "--master-dir", str(tmp_path / "m2")]) == 0
    assert sorted(p.name for p in (tmp_path / "m2").glob("master_*.csv.gz")) == [
        f"master_{REGION}_SAV.csv.gz", f"master_{REGION}_タイムM.csv.gz"]
