"""結果指数（SP指数・ST指数）から「過去3走／5走の平均・最高」の8指数を作る。

SP指数・ST指数はレースのあとに確定する値なので、そのレースの値をそのレースの指数として使わない（未来の情報になる）。
ここで作るのは、各レース・各馬について「そのレースより前（発走日より前の日）で、600日以内の対象の走」の
直近3走・5走の平均と最高だけ。

入力
  result_indices/（config.result_indices_dir()。環境変数 RESULT_INDICES_DIR）の下を再帰的に探す:
    SP指数: 名前が SP で始まる CSV（SP01260101.csv）／ speed*.zip の中の SP*.csv
    ST指数: 名前が st_se で始まる CSV（st_se_20261010.csv）／ st_se*.zip の中の CSV
  中身は見出しなしの2列「レースID(新) 18桁, 指数値」（負の値も値。見出しの行があっても読む）
  過去走の履歴は csv_files/ の基本CSV（全場_タイムM.csv を優先。無ければ 全場_対戦M.csv、それも無ければ地域別の基本CSV）

作る指数（shisu/indicators.py の台帳に登録。0 は欠損 = zero_missing）
  過去SP_3走平均・過去SP_5走平均・過去SP_3走最高・過去SP_5走最高・過去ST_3走平均・過去ST_5走平均・過去ST_3走最高・過去ST_5走最高

対象の走の決め方（select_runs）
  1. その馬の走のうち、D − 600日 ≦ 走った日 ＜ D（D = 今走の日）で、その指数（SP か ST）の値がある走。新しい順
     （同じ日なら レースID の大きい順）。取消・除外で走っていないレース・障害は入れない
  2. 今走が2歳戦・3歳限定戦なら、1 の全部
  3. それ以外（古馬戦）は、今走の 芝・ダ／距離／馬場状態3区分（良 / 稍・重 / 不）と比べて、次の順に最初に1走以上
     見つかった段階の走だけを使う（1〜2走しか無くても、その段階の走だけで計算する。0走のときだけ次へ）:
       ① 同じ芝・ダ かつ 距離差400以内 かつ 同じ馬場状態（今走の馬場状態が分からなければ飛ばす）
       ② 同じ芝・ダ かつ 距離差400以内
       ③ 距離差400以内（今走の馬場状態が分かれば、同じ馬場状態も）
       ④ 同じ芝・ダ
       ⑤ 1 の全部
  4. 先頭から3走・5走の平均（小数第2位を四捨五入して小数第1位まで。54.0 は 54）と最高

馬の見分け: 血統登録番号（10桁）を優先し、無ければ正規化した馬名。

使う側
  replace_csv_ver3.py   過去の基本CSVの全馬について計算して master を作る。履歴を master_csv/__horse_perf_history__.csv.gz に残す
  calc_entry_hensachi.py 出馬表の各馬について計算（--baba-turf / --baba-dirt で想定の馬場状態）
  /ingest・ingest.py    ベースCSVの各馬について計算し、TARGET 取込用CSV（ext_indices/past_sp_st/ にもコピー）を出す
"""
from __future__ import annotations

import csv
import datetime as _dt
import gzip
import hashlib
import io
import json
import os
import re
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path
from typing import Callable, Iterable, Iterator

import pandas as pd

from . import config
from .csvio import norm_series
from .finish import parse_finish
from .indicators import parse_source_name
from .normalize import distance_series, split_surface_distance

Logger = Callable[[str], None]

RID = "レースID(新)"
KINDS = ("SP", "ST")
WINDOW_DAYS = 600           # 今走の日から遡る日数（D − 600日 ≦ 走った日 ＜ D）
DIST_TOL = 400              # 距離差の許容（メートル）
MAX_VALUE_DIGITS = 6        # 結果指数の値として読む整数部の桁数の上限（100万以上は壊れた行として飛ばす）
ZERO_SUBSTITUTE = "0.1"     # 平均・最高がちょうど 0 になった馬の値（0 は「対象の走なし」＝欠損の印なので、区別するために置き換える）
RUN_COUNTS = (3, 5)
# 指数名 → (結果指数の種類, 何走, 'avg' | 'max')。並びは台帳・画面の順
INDICATORS: dict[str, tuple[str, int, str]] = {
    f"過去{kind}_{n}走{label}": (kind, n, how)
    for kind in KINDS for how, label in (("avg", "平均"), ("max", "最高")) for n in RUN_COUNTS
}
# 指数名 → ファイル・フォルダの名前（ext_indices/past_sp_st/{key}/{YYYYMMDD}_{key}.csv）
KEYS: dict[str, str] = {name: f"past_{kind.lower()}_{how}{n}" for name, (kind, n, how) in INDICATORS.items()}
KEY_TO_INDICATOR = {v: k for k, v in KEYS.items()}
EXT_FOLDER = "past_sp_st"

BABA_AUTO = "auto"
BABA_VALUES = ("良", "稍・重", "不")
BABA_CHOICES = (BABA_AUTO,) + BABA_VALUES
_BABA3 = {"良": "良", "稍": "稍・重", "稍重": "稍・重", "重": "稍・重", "稍・重": "稍・重", "不": "不", "不良": "不"}

