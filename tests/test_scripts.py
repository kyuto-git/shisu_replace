"""各スクリプトの関数単位のテスト（レビュー指摘の回帰防止）。"""
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402


def test_replace_without_head_column_keeps_rows(tmp_path, capsys):
    """頭数列が無い過去CSVでも、旧版と同じく頭数で絞らずに処理する（0件にしない）。"""
    import replace_csv_ver3 as r
    df = synth.history_frame(seed=3, years=(2024,), races_per_year=8).drop(columns=["頭数"])
    src = tmp_path / "csv" / "札幌_SAV.csv"
    synth.write_cp932(df, src)
    msg = r.process_file(str(src), str(tmp_path / "master"), None, None)
    assert msg.startswith("[OK]") and "master=0" not in msg
    assert "頭数 列が無い" in capsys.readouterr().err
    m = pd.read_csv(tmp_path / "master" / "master_札幌_SAV.csv.gz", encoding="cp932", dtype=str)
    assert len(m) == len(df)


def test_aggregate_mean_mode_keeps_place_quantile(monkeypatch):
    """AGG_PROP_MODE=mean でも、複勝率の推定は旧版どおり分位点の切替（頻度≤20%→上側90%点）。"""
    import aggregate_stats_ver2 as a
    from shisu.estimates import beta_quantile
    rows = [{"レースID(新)": f"r{i}", "結合データ": "K", "着順": str(4 + i), "馬番": "1", "頭数": "16",
             "単勝オッズ": "10", "単勝配当": "0", "複勝配当": "0"} for i in range(9)]
    monkeypatch.setattr(a, "PROP_MODE", "mean")
    out = a.aggregate_frame(pd.DataFrame(rows))
    expect = float(beta_quantile(pd.Series([0.0]), pd.Series([9.0]), a.UPPER_Q).iloc[0]) * 100
    assert out.loc[0, "提案複勝率"] == f"{expect:.2f}%"
    assert out.loc[0, "提案勝率"] == f"{0.5 / 10 * 100:.2f}%"     # 勝率は事後平均 (0+0.5)/(9+1)


def test_rank_map_skips_unnamed_slots():
    """名前の無い枠（指数2・指数3）があっても、指数順位N を正しい指数に対応させる。"""
    import calc_entry_hensachi as c
    df = pd.DataFrame(columns=["レースID(新)", "GoldAirplane", "指数順位1", "指数2", "指数順位2", "指数3", "指数順位3",
                               "かよちん指数", "指数順位4", "マイニング値", "マイニング順位"])
    idx = c.find_indicator_columns(df)
    assert idx == ["GoldAirplane", "かよちん指数", "マイニング値"]
    assert c.build_rank_source_map(df, idx) == {"GoldAirplane": "指数順位1", "かよちん指数": "指数順位4",
                                                "マイニング値": "マイニング順位"}
    # 順位列が指数列の隣に無い並びでは、枠番号で対応させる（専用の順位列がある指数は枠に数えない）
    df2 = pd.DataFrame(columns=["マイニング値", "マイニング順位", "ZI", "指数2", "オメガ指数", "メモ",
                                "指数順位1", "指数順位2", "指数順位3"])
    assert c.build_rank_source_map(df2, c.find_indicator_columns(df2)) == {
        "マイニング値": "マイニング順位", "ZI": "指数順位1", "オメガ指数": "指数順位3"}


def test_entry_rank_text_accepts_old_format():
    from shisu.keys import entry_key_candidates
    from shisu.matching import entry_fields
    row = {"場所": "東京", "芝・ダ": "ダ", "距離": "1600", "芝(内・外)": "", "重量種別": "定量", "クラス名": "1・2_勝",
           "枠番グループ": "内枠", "騎手": "A", "Gold_順位": "13.0", "Gold_偏差値帯": "40以下"}
    f = entry_fields(row, "Gold")
    assert f["順位"] == "13"
    c = entry_key_candidates(f)
    assert ("東京×ダ×1600×定量×1・2_勝×13", "") in c["normal"] and "normal_band" in c


def test_aggregate_without_material_fails(tmp_path):
    import aggregate_stats_ver2 as a
    (tmp_path / "in").mkdir()
    (tmp_path / "in" / "foragg_normal_旧形式.csv").write_text("結合データ,件数\nA,5\n", encoding="cp932")
    assert a.main(["--in-dir", str(tmp_path / "in"), "--out-dir", str(tmp_path / "out")]) == 2


def test_calc_clean_removes_old_outputs(tmp_path):
    import calc_entry_hensachi as c
    e1, _ = synth.entries_frames()
    synth.write_cp932(e1, tmp_path / "in" / "shisu_20260531_1.csv")
    out = tmp_path / "out"
    out.mkdir()
    (out / "old_hensachi.csv").write_text("x\n1\n", encoding="cp932")
    assert c.main(["--in-dir", str(tmp_path / "in"), "--out-dir", str(out), "--merge-only", "--clean"]) == 0
    assert sorted(p.name for p in out.glob("*.csv")) == ["entries_merged_hensachi.csv"]
