"""前走の列を同じCSVの 血統登録番号 の履歴から補う（TARGET で「前走データ読込」をせずに出した ★6 の過去CSV）。

  - 補うのは 前走の列・間隔・休み明け～戦目 が全部空の行だけ（TARGET が読み込んだ行の空欄はそのまま）
  - 1つ前の走り = 同じ馬の、日付が前の走り（取消・除外は走っていないので数えない）
  - 間隔 = 木曜始まりの週の番号の差（最小1＝連闘）。休み明け～戦目 = 間隔 8 以上なら 1、それ以外は前の値 + 1
  - キャリアが続かない（CSVに無いレースをはさむ）・デビューの行は補わない
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

from shisu.conditions import (PREV_SOURCES, REST_BREAK_INTERVAL, derive, fill_previous_runs,  # noqa: E402
                              standardize_history)

PREV_COLS = [n[0] for n, _ in PREV_SOURCES] + ["間隔", "休み明け～戦目"]
PLACE_CODE = {"東京": "05", "中山": "06", "京都": "08", "阪神": "09", "新潟": "04"}


def run_row(hid: str, date: str, place: str = "東京", r: int = 1, uma: int = 1, dist: str = "芝1600",
            fin: str = "３", pop: str = "2", weight: str = " 56 ", style: str = "先行", agari: str = "1",
            r3: str = " Ｍ ", career: int | None = None, track: str = "11", heads: int = 10) -> dict:
    rid = f"{date}{PLACE_CODE[place]}0101{r:02d}{uma:02d}"
    row = {"日付S": date, "場所": place, "Ｒ": str(r), "距離": dist, "芝(内・外)": " ", "頭数": str(heads),
           "枠番": str(min(8, (uma + 1) // 2)), "馬番": str(uma), "騎手": "騎手A", "クラス名": "1勝", "着順": fin,
           "人気": pop, "単勝オッズ": "5.0", "斤量": weight, "脚質": style, "上り3F順": agari, "レース印３": r3,
           "指数": "50", "指数順位": "1", "レースID(新)": rid, "血統登録番号": hid, "トラックコード(JV)": track,
           "所属": "(美)", "重量種別": "定量", "年齢限定": "３上", "性別限定": ""}
    if career is not None:
        row["キャリア"] = str(career)
    for c in PREV_COLS:
        row[c] = ""
    return row


def frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows).fillna("")


def by_date(out: pd.DataFrame, hid: str) -> pd.DataFrame:
    return out[out["血統登録番号"] == hid].set_index("日付S")


def test_fills_previous_run_values_and_interval_by_thursday_weeks():
    df = frame([
        run_row("H1", "20261004", "中山", 11, 3, "ダ1200", "１", "1", "53▲", "逃げ", "2", " Ｈ ", career=3),
        run_row("H1", "20261017", "東京", 5, 7, "芝1800", "４", "6", " 57 ", "中団", "5", " SS ", career=4),
        run_row("H1", "20261025", "京都", 2, 1, "芝2000", career=5),
    ])
    out = fill_previous_runs(df)
    h = by_date(out, "H1")
    second = h.loc["20261017"]
    assert (second["前走場所"], second["前芝・ダ"], second["前距離"]) == ("中山", "ダ", "1200")
    assert (second["前走斤量"], second["前走人気"], second["前走着順"]) == ("53▲", "1", "１")      # TARGET と同じ書き方
    assert (second["前走脚質"], second["前走上り3F順"], second["前走レース印３"]) == ("逃げ", "2", " Ｈ ")
    assert second["前走レースID(新)"] == "202610040601011103"
    assert second["間隔"] == "2"                # 日曜 → 13日後の土曜 = 中1週（13 // 7 = 1 ではない）
    assert h.loc["20261025", "間隔"] == "1"     # 土曜 → 翌週の日曜（8日）= 連闘
    assert h.loc["20261004", "前走場所"] == ""   # CSVに前の走りが無い
    assert out.attrs["prev_filled"] == {"blank": 3, "filled": 2, "no_history": 1, "gap": 0}


@pytest.mark.parametrize("prev, cur, weeks", [
    ("20261004", "20261010", 1),     # 日 → 土（6日）: 連闘
    ("20261003", "20261018", 2),     # 土 → 翌々週の日: 中1週
    ("20260816", "20261010", 8),     # 中7週: 休み明け
    ("20260823", "20261010", 7),     # 中6週: 叩き
    ("20251228", "20260105", 1),     # 日 → 翌週の月（8日。年明けの開催）: 連闘（土 → 翌週の日と同じ）
    ("20251227", "20260110", 2),     # 土 → 年をまたいで翌々週の土: 中1週
])
def test_interval_weeks_and_rest_threshold(prev, cur, weeks):
    df = frame([run_row("H", prev, career=4), run_row("H", cur, r=2, career=5)])
    df.loc[0, "休み明け～戦目"] = "3"
    df.loc[0, "間隔"] = "4"
    df.loc[0, "前走場所"] = "東京"                 # 1走目は TARGET が読み込んだ行（値がある）
    out = fill_previous_runs(df)
    assert out.loc[1, "間隔"] == str(weeks)
    assert out.loc[1, "休み明け～戦目"] == ("1" if weeks >= REST_BREAK_INTERVAL else "4")
    assert out.loc[0, ["間隔", "休み明け～戦目", "前走場所"]].tolist() == ["4", "3", "東京"]     # 読み込んだ行は触らない


def test_scratched_races_are_not_previous_runs_and_not_counted():
    """取消・除外は1つ前の走りにしない・休み明けの数に入れない。キャリアの差は間の除外の数まで多くてよい
    （TARGET のキャリアは除外も数える）。"""
    df = frame([
        run_row("H2", "20260816", "新潟", 1, 1, "芝1600", "５", career=7),
        run_row("H2", "20261004", "中山", 1, 1, "芝1600", "６", career=8),            # 中6週後: 休み明け1戦目ではない
        run_row("H2", "20261011", "東京", 1, 1, "芝1600", "外", pop="", career=9),    # 除外
        run_row("H2", "20261018", "東京", 2, 1, "芝1400", "２", career=10),
    ])
    df.loc[0, ["前走場所", "間隔", "休み明け～戦目"]] = ["札幌", "12", "1"]           # TARGET が読み込んだ行
    out = fill_previous_runs(df)
    h = by_date(out, "H2")
    assert h.loc["20261004", "休み明け～戦目"] == "2"
    last = h.loc["20261018"]
    assert last["前走場所"] == "中山" and last["前走着順"] == "６"          # 除外の 10/11 ではなく 10/04
    assert last["間隔"] == "2" and last["休み明け～戦目"] == "3"
    assert h.loc["20261011", "前走場所"] == "中山"                         # 除外の行の前走も 10/04（数には入れない）


def test_career_gap_and_debut_are_not_filled():
    """キャリアが続かない（間にCSVに無いレース）なら、CSVの1つ前は本当の前走ではないので補わない。デビューも補わない。"""
    df = frame([
        run_row("H3", "20260906", career=0),           # デビュー
        run_row("H3", "20260927", career=1),
        run_row("H3", "20261025", career=3),           # 10月に地方・9頭立てなど（CSVに無い）を1走はさむ
    ])
    out = fill_previous_runs(df)
    h = by_date(out, "H3")
    assert h.loc["20260906", "前走場所"] == "" and h.loc["20261025", "前走場所"] == ""
    assert h.loc["20260927", "前走場所"] == "東京" and h.loc["20260927", "間隔"] == "3"
    assert h.loc["20260927", "休み明け～戦目"] == ""      # デビューから休んでいない（TARGET も空）
    assert out.attrs["prev_filled"] == {"blank": 3, "filled": 1, "no_history": 1, "gap": 1}


def test_rest_chain_without_break_since_debut_stays_blank_until_a_break():
    df = frame([run_row("H4", d, r=i + 1, career=i) for i, d in enumerate(
        ["20260607", "20260621", "20260705", "20261004", "20261018"])])
    out = fill_previous_runs(df)
    assert by_date(out, "H4")["休み明け～戦目"].tolist() == ["", "", "", "1", "2"]


def test_loaded_rows_keep_their_blanks_and_no_horse_id_is_a_no_op():
    df = frame([run_row("H5", "20261004", career=4), run_row("H5", "20261018", r=2, career=5)])
    df.loc[1, ["前走場所", "前走レースID(新)", "間隔"]] = ["大井", "202610014412010101", "2"]   # 前走は地方（人気が空）
    out = fill_previous_runs(df)
    assert out.loc[1, "前走人気"] == "" and out.loc[1, "前走場所"] == "大井"
    no_id = df.drop(columns=["血統登録番号"])
    out2 = fill_previous_runs(no_id)
    assert out2.equals(no_id) and out2.attrs["prev_filled"]["filled"] == 0


def test_missing_previous_columns_are_created():
    df = frame([run_row("H6", "20261004", career=1), run_row("H6", "20261018", r=2, career=2)])
    df = df.drop(columns=PREV_COLS)
    out = fill_previous_runs(df)
    assert out.loc[1, "前走場所"] == "東京" and out.loc[1, "間隔"] == "2" and out.loc[0, "前走場所"] == ""


def test_standardize_history_fills_by_default():
    df = frame([run_row("H7", "20261004", fin="１", career=1), run_row("H7", "20261018", r=2, career=2)])
    norm = pd.DataFrame({"日付": df["日付S"], "場所": df["場所"], "芝・ダ": "芝", "距離_raw": "1600", "芝(内・外)": ""})
    std = standardize_history(df, norm)
    assert std.loc[1, "前走着順"] == "1" and std.loc[1, "間隔"] == "2" and std.loc[1, "前走場所"] == "東京"
    cond = derive(std)
    assert cond.loc[1, "間隔帯"] != "" and cond.loc[1, "左右遷移"] == "左→左"
    assert standardize_history(df, norm, fill_previous=False).loc[1, "前走場所"] == ""


# ---------------------------------------------------------------------------
# replace（★6 の基本CSV → master）と raw_csv の統合で、前走を読み込んだCSVと同じ結果になる
# ---------------------------------------------------------------------------
DAYS = ["20250105", "20250119", "20250202", "20250413", "20250427", "20250511", "20250622", "20251005", "20251019",
        "20251102", "20251116", "20260111", "20260125", "20260208", "20260412", "20260426", "20260510"]


def season(n_horses: int = 12, seed: int = 3) -> pd.DataFrame:
    """12頭が1日1レース（10頭立て）を走る。各日2頭が休む（出走しない）・1頭が除外。1日だけ馬1の障害レースと9頭立て。"""
    import random
    rng = random.Random(seed)
    rows, career = [], {h: 0 for h in range(1, n_horses + 1)}
    for k, d in enumerate(DAYS):
        place = ["東京", "中山", "京都", "阪神", "新潟"][k % 5]
        rest = set(rng.sample(range(1, n_horses + 1), 2))
        field = [h for h in range(1, n_horses + 1) if h not in rest][:10]
        if k == 6:
            field = field[:9]                                       # 9頭立て（master には入らないが履歴には残る）
        scratch = field[rng.randrange(len(field))] if k % 4 == 1 else None
        dist = "障3000" if k == 9 and 1 in field else rng.choice(["芝1600", "ダ1400", "芝2000", "ダ1800"])
        track = "51" if dist.startswith("障") else "11"
        for pos, h in enumerate(field, 1):
            fin = "外" if h == scratch else "１２３４５６７８９"[pos - 1] if pos < 10 else "10"
            rows.append(run_row(f"20211{h:05d}", d, place, 1, pos, dist, fin, str(rng.randint(1, 10)),
                                f" {rng.choice([54, 55, 56, 57])} ", rng.choice(["逃げ", "先行", "中団", "後方"]),
                                str(rng.randint(1, 10)), rng.choice([" Ｍ ", " Ｈ ", " Ｓ "]), career=career[h],
                                track=track, heads=len(field)))
            rows[-1]["指数"] = str(40 + rng.randint(0, 30))
            rows[-1]["指数順位"] = ""
            career[h] += 1                                          # TARGET のキャリアは除外も数える
    return frame(rows)


def oracle(df: pd.DataFrame) -> pd.DataFrame:
    """TARGET が前走を読み込んだときの値（同じ規則を1行ずつ素直に書いたもの）。"""
    out = df.copy()
    week = lambda d: (pd.Timestamp(d) - pd.Timestamp("2000-01-06")).days // 7
    for hid, g in df.sort_values("日付S").groupby("血統登録番号"):
        last, last_rest = None, ""
        for i, row in g.iterrows():
            if last is not None:
                prev = df.loc[last]
                sd, dist = prev["距離"][0], prev["距離"][1:]
                iv = max(1, week(row["日付S"]) - week(prev["日付S"]))
                rest = "1" if iv >= REST_BREAK_INTERVAL else (str(int(last_rest) + 1) if last_rest else "")
                out.loc[i, PREV_COLS] = [prev["場所"], sd, dist, prev["斤量"], prev["人気"], prev["着順"], prev["脚質"],
                                         prev["上り3F順"], prev["レース印３"], prev["レースID(新)"], str(iv), rest]
            else:
                rest = ""
            if row["着順"] != "外":
                last, last_rest = i, rest
    return out


def test_oracle_matches_fill():
    df = season()
    full = oracle(df)
    out = fill_previous_runs(df)
    for c in PREV_COLS:
        assert out[c].tolist() == full[c].tolist(), c


def _master(tmp: Path, df: pd.DataFrame, name: str) -> pd.DataFrame:
    import replace_csv_ver3
    d = tmp / name
    synth.write_cp932(df, d / "csv_files" / "全場_タイムM.csv")
    assert replace_csv_ver3.main(["--csv-dir", str(d / "csv_files"), "--no-ext", "--master-dir", str(d / "m")]) == 0
    return pd.read_csv(d / "m" / "master_全場_タイムM.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)


def test_replace_master_equals_target_loaded_csv(tmp_path, capsys):
    """前走を読み込まずに出したCSVから作った master の22条件が、前走を読み込んだCSVから作ったものと同じになる
    （障害・9頭立ての前走も、絞る前に補う）。"""
    df = season()
    loaded = _master(tmp_path, oracle(df), "loaded")
    blank = _master(tmp_path, df, "blank")
    assert "血統登録番号の履歴から補いました" in capsys.readouterr().out
    from shisu.conditions import CONDITION_COLUMNS
    assert len(blank) == len(loaded) > 100
    for c in CONDITION_COLUMNS:
        assert blank[c].tolist() == loaded[c].tolist(), c
    assert (blank["叩き"] != "").sum() > 20 and (blank["前走着順"] != "").sum() > 100
    after_jump = blank[blank["日付"] == DAYS[10]]
    assert "" not in set(after_jump["左右遷移"])                     # 障害の次も前走が付く（障害は master に無い）


def test_raw_merge_fills_from_rows_older_than_the_window(tmp_path):
    """raw_csv の統合でも補う。10年より古い行は消す前に、次の走りの前走として使う。"""
    from shisu import raw_merge as rm
    old = frame([run_row("H8", "20160612", "阪神", 3, 4, "芝2400", "２", career=6)])
    old.loc[0, ["前走場所", "間隔", "休み明け～戦目"]] = ["京都", "5", "2"]
    synth.write_cp932(old, tmp_path / "csv_files" / "全場_タイムM.csv")
    new = frame([run_row("H8", "20170108", "中山", 1, 2, "芝2000", career=7),
                 run_row("H8", "20260111", "中山", 1, 2, "芝2000", career=8)])
    synth.write_cp932(new, tmp_path / "raw_csv" / "タイムM_追加.csv")
    logs = []
    r = rm.merge_raw(tmp_path / "raw_csv", tmp_path / "csv_files", log=logs.append)[0]
    out = pd.read_csv(tmp_path / "csv_files" / "全場_タイムM.csv", encoding="cp932", dtype=str, keep_default_na=False)
    assert r.dropped_old == 1 and r.prev_filled == 2 and "前走の列を履歴から補った 2 行" in r.summary()
    first = out[out["日付S"] == "20170108"].iloc[0]
    assert (first["前走場所"], first["前距離"], first["前走着順"], first["休み明け～戦目"]) == ("阪神", "2400", "２", "1")
    assert any("血統登録番号 の履歴から 2 行を補いました" in m for m in logs)
