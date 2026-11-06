"""指数の台帳。指数名の別名・符号反転・ZI 特例・新馬除外をここで一元管理する。

- 過去側はファイル名（例: 主５場（中山、東京、中京、京都、阪神）_先行.csv）から指数名を取る。
- 出馬表側は列名（例: 先行力, GoldAirplane, マイニング値）から指数名を取る。
どちらも canonical_indicator() を通して同じ正式名にそろえ、DB の indicator 列で完全一致させる。
（旧版は「先行力」が「吉馬_先行力」に部分一致し、「持久力」が「持久」に当たらなかった）

新しい指数を追加するときは INDICATORS に1行足すだけでよい。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class IndicatorSpec:
    name: str                       # 正式名（DB・列名・画面表示に使う）
    aliases: tuple[str, ...] = ()   # 別名（ファイル名・出馬表の列名で使われる表記）
    reverse: bool = False           # 小さいほど良い指数（符号反転してから偏差値化）
    zi: bool = False                # ZI 特例: 0/欠損を同レース非ゼロ平均で埋める
    exclude_shinba: bool = False    # 新馬戦を除外する
    zero_missing: bool = False      # 0 を欠損とみなし、順位は元の順位列を使わず値から計算する（両側で同じ扱い）
                                    # TARGET は取り込んでいない馬の外部指数を 0 で出す（同梱サンプルの ZI で確認）ため、
                                    # 0 がほぼ現れない指数で「値なし」を両側そろえて欠損にする


_YOSHIUMA = ("吉馬_SP能力値", "吉馬_先行力", "吉馬_SP信頼", "吉馬_SP調整", "吉馬_SP最大", "吉馬_末脚力")
# 0 を欠損とみなすもの（4ファイルで値0は 0.1〜0.3%）と、0 が普通に出るもの（体力・やる気は約14%が0）
_YASUI_ZERO_MISSING = ("安井_ランク", "安井_スピード", "安井_スタミナ", "安井_パワー", "安井_根性", "安井_賢さ")
_YASUI_ZERO_VALUE = ("安井_体力", "安井_やる気")

INDICATORS: tuple[IndicatorSpec, ...] = (
    IndicatorSpec("Gold", ("GoldAirplane", "Gold指数", "Gold_Airplane")),
    IndicatorSpec("BC", ("BCindex", "BC指数")),
    IndicatorSpec("ZI", ("ZI指数",), zi=True, exclude_shinba=True),
    IndicatorSpec("オメガ指数", ("Ω指数",)),
    IndicatorSpec("かよちん指数"),
    IndicatorSpec("keibaAI指数", ("keibaAI",)),
    *(IndicatorSpec(n, exclude_shinba=True) for n in _YOSHIUMA),
    IndicatorSpec("SAV"),
    IndicatorSpec("ST指数", ("ST",)),
    IndicatorSpec("合計値"),
    IndicatorSpec("展開", ("展開力",), reverse=True),
    IndicatorSpec("仕上り", ("仕上がり",)),
    IndicatorSpec("先行", ("先行力",)),
    IndicatorSpec("追走", ("追走力",)),
    IndicatorSpec("持久", ("持久力",)),
    IndicatorSpec("持続", ("持続力",)),
    IndicatorSpec("瞬発", ("瞬発力",)),
    IndicatorSpec("想定前半", ("想定前半3F",), reverse=True),
    IndicatorSpec("想定後半", ("想定後半3F",), reverse=True),
    IndicatorSpec("タイムM", ("マイニング値", "タイム指数", "TimeM"), reverse=True),
    IndicatorSpec("対戦M", ("対戦型マイニング値", "MatchM")),
    IndicatorSpec("圧勝力"),
    # 安井サロン（月別 Excel。shisu/yasui_extractor.py）
    *(IndicatorSpec(n, zero_missing=True) for n in _YASUI_ZERO_MISSING),
    *(IndicatorSpec(n) for n in _YASUI_ZERO_VALUE),
)

# 画面での並び順（台帳の順）
INDICATOR_ORDER: tuple[str, ...] = tuple(s.name for s in INDICATORS)

# 集計の対象から外した指数（2026-11-06）。csv_files・ext_indices・出馬表の列にあっても使わない
DISCONTINUED: tuple[str, ...] = ("カツラキ", "HPRTV5C", "成績ST指数", "結果分析スピード指数", "一隊", "ittai",
                                 "33rap", "上がり1F", "上がり2F", "1F", "2F")


def _lookup_key(raw) -> str:
    s = unicodedata.normalize("NFKC", str(raw or ""))
    s = re.sub(r"\s+", "", s)
    return s.casefold()


_BY_KEY: dict[str, IndicatorSpec] = {}
for _spec in INDICATORS:
    for _n in (_spec.name, *_spec.aliases):
        _BY_KEY[_lookup_key(_n)] = _spec
_SPEC_BY_NAME = {s.name: s for s in INDICATORS}


def is_known_indicator(raw) -> bool:
    return _lookup_key(raw) in _BY_KEY


_DISCONTINUED_KEYS = {_lookup_key(n) for n in DISCONTINUED}


def is_discontinued(raw) -> bool:
    """廃止した指数か（カツラキ・HPRTV5C・成績ST指数・一隊・33rap・上がり1F/2F）。"""
    return _lookup_key(raw) in _DISCONTINUED_KEYS


def canonical_indicator(raw) -> str:
    """別名を正式名に。台帳に無い名前は NFKC・空白除去しただけで返す。"""
    spec = _BY_KEY.get(_lookup_key(raw))
    if spec is not None:
        return spec.name
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(raw or "")))


def spec_for(name) -> IndicatorSpec:
    """正式名・別名から仕様を返す。台帳に無い指数は特例なし（そのまま偏差値化）。"""
    spec = _BY_KEY.get(_lookup_key(name))
    return spec if spec is not None else IndicatorSpec(canonical_indicator(name))


def parse_source_name(stem: str) -> tuple[str, str]:
    """過去側ファイル名（拡張子なし）→ (地域, 正式指数名)。

    例: '主５場（中山、東京、中京、京都、阪神）_吉馬_SP能力値' → ('主5場(中山、東京、中京、京都、阪神)', '吉馬_SP能力値')
        '札幌_カツラキ' → ('札幌', 'カツラキ')
    '_' で区切った末尾側から、台帳にある最長の指数名を探す。見つからなければ最初の '_' で分ける。
    """
    parts = str(stem).split("_")
    for i in range(len(parts)):
        cand = "_".join(parts[i:])
        if is_known_indicator(cand):
            region = "_".join(parts[:i])
            return unicodedata.normalize("NFKC", region), canonical_indicator(cand)
    if len(parts) >= 2:
        return unicodedata.normalize("NFKC", parts[0]), canonical_indicator("_".join(parts[1:]))
    return "", canonical_indicator(stem)