HISTORY_FILE = "__horse_perf_history__.csv.gz"
HISTORY_META = "__horse_perf_history__.json"
HISTORY_VERSION = 2         # 2: 履歴のファイルを UTF-8 に（cp932 で書けない馬名を落とさない）・印に中身の指紋
HISTORY_COLUMNS = ["馬ID", "馬名", "日付", RID, "芝・ダ", "距離", "馬場", "SP", "ST"]
BASE_INDICATORS = ("タイムM", "対戦M")
ALL_REGION = "全場"
# 基本CSVから読む列（これ以外は読まない。10年分の基本CSVを丸ごと読むとメモリを使うため）
BASE_USECOLS = ("レースID(新)", "馬番", "日付S", "血統登録番号", "馬名", "馬名S", "馬名1", "芝・ダ", "芝ダ", "距離",
                "馬場状態", "馬場", "着順", "トラックコード(JV)")


# ---------------------------------------------------------------------------
# 小さな部品
# ---------------------------------------------------------------------------
def _nfkc(v) -> str:
    if v is None or (isinstance(v, float) and v != v) or v is pd.NA:      # NaN・NA は空（"nan" という文字にしない）
        return ""
    return unicodedata.normalize("NFKC", str(v)).strip()


def baba3(v) -> str:
    """馬場状態 → 3区分（良 / 稍・重 / 不）。空・分からない値は ""。"""
    s = re.sub(r"\s+", "", _nfkc(v))
    return _BABA3.get(s, "")


def norm_baba_choice(v) -> str:
    """想定の馬場状態の指定（auto / 良 / 稍・重 / 不）。「自動」「稍重」「不良」「稍」「重」も受ける。知らない値は ValueError。"""
    s = re.sub(r"\s+", "", _nfkc(v))
    if s in ("", BABA_AUTO, "自動", "指定なし"):
        return BABA_AUTO
    b = _BABA3.get(s)
    if b is None:
        raise ValueError(f"想定の馬場状態は {' / '.join(BABA_CHOICES)} のどれかです: {v!r}")
    return b


_NAME_MARK = re.compile(r"^(?:[\(\[（［【〔<＜][^\)\]）］】〕>＞]{1,3}[\)\]）］】〕>＞]|[○□◎▲△☆★◇])+")


def norm_horse_name(v) -> str:
    """馬名の比較用（NFKC・空白除去・先頭の記号（(外)・[地]・○ など）を除く）。"""
    s = re.sub(r"\s+", "", _nfkc(v))
    return _NAME_MARK.sub("", s)


def norm_horse_id(v) -> str:
    """血統登録番号 → 数字10桁。10桁でなければ ""。"""
    s = re.sub(r"\.0+$", "", _nfkc(v))
    s = re.sub(r"\D", "", s)
    return s if len(s) == 10 else ""


def date_ordinal(ymd: str) -> int | None:
    """'YYYYMMDD' → 日の通し番号。読めなければ None。"""
    if len(ymd) != 8 or not ymd.isdigit():
        return None
    try:
        return _dt.date(int(ymd[:4]), int(ymd[4:6]), int(ymd[6:])).toordinal()
    except ValueError:
        return None


def parse_value(v) -> Decimal | None:
    """指数値 → Decimal。負の値も 0 も値。空・数でない・無限大は None。"""
    s = _nfkc(v).replace(",", "")
    if not s:
        return None
    try:
        d = Decimal(s)
    except InvalidOperation:
        return None
    # 無限大・桁あふれ（ID が2つつながった行・1e30 など）は値として読まない。桁数で見る（abs() は巨大な指数であふれる）
    if not d.is_finite() or (d != 0 and d.adjusted() >= MAX_VALUE_DIGITS):
        return None
    return d


def fmt_value(d: Decimal) -> str:
    """小数第1位まで（小数第2位を四捨五入）。54.0 は 54、-0 は 0。"""
    q = d.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    if q == q.to_integral_value():
        return str(int(q))
    return format(q, "f")


def aggregate(values: list[Decimal]) -> dict[tuple[int, str], str]:
    """新しい順の値 → {(3,'avg'), (5,'avg'), (3,'max'), (5,'max')} の文字列。値が無ければどれも ""。"""
    out: dict[tuple[int, str], str] = {}
    for n in RUN_COUNTS:
        part = values[:n]
        if not part:
            out[(n, "avg")] = out[(n, "max")] = ""
            continue
        out[(n, "avg")] = _nonzero(fmt_value(sum(part, Decimal(0)) / Decimal(len(part))))
        out[(n, "max")] = _nonzero(fmt_value(max(part)))
    return out


def _nonzero(text: str) -> str:
    """対象の走があるのに値が 0（丸めて 0）になったときは ZERO_SUBSTITUTE にする。
    0 は台帳で欠損（対象の走が無い馬）の印なので、そのままだと走のある馬が欠損に数えられ、レースごと使えなくなることがある。"""
    return ZERO_SUBSTITUTE if text == "0" else text


# ---------------------------------------------------------------------------
# 結果指数のファイル
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ResultFile:
    kind: str                # 'SP' | 'ST'
    path: Path               # CSV か ZIP
    member: str = ""         # ZIP の中のファイル名（CSV そのものなら ""）

    @property
    def label(self) -> str:
        return f"{self.path.name}:{self.member}" if self.member else self.path.name


