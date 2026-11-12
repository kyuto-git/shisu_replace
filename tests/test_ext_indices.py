"""外部指数ファイル（shisu/ext_indices.py）のテスト。ファイルはすべて tmp_path に作る合成のもの。"""
from __future__ import annotations

import time
from pathlib import Path

import pandas as pd
import pytest

from shisu import ext_indices as ex
from shisu.indicators import canonical_indicator, is_known_indicator


def write(path: Path, text: str, enc: str = "utf-8") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(text.encode(enc))
    return path


class Log(list):
    def __call__(self, msg):
        self.append(str(msg))


# 形式ごとに中身の正しい小さなファイル
A_TEXT = "レースID(新),ワーク1\n202610250404041206,54\n"
B_TEXT = "0520380101,54,6\n"
C_TEXT = "202609260601,64,1,13,13\n"
D_TEXT = "04261801,99,105\n"


# ---------------------------------------------------------------------------
# ID の変換
# ---------------------------------------------------------------------------
def test_old_id_from_new_examples():
    assert ex.old_id_from_new("202610250404041206") == "0426441206"
    assert ex.old_id_from_new("202611010504110101") == "05264B0101"     # 11日目 = B
    assert ex.old_id_from_new("202611010504120101") == "05264C0101"     # 12日目 = C
    assert ex.old_id_from_new(" 202610250404041206.0 ") == "0426441206"
    assert ex.old_id_from_new("２０２６１０２５０４０４０４１２０６") == "0426441206"
    assert ex.old_id_from_new("2026102504040412") == ""                 # 16桁（レース単位）
    assert ex.old_id_from_new("") == ""
    assert ex.old_id_from_new(None) == ""


def test_normalize_old_id():
    assert ex.normalize_old_id("0426441206") == "0426441206"
    assert ex.normalize_old_id("426441206") == "0426441206"           # 先頭の 0 が落ちた9文字
    assert ex.normalize_old_id("05264b0101") == "05264B0101"
    assert ex.normalize_old_id(" 5264B0101 ") == "05264B0101"
    assert ex.normalize_old_id("520380101.0") == "0520380101"
    assert ex.normalize_old_id("0520380101x") == ""
    assert ex.normalize_old_id("52038010") == ""
    assert ex.normalize_old_id("") == ""
    assert ex.normalize_old_race_id("RX06262301") == "06262301"
    assert ex.normalize_old_race_id("rx6262301") == "06262301"
    assert ex.normalize_old_race_id("4261801") == "04261801"
    assert ex.normalize_old_race_id("042618") == ""


def test_supported_names_match_registry():
    expected = ("オメガ指数", "かよちん指数", "keibaAI指数", "吉馬_SP能力値", "吉馬_先行力", "吉馬_SP信頼",
                "吉馬_SP調整", "吉馬_SP最大", "吉馬_末脚力", "安井_ランク", "安井_スピード", "安井_スタミナ",
                "安井_パワー", "安井_根性", "安井_賢さ", "安井_体力", "安井_やる気", "SAV", "ST指数", "合計値",
                "展開", "仕上り", "先行", "追走", "持久", "持続", "瞬発", "想定前半", "想定後半", "Gold", "BC", "ZI")
    assert ex.SUPPORTED == expected
    for name in ex.SUPPORTED:
        assert is_known_indicator(name) and canonical_indicator(name) == name


