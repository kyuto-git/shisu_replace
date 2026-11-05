"""band 系（順位帯）で件数・勝率が膨らんだ旧版の不具合の再発防止。

旧版の export_foragg() は out.merge(base_df, on=["レースID(新)", "指数順位帯"]) で列を付けていた。
入力の レースID(新) がレース単位（馬番なし）だと、同じレース・同じ順位帯の別の馬同士が直積で結合され
（5頭なら 5×5=25行）、「中山×ダ×1200×ミナリク×1~2位」の本来4件が22件になり、他の騎手の馬の
着順（1着など）が混入して勝率・複勝率が分子ごと膨らんだ。

今の replace は1頭1行の DataFrame にそのまま列を付けてキーを作る（merge しない）。さらに
レース単位（16桁）の レースID(新) は馬番を付けて18桁にそろえる。ここではそれを次の性質で固定する:
  - ミナリク騎手の band キーの件数・勝率が、本人の騎乗だけから数えた値と一致する
  - レース単位のIDで入れても、馬単位のIDで入れたときと成績表が完全に一致する
  - 素材の各ファイルで、同じ馬（レースID × 馬番）が同じキーに2回以上現れない
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

N_HEADS = 16
MINARIK = "ミナリク"
# レースごとの (ミナリクの馬番, ミナリクの指数順位)。1〜5レース目は順位帯 1~3位、6・7レース目は 8位
# （成績表は件数5以上のグループだけ出るので、1~3位 を5件にしている）
MINARIK_RIDES = [(3, 1), (7, 2), (12, 3), (16, 2), (1, 1), (5, 8), (9, 8)]
N_BAND = 5


def minarik_frame(race_level_id: bool, with_rank: bool = True) -> pd.DataFrame:
    """中山ダ1200・16頭立て6レース。ミナリクは常に5着。順位帯 1~3位 の他の2頭は1着・2着。"""
    rows = []
    for r, (m_uma, m_rank) in enumerate(MINARIK_RIDES, 1):
        rid16 = f"202501{r * 2:02d}06010{r}{r:02d}"
        others = iter(k for k in range(1, N_HEADS + 1) if k != m_rank)
        rank_of = {uma: (m_rank if uma == m_uma else next(others)) for uma in range(1, N_HEADS + 1)}
        finish = iter(k for k in range(1, N_HEADS + 1) if k != 5)      # ミナリク以外は順位の順に 1,2,3,4,6,…
        fin_of = {uma: next(finish) for uma in sorted((u for u in rank_of if u != m_uma), key=rank_of.get)}
        fin_of[m_uma] = 5
        for uma in range(1, N_HEADS + 1):
            rank, fin = rank_of[uma], fin_of[uma]
            rows.append({
                "レースID(新)": rid16 if race_level_id else f"{rid16}{uma:02d}",
                "場所": "中山", "芝・ダ": "ダ", "距離": "1200", "重量種別": "定量", "クラス名": "1勝",
                "頭数": str(N_HEADS), "枠番": str(min(8, (uma - 1) // 2 + 1)), "馬番": str(uma),
                "馬名": f"ウマ{r}_{uma}", "騎手": MINARIK if uma == m_uma else f"騎手{uma:02d}",
                "指数": str(200 - rank * 5), "指数順位": str(rank), "着順": str(fin),
                "単勝オッズ": "5.0", "単勝配当": "500" if fin == 1 else "0", "複勝配当": "200" if fin <= 3 else "0",
            })
    df = pd.DataFrame(rows)
    return df if with_rank else df.drop(columns=["指数順位"])


def run_pipeline(tmp: Path, df: pd.DataFrame, name: str = "中山_カツラキ.csv") -> dict[str, pd.DataFrame]:
    import aggregate_stats_ver2
    import replace_csv_ver3
    d = {k: tmp / k for k in ("csv", "foragg", "agg", "master", "processed")}
    synth.write_cp932(df, d["csv"] / name)
    assert replace_csv_ver3.main(["--csv-dir", str(d["csv"]), "--foragg-dir", str(d["foragg"]),
                                  "--master-dir", str(d["master"]), "--processed-dir", str(d["processed"]),
                                  "--clean"]) == 0
    assert aggregate_stats_ver2.main(["--in-dir", str(d["foragg"]), "--out-dir", str(d["agg"]), "--clean",
                                      "--wedge-min-year-count", "0"]) == 0
    out = {}
    for p in sorted(d["agg"].glob("*.csv")):
        out[p.name] = pd.read_csv(p, encoding="cp932", dtype=str, keep_default_na=False)
    out["_foragg_dir"] = d["foragg"]
    out["_master_dir"] = d["master"]
    return out


SOURCE = "中山_カツラキ"


def _agg(res: dict, family: str, scheme: str) -> pd.DataFrame:
    """通常（オッズ帯で絞らない）の成績表。"""
    from shisu.naming import agg_filename
    return res[agg_filename(family, "normal", scheme, SOURCE)]


def _material(res: dict, family: str, scheme: str) -> pd.DataFrame:
    from shisu.naming import material_filename
    return pd.read_csv(res["_foragg_dir"] / material_filename(family, scheme, SOURCE), encoding="cp932",
                       dtype=str, keep_default_na=False)


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    out = {}
    for name, kw in (("horse", {"race_level_id": False}), ("race", {"race_level_id": True}),
                     ("race_norank", {"race_level_id": True, "with_rank": False}),
                     ("horse_norank", {"race_level_id": False, "with_rank": False})):
        out[name] = run_pipeline(tmp_path_factory.mktemp(name), minarik_frame(**kw))
    return out


@pytest.mark.parametrize("variant", ["horse", "race"])
def test_minarik_band_counts_are_true_counts(results, variant):
    """ミナリクの rankJ_band キーの件数・勝率は、本人の5騎乗だけから数えた値（旧版は件数・勝率が膨らんだ）。"""
    res = results[variant]
    s1 = _agg(res, "rankJ_band", "s1").set_index("結合データ")
    row = s1.loc[f"中山×ダ×1200×{MINARIK}×1~3位"]
    assert row["件数"] == str(N_BAND)
    assert float(row["勝率_val"]) == 0.0 and float(row["複勝率_val"]) == 0.0 and float(row["連対率_val"]) == 0.0
    assert float(row["平均着順"]) == 5.0
    assert f"中山×ダ×1200×{MINARIK}×7~9位" not in s1.index     # 8位は2件（件数<5 なので成績表には出ない）
    s2 = _agg(res, "rankJ_band", "s2").set_index("結合データ")
    assert s2.loc[f"中山×ダ×1200×{MINARIK}×1~5位", "件数"] == str(N_BAND)
    # 枠番グループ×騎手×順位帯: 内・外に分かれても合計5件（件数5未満のグループは成績表に出ないので素材で数える）
    mat = _material(res, "rankBJ_band", "s1")
    mine = mat[mat["結合データ"].str.contains(f"×{MINARIK}×1~3位", regex=False)]
    assert len(mine) == N_BAND and (mine["着順"] == "5").all()
    # 同じ順位帯の他の馬（1着・2着）は、他の騎手のキーにだけ入る
    others = mat[mat["結合データ"].str.endswith("×1~3位") & ~mat["結合データ"].str.contains(MINARIK)]
    assert len(others) == N_BAND * 2 + (len(MINARIK_RIDES) - N_BAND) * 3


def test_race_level_ids_give_identical_tables(results):
    """レース単位（16桁）の レースID(新) で入れても、馬単位（18桁）と成績表が完全に一致する。"""
    a, b = results["horse"], results["race"]
    names = sorted(k for k in a if not k.startswith("_"))
    assert names == sorted(k for k in b if not k.startswith("_")) and len(names) == 80
    for n in names:
        pd.testing.assert_frame_equal(a[n], b[n], check_like=False, obj=n)
    a, b = results["horse_norank"], results["race_norank"]
    for n in names:
        pd.testing.assert_frame_equal(a[n], b[n], obj=n)


def test_race_level_ids_keep_races_separate_for_rank(results):
    """指数順位の列が無いと順位はレース内で計算し直す。レース単位のIDでも R ごとに分かれる
    （旧来の「末尾2桁を落とす」だと R が消えて同じ日・場の全レースが1レース扱いになる）。"""
    m = pd.read_csv(next(results["race_norank"]["_master_dir"].glob("master_*.csv.gz")), encoding="cp932",
                    dtype=str, keep_default_na=False)
    assert m["レースID(新)"].str.fullmatch(r"\d{18}").all()
    assert m["レースID(新/馬番無)"].nunique() == len(MINARIK_RIDES)
    for _, g in m.groupby("レースID(新/馬番無)"):
        assert sorted(g["順位"].astype(int)) == list(range(1, N_HEADS + 1))
    mine = m[m["騎手"] == MINARIK].sort_values("レースID(新)")
    assert mine["順位"].astype(int).tolist() == [rk for _, rk in MINARIK_RIDES]


def test_one_horse_one_row_per_material(results):
    """素材の各ファイルで、同じ馬（レースID × 馬番）は同じキー（× 順位帯）に1回だけ。行数も入力の頭数を超えない。"""
    for variant in ("horse", "race"):
        for p in results[variant]["_foragg_dir"].glob("foragg_*.csv.gz"):
            mat = pd.read_csv(p, encoding="cp932", dtype=str, keep_default_na=False)
            assert len(mat) <= N_HEADS * len(MINARIK_RIDES), p.name
            assert not mat.duplicated(["レースID(新)", "馬番"]).any(), p.name      # 1頭がキーを2つ持たない
            assert (mat["レースID(新)"].str.slice(16, 18) == mat["馬番"].str.zfill(2)).all(), p.name


def test_aggregate_dedupe_does_not_merge_horses_without_horse_column():
    """馬番・馬名の無い素材でレース単位のIDなら、重複排除で別の馬をまとめない。馬単位のIDなら重複だけ除く。"""
    import aggregate_stats_ver2 as a
    base = {"結合データ": "K", "着順": "1", "単勝オッズ": "2.0", "単勝配当": "200", "複勝配当": "110", "year": "2025"}
    race_level = pd.DataFrame([{**base, "レースID(新)": "2025010506010101", "着順": str(i)} for i in range(1, 7)])
    out = a.aggregate_frame(race_level)
    assert out.loc[0, "件数"] == 6
    horse_level = pd.DataFrame([{**base, "レースID(新)": f"20250105060101010{i % 3 + 1}"} for i in range(6)])
    out = a.aggregate_frame(horse_level)
    assert out.empty                                         # 重複を除くと3件（< 5）なので出ない
    horse_level = pd.DataFrame([{**base, "レースID(新)": f"2025010506010101{i:02d}"} for i in range(1, 7)])
    assert a.aggregate_frame(pd.concat([horse_level, horse_level])).loc[0, "件数"] == 6


def test_horse_race_id_and_race_id_without_horse():
    from shisu.normalize import horse_race_id, race_id_without_horse
    rid = pd.Series(["2025010506010101", "2025010506010101", "202501050601010103", "", "abc", "2025010506010101"])
    uma = pd.Series(["1", "１２", "3", "4", "5", ""])
    assert horse_race_id(rid, uma).tolist() == ["202501050601010101", "202501050601010112", "202501050601010103",
                                                "", "abc", "2025010506010101"]
    assert race_id_without_horse(pd.Series(["202501050601010103", "2025010506010101", "X1234", ""])).tolist() == \
        ["2025010506010101", "2025010506010101", "X12", ""]
    assert race_id_without_horse(pd.Series(["202501050601010103"]), pd.Series(["given"])).tolist() == ["given"]


def test_replace_source_does_not_merge_on_race_id():
    """構造の確認: replace は素材を作るときにレースIDで merge / join しない（1頭1行の DataFrame に列を付ける）。"""
    src = (Path(__file__).resolve().parent.parent / "replace_csv_ver3.py").read_text(encoding="utf-8")
    assert "merge(" not in src
    joins = [m.group(1) for m in re.finditer(r"(\S)\.join\(", src)]
    assert all(c in "'\"" for c in joins), joins                 # 文字列の結合（"、".join など）だけ


def test_scenario_reproduces_old_merge_inflation():
    """このテストの入力で旧版の結合（レースID × 順位帯 で merge）をすると件数・勝率が膨らむ＝上のテストが効いている確認。"""
    df = minarik_frame(race_level_id=True)
    rank = df["指数順位"].astype(int)
    df["指数順位帯"] = pd.cut(rank, [0, 3, 6, 9, 99], labels=["1~3位", "4~6位", "7~9位", "10位~"]).astype(str)
    keys = df.assign(結合データ="中山×ダ×1200×" + df["騎手"] + "×" + df["指数順位帯"])[
        ["レースID(新)", "指数順位帯", "結合データ"]]
    old = keys.merge(df[["レースID(新)", "指数順位帯", "着順", "馬番"]], on=["レースID(新)", "指数順位帯"], how="left")
    mine = old[old["結合データ"] == f"中山×ダ×1200×{MINARIK}×1~3位"]
    assert len(mine) == N_BAND * 3                      # 本来5件 → 15件
    assert (mine["着順"] == "1").sum() == N_BAND           # 他の馬の1着が混入
