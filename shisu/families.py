"""集計キー（結合データ）の型 = family の定義と、キーの組み立て（過去側も出馬表側もここで作る）。

family = 部品（parts）の並び ＋ 末尾の指数の部品（tail）
  部品: COURSE（場所×芝・ダ×距離×[芝(内・外)]。内外は空なら省く）、重量種別、クラス名、枠番グループ、騎手、芝・ダ、
        shisu/conditions.py の条件（時期・輸送距離・間隔帯・叩き・R値帯・…・前走R印3）
  tail: RANK（指数順位そのもの）/ RBAND（指数順位帯。スキーム s1=細5帯・s2=広3帯）/ HBAND（偏差値帯6種）
  キー = 部品の値を「×」でつないだもの。部品が1つでも空なら作らない
  前走着順・前走人気・前走上り3F順 は帯にしてから使う。帯のスキームは family のスキーム（s1/s2）と同じ。
  これらを含む family は、末尾が偏差値帯でも s1・s2 の2つの集計表になる（uses_schemes）。
  s2 は帯が重なる（2位は 1~3 と 1~5 の両方）ので、1頭から2つ以上のキーができる

初期セット（DEFAULT_TEMPLATES。49種 = A 15 + B 9 + C 10 + D 15 → 83 family・スキーム込み129通り）:
  A（コース固定）: normal, rankB, rankJ, rankBJ, normalJ（指数順位）とその *_band（指数順位帯）、hensachi*（偏差値帯）
  B（コース不問・騎手×条件）, C（全国のコース属性の掛け合わせ）, D（コース×条件）: 指数順位帯（name）と
  偏差値帯（name + "_hen"）の2つの family になる
selected_families.json（screen_families.py の出力）があれば、初期セットの代わりにそれを使う（configure()）。
"""
from __future__ import annotations

import itertools
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Mapping

import numpy as np
import pandas as pd

from .conditions import BANDED_CONDITIONS, CONDITION_COLUMNS
from .hensachi import band_labels

SEP = "×"
COURSE = "COURSE"
COURSE_FIELDS = ("場所", "芝・ダ", "距離")
INNER_FIELD = "芝(内・外)"
BASE_PARTS = ("重量種別", "クラス名", "枠番グループ", "騎手", "芝・ダ")
PART_TOKENS = (COURSE,) + BASE_PARTS + tuple(CONDITION_COLUMNS)
TAILS = ("RANK", "RBAND", "HBAND")
TAIL_LABELS = {"RANK": "指数順位", "RBAND": "指数順位帯", "HBAND": "偏差値帯"}
PART_LABELS = {COURSE: "コース", "間隔帯": "レース間隔", "叩き": "叩き良化", "左右遷移": "左右回り遷移",
               "斤量帯": "当日斤量", "前走上り3F順": "前走上がり3F順"}
_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,60}$")
_RESERVED_RE = re.compile(r"^(odds10u|odds10_50|odds50p|odds5p|rankband)_", re.IGNORECASE)


@dataclass(frozen=True)
class Family:
    name: str
    parts: tuple[str, ...]
    tail: str
    label: str = ""
    layer: str = ""
    only: tuple[str, ...] = ()      # 使うスキームを絞る（例 ("s2",)）。空なら s1 と s2 の両方

    @property
    def uses_schemes(self) -> bool:
        """順位帯のスキーム（s1/s2）で別々の集計表になるか。末尾が指数順位帯、または帯にする条件
        （前走着順・前走人気・前走上り3F順）を含む family（偏差値帯の family でも前走着順は s1・s2 の両方で作る）。"""
        return self.tail == "RBAND" or any(p in BANDED_CONDITIONS for p in self.parts)

    @property
    def schemes(self) -> tuple[str, ...]:
        if not self.uses_schemes:
            return ("",)
        return self.only or ("s1", "s2")

    @property
    def is_band(self) -> bool:
        """末尾が指数順位帯か。"""
        return self.tail == "RBAND"

    def display(self) -> str:
        return self.label or (SEP.join(PART_LABELS.get(p, p) for p in self.parts) + SEP + TAIL_LABELS[self.tail])

    def to_dict(self) -> dict:
        d = {"name": self.name, "parts": list(self.parts), "tail": self.tail, "label": self.display(),
             "layer": self.layer}
        if self.only:
            d["schemes"] = list(self.only)
        return d

    @staticmethod
    def from_dict(d: Mapping) -> "Family":
        parts = tuple(d.get("parts") or ())
        only = tuple(d.get("schemes") or ())
        f = Family(str(d.get("name", "")), parts, str(d.get("tail", "")), str(d.get("label", "")),
                   str(d.get("layer", "")), only)
        validate(f)
        return f

    def columns(self) -> tuple[str, ...]:
        """キーを作るのに要る列（過去側の master・出馬表側の fields）。"""
        cols: list[str] = []
        for p in self.parts:
            cols += list(COURSE_FIELDS) + [INNER_FIELD] if p == COURSE else [p]
        cols.append({"RANK": "順位", "RBAND": "順位", "HBAND": "偏差値帯"}[self.tail])
        return tuple(dict.fromkeys(cols))


