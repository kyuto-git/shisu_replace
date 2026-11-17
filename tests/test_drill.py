"""逆引き（shisu/drill.py）と危険条件メモの保存（shisu/notes.py）。DB を使わない関数単位。
ページ（/entries/drill・/entries/note）と、成績表の件数との一致は tests/test_pipeline.py。"""
import gzip
import json
import sys
import threading
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from shisu import drill, notes  # noqa: E402
from shisu import families as F  # noqa: E402


# ---------------------------------------------------------------- 危険条件メモ
COND = ("SAV", "normal", "", "odds10u", "全場", "東京×ダ×1600×定量×1・2_勝×1")


def test_notes_save_lookup_and_delete(tmp_path):
    p = tmp_path / "sub" / "condition_notes.json"
    assert notes.load(p) == {} and notes.lookup(notes.load(p), *COND) is None            # ファイルが無くても空
    assert notes.save(*COND, "  ⚠️馬体重478kg以下は危険\r\n前走4角10番手以下は消し  ", path=p, now="2026-11-16T10:00:00") == "saved"
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["notes"] == [{
        "indicator": "SAV", "family": "normal", "rank_scheme": "", "odds_mode": "odds10u", "region": "全場",
        "key": COND[5], "note": "⚠️馬体重478kg以下は危険\n前走4角10番手以下は消し", "updated": "2026-11-16T10:00:00"}]
    assert "⚠️馬体重" in p.read_text(encoding="utf-8")                                    # 日本語のまま読める形
    assert notes.lookup(notes.load(p), *COND) == ("⚠️馬体重478kg以下は危険\n前走4角10番手以下は消し", "region")
    # 条件のどれか1つでも違えば出ない
    for i, other in enumerate(("ZI", "rankB", "s2", "normal", "主5場", COND[5] + "x")):
        cond = list(COND)
        cond[i] = other
        assert notes.lookup(notes.load(p), *cond) is None, i
    # 上書き・削除（本文を空にして保存）
    assert notes.save(*COND, "直した", path=p) == "saved" and notes.lookup(notes.load(p), *COND)[0] == "直した"
    assert notes.save(*COND, "   ", path=p) == "deleted" and notes.load(p) == {}
    assert notes.save(*COND, "", path=p) == "none" and notes.delete(*COND, path=p) == "none"
    assert json.loads(p.read_text(encoding="utf-8"))["notes"] == []


def test_notes_common_note_applies_to_every_region_and_region_note_wins(tmp_path):
    p = tmp_path / "n.json"
    common = COND[:4] + ("",) + COND[5:]
    notes.save(*common, "全地域のメモ", path=p)
    assert notes.lookup(notes.load(p), *COND) == ("全地域のメモ", "common")
    assert notes.lookup(notes.load(p), *(COND[:4] + ("主5場",) + COND[5:])) == ("全地域のメモ", "common")
    assert notes.lookup(notes.load(p), *common) == ("全地域のメモ", "common")
    notes.save(*COND, "全場だけのメモ", path=p)
    assert notes.lookup(notes.load(p), *COND) == ("全場だけのメモ", "region")               # その地域のメモを優先
    assert notes.lookup(notes.load(p), *(COND[:4] + ("主5場",) + COND[5:])) == ("全地域のメモ", "common")
    # オッズ帯の空は normal と同じ・前後の空白は無視
    notes.save("SAV", "normal", "", "", "", " k ", "x", path=p)
    assert notes.lookup(notes.load(p), "SAV", "normal", None, "normal", "どこでも", "k") == ("x", "common")
    assert notes.ident(" SAV ", "normal", None, None, None, " k ") == ("SAV", "normal", "", "normal", "", "k")


