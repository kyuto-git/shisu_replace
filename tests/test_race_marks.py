"""外部レース印（メンバーレベル・タイムレベル・ペース判定・クッション値・含水率）→ 前走条件（shisu/race_marks.py・conditions.py）。"""
import zipfile

import pandas as pd

from shisu import conditions as C
from shisu import ext_indices
from shisu import race_marks as RM

SAMPLE = {   # 依頼にあった実データの行
    "pace_decision2026.csv": '"2026年1回中山1日目01R",RX06261101,"05HHH "\r\n"2026年1回中山1日目02R",RX06261102,"09 Ｍ "\r\n',
    "cushion2026.csv": '"2026年1回中山1日目01R","RX06261101","09----"\r\n"2026年1回中山1日目05R","RX06261105","00 9.4"\r\n',
    "member2026.csv": '"2026年1回中山1日目01R",RX06261101,"03 Ｅ "\r\n"2026年1回中山1日目03R",RX06261103,"09 － "\r\n',
    "time2026.csv": '"2026年1回中山1日目01R",RX06261101,"03 Ｅ "\r\n"2026年1回中山1日目05R",RX06261105,"05 Ａ "\r\n',
    "moistureG_2026.csv": '"2026年1回中山1日目01R","RX06261101","0012.5"\r\n"2026年1回中山1日目01R","RX06261101","0D 7.1"\r\n',
}


def test_race_id_to_rx():
    assert RM.rx_of("2026110803030212") == "RX03263212"                       # 依頼の例
    assert RM.rx_of("202611080303021207") == "RX03263212"                     # 18桁（馬番つき）でも同じ
    assert RM.rx_of("2026010506010101") == "RX06261101"
    # 日 10・11・12 は A・B・C。回は1桁
    assert [RM.rx_of(f"20261108030{k}{d:02d}12") for k, d in ((3, 9), (3, 10), (3, 11), (5, 12))] == [
        "RX03263912", "RX03263A12", "RX03263B12", "RX03265C12"]
    assert [RM.day_char(d) for d in (0, 1, 9, 10, 12, 36)] == ["", "1", "9", "A", "C", ""]
    for bad in ("", None, "20261108", "2026110803030A12", "20261108030300121", "2026110803000212", "2026110803100212"):
        assert RM.rx_of(bad) == ""
    # ext_indices の旧ID（場所2 + 年下2 + 回1 + 日1 + R2 + 馬番2）の先頭8文字と同じ作り方
    rids = [f"2025{m:02d}15{p:02d}{k:02d}{d:02d}{r:02d}07" for m, p, k, d, r in
            ((1, 5, 1, 1, 1), (6, 9, 3, 10, 11), (12, 10, 5, 12, 12), (8, 4, 2, 8, 6))]
    base = pd.DataFrame({"レースID(新)": rids})
    old10 = ext_indices._old10_series(base, pd.Series(rids))
    assert ["RX" + o[:8] for o in old10] == [RM.rx_of(r) for r in rids]


def test_values_and_file_kinds():
    assert [RM.clean_value(v) for v in ("0E Ｂ ", "05HHH ", "09 Ｍ ", "00 9.4", "0012.5", "0D 7.1", "03 Ｅ ")] == [
        "B", "HHH", "M", "9.4", "12.5", "7.1", "E"]
    assert [RM.clean_value(v) for v in ("09----", "09 － ", "00 - ", "----", "－", "", None, "0e  ")] == [""] * 8
    assert RM.clean_value(" Ｓ ") == "S" and RM.clean_value("ff10.5") == "10.5"      # カラーコードが無い値・小文字の16進
    kinds = {"member2026.csv": "member", "MEMBER.CSV": "member", "time2026.csv": "time", "Time_lv.csv": "time",
             "pace_decision2026.csv": "pace", "pace2026.csv": "pace", "cushion2026.csv": "cushion",
             "moistureG_2026.csv": "moisture", "MOISTUREg2025.CSV": "moisture", "moisture_2026.csv": "moisture",
             "moisture2026.csv": "moisture", "moistureC_2026.csv": None,          # 4コーナーの含水率は使わない
             "member2026.txt": None, "xmember.csv": None, "sp2026.csv": None}
    assert {n: RM.kind_of(n) for n in kinds} == kinds