# ---------------------------------------------------------------------------
# 振り分け
# ---------------------------------------------------------------------------
VARIANTS = {
    # 形式A
    "OMEGA/20261025_omega.csv": "オメガ指数",
    "omega/sub/data.csv": "オメガ指数",
    "kayochin/20261025_kayochin.csv": "かよちん指数",
    "keibaAI/20261025_keibaAI.csv": "keibaAI指数",
    "misc/20261025_KEIBAAI.csv": "keibaAI指数",
    "kichiuma/kichiuma_sp_ability/20261025_kichiuma_sp_ability.csv": "吉馬_SP能力値",
    "kichiuma-senkou/20261025.csv": "吉馬_先行力",
    "kichiuma_sp_trust/x.csv": "吉馬_SP信頼",
    "kichiuma_sp_control/x.csv": "吉馬_SP調整",
    "misc/20261025_kichiuma_sp_max.csv": "吉馬_SP最大",
    "kichiuma/kichiuma_sueashi/20261025_kichiuma_sueashi.csv": "吉馬_末脚力",
    "yasui_speed/20261025_yasui_speed.csv": "安井_スピード",
    "misc/20261025_yasui_kashikosa.csv": "安井_賢さ",
    "yasui/yasui_rank/20261025_yasui_rank.csv": "安井_ランク",
    "yasui/yasui_stamina/a.csv": "安井_スタミナ",
    "yasui/yasui_power/a.csv": "安井_パワー",
    "yasui/yasui_konjo/a.csv": "安井_根性",
    "yasui/yasui_tairyoku/a.csv": "安井_体力",
    "yasui/yasui_yaruki/a.csv": "安井_やる気",
    # 形式B（StridePlus。ファイル名の接頭辞）
    "StridePlus/sav_2020.csv": "SAV",
    "StridePlus/st_2020.csv": "ST指数",
    "strideplus/2020/GOKEI_2020.csv": "合計値",
    "StridePlus/tenkai2020.csv": "展開",
    "StridePlus/siagari_2020.csv": "仕上り",
    "StridePlus/senko_2020.csv": "先行",
    "StridePlus/tsuiso_2020.csv": "追走",
    "StridePlus/jikyu_2020.csv": "持久",
    "StridePlus/jizoku_2020.csv": "持続",
    "StridePlus/shunpatsu_2020.csv": "瞬発",
    "StridePlus/mae_stime_2020.csv": "想定前半",
    "StridePlus/ato_stime_2020.csv": "想定後半",
    "StridePlus/sav/2021.csv": "SAV",                 # 接頭辞が無ければ StridePlus より下のフォルダ名
    "Stride_Plus/Senkou/2021.csv": "先行",            # 長い接頭辞は続きがあっても当てる
    "StridePlus/shunpatsuryoku2021.csv": "瞬発",
    # 形式C
    "GoldAirplane/2024.csv": "Gold",
    "gold/x.csv": "Gold",
    "GoldAirplane_2023.csv": "Gold",
    # 形式D
    "BCindex/BC2024.csv": "BC",
    "ZI_DATA/ZI2024.CSV": "ZI",
}
PATTERN_TEXT = {"A": A_TEXT, "B": B_TEXT, "C": C_TEXT, "D": D_TEXT}


def test_discover_naming_variants(tmp_path):
    for rel, ind in VARIANTS.items():
        write(tmp_path / rel, PATTERN_TEXT[ex.PATTERN_OF[ind]])
    log = Log()
    sources, skipped = ex.discover(tmp_path, log=log)
    got = {p.relative_to(tmp_path).as_posix(): ind for ind, src in sources.items() for p in src.files}
    assert got == VARIANTS, skipped
    assert skipped == []
    assert list(sources) == [n for n in ex.SUPPORTED if n in set(VARIANTS.values())]
    for ind, src in sources.items():
        assert src.indicator == ind and src.pattern == ex.PATTERN_OF[ind]
    assert any("SAV（形式B）: 2ファイル" in m for m in log)