def test_notes_limits_bad_input_and_broken_file_is_not_overwritten(tmp_path):
    p = tmp_path / "n.json"
    notes.save(*COND, "あ" * 5000, path=p)
    assert len(notes.lookup(notes.load(p), *COND)[0]) == notes.MAX_NOTE == 2000
    for bad in (("", "normal", "", "normal", "", "k"), ("SAV", "", "", "normal", "", "k"), ("SAV", "normal", "", "normal", "", " ")):
        with pytest.raises(notes.NotesError):
            notes.save(*bad, "x", path=p)
    # 壊れたファイル（手で直しかけた JSON など）は読めないと伝え、保存で上書きしない
    broken = tmp_path / "broken.json"
    broken.write_text('{"version": 1, "notes": [{"indicator": "SAV", ', encoding="utf-8")
    with pytest.raises(notes.NotesError):
        notes.load(broken)
    assert notes.load_safe(broken)[0] == {} and "broken.json" in notes.load_safe(broken)[1]
    with pytest.raises(notes.NotesError):
        notes.save(*COND, "x", path=broken)
    assert broken.read_text(encoding="utf-8").startswith('{"version": 1, "notes": [{"indicator": "SAV", ')
    for text in ('{"notes": 3}', '[1, 2]', '{"x": 1}'):
        broken.write_text(text, encoding="utf-8")
        with pytest.raises(notes.NotesError):
            notes.load(broken)
    # 空のファイル・メモの無い形は空として読める。配列だけの形（手書き）も読める
    broken.write_text("", encoding="utf-8")
    assert notes.load(broken) == {}
    broken.write_text(json.dumps([{"indicator": "SAV", "family": "normal", "key": "k", "note": "手書き"}]), encoding="utf-8")
    assert notes.lookup(notes.load(broken), "SAV", "normal", "", "normal", "全場", "k") == ("手書き", "common")
    # 書けない場所は NotesError（例外の型をそろえる）
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(notes.NotesError):
        notes.save(*COND, "x", path=blocker / "n.json")
    assert not list(tmp_path.glob("*.tmp"))


def test_notes_write_is_atomic_keeps_the_mode_and_rejects_lone_surrogates(tmp_path, monkeypatch):
    """書き込みは一時ファイルからの置き換え（途中で失敗しても前のファイルが残る）。権限は umask・前のファイルのまま
    （Docker の root で書いても、ホスト側で読める・コピーできる）。片方だけのサロゲートは読めない形として断る。"""
    import os
    import stat
    p = tmp_path / "n.json"
    old = os.umask(0o022)
    try:
        notes.save(*COND, "最初", path=p)
        assert stat.S_IMODE(p.stat().st_mode) == 0o644
        os.chmod(p, 0o664)
        notes.save(*COND, "2回目", path=p)
        assert stat.S_IMODE(p.stat().st_mode) == 0o664
    finally:
        os.umask(old)
    before = p.read_bytes()

    def boom(*a, **k):
        raise OSError("disk full")
    for name in ("fsync", "replace"):
        with monkeypatch.context() as m:
            m.setattr(notes.os, name, boom)
            with pytest.raises(notes.NotesError):
                notes.save(*COND, "3回目", path=p)
        assert p.read_bytes() == before and not list(tmp_path.glob("*.tmp")), name
    assert notes.lookup(notes.load(p), *COND)[0] == "2回目"
    # 片方だけのサロゲート（絵文字を途中で切った JSON など）: 読めない形として扱い、画面を 500 にしない・上書きしない
    s = tmp_path / "s.json"
    raw = '{"notes": [{"indicator": "SAV", "family": "normal", "key": "k", "note": "half \\ud83d"}]}'
    s.write_text(raw, encoding="utf-8")
    with pytest.raises(notes.NotesError):
        notes.load(s)
    assert notes.load_safe(s)[0] == {} and "s.json" in notes.load_safe(s)[1]
    with pytest.raises(notes.NotesError):
        notes.save(*COND, "x", path=s)
    assert s.read_text(encoding="utf-8") == raw
    s.write_text(raw.replace("\\ud83d", "\\ud83d\\ude00"), encoding="utf-8")          # 対になったサロゲート（ふつうの絵文字）は読める
    assert notes.lookup(notes.load(s), "SAV", "normal", "", "normal", "", "k")[0] == "half \U0001F600"
    # 保存する本文に来た片方だけのサロゲート、長すぎる条件も NotesError（ファイルはそのまま）
    with pytest.raises(notes.NotesError):
        notes.save(*COND, "half \ud83d", path=p)
    with pytest.raises(notes.NotesError):
        notes.save("SAV", "normal", "", "normal", "", "k" * (notes.MAX_FIELD + 1), "x", path=p)
    assert notes.save("SAV", "normal", "", "normal", "", "k" * notes.MAX_FIELD, "x", path=tmp_path / "long.json") == "saved"
    assert p.read_bytes() == before and not list(tmp_path.glob("*.tmp"))