def test_sample_lines_are_read_from_folders_zips_and_both_encodings(tmp_path):
    d = tmp_path / "race_marks"
    (d / "2026" / "deep").mkdir(parents=True)
    (d / "member2026.csv").write_bytes(SAMPLE["member2026.csv"].encode("cp932"))
    (d / "2026" / "deep" / "time2026.csv").write_bytes(SAMPLE["time2026.csv"].encode("utf-8"))
    (d / "2026" / "PACE_DECISION2026.CSV").write_bytes(b"\xef\xbb\xbf" + SAMPLE["pace_decision2026.csv"].encode("utf-8"))
    with zipfile.ZipFile(d / "marks.zip", "w") as z:
        z.writestr("x/cushion2026.csv", SAMPLE["cushion2026.csv"].encode("cp932"))
        z.writestr("x/moistureG_2026.csv", SAMPLE["moistureG_2026.csv"].encode("cp932"))
        z.writestr("__MACOSX/x/member2026.csv", b"junk")
        z.writestr("x/readme.txt", b"junk")
    (d / "moistureC_2026.csv").write_bytes('"x",RX06261101,"0099.9"\r\n'.encode("cp932"))      # 使わない
    (d / ".hidden.csv").write_bytes(b"junk")
    (d / "broken.zip").write_bytes(b"not a zip")
    logs = []
    files = RM.find_files(d, logs.append)
    assert sorted((f.kind, f.label) for f in files) == [
        ("cushion", "marks.zip:x/cushion2026.csv"), ("member", "member2026.csv"), ("moisture", "marks.zip:x/moistureG_2026.csv"),
        ("pace", "PACE_DECISION2026.CSV"), ("time", "time2026.csv")]
    assert any("[WARN]" in m and "broken.zip" in m for m in logs)
    marks = RM.read_files(files, logs.append)
    assert len(marks) == 3 and any("[INFO] レース印:" in m and "3 レース" in m for m in logs)
    # 2026年1回中山1日目 = 20260105 06 01 01（日付は RX に入らないので、同じ年・回・日ならどの日付でも同じレース）
    r1, r2, r3, r5 = (marks.get(f"202601050601010{n}") for n in (1, 2, 3, 5))
    assert r1 == {"member": "E", "time": "E", "pace": "HHH", "cushion": "", "moist_turf": "12.5", "moist_dirt": "7.1"}
    assert r2["pace"] == "M" and r2["member"] == ""
    assert r3 == {f: "" for f in RM.FIELDS}                                  # メンバー「－」だけのレースは印なし
    assert r5["cushion"] == "9.4" and r5["time"] == "A" and r5["moist_turf"] == ""
    assert marks.get("") == {f: "" for f in RM.FIELDS} and marks.get("2026010506010199")["member"] == ""
    assert RM.find_files(tmp_path / "nope") == [] and len(RM.load_dir(tmp_path / "nope")) == 0


def test_bad_rows_headers_later_files_and_moisture_pairs(tmp_path):
    d = tmp_path / "m"
    d.mkdir()
    (d / "member_a.csv").write_text('開催名,レースID,値\n"a",RX06261101,"03 Ａ "\n"b",RX0626110,"03 Ｂ "\n"c",XX06261102,"03 Ｃ "\n'
                                    '\n"d",rx06261103,"03 Ｄ "\n"e",RX06261104\n', encoding="utf-8")
    (d / "member_b.csv").write_text('"a",RX06261101,"09 － "\n"d",RX06261103,"05 Ｅ "\n', encoding="utf-8")   # 後のファイル
    # 含水率: 1行目 = 芝・2行目 = ダート。3行目からは使わない。1行だけなら芝だけ。離れた2行でも同じレースの2行目はダート
    (d / "moistureG_a.csv").write_text('"a",RX06261101,"0012.5"\n"b",RX06261102,"0010.0"\n"a",RX06261101,"0D 7.1"\n'
                                       '"a",RX06261101,"0D 9.9"\n"c",RX06261103,"00 8.8"\n', encoding="utf-8")
    (d / "moistureG_b.csv").write_text('"b",RX06261102,"0011.1"\n"b",RX06261102,"0D 3.3"\n"c",RX06261103,"09----"\n'
                                       '"c",RX06261103,"09----"\n', encoding="utf-8")
    logs = []
    marks = RM.load_dir(d, logs.append)
    t = marks.table
    assert t["RX06261101"]["member"] == "A"                      # 後のファイルの「－」（値なし）では上書きしない
    assert t["RX06261103"]["member"] == "E"                      # 小文字の rx も読む・後のファイルの値
    assert "RX06261104" not in t or t["RX06261104"]["member"] == ""
    bad = [m for m in logs if "[WARN]" in m and "member_a.csv" in m]
    assert len(bad) == 1 and "読めない行 2 件" in bad[0]         # RX0626110（7桁）と XX…。見出しの行は数えない
    assert (t["RX06261101"]["moist_turf"], t["RX06261101"]["moist_dirt"]) == ("12.5", "7.1")
    assert any("[WARN]" in m and "3行目以降が 1 行" in m for m in logs)
    assert (t["RX06261102"]["moist_turf"], t["RX06261102"]["moist_dirt"]) == ("11.1", "3.3")     # 後のファイルの組
    assert (t["RX06261103"]["moist_turf"], t["RX06261103"]["moist_dirt"]) == ("8.8", "")         # 2行とも値なしなら前の値を残す