def validate(f: Family) -> None:
    if not _NAME_RE.match(f.name):
        raise ValueError(f"family の名前は英字で始まる英数字と _ だけ（最大61文字）: {f.name!r}")
    if f.tail not in TAILS:
        raise ValueError(f"family {f.name}: tail は {TAILS} のどれか: {f.tail!r}")
    if not f.parts:
        raise ValueError(f"family {f.name}: parts が空です")
    bad = [p for p in f.parts if p not in PART_TOKENS]
    if bad:
        raise ValueError(f"family {f.name}: 知らない部品 {bad}（使えるのは {list(PART_TOKENS)}）")
    if len(set(f.parts)) != len(f.parts):
        raise ValueError(f"family {f.name}: 同じ部品が2回あります: {list(f.parts)}")
    if any(sc not in ("s1", "s2") for sc in f.only) or len(set(f.only)) != len(f.only):
        raise ValueError(f"family {f.name}: schemes は s1・s2 のどれか: {list(f.only)}")
    if f.only and not f.uses_schemes:
        raise ValueError(f"family {f.name}: schemes は指数順位帯（RBAND）か、前走着順・前走人気・前走上り3F順を含む family だけに書けます")
    if _RESERVED_RE.match(f.name):
        raise ValueError(f"family {f.name}: 名前をオッズ帯の接頭辞（odds10u_・odds10_50_・odds50p_・odds5p_）や "
                         "rankband_ で始めることはできません（成績表のファイル名と区別できなくなる）")


# ---------------------------------------------------------------------------
# 初期セット
# ---------------------------------------------------------------------------
_A = [
    ("normal", (COURSE, "重量種別", "クラス名"), "RANK"), ("rankB", (COURSE, "枠番グループ"), "RANK"),
    ("rankJ", (COURSE, "騎手"), "RANK"), ("rankBJ", (COURSE, "枠番グループ", "騎手"), "RANK"),
    ("normalJ", (COURSE, "重量種別", "クラス名", "騎手"), "RANK"),
    ("normal_band", (COURSE, "重量種別", "クラス名"), "RBAND"), ("rankB_band", (COURSE, "枠番グループ"), "RBAND"),
    ("rankJ_band", (COURSE, "騎手"), "RBAND"), ("rankBJ_band", (COURSE, "枠番グループ", "騎手"), "RBAND"),
    ("normalJ_band", (COURSE, "重量種別", "クラス名", "騎手"), "RBAND"),
    ("hensachiA", (COURSE, "重量種別", "クラス名"), "HBAND"), ("hensachiB", (COURSE, "枠番グループ"), "HBAND"),
    ("hensachiJ", (COURSE, "騎手"), "HBAND"), ("hensachiBJ", (COURSE, "枠番グループ", "騎手"), "HBAND"),
    ("hensachiAJ", (COURSE, "重量種別", "クラス名", "騎手"), "HBAND"),
]
_B = [
    ("B_jk_prevfin", ("騎手", "前走着順")), ("B_jk_prevpop", ("騎手", "前走人気")),
    ("B_jk_prevagari", ("騎手", "前走上り3F順")), ("B_jk_prevstyle", ("騎手", "前走脚質")),
    ("B_jk_distchg", ("騎手", "距離増減")), ("B_jk_interval", ("騎手", "間隔帯")), ("B_jk_tataki", ("騎手", "叩き")),
    ("B_jk_wtchg", ("騎手", "斤量増減")), ("B_sd_frame_jk", ("芝・ダ", "枠番グループ", "騎手")),
]
_C = [
    ("C_sd_dist_cfs", ("芝・ダ", "距離帯", "CFS帯")), ("C_sd_dist_f", ("芝・ダ", "距離帯", "F係数帯")),
    ("C_sd_dist_r", ("芝・ダ", "距離帯", "R値帯")), ("C_sd_straight_slope", ("芝・ダ", "直線帯", "坂")),
    ("C_sd_corner_frame", ("芝・ダ", "初角帯", "枠番グループ")), ("C_sd_f_style", ("芝・ダ", "F係数帯", "前走脚質")),
    ("C_sd_straight_agari", ("芝・ダ", "直線帯", "前走上り3F順")), ("C_sd_r3_style", ("芝・ダ", "前走R印3", "前走脚質")),
    ("C_sd_distchg_style", ("芝・ダ", "距離増減", "前走脚質")), ("C_quad_prevquad", ("今走クワドラント", "前走クワドラント")),
]
_D = [
    ("D_season", "時期"), ("D_transport", "輸送距離"), ("D_interval", "間隔帯"), ("D_tataki", "叩き"),
    ("D_lr", "左右遷移"), ("D_prevquad", "前走クワドラント"), ("D_weight", "斤量帯"), ("D_wtchg", "斤量増減"),
    ("D_mare", "牝馬限定"), ("D_distchg", "距離増減"), ("D_prevfin", "前走着順"), ("D_prevpop", "前走人気"),
    ("D_prevstyle", "前走脚質"), ("D_prevagari", "前走上り3F順"), ("D_r3", "前走R印3"),
]
# 初期セットの「型」49種（レイヤー, 名前, 部品, tail。B〜D は tail を2つ持つ）
DEFAULT_TEMPLATES: tuple[tuple[str, str, tuple[str, ...], tuple[str, ...]], ...] = (
    tuple(("A", n, p, (t,)) for n, p, t in _A)
    + tuple(("B", n, p, ("RBAND", "HBAND")) for n, p in _B)
    + tuple(("C", n, p, ("RBAND", "HBAND")) for n, p in _C)
    + tuple(("D", n, (COURSE, c), ("RBAND", "HBAND")) for n, c in _D)
)