def test_notes_reload_after_external_edit_and_concurrent_saves(tmp_path):
    p = tmp_path / "n.json"
    notes.save(*COND, "1", path=p)
    assert notes.lookup(notes.load(p), *COND)[0] == "1"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["notes"][0]["note"] = "外から直した"
    p.write_text(json.dumps(data, ensure_ascii=False) + " ", encoding="utf-8")            # 大きさが変わる → 読み直す
    assert notes.lookup(notes.load(p), *COND)[0] == "外から直した"
    errors = []

    def work(i):
        try:
            notes.save("SAV", "normal", "", "normal", "全場", f"k{i}", f"note{i}", path=p)
        except Exception as e:                                                            # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=work, args=(i,)) for i in range(24)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert not errors and len(notes.load(p)) == 25                                        # どの保存も失われない
    assert notes.config.notes_path().name == "condition_notes.json"


def test_drill_survives_an_empty_master_and_infinite_odds(tmp_path):
    """空の master（書きかけで止まったファイル）は DrillError（画面は理由を出す）。単勝オッズ inf の行があってもまとめは落ちない。"""
    fam = F.get("normal")
    for name, body in (("master_全場_A.csv.gz", b""), ("master_全場_B.csv", b""), ("master_全場_C.csv.gz", gzip.compress(b""))):
        p = tmp_path / name
        p.write_bytes(body)
        with pytest.raises(drill.DrillError):
            drill.extract(p, fam, "", "normal", KEY1)
    assert [drill.odds_bin(v) for v in ("inf", "1e999", "Infinity", "50", "49.9", "-inf", "nan", "")] == [
        "50倍以上", "50倍以上", "50倍以上", "50倍以上", "20〜49.9倍", "", "", ""]
    p = tmp_path / "master_全場_SAV.csv.gz"
    _master([(i, 1, 1, "inf" if i == 0 else "3.5", 1 + i % 5, {}) for i in range(8)]).to_csv(p, index=False, encoding="cp932", compression="gzip")
    df = drill.extract(p, fam, "", "normal", KEY1)
    odds = next(it for it in drill.summarize(df) if it["name"] == "単勝オッズ")
    assert len(df) == 8 and {it["value"]: it["n"] for it in odds["items"]} == {"3.0〜4.9倍": 7, "50倍以上": 1}


def test_notes_path_follows_env(tmp_path, monkeypatch):
    monkeypatch.setenv("NOTES_PATH", str(tmp_path / "my_notes.json"))
    assert notes.config.notes_path() == tmp_path / "my_notes.json"
    notes.save(*COND, "env", now="t")
    assert (tmp_path / "my_notes.json").exists() and notes.lookup(notes.load(), *COND) == ("env", "region")
    monkeypatch.delenv("NOTES_PATH")
    monkeypatch.setenv("SHISU_BASE_DIR", str(tmp_path / "base"))
    assert notes.config.notes_path() == tmp_path / "base" / "condition_notes.json"