def _csv_kind(name: str) -> str | None:
    n = name.casefold()
    if not n.endswith(".csv"):
        return None
    if n.startswith("st_se"):
        return "ST"
    if n.startswith("sp"):
        return "SP"
    return None


def _zip_kind(name: str) -> str | None:
    n = name.casefold()
    if not n.endswith(".zip"):
        return None
    if n.startswith("st_se"):
        return "ST"
    if n.startswith("speed"):
        return "SP"
    return None


def _walk(root: Path) -> Iterator[Path]:
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d.casefold() not in ("__macosx", "__pycache__"))
        for f in sorted(files):
            if not f.startswith("."):
                yield Path(dirpath) / f


def find_result_files(root: Path | str | None = None, log: Logger | None = None) -> list[ResultFile]:
    """result_indices/ の下の結果指数のファイル（相対パスの順。ZIP の中は名前の順）。フォルダが無ければ空。"""
    root = Path(root) if root is not None else config.result_indices_dir()
    log = log or (lambda m: None)
    out: list[ResultFile] = []
    if not root.is_dir():
        return out
    for p in _walk(root):
        kind = _csv_kind(p.name)
        if kind:
            out.append(ResultFile(kind, p))
            continue
        kind = _zip_kind(p.name)
        if not kind:
            continue
        try:
            with zipfile.ZipFile(p) as z:
                names = sorted(n for n in z.namelist() if not n.endswith("/"))
        except (zipfile.BadZipFile, OSError) as e:
            log(f"[WARN] 結果指数: {p.name} を ZIP として読めません（{type(e).__name__}: {e}）")
            continue
        for n in names:
            base = n.replace("\\", "/").rsplit("/", 1)[-1]
            if base.startswith(".") or not base.casefold().endswith(".csv"):
                continue
            if kind == "SP" and not base.casefold().startswith("sp"):
                continue
            out.append(ResultFile(kind, p, n))
    return out


