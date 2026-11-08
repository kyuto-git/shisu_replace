"""raw_csv/ の過去基本CSV（追加抽出したタイムM・対戦M）を csv_files/全場_*.csv に統合する（最大10年分）。"""
from __future__ import annotations

import io
import os
import sys
import time
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent))
import synth  # noqa: E402

from shisu import raw_merge as rm  # noqa: E402


def rows(year: int, races: int = 1, heads: int = 10, value: int = 50, day: str = "1025") -> pd.DataFrame:
    out = []
    for r in range(1, races + 1):
        for u in range(1, heads + 1):
            out.append({"日付S": f"{year}{day}", "場所": "東京", "Ｒ": str(r), "距離": "芝1600", "頭数": str(heads),
                        "枠番": str(min(8, (u + 1) // 2)), "馬番": str(u), "騎手": f"騎手{u}", "クラス名": "1勝",
                        "着順": str(u), "単勝オッズ": "5.0", "指数": str(value + u), "指数順位": str(heads - u + 1),
                        "レースID(新)": f"{year}{day}050401{r:02d}{u:02d}"})
    return pd.DataFrame(out)


def test_kind_of_names():
    assert rm.kind_of("追加_タイムM_2026.csv") == "タイムM" and rm.kind_of("mining_2026.CSV") == "タイムM"
    assert rm.kind_of("マイニング.csv") == "タイムM" and rm.kind_of("TimeM.csv") == "タイムM"
    assert rm.kind_of("対戦型マイニング_2026.csv") == "対戦M"          # マイニングも含むが対戦が先
    assert rm.kind_of("MatchM.csv") == "対戦M" and rm.kind_of("202610.csv") is None


def _write(df, path, mtime=None):
    synth.write_cp932(df, path)
    if mtime is not None:
        os.utime(path, (mtime, mtime))


def test_merge_dedupes_keeps_ten_years_and_moves_files(tmp_path):
    csv_dir, raw = tmp_path / "csv_files", tmp_path / "raw_csv"
    existing = pd.concat([rows(y) for y in range(2014, 2026)], ignore_index=True)        # 2014〜2025
    _write(existing, csv_dir / "全場_タイムM.csv")
    now = time.time()
    upd = rows(2025, value=80)                                                            # 2025年を値ごと出し直し
    _write(pd.concat([upd, rows(2026, races=2)], ignore_index=True), raw / "追加_タイム_2026.csv", now - 100)
    _write(rows(2026, races=2, value=90), raw / "追加_タイム_2026_再.csv", now)          # 後のファイルが優先
    _write(rows(2026), raw / "対戦_2026.csv", now)
    _write(rows(2026), raw / "メモ.csv", now)                                            # 種類が分からない → 触らない
    logs = []
    res = {r.kind: r for r in rm.merge_raw(raw, csv_dir, log=logs.append)}
    t = res["タイムM"]
    assert t.before == 120 and len(t.files) == 2 and t.added_rows == 30 + 20
    assert t.years == (2017, 2026) and t.dropped_old == 3 * 10                           # 2014〜2016 を消す
    out = pd.read_csv(csv_dir / "全場_タイムM.csv", encoding="cp932", dtype=str, keep_default_na=False)
    assert len(out) == t.after == 9 * 10 + 20 and not out["レースID(新)"].duplicated().any()
    y = out["レースID(新)"].str[:4]
    assert set(y) == {str(v) for v in range(2017, 2027)}
    assert set(out.loc[y == "2025", "指数"].astype(int)) == set(range(81, 91))          # 出し直した値
    assert set(out.loc[y == "2026", "指数"].astype(int)) == set(range(91, 101))         # 後のファイル
    assert sorted(p.name for p in (raw / "merged_done").iterdir()) == ["対戦_2026.csv", "追加_タイム_2026.csv",
                                                                       "追加_タイム_2026_再.csv"]
    assert (raw / "メモ.csv").exists() and any("メモ.csv" in m for m in logs)
    assert res["対戦M"].before == 0 and (csv_dir / "全場_対戦M.csv").exists()
    assert rm.pending_files(raw) == [raw / "メモ.csv"]                                    # 次は統合し直さない
    # もう一度同じ名前で置いても merged_done では上書きしない
    _write(rows(2026), raw / "対戦_2026.csv")
    rm.merge_raw(raw, csv_dir, log=lambda m: None)
    assert (raw / "merged_done" / "対戦_2026_2.csv").exists()


def test_ref_year_and_unreadable_file(tmp_path):
    csv_dir, raw = tmp_path / "csv", tmp_path / "raw"
    _write(pd.concat([rows(y) for y in range(2016, 2025)], ignore_index=True), raw / "タイム.csv")
    (raw / "タイム_壊れ.csv").write_bytes(b"\x00\x01")
    logs = []
    r = rm.merge_raw(raw, csv_dir, ref_year=2026, log=logs.append)[0]
    assert r.years == (2017, 2024) and r.dropped_old == 10                                 # 基準 2026 → 2017〜
    assert any("タイム_壊れ.csv" in m for m in logs) and (raw / "タイム_壊れ.csv").exists()


def test_replace_merges_raw_csv_before_masters(tmp_path, capsys):
    import replace_csv_ver3
    csv_dir, raw = tmp_path / "csv_files", tmp_path / "raw_csv"
    _write(pd.concat([rows(2025, races=2), rows(2026, races=2)], ignore_index=True), raw / "タイムM_追加.csv")
    assert replace_csv_ver3.main(["--csv-dir", str(csv_dir), "--raw-dir", str(raw), "--no-ext",
                                  "--master-dir", str(tmp_path / "m")]) == 0
    assert "統合します" in capsys.readouterr().out
    m = pd.read_csv(tmp_path / "m" / "master_全場_タイムM.csv.gz", encoding="cp932", dtype=str, keep_default_na=False)
    assert len(m) == 40 and not rm.pending_files(raw)
    _write(rows(2026, races=1, value=70, day="1101"), raw / "タイムM_追加2.csv")
    assert replace_csv_ver3.main(["--csv-dir", str(csv_dir), "--raw-dir", str(raw), "--no-ext", "--no-raw-merge",
                                  "--master-dir", str(tmp_path / "m2")]) == 0
    assert rm.pending_files(raw)                                                           # --no-raw-merge なら残る


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("SHISU_BASE_DIR", str(tmp_path))
    import app as am
    return am.app.test_client()


def test_web_upload_and_merge(client, tmp_path):
    page = client.get("/ingest").get_data(as_text=True)
    assert "過去基本CSV（タイムM・対戦M）の統合" in page
    _write(rows(2025), tmp_path / "raw_csv" / "タイム_置いた.csv")
    assert 'name="raw_pick" value="タイム_置いた.csv" checked' in client.get("/ingest").get_data(as_text=True)
    up = rows(2026).to_csv(index=False).encode("cp932")
    r = client.post("/ingest/raw_merge", data={
        "raw_pick": ["タイム_置いた.csv", "../../etc/passwd"],
        "raw_files": [(io.BytesIO(up), "../対戦型マイニング_2026.csv"), (io.BytesIO(up), "タイム_2026.csv")]},
        content_type="multipart/form-data")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "完了" in body and "タイムM: 既存 0 行" in body and "対戦M: 既存 0 行" in body
    out = pd.read_csv(tmp_path / "csv_files" / "全場_タイムM.csv", encoding="cp932", dtype=str)
    assert set(out["レースID(新)"].str[:4]) == {"2025", "2026"}
    assert len(list((tmp_path / "raw_csv" / "merged_done").iterdir())) == 3
    assert not (tmp_path / "etc").exists()
    r = client.post("/ingest/raw_merge", data={"raw_files": [(io.BytesIO(up), "202610.csv")]},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and "対戦" in r.get_data(as_text=True)
    assert client.post("/ingest/raw_merge", data={}).status_code == 400
    r = client.post("/ingest/raw_merge", data={}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403


def test_race_level_ids_are_deduped_per_horse(tmp_path):
    """レース単位（16桁）の レースID(新) でも、馬番を付けて馬ごとに比べる（1レースが1頭に潰れない）。"""
    csv_dir, raw = tmp_path / "csv_files", tmp_path / "raw_csv"
    old = rows(2025, races=3)
    old["レースID(新)"] = old["レースID(新)"].str[:16]                                  # 既存も16桁
    _write(old, csv_dir / "全場_タイムM.csv")
    new = rows(2025, races=1, value=80)                                                 # R1 を18桁で出し直し
    add16 = rows(2026, races=1)
    add16["レースID(新)"] = add16["レースID(新)"].str[:16]
    _write(pd.concat([new, add16], ignore_index=True), raw / "タイム_追加.csv")
    r = rm.merge_raw(raw, csv_dir, log=lambda m: None)[0]
    out = pd.read_csv(csv_dir / "全場_タイムM.csv", encoding="cp932", dtype=str, keep_default_na=False)
    assert r.replaced == 10 and len(out) == 3 * 10 + 10 == r.after                     # R1 の10頭だけ置き換え
    r1 = out[(out["レースID(新)"].str[:16] == old["レースID(新)"].iloc[0]) | (out["レースID(新)"].str[:16] ==
                                                                              new["レースID(新)"].str[:16].iloc[0])]
    assert set(r1["指数"].astype(int)) == set(range(81, 91))
    assert (out["レースID(新)"].str.len() == 16).sum() == 20 + 10                         # 元の表記のまま書く


def test_failed_write_keeps_existing_history(tmp_path, monkeypatch):
    """書き込みが途中で失敗しても、既存の 全場_*.csv は元のまま（一時ファイルに書いてから置き換える）。"""
    csv_dir, raw = tmp_path / "csv_files", tmp_path / "raw_csv"
    _write(pd.concat([rows(y) for y in range(2020, 2026)], ignore_index=True), csv_dir / "全場_タイムM.csv")
    before = (csv_dir / "全場_タイムM.csv").read_bytes()
    _write(rows(2026), raw / "タイム_追加.csv")

    def boom(df, path):
        Path(path).write_bytes(b"partial")
        raise OSError("No space left on device")
    monkeypatch.setattr(rm, "write_csv", boom)
    with pytest.raises(OSError):
        rm.merge_raw(raw, csv_dir, log=lambda m: None)
    assert (csv_dir / "全場_タイムM.csv").read_bytes() == before
    assert (raw / "タイム_追加.csv").exists()                                               # 移さない


def test_replace_default_raw_dir_follows_csv_dir(tmp_path, monkeypatch):
    """--csv-dir を別の場所にしたら、--raw-dir の既定はその隣の raw_csv（本物の raw_csv を取り込んで動かさない）。"""
    import replace_csv_ver3
    monkeypatch.setenv("SHISU_BASE_DIR", str(tmp_path / "base"))
    real = tmp_path / "base" / "raw_csv"
    _write(rows(2026), real / "タイムM_本番.csv")
    other = tmp_path / "work"
    _write(pd.concat([rows(2025, races=2), rows(2026, races=2)], ignore_index=True), other / "raw_csv" / "タイムM_試し.csv")
    assert replace_csv_ver3.main(["--csv-dir", str(other / "csv_files"), "--no-ext",
                                  "--master-dir", str(other / "m")]) == 0
    assert (real / "タイムM_本番.csv").exists() and not (real / "merged_done").exists()
    assert (other / "raw_csv" / "merged_done" / "タイムM_試し.csv").exists()
    assert (other / "csv_files" / "全場_タイムM.csv").exists()


# ---------------------------------------------------------------------------
# 列パッチ（レースID(新) と補う列だけのCSV）
# ---------------------------------------------------------------------------
PREV = ["間隔", "休み明け～戦目", "前走場所", "前芝・ダ", "前距離", "前走斤量", "前走人気", "前走着順", "前走脚質",
        "前走上り3F順", "前走レース印３", "前走レースID(新)"]


def patch_rows(base: pd.DataFrame, **cols) -> pd.DataFrame:
    out = pd.DataFrame({"レースID(新)": base["レースID(新)"].to_numpy()})
    for c, v in cols.items():
        out[c] = v if not callable(v) else [v(i) for i in range(len(out))]
    return out


def read(path):
    return pd.read_csv(path, encoding="cp932", dtype=str, keep_default_na=False)


def test_file_role_by_columns_then_name(tmp_path):
    _write(rows(2026), tmp_path / "202610.csv")                                     # 指数あり・名前で分からない
    _write(rows(2026), tmp_path / "タイムM_2026.csv")
    _write(patch_rows(rows(2026), 前走場所="東京"), tmp_path / "前走_2026.csv")
    _write(patch_rows(rows(2026), 前走場所="東京"), tmp_path / "対戦M_前走.csv")      # 名前に 対戦 があっても 指数 が無ければパッチ
    assert rm.file_role(tmp_path / "202610.csv") is None
    assert rm.file_role(tmp_path / "タイムM_2026.csv") == "タイムM"
    assert rm.file_role(tmp_path / "前走_2026.csv") == rm.PATCH_KIND == rm.file_role(tmp_path / "対戦M_前走.csv")


def test_patch_fills_both_merged_files(tmp_path):
    """複数のパッチを読み、レースID(新) ごとに空でない値で 全場_タイムM・全場_対戦M の両方を上書き・補完する。
    同じ馬・同じ列は後のファイルの値。パッチの空欄は上書きしない。無い列は足す。"""
    csv_dir, raw = tmp_path / "csv_files", tmp_path / "raw_csv"
    base = pd.concat([rows(2025), rows(2026)], ignore_index=True)
    t = base.copy()
    for c in PREV:
        t[c] = ""
    t.loc[0, "前走場所"] = "中山"                                                     # もとからある値
    _write(t, csv_dir / "全場_タイムM.csv")
    m = t.drop(columns=["前走場所"])                                                  # 対戦M には 前走場所 の列が無い
    m["指数"] = "77"
    _write(m, csv_dir / "全場_対戦M.csv")
    now = time.time()
    p1 = patch_rows(base, 前走場所=lambda i: "東京" if i % 2 else "", 間隔="3", 前走着順="２")
    _write(p1, raw / "前走_前半.csv", now - 100)
    p2 = patch_rows(base.iloc[:5], 前走着順="１", **{"前走レースID(新)": lambda i: f"2025091005040101{i + 1:02d}"})
    _write(p2, raw / "前走_後半.csv", now)
    logs = []
    res = rm.merge_raw(raw, csv_dir, log=logs.append)
    assert len(res) == 1 and res[0].kind == rm.PATCH_KIND
    r = res[0]
    assert len(r.files) == 2 and r.rows == 25 and r.unmatched == 0
    assert r.updated == {"全場_タイムM.csv": 20, "全場_対戦M.csv": 20}
    assert r.columns["全場_タイムM.csv"] == {"前走場所": 10, "間隔": 20, "前走着順": 20, "前走レースID(新)": 5}
    assert r.columns["全場_対戦M.csv"]["前走場所"] == 10                            # 列を足した
    for name in ("全場_タイムM.csv", "全場_対戦M.csv"):
        out = read(csv_dir / name)
        assert len(out) == 20 and out["レースID(新)"].tolist() == base["レースID(新)"].tolist()
        assert out["前走着順"].tolist() == ["１"] * 5 + ["２"] * 15                  # 後のファイルの値
        assert out["間隔"].tolist() == ["3"] * 20
    out = read(csv_dir / "全場_タイムM.csv")
    assert out.loc[0, "前走場所"] == "中山" and out.loc[1, "前走場所"] == "東京"     # パッチの空欄は上書きしない
    assert read(csv_dir / "全場_対戦M.csv")["指数"].tolist() == ["77"] * 20          # パッチに無い列はそのまま
    assert sorted(p.name for p in (raw / "merged_done").iterdir()) == ["前走_前半.csv", "前走_後半.csv"]
    msg = next(m for m in logs if "前走・追加列パッチを反映しました" in m)
    assert "全場_タイムM.csv 更新 20 行" in msg and "対象列: 前走場所・間隔・前走着順・前走レースID(新)" in msg


def test_patch_with_race_level_ids_and_bad_ids(tmp_path):
    csv_dir, raw = tmp_path / "csv_files", tmp_path / "raw_csv"
    base = rows(2026)
    _write(base, csv_dir / "全場_タイムM.csv")
    p = patch_rows(base, 前走場所="阪神")
    p["馬番"] = base["馬番"].to_numpy()
    p["レースID(新)"] = p["レースID(新)"].str[:16]                                     # レース単位の ID ＋ 馬番
    p.loc[0, "レースID(新)"] = "abc"
    _write(p, raw / "前走.csv")
    r = rm.merge_raw(raw, csv_dir, log=lambda m: None)[0]
    assert (r.rows, r.bad_ids, r.updated["全場_タイムM.csv"]) == (9, 1, 9)
    assert read(csv_dir / "全場_タイムM.csv")["前走場所"].tolist() == [""] + ["阪神"] * 9


def test_patch_without_targets_or_matches_stays_in_raw_csv(tmp_path):
    csv_dir, raw = tmp_path / "csv_files", tmp_path / "raw_csv"
    _write(patch_rows(rows(2026), 前走場所="東京"), raw / "前走.csv")
    logs = []
    r = rm.merge_raw(raw, csv_dir, log=logs.append)[0]
    assert not r.moved and (raw / "前走.csv").exists() and any("当てられません" in m for m in logs)
    _write(rows(2025), csv_dir / "全場_タイムM.csv")                                   # 別の年だけ
    _write(rows(2025), csv_dir / "全場_対戦M.csv")
    logs.clear()
    r = rm.merge_raw(raw, csv_dir, log=logs.append)[0]
    assert not r.moved and (raw / "前走.csv").exists() and r.missing == {"全場_タイムM.csv": 10, "全場_対戦M.csv": 10}
    assert any("前走.csv: 10 行のうち 10 行" in m and "raw_csv に残します" in m for m in logs)
    assert not (raw / "merged_done").exists()


def test_base_and_patch_in_one_run_and_partial_match(tmp_path):
    """同じ回に置いた基本CSVを先に統合してからパッチを当てる。当たらない行があれば、当たった分は反映して raw_csv に残す
    （WARN）。あとでその基本CSVを統合すると、残ったパッチが当たって移る。"""
    csv_dir, raw = tmp_path / "csv_files", tmp_path / "raw_csv"
    _write(rows(2026), raw / "タイムM_2026.csv")
    _write(rows(2026), raw / "対戦M_2026.csv")
    _write(patch_rows(pd.concat([rows(2026), rows(2027)], ignore_index=True), 前走場所="京都"), raw / "前走.csv")
    logs = []
    res = {r.kind: r for r in rm.merge_raw(raw, csv_dir, log=logs.append)}
    p = res[rm.PATCH_KIND]
    assert res["タイムM"].after == 10 and p.updated == {"全場_タイムM.csv": 10, "全場_対戦M.csv": 10}
    assert p.unmatched == 10 and not p.moved and (raw / "前走.csv").exists()
    assert any("前走.csv: 20 行のうち 10 行" in m for m in logs)
    assert set(read(csv_dir / "全場_タイムM.csv")["前走場所"]) == {"京都"}
    _write(rows(2027), raw / "タイムM_2027.csv")
    _write(rows(2027), raw / "対戦M_2027.csv")
    res = {r.kind: r for r in rm.merge_raw(raw, csv_dir, log=lambda m: None)}
    assert res[rm.PATCH_KIND].unmatched == 0 and (raw / "merged_done" / "前走.csv").exists()
    for name in ("全場_タイムM.csv", "全場_対戦M.csv"):
        assert read(csv_dir / name)["前走場所"].tolist() == ["京都"] * 20


def test_patch_waits_for_the_other_merged_file(tmp_path):
    """全場_対戦M.csv がまだ無い（タイムM の基本CSVとパッチを先に置いた）ときは、タイムM に当ててパッチを残す。
    対戦M の基本CSVを統合した回に対戦M にも当たり、そこで移す。"""
    csv_dir, raw = tmp_path / "csv_files", tmp_path / "raw_csv"
    _write(rows(2026), raw / "タイムM_2026.csv")
    _write(patch_rows(rows(2026), 前走場所="新潟"), raw / "前走.csv")
    logs = []
    p = {r.kind: r for r in rm.merge_raw(raw, csv_dir, log=logs.append)}[rm.PATCH_KIND]
    assert p.updated == {"全場_タイムM.csv": 10} and p.missing["全場_対戦M.csv"] == 10 and not p.moved
    assert any("全場_対戦M.csv がありません" in m for m in logs) and (raw / "前走.csv").exists()
    _write(rows(2026), raw / "対戦M_2026.csv")
    p = {r.kind: r for r in rm.merge_raw(raw, csv_dir, log=lambda m: None)}[rm.PATCH_KIND]
    assert p.updated == {"全場_タイムM.csv": 0, "全場_対戦M.csv": 10} and p.moved
    assert set(read(csv_dir / "全場_対戦M.csv")["前走場所"]) == {"新潟"}


def test_only_fully_applied_patch_files_are_moved(tmp_path):
    """同じ回の複数のパッチで、当たらないパッチは（ほかのパッチが当たっても）raw_csv に残す。"""
    csv_dir, raw = tmp_path / "csv_files", tmp_path / "raw_csv"
    for kind in ("タイムM", "対戦M"):
        _write(rows(2026), csv_dir / f"全場_{kind}.csv")
    _write(patch_rows(rows(2026), 前走場所="東京"), raw / "前走_2026.csv")
    _write(patch_rows(rows(2027), 前走場所="東京"), raw / "前走_2027.csv")      # 2027 の基本CSVはまだ
    logs = []
    p = rm.merge_raw(raw, csv_dir, log=logs.append)[0]
    assert [x.name for x in p.moved] == ["前走_2026.csv"] and (raw / "前走_2027.csv").exists()
    assert any("前走_2027.csv: 10 行のうち 10 行" in m for m in logs)


def test_patch_headers_follow_existing_columns_and_rank_is_protected(tmp_path):
    """パッチの見出しの空白・全角半角のゆれは 全場_*.csv の列に合わせる。指数順位（タイムM と対戦M で違う）は書き換えない。"""
    csv_dir, raw = tmp_path / "csv_files", tmp_path / "raw_csv"
    base = rows(2026)
    base["前走レース印３"] = ""
    for kind in ("タイムM", "対戦M"):
        _write(base, csv_dir / f"全場_{kind}.csv")
    p = patch_rows(base, **{" 前走レース印3 ": " Ｈ ", "指数順位": "1"})
    _write(p, raw / "前走.csv")
    rm.merge_raw(raw, csv_dir, log=lambda m: None)
    out = read(csv_dir / "全場_タイムM.csv")
    assert set(out["前走レース印３"]) == {" Ｈ "} and "前走レース印3" not in out.columns
    assert out["指数順位"].tolist() == base["指数順位"].tolist()


def test_replace_uses_patched_previous_columns(tmp_path):
    """replace の自動統合でパッチが当たり、master の22条件（前走着順・間隔帯）に入る。"""
    import replace_csv_ver3
    csv_dir, raw = tmp_path / "csv_files", tmp_path / "raw_csv"
    base = rows(2026)
    _write(base, csv_dir / "全場_タイムM.csv")
    _write(base, csv_dir / "全場_対戦M.csv")
    _write(patch_rows(base, 前走着順="２", 間隔="3", 前走場所="中山"), raw / "前走パッチ.csv")
    assert replace_csv_ver3.main(["--csv-dir", str(csv_dir), "--raw-dir", str(raw), "--no-ext",
                                  "--master-dir", str(tmp_path / "m")]) == 0
    m = read(tmp_path / "m" / "master_全場_タイムM.csv.gz")
    assert set(m["前走着順"]) == {"2"} and set(m["間隔帯"]) == {"中2-3"} and set(m["左右遷移"]) == {"右→左"}
    assert not rm.pending_files(raw)


def test_web_accepts_patch_upload(client, tmp_path):
    _write(rows(2026), tmp_path / "csv_files" / "全場_タイムM.csv")
    _write(rows(2026), tmp_path / "csv_files" / "全場_対戦M.csv")
    up = patch_rows(rows(2026), 前走場所="東京").to_csv(index=False).encode("cp932")
    r = client.post("/ingest/raw_merge", data={"raw_files": [(io.BytesIO(up), "前走だけ.csv")]},
                    content_type="multipart/form-data")
    body = r.get_data(as_text=True)
    assert r.status_code == 200 and "前走・追加列パッチを反映しました" in body and "更新 10 行" in body
    assert set(read(tmp_path / "csv_files" / "全場_タイムM.csv")["前走場所"]) == {"東京"}
    full = rows(2026).to_csv(index=False).encode("cp932")
    r = client.post("/ingest/raw_merge", data={"raw_files": [(io.BytesIO(full), "202611.csv")]},
                    content_type="multipart/form-data")
    assert r.status_code == 400 and "列パッチなら" in r.get_data(as_text=True)
    assert not (tmp_path / "raw_csv" / "202611.csv").exists()                          # 受け付けないファイルは消す