def test_band_rules_and_boundaries():
    assert [C.member_level(v) for v in ("A", "Ｂ", "c", "E", "S", "F", "", "AB", None)] == ["A", "B", "C", "E", "", "", "", "", ""]
    assert [C.time_level(v) for v in ("S", "A", "ｅ", "F", "", "SS")] == ["S", "A", "E", "", "", ""]
    assert [C.pace_r3(v) for v in ("HHH", "HH", "H", "M", "S", "SS", "SSS", "s", "", "X", "----")] == [
        "HHH~HH", "HHH~HH", "H", "M", "S", "SS~SSS", "SS~SSS", "S", "", "", ""]
    cush = lambda v, sd="芝": C.cushion_band(v, sd)
    assert [cush(v) for v in ("8.9", "8.99", "9.0", "9", "9.9", "9.99", "10.0", "10", "12.3", "７．５")] == [
        "軟(8.9以下)", "軟(8.9以下)", "標準(9.0~9.9)", "標準(9.0~9.9)", "標準(9.0~9.9)", "標準(9.0~9.9)", "硬(10.0以上)",
        "硬(10.0以上)", "硬(10.0以上)", "軟(8.9以下)"]
    assert [cush(v) for v in ("", "----", "abc", "0", "-1", "nan", "inf", None)] == [""] * 8
    assert cush("9.4", "ダ") == "" and cush("9.4", "") == "" and cush("9.4", "障") == ""      # 前走が芝のときだけ
    mo = C.moisture_band
    assert [mo(v, "1", "芝") for v in ("9.9", "10.0", "14.9", "15.0", "15", "3")] == [
        "芝乾(9.9以下)", "芝標準(10.0~14.9)", "芝標準(10.0~14.9)", "芝湿(15.0以上)", "芝湿(15.0以上)", "芝乾(9.9以下)"]
    assert [mo("99", v, "ダ") for v in ("4.9", "5.0", "9.9", "10.0", "22")] == [
        "ダ乾(4.9以下)", "ダ標準(5.0~9.9)", "ダ標準(5.0~9.9)", "ダ湿(10.0以上)", "ダ湿(10.0以上)"]
    assert mo("12.5", "7.1", "芝") == "芝標準(10.0~14.9)" and mo("12.5", "7.1", "ダ") == "ダ標準(5.0~9.9)"   # 1行目 = 芝・2行目 = ダ
    assert mo("12.5", "", "ダ") == "" and mo("", "7.1", "芝") == "" and mo("12.5", "7.1", "") == "" and mo("x", "y", "芝") == ""
    assert set(C.MARK_CONDITIONS) <= set(C.CONDITION_COLUMNS) and len(C.CONDITION_COLUMNS) == 30


def _std(rows):
    df = pd.DataFrame(rows)
    for c in C.STD_COLUMNS:
        if c not in df.columns:
            df[c] = ""
    return df[list(C.STD_COLUMNS)]