def _decode(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    for enc in ("utf-8", "cp932"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("cp932", errors="replace")


def _read_bytes(rf: ResultFile) -> bytes:
    if not rf.member:
        return rf.path.read_bytes()
    with zipfile.ZipFile(rf.path) as z:
        return z.read(rf.member)


def parse_result_text(text: str) -> tuple[dict[str, Decimal], int, list[str]]:
    """「レースID(新) 18桁, 指数値」の行 → ({ID: 値}, 読めなかった行数, その例)。
    値が空の行は入れない。ID が18桁でない行・値が数でない行は読めなかった行に数える（1行目の見出しは数えない）。"""
    values: dict[str, Decimal] = {}
    bad, examples = 0, []
    for i, row in enumerate(csv.reader(io.StringIO(text))):
        if not row or not any(c.strip() for c in row):
            continue
        rid = re.sub(r"\.0+$", "", _nfkc(row[0]))
        raw = row[1] if len(row) > 1 else ""
        if not (len(rid) == 18 and rid.isdigit()):
            if i == 0 and not re.search(r"\d{6,}", rid):          # 見出しの行
                continue
            bad += 1
            if len(examples) < 3:
                examples.append(f"{i + 1}行目 {rid[:24]!r}")
            continue
        if not _nfkc(raw):
            continue
        v = parse_value(raw)
        if v is None:
            bad += 1
            if len(examples) < 3:
                examples.append(f"{i + 1}行目 値 {str(raw)[:16]!r}")
            continue
        values[rid] = v
    return values, bad, examples


def load_results(files: Iterable[ResultFile], log: Logger | None = None) -> dict[str, dict[str, Decimal]]:
    """結果指数を読む → {'SP': {レースID(新): 値}, 'ST': {…}}。同じ馬の値が複数あれば、後のファイルの値。"""
    log = log or (lambda m: None)
    out: dict[str, dict[str, Decimal]] = {k: {} for k in KINDS}
    n_files = {k: 0 for k in KINDS}
    for rf in files:
        try:
            values, bad, examples = parse_result_text(_decode(_read_bytes(rf)))
        except (OSError, zipfile.BadZipFile, KeyError, csv.Error) as e:
            log(f"[WARN] 結果指数（{rf.kind}）: {rf.label} を読めません（{type(e).__name__}: {e}）")
            continue
        if bad:
            log(f"[WARN] 結果指数（{rf.kind}）: {rf.label} の「レースID(新) 18桁, 指数値」として読めない行 {bad} 件を"
                f"飛ばしました（{', '.join(examples)}）")
        out[rf.kind].update(values)
        n_files[rf.kind] += 1
    for k in KINDS:
        if n_files[k]:
            log(f"[INFO] 結果指数 {k}指数: {n_files[k]} ファイル → {len(out[k])} 頭分")
    return out


# ---------------------------------------------------------------------------
# 基本CSV・出馬表の列 → 計算に使う形
# ---------------------------------------------------------------------------
def _col(df: pd.DataFrame, *names: str) -> pd.Series:
    """names のうち df にある列。複数あれば行ごとに、値のある最初の列（列の名前の違うファイルを縦につないだ表で、
    片方のファイルの行が NaN になっていても、もう片方の名前の列から読む）。NaN は ""。"""
    out = None
    for n in names:
        if n not in df.columns:
            continue
        s = df[n]
        if isinstance(s, pd.DataFrame):          # 同じ見出しが2列あれば最初の列
            s = s.iloc[:, 0]
        s = s.astype(object).where(s.notna(), "")
        out = s if out is None else out.where(out.map(lambda v: str(v).strip() != ""), s)
    return out if out is not None else pd.Series("", index=df.index, dtype=object)


def _rid18(df: pd.DataFrame) -> pd.Series:
    from .ext_indices import _rid18_series
    return _rid18_series(df)


def _dates(df: pd.DataFrame, rid18: pd.Series) -> pd.Series:
    """日付（YYYYMMDD）。レースID(新) の先頭8桁。無ければ 日付S・日付。"""
    d = rid18.str.slice(0, 8)
    for name in ("日付S", "日付"):
        if name in df.columns:
            alt = norm_series(df[name]).str.replace(r"\D", "", regex=True).str.slice(0, 8)
            d = d.where(d != "", alt)
    return d.where(d.str.fullmatch(r"(19|20)\d{6}").fillna(False).astype(bool), "").astype(object)


_AGE_OPEN = re.compile(r"[34]歳?(?:以上|上)|古馬")
_AGE_LIMITED = re.compile(r"[23]歳(?!以上|上)")


def age_limited(age, cls="", race_name="") -> bool:
    """今走が2歳戦・3歳限定戦か。年齢限定（年限定）の列に値があればそれで決める（2歳・3歳 → 限定。3上・3歳以上・4上・
    4歳以上 → 古馬戦）。空なら クラス名・レース名 から（新馬・未勝利、または「2歳」「3歳」を含み「以上・上」が続かない）。"""
    a = re.sub(r"\s+", "", _nfkc(age))
    if a:
        if _AGE_OPEN.search(a) or "上" in a:
            return False
        if _AGE_LIMITED.search(a) or a in ("2", "3"):
            return True
        # 年齢として読めない値（別の内容の列など）は、列が空のときと同じに クラス名・レース名 から
    text = re.sub(r"\s+", "", _nfkc(cls)) + " " + re.sub(r"\s+", "", _nfkc(race_name))
    if _AGE_OPEN.search(text):
        return False
    c = re.sub(r"\s+", "", _nfkc(cls))
    return "新馬" in c or "未勝利" in c or bool(_AGE_LIMITED.search(text))


def target_frame(raw: pd.DataFrame) -> pd.DataFrame:
    """基本CSV・出馬表・ベースCSVの列 → 計算に使う列（index は raw のまま）:
    レースID(新)（18桁。読めなければ ""）・日付・馬ID・馬名・芝・ダ・距離（数。無ければ NaN）・馬場（3区分。列が無ければ ""）・限定。"""
    out = pd.DataFrame(index=raw.index)
    out[RID] = _rid18(raw)
    out["日付"] = _dates(raw, out[RID])
    out["馬ID"] = _col(raw, "血統登録番号").map(norm_horse_id)
    out["馬名"] = _col(raw, "馬名", "馬名S", "馬名1").map(norm_horse_name)
    surface, dist_raw = split_surface_distance(_col(raw, "芝・ダ", "芝ダ"), _col(raw, "距離"))
    out["芝・ダ"] = surface
    out["距離"] = pd.to_numeric(distance_series(dist_raw), errors="coerce")
    out["馬場"] = _col(raw, "馬場状態", "馬場").map(baba3)
    out["限定"] = [age_limited(a, c, n) for a, c, n in zip(_col(raw, "年齢限定", "年限定"), _col(raw, "クラス名", "クラス"),
                                                            _col(raw, "レース名"))]
    return out


def column_notes(columns: Iterable[str], side: str = "") -> list[str]:
    """計算に使う列が無いときの注意書き（ログ用。side = どのファイルか）。
    年齢限定（年限定）が無いと 2歳・3歳限定戦を 新馬・未勝利・レース名でしか見分けられず、馬場状態が無いと段階①を使えず、
    血統登録番号が無いと馬名で見分ける。過去側と出馬表側で列がそろっていないと、同じレースでも対象の走が変わる。"""
    cols = {str(c).strip() for c in columns}
    head = f"{side}: " if side else ""
    out = []
    if not cols & {"年齢限定", "年限定"}:
        out.append(f"[WARN] {head}年齢限定（年限定）の列が無いので、2歳・3歳限定戦は クラス名が 新馬・未勝利 のレースと、クラス名・レース名に"
                   "「2歳」「3歳」のあるレースだけになります（3歳限定の1勝クラス・オープン・重賞は古馬戦として絞り込みます。"
                   "年限定 の列のある出馬表側と条件がずれるので、基本CSVに 年齢限定 の列を入れてください）")
    if not cols & {"馬場状態", "馬場"}:
        out.append(f"[INFO] {head}馬場状態 の列が無いので、今走の馬場状態は指定（想定馬場状態）が無ければ「指定なし」です"
                   "（段階①を飛ばして ② 同じ芝ダ・距離差400以内 から）")
    if "血統登録番号" not in cols:
        out.append(f"[INFO] {head}血統登録番号 の列が無いので、馬は馬名で見分けます")
    return out


def history_rows(raw: pd.DataFrame, results: dict[str, dict[str, Decimal]]) -> pd.DataFrame:
    """基本CSV（過去レース）の行 → 履歴の行（HISTORY_COLUMNS）。SP・ST のどちらの値も無い走、取消・除外で走っていない走、
    障害、レースID(新)・日付が読めない行、馬を見分けられない行（血統登録番号も馬名も無い）は入れない。"""
    t = target_frame(raw)
    ran = parse_finish(_col(raw, "着順"))["denom"] if "着順" in raw.columns else pd.Series(True, index=raw.index)
    track = pd.to_numeric(norm_series(_col(raw, "トラックコード(JV)")), errors="coerce")
    jump = (t["芝・ダ"] == "障") | (track >= 51).fillna(False)
    sp, st = results.get("SP", {}), results.get("ST", {})
    rids = t[RID].tolist()
    t["SP"] = [fmt_raw(sp.get(r)) for r in rids]
    t["ST"] = [fmt_raw(st.get(r)) for r in rids]
    keep = (ran & ~jump & (t[RID] != "") & (t["日付"] != "") & ((t["馬ID"] != "") | (t["馬名"] != ""))
            & ((t["SP"] != "") | (t["ST"] != "")))
    h = t[keep].copy()
    h["距離"] = h["距離"].map(lambda v: "" if pd.isna(v) else str(int(v)))
    return h[HISTORY_COLUMNS].reset_index(drop=True)


def fmt_raw(d: Decimal | None) -> str:
    """結果指数の値をそのまま文字列に（履歴のファイルに書く形。丸めない）。"""
    if d is None:
        return ""
    s = format(d.normalize(), "f")
    return "0" if s in ("-0", "") else s


# ---------------------------------------------------------------------------
# 履歴（馬ごとの走）
# ---------------------------------------------------------------------------
Run = tuple  # (日の通し番号, レースID(新), 芝・ダ, 距離 or None, 馬場, 値)


def _RUN_ORDER(r: Run) -> tuple:
    return (r[0], r[1])


class History:
    """馬ごとの走（新しい順）。SP・ST は別々に持つ（その指数の値がある走だけが母集団）。"""

    def __init__(self, frame: pd.DataFrame):
        self.rows = len(frame)
        self.by_id: dict[str, dict[str, list[Run]]] = {k: {} for k in KINDS}
        self.by_name: dict[str, dict[str, list[Run]]] = {k: {} for k in KINDS}        # その馬名の全部の走
        self.by_name_noid: dict[str, dict[str, list[Run]]] = {k: {} for k in KINDS}   # 血統登録番号の無い行だけ
        cols = [frame[c].tolist() for c in HISTORY_COLUMNS]
        for hid, name, date, rid, surface, dist, baba, sp, st in zip(*cols):
            o = date_ordinal(str(date))
            if o is None:
                continue
            try:
                d = int(float(dist)) if str(dist) != "" else None
            except ValueError:
                d = None
            for kind, raw in (("SP", sp), ("ST", st)):
                v = parse_value(raw)
                if v is None:
                    continue
                run = (o, str(rid), str(surface), d, str(baba), v)
                if hid:
                    self.by_id[kind].setdefault(str(hid), []).append(run)
                elif name:
                    self.by_name_noid[kind].setdefault(str(name), []).append(run)
                if name:
                    self.by_name[kind].setdefault(str(name), []).append(run)
        for kind in KINDS:
            for table in (self.by_id[kind], self.by_name[kind], self.by_name_noid[kind]):
                for runs in table.values():
                    runs.sort(key=_RUN_ORDER, reverse=True)

    def runs(self, kind: str, horse_id: str, name: str) -> list[Run]:
        """この馬の走（新しい順）。
        - 今走に血統登録番号があれば、その番号の走 ＋ 同じ馬名で番号の無い行の走（番号の列が無い期間の基本CSVの分）。
          同じ馬名でも別の番号の行（別の馬）は入れない
        - 今走に番号が無ければ（番号の列の無い出馬表など）、その馬名の全部の走"""
        if not horse_id:
            return self.by_name[kind].get(name, []) if name else []
        own = self.by_id[kind].get(horse_id, [])
        extra = self.by_name_noid[kind].get(name, []) if name else []
        if not extra:
            return own
        if not own:
            return extra
        return sorted(own + extra, key=_RUN_ORDER, reverse=True)


def select_runs(runs: list[Run], day: int, surface: str, dist: float | None, baba: str, limited: bool
                ) -> tuple[list[Run], int]:
    """対象の走（新しい順）と、採用した段階（0 = 対象なし・1〜5 = 古馬戦の段階・9 = 2歳戦・3歳限定戦）。"""
    lo = day - WINDOW_DAYS
    cand = [r for r in runs if lo <= r[0] < day]
    if not cand:
        return [], 0
    if limited:
        return cand, 9

    def near(r: Run) -> bool:
        return dist is not None and r[3] is not None and abs(r[3] - dist) <= DIST_TOL

    def same_surface(r: Run) -> bool:
        return surface != "" and r[2] == surface

    def same_baba(r: Run) -> bool:
        return r[4] == baba

    if baba:
        got = [r for r in cand if same_surface(r) and near(r) and same_baba(r)]
        if got:
            return got, 1
    got = [r for r in cand if same_surface(r) and near(r)]
    if got:
        return got, 2
    got = [r for r in cand if near(r) and (not baba or same_baba(r))]
    if got:
        return got, 3
    got = [r for r in cand if same_surface(r)]
    if got:
        return got, 4
    return cand, 5


@dataclass
class Computed:
    """compute の結果。values: 指数名 → 値（文字列。対象の走が無ければ ""。index は targets と同じ）。"""
    values: dict[str, pd.Series]
    stages: dict[str, dict[int, int]] = field(default_factory=dict)     # 種類 → 段階 → 馬の数
    total: int = 0

    def hit(self, indicator: str) -> int:
        return int((self.values[indicator] != "").sum())

    def lookups(self, rid18: pd.Series) -> dict:
        """指数名 → ExtLookup（形式A。レースID(新) → 値）。値のある馬だけ。"""
        from .ext_indices import ExtLookup
        out = {}
        rids = rid18.tolist()
        for name, s in self.values.items():
            vals = {r: v for r, v in zip(rids, s.tolist()) if r and v}
            out[name] = ExtLookup(name, "A", vals, None, [])
        return out


def resolve_baba(targets: pd.DataFrame, baba_turf: str = BABA_AUTO, baba_dirt: str = BABA_AUTO) -> pd.Series:
    """今走の馬場状態3区分。指定（良 / 稍・重 / 不）があれば、その芝・ダのレースはその値。auto なら列の値（無ければ ""）。"""
    turf, dirt = norm_baba_choice(baba_turf), norm_baba_choice(baba_dirt)
    b = targets["馬場"].astype(object)
    if turf != BABA_AUTO:
        b = b.where(targets["芝・ダ"] != "芝", turf)
    if dirt != BABA_AUTO:
        b = b.where(targets["芝・ダ"] != "ダ", dirt)
    return b


def compute(targets: pd.DataFrame, history: History, baba_turf: str = BABA_AUTO, baba_dirt: str = BABA_AUTO,
            kinds: Iterable[str] = KINDS) -> Computed:
    """targets（target_frame の結果）の各行について8指数を計算する。"""
    kinds = [k for k in KINDS if k in set(kinds)]
    baba = resolve_baba(targets, baba_turf, baba_dirt).tolist()
    days = [date_ordinal(str(d)) for d in targets["日付"].tolist()]
    ids, names = targets["馬ID"].tolist(), targets["馬名"].tolist()
    surfaces = targets["芝・ダ"].tolist()
    dists = [None if pd.isna(v) else float(v) for v in targets["距離"].tolist()]
    limited = targets["限定"].tolist()
    cols: dict[str, list[str]] = {name: [] for name, (k, _n, _h) in INDICATORS.items() if k in kinds}
    stages: dict[str, dict[int, int]] = {k: {} for k in kinds}
    for i in range(len(targets)):
        day = days[i]
        for kind in kinds:
            if day is None:
                picked, stage = [], 0
            else:
                picked, stage = select_runs(history.runs(kind, ids[i], names[i]), day, surfaces[i], dists[i], baba[i],
                                            bool(limited[i]))
            stages[kind][stage] = stages[kind].get(stage, 0) + 1
            agg = aggregate([r[5] for r in picked])
            for n in RUN_COUNTS:
                for how, label in (("avg", "平均"), ("max", "最高")):
                    cols[f"過去{kind}_{n}走{label}"].append(agg[(n, how)])
    values = {name: pd.Series(v, index=targets.index, dtype=object) for name, v in cols.items()}
    return Computed(values, stages, len(targets))


STAGE_LABELS = {0: "対象の走なし", 1: "①同じ芝ダ・距離差400以内・同じ馬場", 2: "②同じ芝ダ・距離差400以内",
                3: "③距離差400以内", 4: "④同じ芝ダ", 5: "⑤600日以内の全走", 9: "2歳・3歳限定（600日以内の全走）"}


def stage_text(stages: dict[int, int]) -> str:
    return "・".join(f"{STAGE_LABELS[s]} {n}" for s, n in sorted(stages.items()) if n)


# ---------------------------------------------------------------------------
# 履歴の元（基本CSV・結果指数）とキャッシュ
# ---------------------------------------------------------------------------
def base_csv_paths(csv_dir: Path | str | None = None) -> list[Path]:
    """履歴を作る基本CSV。全場_タイムM.csv → 全場_対戦M.csv の順に、あれば1本だけ。どちらも無ければ、
    地域ごとの基本CSV（タイムM を優先、無ければ 対戦M）を全部。"""
    d = Path(csv_dir) if csv_dir is not None else config.csv_dir()
    if not d.is_dir():
        return []
    best: dict[str, tuple[int, Path]] = {}
    for p in sorted(d.glob("*.csv")):
        region, indicator = parse_source_name(p.stem)
        if indicator not in BASE_INDICATORS:
            continue
        prio = BASE_INDICATORS.index(indicator)
        if region not in best or prio < best[region][0]:
            best[region] = (prio, p)
    if ALL_REGION in best:
        return [best[ALL_REGION][1]]
    return [p for _prio, p in (best[r] for r in sorted(best))]


def read_base_columns(path: Path | str) -> pd.DataFrame:
    """基本CSVから、履歴に要る列だけを文字列で読む（cp932 → utf-8-sig → utf-8）。"""
    last: Exception | None = None
    for enc in ("cp932", "utf-8-sig", "utf-8"):
        try:
            return pd.read_csv(path, encoding=enc, dtype=str, keep_default_na=False,
                               usecols=lambda c: str(c).strip() in BASE_USECOLS)
        except UnicodeDecodeError as e:
            last = e
    raise last  # type: ignore[misc]


def file_crc(path: Path) -> int:
    """ファイルの中身の CRC32（少しずつ読む）。"""
    crc = 0
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            crc = zlib.crc32(block, crc)
    return crc


def source_signature(bases: list[Path], files: list[ResultFile]) -> str:
    """履歴の元（基本CSV・結果指数のファイル）の名前・大きさ・中身の CRC32 から作る印。元が変わったら履歴を作り直すために比べる。
    大きさが同じまま値だけ直したファイル（訂正版の結果指数・馬場状態の直し）も見分ける。更新時刻は、コピー・Docker の
    マウントで変わるので使わない。"""
    h = hashlib.sha1()
    h.update(f"v{HISTORY_VERSION}|{WINDOW_DAYS}\n".encode())
    for p in bases:
        h.update(f"B|{p.name}|{p.stat().st_size}|{file_crc(p)}\n".encode())
    seen: set[Path] = set()
    for rf in files:
        if rf.path in seen:
            continue
        seen.add(rf.path)
        h.update(f"R|{rf.kind}|{rf.path.name}|{rf.path.stat().st_size}|{file_crc(rf.path)}\n".encode())
    return h.hexdigest()


def build_history_frame(bases: list[Path], files: list[ResultFile], log: Logger | None = None) -> pd.DataFrame:
    """基本CSVと結果指数から履歴の表（HISTORY_COLUMNS）を作る。同じ レースID(新) の行は後のファイルの行。"""
    log = log or (lambda m: None)
    results = load_results(files, log)
    frames = []
    for p in bases:
        raw = read_base_columns(p)
        raw.columns = [str(c).strip() for c in raw.columns]
        if not {"馬場状態", "馬場"} & set(raw.columns):
            log(f"[WARN] 過去走の履歴: {p.name} に 馬場状態 の列が無いので、過去走の馬場状態が分かりません"
                "（「同じ馬場状態」の段階 ①・③ には当たらず、② 同じ芝ダ・距離差400以内 からになります）")
        if "血統登録番号" not in raw.columns:
            log(f"[INFO] 過去走の履歴: {p.name} に 血統登録番号 の列が無いので、馬は馬名で見分けます")
        h = history_rows(raw, results)
        log(f"[INFO] 過去走の履歴: {p.name} {len(raw)} 行 → 結果指数のある走 {len(h)} 行")
        frames.append(h)
    if not frames:
        return pd.DataFrame(columns=HISTORY_COLUMNS, dtype=object)
    out = pd.concat(frames, ignore_index=True)
    return out.drop_duplicates(subset=[RID], keep="last").reset_index(drop=True)


def save_history(frame: pd.DataFrame, master_dir: Path | str, signature: str) -> Path:
    """履歴の表と印を master_csv/ に書く（一時ファイルから置き換える）。"""
    d = Path(master_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / HISTORY_FILE
    tmp = d / (HISTORY_FILE + ".part")
    # 内部のファイルなので UTF-8 で書く（cp932 で書けない文字のある馬名を落とすと、馬名で引けなくなる）。時刻は入れない
    data = frame[HISTORY_COLUMNS].to_csv(index=False).encode("utf-8")
    tmp.write_bytes(gzip.compress(data, compresslevel=6, mtime=0))
    os.replace(tmp, path)
    meta = d / HISTORY_META
    mtmp = d / (HISTORY_META + ".part")
    mtmp.write_text(json.dumps({"version": HISTORY_VERSION, "signature": signature, "rows": int(len(frame))},
                               ensure_ascii=False), encoding="utf-8")
    os.replace(mtmp, meta)
    return path


def load_history_cache(master_dir: Path | str) -> tuple[pd.DataFrame, str] | None:
    """master_csv/ の履歴（表, 印）。無い・読めない・版が違えば None。"""
    d = Path(master_dir)
    path, meta = d / HISTORY_FILE, d / HISTORY_META
    if not path.exists():
        return None
    sig, rows = "", None
    try:
        m = json.loads(meta.read_text(encoding="utf-8"))
        if m.get("version") != HISTORY_VERSION:
            return None
        sig = str(m.get("signature") or "")
        rows = m.get("rows")
    except (OSError, ValueError, AttributeError):
        sig = ""
    try:
        frame = pd.read_csv(path, encoding="utf-8", dtype=str, keep_default_na=False)
    except Exception:
        return None
    if any(c not in frame.columns for c in HISTORY_COLUMNS):
        return None
    if rows is not None and rows != len(frame):           # 途中で切れた・別のファイルと入れ替わった履歴は、元と違うものとして扱う
        sig = ""
    return frame[HISTORY_COLUMNS], sig


def has_result_files(result_dir: Path | str | None = None) -> bool:
    return bool(find_result_files(result_dir))


def get_history(csv_dir: Path | str | None = None, result_dir: Path | str | None = None,
                master_dir: Path | str | None = None, log: Logger | None = None, rebuild: bool = False,
                save: bool = True) -> History | None:
    """履歴を用意する。戻り値が None なら、結果指数が無い（この8指数は作れない）。

    - 元（基本CSV・結果指数のファイル）があり、master_csv/ の履歴の印がそれと同じなら、履歴のファイルを読む
    - 印が違う・履歴が無い・rebuild=True なら、元から作り直して master_csv/ に書く（save=False なら書かない）
    - 元が無く（別の場所に master_csv だけ持ってきた など）履歴のファイルだけあれば、それを使う"""
    log = log or (lambda m: None)
    master_dir = Path(master_dir) if master_dir is not None else config.master_dir()
    files = find_result_files(result_dir, log)
    bases = base_csv_paths(csv_dir) if files else []
    cached = load_history_cache(master_dir)
    if files and bases:
        sig = source_signature(bases, files)
        if cached is not None and cached[1] == sig and not rebuild:
            log(f"[INFO] 過去走の履歴: {HISTORY_FILE}（{len(cached[0])} 走）を使います")
            return History(cached[0])
        if cached is not None and not rebuild:
            log("[INFO] 過去走の履歴: 基本CSV・結果指数のファイルが変わったので作り直します")
        frame = build_history_frame(bases, files, log)
        if save:
            try:
                save_history(frame, master_dir, sig)
                log(f"[INFO] 過去走の履歴 {len(frame)} 走を {master_dir.name}/{HISTORY_FILE} に書きました")
            except OSError as e:
                log(f"[WARN] 過去走の履歴を {master_dir} に書けませんでした（{type(e).__name__}: {e}）。計算は続けます")
        return History(frame)
    if cached is not None:
        why = "結果指数のファイルが無い" if not files else "基本CSV（csv_files の タイムM・対戦M）が無い"
        log(f"[INFO] 過去走の履歴: {why}ので、前に作った {HISTORY_FILE}（{len(cached[0])} 走）を使います")
        return History(cached[0])
    if (master_dir / HISTORY_FILE).exists():
        log(f"[WARN] 過去走の履歴 {master_dir.name}/{HISTORY_FILE} を読めません（壊れている・前の版のファイル）。作り直す元"
            "（result_indices の結果指数と csv_files の基本CSV）も無いので、過去SP・ST の8指数は付けません。"
            "replace_csv_ver3.py を回して作り直してください")
    if files and not bases:
        log("[WARN] 結果指数（result_indices）はありますが、過去走の履歴を作る基本CSV（csv_files の {地域}_タイムM.csv / "
            "{地域}_対戦M.csv）がありません。過去SP・ST の8指数は作りません")
    return None


# ---------------------------------------------------------------------------
# 想定の馬場状態の設定（/ingest の画面で選んだ値を、calc_entry_hensachi が既定として読む）
# ---------------------------------------------------------------------------
def load_settings(path: Path | str | None = None) -> dict[str, str]:
    """{'baba_turf': …, 'baba_dirt': …}。ファイルが無い・読めない・値が違えば auto。"""
    p = Path(path) if path is not None else config.past_perf_settings_path()
    out = {"baba_turf": BABA_AUTO, "baba_dirt": BABA_AUTO}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return out
    if isinstance(data, dict):
        for k in out:
            try:
                out[k] = norm_baba_choice(data.get(k))
            except ValueError:
                pass
    return out


def save_settings(baba_turf: str, baba_dirt: str, path: Path | str | None = None) -> Path:
    p = Path(path) if path is not None else config.past_perf_settings_path()
    data = {"baba_turf": norm_baba_choice(baba_turf), "baba_dirt": norm_baba_choice(baba_dirt)}
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_name(p.name + ".part")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, p)
    return p


def baba_text(baba_turf: str, baba_dirt: str) -> str:
    def one(v: str) -> str:
        return "自動" if v == BABA_AUTO else v
    return f"芝 {one(norm_baba_choice(baba_turf))}・ダ {one(norm_baba_choice(baba_dirt))}"


__all__ = [
    "INDICATORS", "KEYS", "KEY_TO_INDICATOR", "EXT_FOLDER", "KINDS", "WINDOW_DAYS", "DIST_TOL", "BABA_AUTO", "BABA_CHOICES",
    "HISTORY_FILE", "HISTORY_META", "HISTORY_COLUMNS", "ResultFile", "History", "Computed",
    "find_result_files", "load_results", "parse_result_text", "target_frame", "history_rows", "select_runs", "aggregate",
    "compute", "resolve_baba", "age_limited", "baba3", "norm_baba_choice", "base_csv_paths", "source_signature",
    "build_history_frame", "save_history", "load_history_cache", "get_history", "has_result_files",
    "load_settings", "save_settings", "baba_text", "stage_text", "column_notes",
]