def default_families() -> list[Family]:
    out = []
    for layer, name, parts, tails in DEFAULT_TEMPLATES:
        for t in tails:
            out.append(Family(name if t != "HBAND" or layer == "A" else f"{name}_hen", parts, t, layer=layer))
    for f in out:
        validate(f)
    return out


# ---------------------------------------------------------------------------
# 台帳（使う family と、ファイル名・DB の family 列から引ける family）
# ---------------------------------------------------------------------------
_KNOWN: dict[str, Family] = {f.name: f for f in default_families()}
_ACTIVE: list[str] = list(_KNOWN)
_SOURCE = "初期セット（49種）"


def register(fams: Iterable[Family]) -> None:
    for f in fams:
        old = _KNOWN.get(f.name)
        if old is not None and (old.parts, old.tail) != (f.parts, f.tail):
            raise ValueError(f"family {f.name} は別の定義で登録済みです（{old.parts} {old.tail} / {f.parts} {f.tail}）")
        _KNOWN[f.name] = f


def set_active(fams: Iterable[Family], source: str = "") -> None:
    """使う family を決める。この定義が正（同じ名前で別の定義が台帳にあれば置き換える。
    selected_families.json・DB の __families__ は初期セットと同じ名前を別の部品で使ってもよい）。"""
    global _ACTIVE, _SOURCE
    fams = list(fams)
    names = [f.name for f in fams]
    if len(set(names)) != len(names):
        raise ValueError("family の名前が重複しています")
    for f in fams:
        validate(f)
        _KNOWN[f.name] = f
    _ACTIVE = names
    _SOURCE = source or _SOURCE


def reset() -> None:
    """初期セットに戻す（テスト用）。"""
    global _KNOWN, _ACTIVE, _SOURCE
    _KNOWN = {f.name: f for f in default_families()}
    _ACTIVE = list(_KNOWN)
    _SOURCE = "初期セット（49種）"


def active() -> list[Family]:
    return [_KNOWN[n] for n in _ACTIVE]


def active_names() -> tuple[str, ...]:
    return tuple(_ACTIVE)


def active_source() -> str:
    return _SOURCE


def get(name: str) -> Family | None:
    return _KNOWN.get(name)


def known_names() -> tuple[str, ...]:
    return tuple(_KNOWN)


def label(name: str) -> str:
    f = _KNOWN.get(name)
    return f.display() if f else name