# ---------------------------------------------------------------- 逆引き: master からの取り出し
def _master(rows) -> pd.DataFrame:
    """normal（COURSE×重量種別×クラス名×指数順位）で キー 東京×ダ×1600×定量×1・2_勝×{順位} になる master の行。
    rows = (レースの番号, 馬番, 順位, 単勝オッズ, 着順, 追加の列)。"""
    out = []
    for race, uma, rank, odds, finish, more in rows:
        rid16 = f"202{race:05d}05010101"[:16]
        out.append({"レースID(新)": f"{rid16}{uma:02d}", "レースID(新/馬番無)": rid16, "日付": f"202{race:05d}"[:8], "year": "2025",
                    "場所": "東京", "芝・ダ": "ダ", "距離_raw": "1600", "距離": "1600", "芝(内・外)": "", "重量種別": "定量",
                    "クラス名": "1・2_勝", "頭数": "16", "枠番": str((uma + 1) // 2), "馬番": str(uma), "馬名": f"馬{race}_{uma}",
                    "騎手": "騎手A", "指数": "50", "順位": str(rank), "偏差値": "60", "偏差値帯": "51~60", "指数有効": "1",
                    "着順": str(finish), "単勝オッズ": str(odds), "間隔帯": "中4-6", "前走脚質": "先行", **more})
    return pd.DataFrame(out).fillna("")


KEY1 = "東京×ダ×1600×定量×1・2_勝×1"


@pytest.fixture()
def master_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SHISU_BASE_DIR", str(tmp_path))
    F.reset()
    d = tmp_path / "master_csv"
    d.mkdir()
    rows = [(10101, 1, 1, "3.5", "1", {}), (10102, 2, 1, "1.5", "2", {}), (10103, 3, 1, "9.9", "3", {"間隔帯": "連闘"}),
            (10104, 4, 1, "10.0", "4", {}), (10105, 5, 1, "55.0", "中止", {"前走脚質": "逃げ"}),
            (10106, 6, 1, "4.0", "取消", {}), (10107, 7, 1, "", "8", {}), (10108, 8, 1, "2.0", "12", {"指数有効": "0"}),
            (10109, 9, 2, "3.0", "1", {}), (10110, 10, 1, "6.0", "5", {"場所": "中山"}),
            (10101, 12, 1, "7.0", "6", {})]                                                 # 同じレースにもう1頭
    df = _master(rows)
    df = pd.concat([df, df.iloc[[0]]], ignore_index=True)                                     # 同じ馬の行が2回（重複）
    (d / "master_全場_SAV.csv.gz").write_bytes(gzip.compress(df.to_csv(index=False).encode("cp932")))
    (d / "master_主５場（中山、東京、中京、京都、阪神）_吉馬_SP能力値.csv.gz").write_bytes(
        gzip.compress(df.head(3).to_csv(index=False).encode("cp932")))
    (d / "__inner_outer__.csv.gz").write_bytes(gzip.compress(b"x\n"))
    (d / "master_全場_タイムM.csv").write_bytes(df.head(2).to_csv(index=False).encode("cp932"))
    yield d
    F.reset()


def test_find_master_matches_names_like_the_aggregate_does(master_dir):
    assert drill.find_master("全場", "SAV").name == "master_全場_SAV.csv.gz"
    assert drill.find_master("主5場(中山、東京、中京、京都、阪神)", "吉馬_SP能力値").name.startswith("master_主５場（")   # NFKC の地域名で引ける
    assert drill.find_master("全場", "タイムM").name == "master_全場_タイムM.csv"
    for region, ind in (("全場", "ZI"), ("", "SAV"), ("全場", ""), ("../x", "SAV"), ("全場", "../../etc/passwd"), ("", "")):
        assert drill.find_master(region, ind) is None
    assert drill.find_master("全場", "SAV", master_dir / "nowhere") is None and drill.master_files(master_dir / "nowhere") == []


def test_extract_uses_the_same_keys_odds_band_dedupe_and_denominator_as_the_aggregate(master_dir):
    path = drill.find_master("全場", "SAV")
    df = drill.extract(path, "normal", "", "normal", KEY1)
    # 指数有効=0・順位2・中山 は入らない。重複した行は1回。取消の馬は取り出すが分母の外
    assert sorted(df["馬番"]) == sorted(["1", "2", "3", "4", "5", "6", "7", "12"])
    assert drill.overview(df) == {"rows": 8, "n": 7, "good": 3, "bad": 4, "excluded": 1, "rate": 3 / 7}
    by = df.set_index("馬番")[drill.RESULT].to_dict()
    assert by == {"1": "好走", "2": "好走", "3": "好走", "4": "凡走", "5": "凡走", "7": "凡走", "12": "凡走", "6": "集計外"}
    assert df.set_index("馬番")[drill.FINISH]["5"] == 16                                     # 中止は着順=頭数（集計と同じ）
    assert list(df["日付"]) == sorted(df["日付"], reverse=True)                              # 新しい日付が上
    # オッズ帯（2〜10倍未満 = 2.0 以上 10.0 未満。オッズの無い馬は通常だけ）
    assert sorted(drill.extract(path, "normal", "", "odds10u", KEY1)["馬番"]) == ["1", "12", "3", "6"]
    assert sorted(drill.extract(path, "normal", "", "odds10_50", KEY1)["馬番"]) == ["4"]
    assert sorted(drill.extract(path, "normal", "", "odds50p", KEY1)["馬番"]) == ["5"]
    # 別のキー・当たらないキー
    assert list(drill.extract(path, "normal", "", "normal", KEY1[:-1] + "2")["馬番"]) == ["9"]
    empty = drill.extract(path, "normal", "", "normal", "東京×ダ×1600×定量×1・2_勝×18")
    assert len(empty) == 0 and drill.overview(empty) == {"rows": 0, "n": 0, "good": 0, "bad": 0, "excluded": 0, "rate": None}
    assert drill.id_lists(empty) == {k: {"horse": [], "race": []} for k in ("all", "good", "bad")} and drill.summarize(empty) == []
    # 順位帯の family（s2 は帯が重なる: 1位の馬は 1~3位 と 1~5位 の両方のキーに入る）
    for band in ("1~3位", "1~5位"):
        got = drill.extract(path, "normal_band", "s2", "normal", f"東京×ダ×1600×定量×1・2_勝×{band}")
        assert sorted(got["馬番"]) == sorted(["1", "2", "3", "4", "5", "6", "7", "9", "12"])
    assert sorted(drill.extract(path, "normal_band", "s1", "normal", "東京×ダ×1600×定量×1・2_勝×2~3位")["馬番"]) == ["9"]
    # 小さく分けて読んでも同じ
    whole = drill.extract(path, "normal", "", "normal", KEY1)
    for size in (1, 3, 5):
        old, drill.CHUNK_ROWS = drill.CHUNK_ROWS, size
        try:
            pd.testing.assert_frame_equal(drill.extract(path, "normal", "", "normal", KEY1), whole)
        finally:
            drill.CHUNK_ROWS = old
    # gz でない master・UTF-8 の master も読める
    assert len(drill.extract(drill.find_master("全場", "タイムM"), "normal", "", "normal", KEY1)) == 2
    utf = master_dir / "master_全場_ZI.csv"
    utf.write_bytes(_master([(10101, 1, 1, "3.5", "1", {"騎手": "髙杉吏麒♥"})]).to_csv(index=False).encode("utf-8"))
    assert list(drill.extract(utf, "normal", "", "normal", KEY1)["騎手"]) == ["髙杉吏麒♥"]


def test_extract_rejects_bad_arguments_and_unreadable_masters(master_dir):
    path = drill.find_master("全場", "SAV")
    for fam, scheme, mode in (("nope", "", "normal"), ("normal", "", "odds5p"), ("normal", "s1", "normal"),
                              ("normal_band", "", "normal"), ("normal_band", "s3", "normal")):
        with pytest.raises(drill.DrillError):
            drill.extract(path, fam, scheme, mode, KEY1)
    bad = master_dir / "master_全場_BC.csv.gz"
    bad.write_bytes(gzip.compress(b"a,b\n1,2\n"))                                            # master ではない
    with pytest.raises(drill.DrillError):
        drill.extract(bad, "normal", "", "normal", KEY1)
    bad.write_bytes(gzip.compress(_master([(10101, 1, 1, "3.5", "1", {})]).to_csv(index=False).encode("cp932"))[:40])
    with pytest.raises(drill.DrillError):                                                    # 途中で切れた gzip
        drill.extract(bad, "normal", "", "normal", KEY1)
    bad.write_bytes(b"not a gzip file")
    with pytest.raises(drill.DrillError):
        drill.extract(bad, "normal", "", "normal", KEY1)


def test_id_lists_for_target_are_horse_and_race_ids_split_by_result(master_dir):
    df = drill.extract(drill.find_master("全場", "SAV"), "normal", "", "normal", KEY1)
    ids = drill.id_lists(df)
    assert all(len(v) == 18 and v.isdigit() for v in ids["all"]["horse"]) and all(len(v) == 16 for v in ids["all"]["race"])
    assert len(ids["all"]["horse"]) == 7 and len(ids["all"]["race"]) == 6                    # 同じレースの2頭は、レース単位では1つ
    assert len(ids["good"]["horse"]) == 3 and len(ids["bad"]["horse"]) == 4
    assert set(ids["good"]["horse"]) | set(ids["bad"]["horse"]) == set(ids["all"]["horse"])
    assert not set(ids["good"]["horse"]) & set(ids["bad"]["horse"])
    cancelled = df[df["馬番"] == "6"]["レースID(新)"].iloc[0]
    assert cancelled not in ids["all"]["horse"]                                              # 取消の馬は貼らない
    assert ids["all"]["horse"] == [v for v, d in zip(df["レースID(新)"], df[drill.DENOM]) if d]   # 明細と同じ順（新しい日付が上）
    assert len(set(ids["all"]["race"])) == len(ids["all"]["race"])


# ---------------------------------------------------------------- 逆引き: 分布のまとめ
def test_odds_bins():
    assert [drill.odds_bin(v) for v in ("1.0", "1.9", "2.0", "2.9", "3", "4.9", "5.0", "9.9", "10", "19.9", "20", "49.9", "50", "300")] == [
        "2.0倍未満", "2.0倍未満", "2.0〜2.9倍", "2.0〜2.9倍", "3.0〜4.9倍", "3.0〜4.9倍", "5.0〜9.9倍", "5.0〜9.9倍",
        "10〜19.9倍", "10〜19.9倍", "20〜49.9倍", "20〜49.9倍", "50倍以上", "50倍以上"]
    assert [drill.odds_bin(v) for v in ("", None, "abc", "0", "-3", "nan")] == [""] * 6


def test_summary_core_is_the_narrowest_run_for_ordered_items():
    """数の項目: つながった範囲のうち 80% 以上を覆ういちばん狭いもの。外の単独1件は飛び地。"""
    values = ["1"] + ["4"] * 6 + ["5"] * 8 + ["6"] * 5 + ["9"] + ["16"]
    good = [False] + [True] * 19 + [False, False]
    it = drill.summarize_item("枠番", values, good)
    assert it["ordered"] and [v["value"] for v in it["items"]] == ["1", "4", "5", "6", "9", "16"]              # 数の小さい順
    assert [v["value"] for v in it["core"]] == ["4", "5", "6"] and it["core_text"] == "4 〜 6"
    assert it["core_cover"] == pytest.approx(19 / 22) and it["n"] == 22 and it["kinds"] == 6
    assert [(d["value"], d["flag"], d["good"]) for d in it["dangers"]] == [("1", "single", 0), ("9", "single", 0), ("16", "single", 0)]
    # コアの中の単独1件は飛び地にしない（範囲の中にある）
    it = drill.summarize_item("頭数", ["10"] * 5 + ["11"] + ["12"] * 6, [True] * 12)
    assert [v["value"] for v in it["core"]] == ["10", "11", "12"] and it["dangers"] == []
    # 同じ長さなら、多く覆うほう。1つの値で 80% なら範囲は1つ
    it = drill.summarize_item("x", ["1"] * 4 + ["2"] * 5 + ["3"] * 1, [True] * 10)
    assert it["core_text"] == "1 〜 2" and it["core_cover"] == 0.9
    assert drill.summarize_item("x", ["16"] * 9 + ["18"], [True] * 10)["core_text"] == "16"
    # オッズの帯は帯の順（文字の順ではない）
    vals = [drill.odds_bin(v) for v in ["3.0"] * 5 + ["12.0"] * 4 + ["60"] + ["1.5"]]
    it = drill.summarize_item("単勝オッズ", vals, [True] * 11, [label for _, label in drill.ODDS_BINS])
    assert [v["value"] for v in it["items"]] == ["2.0倍未満", "3.0〜4.9倍", "10〜19.9倍", "50倍以上"]
    assert it["core_text"] == "3.0〜4.9倍 〜 10〜19.9倍"


def test_summary_core_for_names_skips_singletons_and_flags_bad_clusters():
    """名前の項目: 単独1件を除いて件数の多い値から 80% まで。凡走の集まり = 2件以上・好走率が全体の半分以下・期待の好走が1以上。"""
    values = ["先行"] * 10 + ["差し"] * 6 + ["逃げ"] * 3 + ["追込"] * 4 + ["マクリ"]
    good = [True] * 10 + [True] * 5 + [False] + [True] * 3 + [False] * 4 + [True]
    it = drill.summarize_item("前走脚質", values, good)
    assert not it["ordered"] and [v["value"] for v in it["items"]] == ["先行", "差し", "追込", "逃げ", "マクリ"]  # 件数の多い順
    assert [v["value"] for v in it["core"]] == ["先行", "差し", "追込"] and it["core_cover"] == pytest.approx(20 / 24)
    assert it["rate"] == pytest.approx(19 / 24)
    assert [(d["value"], d["flag"]) for d in it["dangers"]] == [("追込", "bad"), ("マクリ", "single")]            # コアの中でも凡走の集まりは赤
    # 全体の好走率が低い条件（消しの条件など）では、少ない件数の「好走0」を危険にしない（期待の好走が1に届かない）
    it = drill.summarize_item("騎手", ["A"] * 20 + ["B"] * 3 + ["C"] * 2, [True] + [False] * 19 + [False] * 5)
    assert it["rate"] == 0.04 and it["dangers"] == []
    it = drill.summarize_item("騎手", ["A"] * 20 + ["B"] * 30, [True] * 4 + [False] * 16 + [False] * 30)          # 30件で0 → 凡走の集まり
    assert [(d["value"], d["flag"]) for d in it["dangers"]] == [("B", "bad")]
    # 好走が全体と同じくらいなら赤にしない。全部が単独1件なら、どれもコア（外れ値と言えない）
    it = drill.summarize_item("x", ["A"] * 6 + ["B"] * 4, [True, True, True, False, False, False, True, True, False, False])
    assert it["dangers"] == []
    it = drill.summarize_item("x", list("ABCDEF"), [True] * 6)
    assert [v["value"] for v in it["core"]] == list("ABCDEF") and it["dangers"] == [] and it["core_cover"] == 1.0
    # 少ない件数（5件未満）では単独1件に印を付けない。空の値は数えるだけ。全部が空なら項目を出さない
    it = drill.summarize_item("x", ["A", "A", "B", "", ""], [True] * 5)
    assert it["n"] == 3 and it["n_empty"] == 2 and it["dangers"] == []
    assert drill.summarize_item("x", ["", " ", None], [True] * 3) is None
    # 種類の多い項目は件数の多いものから MAX_CHIPS 個まで出す（残りは数だけ）
    many = [f"騎手{i:02d}" for i in range(40) for _ in range(40 - i)]
    it = drill.summarize_item("騎手", many, [True] * len(many))
    assert len(it["items"]) == drill.MAX_CHIPS and it["hidden"] == 40 - drill.MAX_CHIPS and it["kinds"] == 40


def test_summarize_counts_only_the_rows_in_the_denominator(master_dir):
    df = drill.extract(drill.find_master("全場", "SAV"), "normal", "", "normal", KEY1)
    s = {it["name"]: it for it in drill.summarize(df)}
    assert s["場所"]["n"] == 7 and s["場所"]["core_text"] == "東京" and s["場所"]["core_cover"] == 1.0   # 取消の1頭は数えない
    assert s["単勝オッズ"]["n"] == 6 and s["単勝オッズ"]["n_empty"] == 1                                 # オッズの無い馬は「値なし」
    assert {v["value"]: v["n"] for v in s["間隔帯"]["items"]} == {"中4-6": 6, "連闘": 1}
    assert [d["value"] for d in s["間隔帯"]["dangers"]] == ["連闘"] and s["間隔帯"]["dangers"][0]["good"] == 1
    assert {v["value"]: (v["n"], v["good"]) for v in s["前走脚質"]["items"]} == {"先行": (6, 3), "逃げ": (1, 0)}
    assert "距離" in s and s["距離"]["core_text"] == "1600" and "年" in s
    assert "輸送距離" not in s                                                                          # 値が1つも無い項目は出さない
    names = [n for n, _ in drill.SUMMARY_ITEMS]
    assert names[:4] == ["単勝オッズ", "枠番", "馬番", "頭数"] and len(names) == 13 + 30      # 条件は 24 + レース印4 + 近5走2