def test_derive_looks_marks_up_by_the_previous_race_and_fills_the_pace():
    marks = RM.RaceMarks({
        "RX06261101": {"member": "E", "time": "A", "pace": "HHH", "cushion": "9.4", "moist_turf": "12.5", "moist_dirt": "7.1"},
        "RX06261102": {"member": "", "time": "X", "pace": "SS", "cushion": "----", "moist_turf": "16", "moist_dirt": "4"}})
    base = {"日付": "20260201", "場所": "東京", "芝・ダ": "芝", "距離_raw": "1600"}
    std = _std([
        {**base, "前走RID16": "2026010506010101", "前走芝ダ": "芝", "前走R印3": ""},          # 0: 印で全部埋まる
        {**base, "前走RID16": "2026010506010101", "前走芝ダ": "ダ", "前走R印3": "M"},         # 1: ダ → クッションなし・R印3 は列の値
        {**base, "前走RID16": "2026010506010102", "前走芝ダ": "ダ", "前走R印3": "----"},      # 2: ---- は空と同じ → 印で補う
        {**base, "前走RID16": "2026010506010199", "前走芝ダ": "芝", "前走R印3": "S"},         # 3: 印の無いレース
        {**base, "前走RID16": "", "前走芝ダ": "", "前走R印3": ""},                            # 4: 前走なし
    ])
    out = C.derive(std, None, marks)
    assert out["前走メンバーLv"].tolist() == ["E", "E", "", "", ""]
    assert out["前走タイムLv"].tolist() == ["A", "A", "", "", ""]
    assert out["前走クッション帯"].tolist() == ["標準(9.0~9.9)", "", "", "", ""]
    assert out["前走含水率帯"].tolist() == ["芝標準(10.0~14.9)", "ダ標準(5.0~9.9)", "ダ乾(4.9以下)", "", ""]
    assert out["前走R印3"].tolist() == ["HHH~HH", "M", "SS~SSS", "S", ""]
    # 印を渡さない・空の印なら、前走R印3 は今までどおり・新しい列は空
    for none in (None, RM.RaceMarks()):
        plain = C.derive(std, None, none)
        assert plain["前走R印3"].tolist() == ["", "M", "", "S", ""]
        assert all((plain[c] == "").all() for c in C.MARK_CONDITIONS)
    assert all((out[c] == "").all() for c in C.RECENT_CONDITIONS)             # 近5走は recent を渡したときだけ
    recent = pd.DataFrame({"近5走同条件": ["同条件連対あり", "", "", "", ""], "近5走同場": ["", "同場4着以下", "", "", ""]})
    got = C.derive(std, None, marks, recent)
    assert got["近5走同条件"].tolist() == recent["近5走同条件"].tolist() and got["近5走同場"].tolist() == recent["近5走同場"].tolist()


def test_cache_file_round_trip_and_entries_side_source(tmp_path, monkeypatch):
    d = tmp_path / "race_marks"
    d.mkdir()
    for name, text in SAMPLE.items():
        (d / name).write_bytes(text.encode("cp932"))
    marks = RM.load_dir(d)
    master = tmp_path / "master_csv"
    path = RM.save(marks, master)
    assert path == master / RM.FILE and not list(master.glob("*.part"))
    again = RM.load_cache(master)
    assert again.table == marks.table and path.read_bytes() == RM.save(again, master).read_bytes()     # 同じ中身なら同じバイト列
    frame = pd.read_csv(path, encoding="utf-8", dtype=str, keep_default_na=False)
    assert list(frame.columns) == RM.COLUMNS and frame[RM.KEY].tolist() == sorted(marks.table)
    # 出馬表側: race_marks にファイルがあればそれ（新しいほう）、無ければ master_csv の表、どちらも無ければ空
    (d / "member2027.csv").write_text('"x",RX06261109,"03 Ｃ "\n', encoding="utf-8")
    logs = []
    assert RM.load(d, master, logs.append).get("2026010506010109")["member"] == "C"
    assert RM.load(tmp_path / "empty", master, logs.append).table == marks.table
    assert any("race_marks のファイルが無いので" in m for m in logs)
    assert len(RM.load(tmp_path / "empty", tmp_path / "nomaster")) == 0 and RM.load_cache(tmp_path / "nomaster") is None
    path.write_bytes(b"broken")
    assert RM.load_cache(master) is None
    monkeypatch.setenv("RACE_MARKS_DIR", str(d))
    from shisu import config
    assert config.race_marks_dir() == d and len(RM.find_files()) == 6
    monkeypatch.delenv("RACE_MARKS_DIR")
    monkeypatch.setenv("SHISU_BASE_DIR", str(tmp_path))
    assert config.race_marks_dir() == tmp_path / "race_marks"


def test_two_hex_characters_at_the_head_are_always_taken_as_the_colour_code():
    """先頭2文字が16進なら、カラーコードとして除く（依頼どおり。カラーコードの無い2文字だけの値は空になる）。"""
    assert [RM.clean_value(v) for v in ("12", "ab", "0f")] == ["", "", ""]
    assert [RM.clean_value(v) for v in ("0012", "00ab", "0E 0f")] == ["12", "ab", "0f"]