def load_families(path: Path | str) -> list[Family]:
    """selected_families.json / families.json を読む。形式: {"families": [{"name", "parts", "tail", "label"?}, ...]}。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    items = data.get("families") if isinstance(data, dict) else data
    if not isinstance(items, list) or not items:
        raise ValueError(f"{path}: families が空です")
    return [Family.from_dict(d) for d in items]


def save_families(path: Path | str, fams: Iterable[Family], extra: Mapping | None = None) -> None:
    data = {"version": 1, "families": [f.to_dict() for f in fams]}
    if extra:
        data.update(extra)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")


DB_TABLE = "__families__"


def save_to_db(con, fams: Iterable[Family]) -> None:
    """DB に family の定義を書く（build_db。画面・出馬表の照合が DB を作ったときの family を使うため）。"""
    con.execute(f"DROP TABLE IF EXISTS {DB_TABLE}")
    con.execute(f"CREATE TABLE {DB_TABLE} (name TEXT PRIMARY KEY, parts TEXT, tail TEXT, label TEXT, layer TEXT, "
                "schemes TEXT)")
    con.executemany(f"INSERT INTO {DB_TABLE} VALUES (?, ?, ?, ?, ?, ?)",
                    [(f.name, json.dumps(list(f.parts), ensure_ascii=False), f.tail, f.display(), f.layer,
                      ",".join(f.only)) for f in fams])


def load_from_db(con) -> list[Family]:
    """DB の __families__ → Family のリスト（表が無ければ []）。"""
    if con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (DB_TABLE,)).fetchone() is None:
        return []
    cols = {r[1] for r in con.execute(f"PRAGMA table_info({DB_TABLE})")}
    sel = "name, parts, tail, label, layer" + (", schemes" if "schemes" in cols else ", ''")
    return [Family.from_dict({"name": n, "parts": json.loads(p), "tail": t, "label": lab, "layer": ly,
                              "schemes": [x for x in (sc or "").split(",") if x]})
            for n, p, t, lab, ly, sc in con.execute(f"SELECT {sel} FROM {DB_TABLE}")]


def configure(path: Path | str | None = None, log=print) -> list[Family]:
    """使う family を決める。path（無ければ config.selected_families_path()）があればその定義、無ければ初期セット。"""
    import os
    from . import config
    p = Path(path) if path else config.selected_families_path()
    if not p.exists() and (path or os.environ.get("SHISU_FAMILIES_FILE")):
        # 明示したファイル（--families・SHISU_FAMILIES_FILE）が無いのに初期セットで集計すると気づけないので止める
        raise FileNotFoundError(f"family の定義ファイルがありません: {p}")
    if p.exists():
        fams = load_families(p)
        set_active(fams, f"{p.name}（{len(fams)} family）")
        log(f"[INFO] family: {p} の {len(fams)} 種を使います（初期セットの代わり）")
    else:
        reset()
    return active()


# ---------------------------------------------------------------------------
# キーの組み立て
# ---------------------------------------------------------------------------
def _scheme_for_part(fam: Family, scheme: str) -> str:
    return scheme or "s1"


def _labels_map(values: np.ndarray, scheme: str, kind: str) -> np.ndarray:
    """整数の文字列の配列 → 帯の名前（s1 は str、s2 は list）。"""
    cache: dict[str, object] = {}
    out = np.empty(len(values), dtype=object)
    for i, v in enumerate(values):
        r = cache.get(v)
        if r is None:
            labs = band_labels(v, scheme, kind) if v else []
            r = (labs[0] if labs else "") if scheme == "s1" else tuple(labs)
            cache[v] = r
        out[i] = r
    return out


def _col(df: pd.DataFrame, name: str) -> np.ndarray:
    if name not in df.columns:
        return np.full(len(df), "", dtype=object)
    s = df[name]
    return s.astype(object).where(s.notna(), "").astype(str).to_numpy(dtype=object)


def _part_values(df: pd.DataFrame, fam: Family, part: str, scheme: str) -> tuple[np.ndarray, bool]:
    """(値の配列, 複数値か)。複数値は tuple の配列。"""
    if part == COURSE:
        head = [_col(df, c) for c in COURSE_FIELDS]
        inner = _col(df, INNER_FIELD)
        v = head[0] + SEP + head[1] + SEP + head[2]
        v = np.where(inner != "", v + SEP + inner, v)
        ok = (head[0] != "") & (head[1] != "") & (head[2] != "")
        return np.where(ok, v, ""), False
    if part in BANDED_CONDITIONS:
        sc = _scheme_for_part(fam, scheme)
        return _labels_map(_col(df, part), sc, BANDED_CONDITIONS[part]), sc == "s2"
    return _col(df, part), False


def _tail_values(df: pd.DataFrame, fam: Family, scheme: str) -> tuple[np.ndarray, bool]:
    if fam.tail == "RANK":
        return _col(df, "順位"), False
    if fam.tail == "HBAND":
        return _col(df, "偏差値帯"), False
    return _labels_map(_col(df, "順位"), scheme, "rank"), scheme == "s2"


def build_keys(df: pd.DataFrame, fam: Family | str, scheme: str = "") -> pd.Series:
    """過去側（1頭1行の DataFrame）→ キーの Series。index は元の行の index（s2 の重なりでは同じ行が2回以上出る）。
    キーが作れない行は入らない。"""
    fam = _KNOWN[fam] if isinstance(fam, str) else fam
    if fam.uses_schemes and scheme not in ("s1", "s2"):
        raise ValueError(f"{fam.name} は順位帯の family なので scheme（s1/s2）が要ります")
    pos = np.arange(len(df))
    key = None
    for vals, multi in [_part_values(df, fam, p, scheme) for p in fam.parts] + [_tail_values(df, fam, scheme)]:
        if multi:
            lens = np.fromiter((len(vals[i]) for i in pos), dtype=np.int64, count=len(pos))
            take = np.repeat(np.arange(len(pos)), lens)
            flat = np.fromiter(itertools.chain.from_iterable(vals[i] for i in pos), dtype=object,
                               count=int(lens.sum())) if lens.sum() else np.empty(0, dtype=object)
            pos = pos[take]
            key = None if key is None else key[take]
            v = flat
        else:
            v = vals[pos]
        ok = v != ""
        pos, v = pos[ok], v[ok]
        key = v if key is None else key[ok] + SEP + v
    if key is None:
        key = np.empty(0, dtype=object)
    return pd.Series(key, index=df.index[pos], dtype=object)


def inner_variants(inner: str) -> list[str]:
    """出馬表側で試す 芝(内・外) の候補。分かっていれば [その値, 空]、分からなければ [空, 内, 外, 内外]。"""
    return [inner, ""] if inner else ["", "内", "外", "内外"]


def entry_keys(fields: Mapping, fam: Family | str, scheme: str = "") -> list[str]:
    """出馬表側の1頭×1指数 → キーの候補（重複なし）。fields は master と同じ列名の値。"""
    fam = _KNOWN[fam] if isinstance(fam, str) else fam
    choices: list[list[str]] = []
    for p in fam.parts:
        if p == COURSE:
            head = [str(fields.get(c) or "") for c in COURSE_FIELDS]
            if not all(head):
                return []
            base = SEP.join(head)
            vs = [base + (SEP + io if io else "") for io in inner_variants(str(fields.get(INNER_FIELD) or ""))]
        elif p in BANDED_CONDITIONS:
            vs = band_labels(fields.get(p) or None, _scheme_for_part(fam, scheme), BANDED_CONDITIONS[p])
        else:
            v = str(fields.get(p) or "")
            vs = [v] if v else []
        if not vs:
            return []
        choices.append(list(dict.fromkeys(vs)))
    if fam.tail == "RANK":
        t = str(fields.get("順位") or "")
        tails = [t] if t else []
    elif fam.tail == "HBAND":
        t = str(fields.get("偏差値帯") or "")
        tails = [t] if t else []
    else:
        tails = band_labels(fields.get("順位") or None, scheme, "rank")
    if not tails:
        return []
    choices.append(tails)
    return list(dict.fromkeys(SEP.join(c) for c in itertools.product(*choices)))


def entry_key_candidates(fields: Mapping, families: Iterable[str] | None = None) -> dict[str, list[tuple[str, str]]]:
    """出馬表の1頭×1指数について、family ごとの (キー, 順位帯スキーム) の候補。"""
    out: dict[str, list[tuple[str, str]]] = {}
    for name in (families if families is not None else _ACTIVE):
        fam = _KNOWN.get(name)
        if fam is None:
            continue
        cands = [(k, sc) for sc in fam.schemes for k in entry_keys(fields, fam, sc)]
        if cands:
            out[name] = list(dict.fromkeys(cands))
    return out