def test_discover_skips_helpers_discontinued_and_unknown(tmp_path):
    ok = write(tmp_path / "yasui/yasui_speed/20261025_yasui_speed.csv", A_TEXT)
    helpers = {
        "yasui/yasui_speed/base_speed_merged.csv": "マージ完全版",
        "kichiuma/kichiuma_sp_ability/base_sp_ability_merged.csv": "マージ完全版",
        "omega/base_merged_2.csv": "マージ完全版",
        "yasui/yasui_extract.csv": "yasui_extract",
        "yasui/yasui_speed/20261025_yasui_speed_noid.csv": "_noid",
        "entries_in/yasui_20261025.csv": "entries_in",
        "omega/notes.txt": "CSV でない",
    }
    for rel in helpers:
        write(tmp_path / rel, "レースID(新),日付S,枠番,馬番,ワーク1\n202610250404041206,20261025,1,6,54\n")
    # 日付から始まる（ID の無い）安井の出力は、名前が合っていても使わない
    write(tmp_path / "yasui/yasui_rank/20261101_yasui_rank.csv",
          "日付,場所,場所コード,R,馬番,馬名,ワーク1\n20261101,東京,05,1,1,アイウ,40\n")
    discontinued = {
        "ittai/a.csv": "一隊", "ittai/b.csv": "一隊", "結果分析スピード指数/x.csv": "一隊",
        "33rap/20261017_33rap.csv": "33rap", "1F/20261017_1F.csv": "上がり1F", "2F/20261017_2F.csv": "上がり2F",
        "katsuraki/k.csv": "カツラキ", "カツラキ/k.csv": "カツラキ", "HPRTV5C.csv": "HPRTV5C",
        "StridePlus/st_se_2020.csv": "成績ST指数", "StridePlus/ST_SE.csv": "成績ST指数",
        "StridePlus/st_seiseki/2020.csv": "成績ST指数",
    }
    for rel in discontinued:
        write(tmp_path / rel, B_TEXT)
    unknown = {
        "lap/rabbit_1F/20261017_rabbit_1F.csv": "名前から指数を決められない",   # 廃止ではないが未対応
        "lap/4F/20261017_4F.csv": "名前から指数を決められない",
        "misc/readme.csv": "名前から指数を決められない",
        "StridePlus/stride_notes.csv": "接頭辞",                                # 'st' は 'stride' に当てない
        "StridePlus/savings.csv": "接頭辞",
        "omega/20261025_kayochin.csv": "食い違う",
        "BCindex/BC_bad.csv": "旧8桁のレースIDでない",
    }
    for rel in unknown:
        write(tmp_path / rel, "x,y\n1,2\n")
    write(tmp_path / ".DS_Store", "x")
    write(tmp_path / "__MACOSX/omega/._20261025_omega.csv", "x")
    write(tmp_path / "omega/._20261025_omega.csv", "x")
    write(tmp_path / "omega/empty_omega.csv", "")

    log = Log()
    sources, skipped = ex.discover(tmp_path, log=log)
    assert set(sources) == {"安井_スピード"} and sources["安井_スピード"].files == [ok]
    reasons = {p.relative_to(tmp_path).as_posix(): why for p, why in skipped}
    for rel, word in {**helpers, **discontinued, **unknown}.items():
        assert word in reasons[rel], (rel, reasons.get(rel))
    assert "空のファイル" in reasons["omega/empty_omega.csv"]
    assert "1列目" in reasons["yasui/yasui_rank/20261101_yasui_rank.csv"]
    assert not any("DS_Store" in r or "._" in r for r in reasons)
    # 廃止は種類ごとに INFO を1回だけ
    gone = [m for m in log if m.startswith("[INFO] 廃止")]
    assert len([m for m in gone if "一隊" in m]) == 1 and "3ファイル" in next(m for m in gone if "一隊" in m)
    assert len(gone) == len(set(discontinued.values()))


def test_discover_missing_root(tmp_path):
    log = Log()
    assert ex.discover(tmp_path / "nope", log=log) == ({}, [])
    assert log and "ありません" in log[0]


# ---------------------------------------------------------------------------
# 読込と付与
# ---------------------------------------------------------------------------
def base_frame(rows) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=["レースID(新)", "馬番"])


def load(tmp_path, rel_texts: dict, log=None):
    """{相対パス: (中身, 符号化)} を書いて discover → load_source。指数1つ分を返す。"""
    for rel, (text, enc) in rel_texts.items():
        write(tmp_path / rel, text, enc)
    log = log if log is not None else Log()
    sources, skipped = ex.discover(tmp_path, log=log)
    assert len(sources) == 1, (sources, skipped)
    return ex.load_source(next(iter(sources.values())), log=log)


