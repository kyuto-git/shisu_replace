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
    # 後のファイルに芝の1行しか無いレースは、組ごと入れ替える（前のファイルのダートの値を残さない）
    (d / "moistureG_c.csv").write_text('"a",RX06261101,"0013.3"\n', encoding="utf-8")
    t = RM.load_dir(d).table
    assert (t["RX06261101"]["moist_turf"], t["RX06261101"]["moist_dirt"]) == ("13.3", "")
    # 値が1つも無いレース（「－」だけ）は表に入れない
    (d / "member_c.csv").write_text('"z",RX06261112,"09 － "\n', encoding="utf-8")
    (d / "moistureG_d.csv").write_text('"y",RX06261113,"09----"\n"y",RX06261113,"09----"\n', encoding="utf-8")
    marks = RM.load_dir(d)
    assert not {"RX06261112", "RX06261113"} & set(marks.table) and len(marks) == 3 and len(marks.to_frame()) == 3


def _encrypt_flag(path):
    """ZIP の中の全部のファイルに「暗号化」の印を付ける（読むと RuntimeError: password required）。"""
    data = bytearray(path.read_bytes())
    for sig, off in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        i = data.find(sig)
        while i >= 0:
            data[i + off] |= 1
            i = data.find(sig, i + 4)
    path.write_bytes(bytes(data))


def test_search_order_hidden_folders_and_unreadable_files(tmp_path, monkeypatch):
    d = tmp_path / "m"
    for sub in ("a", ".old", "__MACOSX", "__pycache__"):
        (d / sub).mkdir(parents=True)
    line = lambda rx, v: f'"x",{rx},"03 {v} "\n'
    # 優先は相対パスの名前順で後のファイル: a/member.csv < member.csv（サブフォルダのほうが前）
    (d / "member.csv").write_text(line("RX06261101", "Ａ"), encoding="utf-8")
    (d / "a" / "member.csv").write_text(line("RX06261101", "Ｂ") + line("RX06261102", "Ｃ"), encoding="utf-8")
    for sub in (".old", "__MACOSX", "__pycache__"):                    # 隠しフォルダなどの中は読まない
        (d / sub / "member_zz.csv").write_text(line("RX06261101", "Ｅ"), encoding="utf-8")
    files = RM.find_files(d)
    assert [f.path.relative_to(d).as_posix() for f in files] == ["a/member.csv", "member.csv"]
    t = RM.read_files(files).table
    assert t["RX06261101"]["member"] == "A" and t["RX06261102"]["member"] == "C"
    # ZIP の中: 「.」で始まるフォルダは読まない。名前の順に読み、後の名前の値が残る
    with zipfile.ZipFile(d / "z.zip", "w") as z:
        z.writestr("time_b.csv", line("RX06261101", "Ｂ"))
        z.writestr("time_a.csv", line("RX06261101", "Ａ"))
        z.writestr(".x/time_c.csv", line("RX06261101", "Ｃ"))
        z.writestr("sub/.y/time_d.csv", line("RX06261101", "Ｄ"))
    files = RM.find_files(d)
    assert [f.member for f in files if f.member] == ["time_a.csv", "time_b.csv"]
    assert RM.read_files(files).table["RX06261101"]["time"] == "B"
    # 読めない ZIP の中身（パスワードつき）は、その1本だけ飛ばして残りを読む
    with zipfile.ZipFile(d / "locked.zip", "w") as z:
        z.writestr("pace_x.csv", '"x",RX06261101,"05HHH "\n')
    _encrypt_flag(d / "locked.zip")
    logs = []
    marks = RM.load_dir(d, logs.append)
    assert marks.table["RX06261101"] == {"member": "A", "time": "B", "pace": "", "cushion": "", "moist_turf": "", "moist_dirt": ""}
    assert sum("[WARN]" in m and "locked.zip:pace_x.csv を読めません" in m for m in logs) == 1
    assert RM.load(d, tmp_path / "nomaster").table == marks.table                      # 出馬表側の読み方も止まらない
    # どんな読み込みの失敗でも（途中で切れた圧縮データ など）同じ
    real = RM._read_bytes
    def flaky(mf):
        if mf.path == d / "member.csv":
            raise EOFError("compressed file ended")
        return real(mf)
    monkeypatch.setattr(RM, "_read_bytes", flaky)
    logs = []
    assert RM.load_dir(d, logs.append).table["RX06261101"]["member"] == "B"            # a/member.csv の値
    assert any("[WARN]" in m and "member.csv を読めません（EOFError" in m for m in logs)


