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
                        "レースID(新)": f"{year}{day}05040{r % 10}{r:02d}{r:02d}{u:02d}"})
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
