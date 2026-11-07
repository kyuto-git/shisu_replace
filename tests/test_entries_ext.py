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
from test_replace_ext import HEADS, RACES, REGION, base_frame, write_ext  # noqa: E402

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