def test_pattern_a_header_encodings_and_messy_rows(tmp_path):
    log = Log()
    lk = load(tmp_path, {
        # 見出しあり・cp932
        "OMEGA/20261025_omega.csv": ("レースID(新),ワーク1\r\n202610250404041201,54\r\n202610250404041202,\r\n", "cp932"),
        # 見出しなし・UTF-8・引用符・空行・末尾のカンマ
        "OMEGA/20261026_omega.csv": ('"202610260404051203","61.5",\n\n202610260404051204, 47 ,,\n', "utf-8"),
        # BOM 付き UTF-8・CRLF
        "OMEGA/20261027_omega.csv": ("レースID(新),ワーク1\r\n202610270404061205,70\r\n", "utf-8-sig"),
        # つないだファイルの途中の見出しは飛ばす。ID でない行は数えて警告
        "OMEGA/20261028_omega.csv": ("202610280404071206,33\nレースID(新),ワーク1\n2026102804040712,9\n", "utf-8"),
    }, log=log)
    assert lk.pattern == "A" and lk.ranks is None
    assert lk.values == {"202610250404041201": "54", "202610260404051203": "61.5",
                         "202610260404051204": "47", "202610270404061205": "70", "202610280404071206": "33"}
    assert any("読めない行 1件" in m for m in log)
    base = base_frame([
        ["202610250404041201", "1"],
        ["202610250404041202.0", 2],              # 値が空の馬
        [" 2026102604040512 ", "03"],             # 16桁（レース単位）+ 馬番
        ["２０２６１０２６０４０４０５１２０４", "4"],  # 全角
        ["202610270404061205", None],
        ["202610270404061299", "99"],             # ファイルに無い
        [None, None],
    ])
    base.index = [10, 11, 12, 13, 14, 15, 16]
    vals, ranks = ex.attach(base, lk)
    assert ranks is None
    assert list(vals.index) == list(base.index)
    assert vals.tolist() == ["54", "", "61.5", "47", "70", "", ""]


def test_pattern_a_utf8_header_without_bom(tmp_path):
    # BOM の無い UTF-8 の日本語見出しを cp932 と読み違えない
    lk = load(tmp_path, {"kayochin/20261025_kayochin.csv":
                         ("レースID(新),ワーク1\n202610250404041206,12\n", "utf-8")})
    assert lk.values == {"202610250404041206": "12"}


def test_pattern_b_old_id_value_rank(tmp_path):
    log = Log()
    lk = load(tmp_path, {"StridePlus/2026/sav_2026.csv": (
        "0426441206,54,6\r\n"
        "520380101,48,3\r\n"          # 9文字（先頭の 0 が落ちた）
        "05264b0101, 61 ,2,\r\n"      # 日 = B（小文字）・空白・末尾のカンマ
        "bad,1,1\r\n"
        "0426441207,,5\r\n", "cp932")}, log=log)
    assert lk.values == {"0426441206": "54", "0520380101": "48", "05264B0101": "61"}
    assert lk.ranks == {"0426441206": "6", "0520380101": "3", "05264B0101": "2"}
    assert any("読めない行 1件" in m and "bad" in m for m in log)
    base = pd.DataFrame({
        "レースID(新)": ["202610250404041206", "202611010504110101", "", "202610250404041207", "202610250404041208"],
        "レースID(旧)": ["", "", "520380101", "", None],
        "馬番": ["6", "1", "1", "7", "8"],
    })
    vals, ranks = ex.attach(base, lk)
    assert vals.tolist() == ["54", "61", "48", "", ""]
    assert ranks.tolist() == ["6", "2", "3", "", ""]
    # レースID(旧) 列が無くても レースID(新) から作る
    vals2, _ = ex.attach(base[["レースID(新)", "馬番"]], lk)
    assert vals2.tolist() == ["54", "61", "", "", ""]


