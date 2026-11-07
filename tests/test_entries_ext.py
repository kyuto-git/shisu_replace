"""出馬表（calc_entry_hensachi.py）での ext_indices/ の自動結合。

  - 出馬表CSVは マイニング値だけの1本でよい。ext_indices の指数を各馬に付けてから偏差値・順位を計算する
  - 過去側（replace_csv_ver3.py）と同じ関数で付けるので、同じ馬なら順位・偏差値が一致する
  - 出馬表にすでに値のある指数は出馬表の値を使う。0 だけの列（TARGET が読み込んでいない指数）は ext で上書きする
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402
from test_replace_ext import HEADS, REGION, base_frame, write_ext  # noqa: E402

EXT_INDICATORS = ("オメガ指数", "ST指数", "Gold", "BC", "ZI")


def entries_frame(with_gold: str | None = None) -> pd.DataFrame:
    """test.csv の形の出馬表（基本CSVと同じ馬）。障害の2レースは除く（出馬表側は build_db で照合しない）。"""
    b = base_frame()
    b = b[~b["距離"].str.startswith("障") & (b["トラックコード(JV)"].astype(int) < 51)]
    e = pd.DataFrame({
        "場 R": "東" + b["Ｒ"], "場所": b["場所"], "Ｒ": b["Ｒ"], "レース名": "テスト", "クラス": b["クラス名"],
        "年限定": "３歳以上", "芝ダ": b["距離"].str[0], "距離": b["距離"].str[1:], "外回り": "", "頭": b["頭数"],
        "枠番": b["枠番"], "馬番": b["馬番"], "馬名": b["馬名"], "騎手": b["騎手"],
        "レースID(旧)": b["レースID(旧)"], "レースID(新)": b["レースID(新)"],
        "マイニング値": b["指数"], "マイニング順位": b["指数順位"],
    })
    if with_gold is not None:                     # TARGET で Gold を読み込んだ出馬表（指数順位1 はその右）
        e.insert(e.columns.get_loc("マイニング値"), "GoldAirplane", with_gold)
        e.insert(e.columns.get_loc("マイニング値"), "指数順位1", "1")
    return e.reset_index(drop=True)


def run_both(tmp: Path, entries: pd.DataFrame, extra_args=()):
    import calc_entry_hensachi
    import replace_csv_ver3
    csv_dir, ext = tmp / "csv", tmp / "ext"
    synth.write_cp932(base_frame(), csv_dir / f"{REGION}_タイムM.csv")
    write_ext(ext)
    assert replace_csv_ver3.main(["--csv-dir", str(csv_dir), "--ext-dir", str(ext),
                                  "--master-dir", str(tmp / "m"), "--clean"]) == 0
    synth.write_cp932(entries, tmp / "ein" / "shisu_20261025_1.csv")
    assert calc_entry_hensachi.main(["--in-dir", str(tmp / "ein"), "--out-dir", str(tmp / "eout"), "--merge-only",
                                     "--clean", "--ext-dir", str(ext), *extra_args]) == 0
    out = pd.read_csv(tmp / "eout" / "entries_merged_hensachi.csv", encoding="cp932", dtype=str, keep_default_na=False)
    return out


def master(tmp: Path, ind: str) -> pd.DataFrame:
    return pd.read_csv(tmp / "m" / f"master_{REGION}_{ind}.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)


def test_entries_get_ext_indicators_matching_past_side(tmp_path, capsys):
    out = run_both(tmp_path, entries_frame())
    log = capsys.readouterr().out
    for ind in EXT_INDICATORS:
        assert f"外部指数 {ind} を ext_indices から付けました" in log
        assert {f"{ind}_偏差値", f"{ind}_順位", f"{ind}_順位帯_s1", f"{ind}_順位帯_s2"} <= set(out.columns)
        m = master(tmp_path, ind)
        m = m[m["指数有効"] == "1"].set_index("レースID(新)")
        e = out.set_index("レースID(新)").loc[m.index]
        for c in ("順位", "偏差値", "偏差値帯", "順位帯_s1"):
            assert e[f"{ind}_{c}"].tolist() == m[c].tolist(), (ind, c)
    assert len(out) == 3 * HEADS and "タイムM_順位" in out.columns


def test_entries_values_take_precedence_over_ext(tmp_path, capsys):
    """出馬表に Gold の値があれば ext_indices の Gold は使わない。"""
    out = run_both(tmp_path, entries_frame(with_gold="55"))
    log = capsys.readouterr().out
    assert "Gold: 出馬表CSVの値を使います" in log and "外部指数 Gold を" not in log
    assert set(out["GoldAirplane"]) == {"55"} and "Gold" not in out.columns
    assert "オメガ指数_順位" in out.columns                      # ほかの指数は ext から


def test_entries_zero_only_column_is_overwritten_from_ext(tmp_path):
    """TARGET が読み込んでいない指数を 0 で出した列（GoldAirplane が全部 0）は ext の値で上書きし、
    すぐ右の 指数順位1 も ext の順位にする（列を消さないので、ほかの 指数順位N の対応はずれない）。"""
    out = run_both(tmp_path, entries_frame(with_gold="0"))
    m = master(tmp_path, "Gold")
    m = m[m["指数有効"] == "1"].set_index("レースID(新)")
    e = out.set_index("レースID(新)").loc[m.index]
    assert e["Gold_順位"].tolist() == m["順位"].tolist()
    assert (e["GoldAirplane"].astype(float) != 0).any() and e["指数順位1"].tolist() == m["指数順位_元"].tolist()
    assert "Gold" not in out.columns


def test_no_ext_flag_and_entries_without_race_ids(tmp_path, capsys):
    out = run_both(tmp_path, entries_frame(), ["--no-ext"])
    assert not any(c.startswith("オメガ指数") for c in out.columns)
    import calc_entry_hensachi
    e = entries_frame().drop(columns=["レースID(新)", "レースID(旧)"])
    synth.write_cp932(e, tmp_path / "ein2" / "shisu_x.csv")
    assert calc_entry_hensachi.main(["--in-dir", str(tmp_path / "ein2"), "--out-dir", str(tmp_path / "eout2"),
                                     "--ext-dir", str(tmp_path / "ext")]) == 0     # 個別出力（統合は レースID が要る）
    assert "外部指数を結び付けられません" in capsys.readouterr().out


def _replace_once(tmp: Path) -> Path:
    import replace_csv_ver3
    csv_dir, ext = tmp / "csv", tmp / "ext"
    synth.write_cp932(base_frame(), csv_dir / f"{REGION}_タイムM.csv")
    write_ext(ext)
    assert replace_csv_ver3.main(["--csv-dir", str(csv_dir), "--ext-dir", str(ext), "--master-dir", str(tmp / "m"),
                                  "--clean"]) == 0
    return ext


def _calc(tmp: Path, ext: Path, frames: dict[str, pd.DataFrame], merge: bool = True) -> pd.DataFrame:
    import calc_entry_hensachi
    ein, eout = tmp / "ein", tmp / "eout"
    for name, f in frames.items():
        synth.write_cp932(f, ein / name)
    args = ["--in-dir", str(ein), "--out-dir", str(eout), "--clean", "--ext-dir", str(ext)]
    assert calc_entry_hensachi.main(args + (["--merge-only"] if merge else [])) == 0
    return pd.read_csv(eout / "entries_merged_hensachi.csv", encoding="cp932", dtype=str, keep_default_na=False)


def _gold_master(tmp: Path) -> pd.DataFrame:
    m = master(tmp, "Gold")
    return m.set_index("レースID(新)")


def test_entries_value_priority_is_decided_per_file_and_per_race(tmp_path, capsys):
    """出馬表の値を使うかどうかはレースごと。別のファイルに Gold の値があっても、Gold の列が無いファイルのレース、
    同じファイルでも Gold が 0 だけのレースは ext_indices から付ける（どれも過去側と同じ順位）。"""
    ext = _replace_once(tmp_path)
    gm = _gold_master(tmp_path)
    e = entries_frame()
    r11 = e[e["Ｒ"] == "11"].copy()
    r12 = e[e["Ｒ"] == "12"].copy()
    r1 = e[e["Ｒ"] == "1"].copy()
    # 1本目: R11・R12 は TARGET で Gold を読み込み済み（値と順位は過去側と同じ）。ただし R12 は全頭 0（読み込めていない）
    with_gold = pd.concat([r11, r12], ignore_index=True)
    vals = with_gold["レースID(新)"].map(lambda k: gm.loc[k, "指数"] if k in gm.index else "0")
    ranks = with_gold["レースID(新)"].map(lambda k: gm.loc[k, "指数順位_元"] if k in gm.index else "0")
    vals[with_gold["Ｒ"] == "12"] = "0"
    ranks[with_gold["Ｒ"] == "12"] = "0"
    with_gold.insert(with_gold.columns.get_loc("マイニング値"), "GoldAirplane", vals.values)
    with_gold.insert(with_gold.columns.get_loc("マイニング値"), "指数順位1", ranks.values)
    out = _calc(tmp_path, ext, {"shisu_a.csv": with_gold, "shisu_b.csv": r1.reset_index(drop=True)})
    log = capsys.readouterr().out
    assert "出馬表に値のあるレース 1/2 は出馬表の値を使用" in log          # 1本目: R11 は出馬表、R12 は ext
    valid = gm[gm["指数有効"] == "1"]
    o = out.set_index("レースID(新)").loc[valid.index]
    assert o["Gold_順位"].tolist() == valid["順位"].tolist()            # R11（出馬表）・R12（ext）・R1（2本目・ext）
    assert set(o.index.str[14:16]) == {"11", "12", "01"}


def test_appended_ext_columns_do_not_take_unregistered_rank_columns(tmp_path):
    """台帳に無い指数（ラップ2F など）と 指数順位1 が出馬表にあっても、ext から末尾に足した オメガ指数 の順位にしない。"""
    ext = _replace_once(tmp_path)
    e = entries_frame()
    e.insert(e.columns.get_loc("マイニング値"), "ラップ2F", [str(30 + (i * 7) % 11) for i in range(len(e))])
    e.insert(e.columns.get_loc("マイニング値"), "指数順位1", [str(1 + (i * 3) % 10) for i in range(len(e))])
    out = _calc(tmp_path, ext, {"shisu_x.csv": e})
    for ind in ("オメガ指数", "BC"):
        m = master(tmp_path, ind)
        m = m[m["指数有効"] == "1"].set_index("レースID(新)")
        o = out.set_index("レースID(新)").loc[m.index]
        for c in ("順位", "偏差値", "順位帯_s1"):
            assert o[f"{ind}_{c}"].tolist() == m[c].tolist(), (ind, c)


def test_rank_map_ignores_ext_columns_for_numbered_and_plain_ranks():
    import calc_entry_hensachi as c
    # TARGET の並び（マイニング順位は明示）: 台帳に無い指数の 指数順位1 を、足した オメガ指数 に回さない
    df = pd.DataFrame({"ラップ2F": ["1"], "指数順位1": ["2"], "マイニング値": ["3"], "マイニング順位": ["1"],
                       "オメガ指数": ["5"]})
    idx = c.find_indicator_columns(df)
    assert c.build_rank_source_map(df, idx) == {"マイニング値": "マイニング順位", "オメガ指数": "指数順位1"}   # 足す前の数え方
    assert c.build_rank_source_map(df, idx, ext_cols={"オメガ指数"}) == {"マイニング値": "マイニング順位"}
    # 指数が1種類（マイニング値）で無印の 指数順位: 足した列があっても1種類と数える
    df2 = pd.DataFrame({"マイニング値": ["3"], "指数順位": ["1"], "オメガ指数": ["5"]})
    idx2 = c.find_indicator_columns(df2)
    assert c.build_rank_source_map(df2, idx2, ext_cols={"オメガ指数"}) == {"マイニング値": "指数順位"}