def test_result_files_follow_the_same_order_and_skip_unreadable_members(tmp_path):
    """結果指数（過去SP・ST）のファイルも、相対パスの名前順・読めない ZIP の中身は1本だけ飛ばす。"""
    from shisu import past_perf_indices as PP
    d = tmp_path / "r"
    (d / "a").mkdir(parents=True)
    rid = "202601050601010101"
    (d / "sp_x.csv").write_text(f"{rid},50\n", encoding="utf-8")
    (d / "a" / "sp_x.csv").write_text(f"{rid},60\n", encoding="utf-8")
    files = PP.find_result_files(d)
    assert [f.path.relative_to(d).as_posix() for f in files] == ["a/sp_x.csv", "sp_x.csv"]
    assert str(PP.load_results(files)["SP"][rid]) == "50"
    with zipfile.ZipFile(d / "st_se_locked.zip", "w") as z:
        z.writestr("st_se_1.csv", f"{rid},70\n")
    _encrypt_flag(d / "st_se_locked.zip")
    logs = []
    got = PP.load_results(PP.find_result_files(d), logs.append)
    assert str(got["SP"][rid]) == "50" and got["ST"] == {}
    assert sum("[WARN]" in m and "st_se_1.csv を読めません" in m for m in logs) == 1


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


def test_entries_side_follows_how_replace_read_the_marks(tmp_path, monkeypatch):
    """replace の読み方（__race_marks__.json）: 読まなかったなら出馬表側も読まない。別のフォルダならそのフォルダ。"""
    monkeypatch.setenv("SHISU_BASE_DIR", str(tmp_path))
    monkeypatch.delenv("RACE_MARKS_DIR", raising=False)
    d, other, master = tmp_path / "race_marks", tmp_path / "other", tmp_path / "master_csv"
    d.mkdir(), other.mkdir()
    (d / "member.csv").write_text('"x",RX06261101,"03 Ａ "\n', encoding="utf-8")
    (other / "member.csv").write_text('"x",RX06261101,"03 Ｂ "\n', encoding="utf-8")
    rid = "2026010506010101"
    assert RM.load_meta(master) is None and RM.load().get(rid)["member"] == "A"       # 記録が無ければ既定のフォルダ
    # 既定のフォルダで読んだ: フォルダは記録しない（出馬表側も、その場所の既定のフォルダ）
    path = RM.save_meta(master, True, d)
    assert path == master / RM.META and RM.load_meta(master) == {"enabled": True, "dir": ""} and not list(master.glob("*.part"))
    assert RM.load().get(rid)["member"] == "A"
    # --no-race-marks: 既定のフォルダにファイルがあっても読まない
    RM.save_meta(master, False, d)
    logs = []
    assert len(RM.load(log=logs.append)) == 0 and any("[INFO]" in m and "--no-race-marks" in m for m in logs)
    assert RM.load(d).get(rid)["member"] == "A"                    # フォルダを指定すれば読む（build_db --marks-dir）
    assert RM.load(enabled=True).get(rid)["member"] == "A"
    # --marks-dir: 同じフォルダを読む。そこにファイルが無ければ master_csv の表
    RM.save_meta(master, True, other)
    assert RM.load_meta(master) == {"enabled": True, "dir": str(other.resolve())}
    logs = []
    assert RM.load(log=logs.append).get(rid)["member"] == "B" and any("replace と同じフォルダ" in m for m in logs)
    assert RM.load(d).get(rid)["member"] == "A" and len(RM.load(enabled=False)) == 0
    RM.save(RM.load(), master)
    (other / "member.csv").unlink()
    assert RM.load().get(rid)["member"] == "B"
    # 読めない記録は無いものとして扱う
    for text in ("{broken", "[]", '{"enabled": "yes"}'):
        (master / RM.META).write_text(text, encoding="utf-8")
        assert RM.load_meta(master) is None
    assert RM.load().get(rid)["member"] == "A"