def test_pattern_c_gold_pairs(tmp_path):
    lk = load(tmp_path, {"GoldAirplane/gold_2026.csv": (
        "202609260601,64,1,13,13,34,9,53,3,,,\n"
        "202609260602,70,2,71,1,40\n", "utf-8")})       # 3頭目は値だけ（順位なし）
    assert lk.pattern == "C"
    assert lk.values == {"20260926060101": "64", "20260926060102": "13", "20260926060103": "34",
                         "20260926060104": "53", "20260926060201": "70", "20260926060202": "71",
                         "20260926060203": "40"}
    base = base_frame([
        ["202609260604070101", "1"], ["202609260604070102", "2"], ["202609260604070103", "3"],
        ["202609260604070104", "4"], ["202609260604070105", "5"],     # 5番は無い
        ["202609260604070203", "3"], ["202609270604070101", "1"],     # 別の日
    ])
    vals, ranks = ex.attach(base, lk)
    assert vals.tolist() == ["64", "13", "34", "53", "", "40", ""]
    assert ranks.tolist() == ["1", "13", "9", "3", "", "", ""]


def test_pattern_d_bc_and_zi(tmp_path):
    bc = load(tmp_path / "bc", {"BCindex/BC2026.csv": (
        "RX06262301,50.19 ,59.26 ,44.86 , ,\r\n"
        "rx6262302,48.5,,51\r\n", "cp932")})
    assert bc.pattern == "D" and bc.ranks is None
    assert bc.values == {"0626230101": "50.19", "0626230102": "59.26", "0626230103": "44.86",
                         "0626230201": "48.5", "0626230203": "51"}
    # 中山(06) 2026年 2回 3日 1R・2R
    base = base_frame([[f"20260301060203{r:02d}{u:02d}", str(u)] for r, u in
                       [(1, 1), (1, 2), (1, 3), (1, 4), (1, 5), (2, 1), (2, 2), (2, 3)]])
    vals, ranks = ex.attach(base, bc)
    assert ranks is None
    assert vals.tolist() == ["50.19", "59.26", "44.86", "", "", "48.5", "", "51"]

    zi = load(tmp_path / "zi", {"ZI_DATA/ZI2026.CSV": (
        "04261801,99,105,84,90,0,92\n"
        "4261802,88,77\n", "utf-8")})                    # 先頭の 0 が落ちた7文字
    base = base_frame([["202607200401080101", "1"], ["202607200401080105", "5"],
                       ["202607200401080106", "6"], ["202607200401080107", "7"],
                       ["202607200401080202", "2"]])
    vals, _ = ex.attach(base, zi)
    assert vals.tolist() == ["99", "0", "92", "", "77"]


def test_later_file_overrides_and_logs(tmp_path):
    log = Log()
    lk = load(tmp_path, {
        "StridePlus/sav_2019.csv": ("0426441206,50,3\n0426441207,40,4\n0426441208,30,5\n", "utf-8"),
        "StridePlus/sav_2020.csv": ("0426441206,55,1\n0426441207,40,4\n0426441208,,\n", "utf-8"),
    }, log=log)
    assert lk.values == {"0426441206": "55", "0426441207": "40", "0426441208": "30"}   # 空では消さない
    assert lk.ranks["0426441206"] == "1"
    msg = next(m for m in log if "上書き" in m)
    assert "2件" in msg and "値が違う 1件" in msg


def test_attach_requires_id_columns(tmp_path):
    lk = ex.ExtLookup("オメガ指数", "A", {}, None)
    with pytest.raises(KeyError):
        ex.attach(pd.DataFrame({"馬番": ["1"]}), lk)
    lk_b = ex.ExtLookup("SAV", "B", {"0426441206": "54"}, {"0426441206": "6"})
    vals, ranks = ex.attach(pd.DataFrame({"レースID(旧)": ["426441206"]}), lk_b)
    assert vals.tolist() == ["54"] and ranks.tolist() == ["6"]
    vals, ranks = ex.attach(pd.DataFrame({"レースID(新)": [], "馬番": []}), lk_b)
    assert len(vals) == 0 and len(ranks) == 0


def test_large_pattern_b_file_is_fast(tmp_path):
    rids = []
    for year in (2024, 2025):
        for place in range(1, 11):
            for kai in range(1, 7):
                for nichi in range(1, 13):
                    for r in range(1, 13):
                        for uma in range(1, 19):
                            rids.append(f"{year}0101{place:02d}{kai:02d}{nichi:02d}{r:02d}{uma:02d}")
    rids = rids[:200_000]
    lines = [f"{ex.old_id_from_new(rid)},{i % 97},{i % 18 + 1}" for i, rid in enumerate(rids)]
    write(tmp_path / "StridePlus/st_2025.csv", "\n".join(lines) + "\n")
    base = pd.DataFrame({"レースID(新)": rids + ["202501010101010119"], "馬番": [r[-2:] for r in rids] + ["19"]})

    t0 = time.perf_counter()
    sources, _ = ex.discover(tmp_path, log=lambda m: None)
    lk = ex.load_source(sources["ST指数"], log=lambda m: None)
    vals, ranks = ex.attach(base, lk)
    elapsed = time.perf_counter() - t0
    assert len(lk) == 200_000
    assert vals.iloc[0] == "0" and vals.iloc[12345] == str(12345 % 97) and vals.iloc[-1] == ""
    assert ranks.iloc[199_999] == str(199_999 % 18 + 1)
    assert elapsed < 10, elapsed


# ---------------------------------------------------------------------------
# StridePlus のボーダー（*_border*）を指数にしない
# ---------------------------------------------------------------------------
def test_border_files_never_become_indices(tmp_path):
    """st_border・siagari_border（基準値との差。形は 旧ID, 値, 順位 で指数と同じ）を ST指数・仕上り に混ぜない。
    フォルダ名で指数が決まる置き方でも、ほかの指数の border でも使わない。本物の値は上書きされない。"""
    real = {"StridePlus/st082652.csv": "0520380101,54,2\n0520380102,56,1\n",
            "StridePlus/siagari082652.csv": "0520380101,61,1\n0520380102,48,2\n"}
    borders = ["StridePlus/st_border082652.csv", "StridePlus/siagari_border082652.csv",
               "StridePlus/ST_BORDER.csv", "StridePlus/st-border_2026.csv",
               "StridePlus/st/st_border082652.csv",            # フォルダ名（st）から ST指数 にしない
               "StridePlus/siagari/border082652.csv", "StridePlus/border/st082652.csv",
               "StridePlus/sav_border082652.csv", "StridePlus/gokei_border.csv"]
    for rel, text in real.items():
        write(tmp_path / rel, text)
    for rel in borders:                                          # 本物より後に読まれる名前で、値はマイナスの差
        write(tmp_path / rel, "0520380101,-12,2\n0520380102,-3,1\n")
    log = Log()
    sources, skipped = ex.discover(tmp_path, log=log)
    assert set(sources) == {"ST指数", "仕上り"}
    assert [p.name for p in sources["ST指数"].files] == ["st082652.csv"]
    assert [p.name for p in sources["仕上り"].files] == ["siagari082652.csv"]
    reasons = {p.relative_to(tmp_path).as_posix(): why for p, why in skipped}
    assert set(reasons) == set(borders) and all("ボーダー" in why for why in reasons.values())
    assert any("ボーダーのファイル 9本" in m for m in log)
    st = ex.load_source(sources["ST指数"], log=log)
    rids = ["202006200503080101", "202006200503080102"]
    assert [ex.old_id_from_new(r) for r in rids] == ["0520380101", "0520380102"]
    att = ex.attach_filled(pd.DataFrame({"レースID(新)": rids, "馬番": ["1", "2"]}), st)
    assert list(att.values) == ["54", "56"]
    # 接頭辞の表でも border は指数にならない（'st'・'siagari' より先に当たる）
    assert ex._stride_prefix("st_border082652") == (True, None)
    assert ex._stride_prefix("siagari_border082652") == (True, None)
    assert ex._stride_prefix("st082652") == (True, "ST指数") and ex._stride_prefix("siagari082652") == (True, "仕上り")
    # border という語でなければ外さない
    assert not ex._is_border("borderline_x") and not ex._is_border("stborder") and ex._is_border("ST-Border_2026")