MARK_COLS = ["前走メンバーLv", "前走タイムLv", "前走クッション帯", "前走含水率帯"]


def test_replace_options_reach_every_master_and_the_entries_side(tmp_path, monkeypatch, capsys):
    """replace の --no-race-marks・--marks-dir が、全部の種類の master（基本CSV・ext・過去SP）と出馬表側（build_db）に同じに効く。"""
    import sqlite3

    from tests import synth2
    root = tmp_path
    monkeypatch.setenv("SHISU_BASE_DIR", str(root))
    for k in ("RESULT_INDICES_DIR", "RACE_MARKS_DIR", "SHISU_EXT_DIR", "DB_PATH", "SHISU_FAMILIES_FILE", "NOTES_PATH"):
        monkeypatch.delenv(k, raising=False)
    base, marks, ent, _names = synth2.write_site(root, days=26)
    rid = "レースID(新)"
    (root / "ext_indices" / "OMEGA").mkdir(parents=True)
    (root / "ext_indices" / "OMEGA" / "20260101_omega.csv").write_text(
        "".join(f"{r},{50 + i % 37}\n" for i, r in enumerate(base[rid])), encoding="utf-8")
    (root / "result_indices").mkdir()
    (root / "result_indices" / "sp_all.csv").write_text(
        "".join(f"{r},{40 + i % 41}\n" for i, r in enumerate(base[rid])), encoding="utf-8")
    import build_db, calc_entry_hensachi, replace_csv_ver3
    master = root / "master_csv"
    read = lambda ind: pd.read_csv(master / f"master_全場_{ind}.csv.gz", encoding="cp932", dtype=str,
                                   keep_default_na=False).sort_values(rid).reset_index(drop=True)

    def entries_table(*args):
        build_db.main(["--entries-only", "--no-xjoin", *args])      # 成績表の無い DB なので警告つき（出馬表の表は入る）
        con = sqlite3.connect(root / "racing.db")
        try:
            return pd.read_sql('SELECT * FROM entries_merged_hensachi ORDER BY "レースID(新)"', con).fillna("")
        finally:
            con.close()

    # 1) ふつうに読む: 3種類の master（基本CSV・ext の指数・過去SP）が同じ前走条件を持つ
    assert replace_csv_ver3.main(["--clean"]) == 0
    assert calc_entry_hensachi.main(["--merge-only", "--clean"]) == 0
    full = read("タイムM")
    assert all(len(set(full[c]) - {""}) >= 3 for c in MARK_COLS) and (master / RM.FILE).exists()
    assert RM.load_meta(master) == {"enabled": True, "dir": ""}
    for ind in ("オメガ指数", "過去SP_3走平均"):
        other = read(ind)
        assert other[rid].tolist() == full[rid].tolist()
        for c in MARK_COLS + ["前走R印3"]:
            assert other[c].tolist() == full[c].tolist(), (ind, c)
    e_full = entries_table()
    assert all((e_full[c] != "").sum() >= 5 for c in MARK_COLS)
    # 出馬表側は race_marks/ を先に読む: 表（__race_marks__.csv.gz）に無い新しい値が効く
    prev_of = base[base["着順"] != "取消"].groupby("血統登録番号").tail(1).set_index("血統登録番号")[rid]
    horse = next(h for h in ent["血統登録番号"] if h in prev_of.index)
    rx = RM.rx_of(prev_of[horse][:16])
    old = RM.load_cache(master).table[rx]["member"]
    new = "E" if old != "E" else "A"
    (root / "race_marks" / "member_zz.csv").write_text(f'"x",{rx},"03 {new} "\n', encoding="utf-8")
    e_new = entries_table()
    row = (e_new["血統登録番号"] == horse) if "血統登録番号" in e_new.columns else (e_new[rid] == ent.set_index("血統登録番号")[rid][horse])
    assert set(e_new.loc[row, "前走メンバーLv"]) == {new} and RM.load_cache(master).table[rx]["member"] == old
    (root / "race_marks" / "member_zz.csv").unlink()
    # build_db の --no-race-marks・--marks-dir
    e_none = entries_table("--no-race-marks")
    assert all((e_none[c] == "").all() for c in MARK_COLS) and (e_none["近5走同場"] != "").any()
    only = root / "only_one"
    only.mkdir()
    (only / "member.csv").write_text(f'"x",{rx},"03 {new} "\n', encoding="utf-8")
    e_dir = entries_table("--marks-dir", str(only))
    assert set(e_dir["前走メンバーLv"]) == {"", new} and all((e_dir[c] == "").all() for c in MARK_COLS[1:])
    assert set(e_dir.loc[row, "前走メンバーLv"]) == {new}

    # 2) replace --no-race-marks: 前の実行の表を残さず、どの master も空。出馬表側も race_marks/ があっても読まない
    capsys.readouterr()
    assert replace_csv_ver3.main(["--no-race-marks"]) == 0
    assert not (master / RM.FILE).exists() and RM.load_meta(master) == {"enabled": False, "dir": ""}
    base_r3 = full["前走R印3"].tolist()
    for ind in ("タイムM", "オメガ指数", "過去SP_3走平均"):
        m = read(ind)
        assert all((m[c] == "").all() for c in MARK_COLS), ind
        assert (m["前走R印3"] != "").sum() < sum(1 for v in base_r3 if v)            # ペース判定での補完もしない
        assert (m["近5走同場"] != "").any()                                          # 近5走の条件はそのまま
    e_off = entries_table()
    assert all((e_off[c] == "").all() for c in MARK_COLS) and "--no-race-marks" in capsys.readouterr().out
    assert e_off["前走R印3"].tolist() == e_none["前走R印3"].tolist()
    e_forced = entries_table("--marks-dir", str(root / "race_marks"))                 # 指定すれば読む
    assert e_forced[MARK_COLS].equals(e_full[MARK_COLS])

    # 3) replace --marks-dir: そのフォルダの印だけ（既定の race_marks/ は読まない）。出馬表側も同じフォルダ
    assert replace_csv_ver3.main(["--marks-dir", str(only)]) == 0
    assert RM.load_meta(master) == {"enabled": True, "dir": str(only.resolve())} and len(RM.load_cache(master)) == 1
    for ind in ("タイムM", "オメガ指数", "過去SP_3走平均"):
        m = read(ind)
        assert set(m["前走メンバーLv"]) == {"", new} and all((m[c] == "").all() for c in MARK_COLS[1:]), ind
    e_only = entries_table()
    assert e_only[MARK_COLS].equals(e_dir[MARK_COLS])

    # 4) 印のフォルダが空になった: 前の実行の表を残さない
    (only / "member.csv").unlink()
    assert replace_csv_ver3.main(["--marks-dir", str(only)]) == 0
    assert not (master / RM.FILE).exists() and (read("タイムM")["前走メンバーLv"] == "").all()
    assert (entries_table()["前走メンバーLv"] == "").all()

    # 5) 読めない ZIP があっても止まらず、残りの印で master を作る
    assert replace_csv_ver3.main([]) == 0
    with zipfile.ZipFile(root / "race_marks" / "locked.zip", "w") as z:
        z.writestr("member_x.csv", f'"x",{rx},"03 {new} "\n')
    _encrypt_flag(root / "race_marks" / "locked.zip")
    capsys.readouterr()
    assert replace_csv_ver3.main(["--clean"]) == 0
    assert "locked.zip:member_x.csv を読めません" in capsys.readouterr().out
    again = read("タイムM")
    assert all(again[c].tolist() == full[c].tolist() for c in MARK_COLS)
    assert entries_table()[MARK_COLS].equals(e_full[MARK_COLS])


def test_two_hex_characters_at_the_head_are_always_taken_as_the_colour_code():
    """先頭2文字が16進なら、カラーコードとして除く（依頼どおり。カラーコードの無い2文字だけの値は空になる）。"""
    assert [RM.clean_value(v) for v in ("12", "ab", "0f")] == ["", "", ""]
    assert [RM.clean_value(v) for v in ("0012", "00ab", "0E 0f")] == ["12", "ab", "0f"]
    # カラーコードを除くのは、空白を除く・NFKC の前（先頭が空白の値から数字を落とさない）
    assert RM.clean_value("  10.2") == "10.2" and RM.clean_value("　１２") == "12" and RM.clean_value("00 １０.２ ") == "10.2"
