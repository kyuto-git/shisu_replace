"""過去SP・ST指数（結果指数 SP指数・ST指数 から計算する「過去3走／5走の平均・最高」の8指数）。ネットワークには出ない。

入力: ベースCSV（レースID(新) と、馬名（または 血統登録番号）・芝ダ・距離・年限定・クラス・レース名 のある形。
      TARGET の出馬表CSV（entries_in/ に置く形）をそのまま選べる）
      結果指数は result_indices/、過去走の履歴は master_csv/__horse_perf_history__.csv.gz（無ければ csv_files の基本CSVから作る）
想定の馬場状態（芝・ダ それぞれ 自動 / 良 / 稍・重 / 不）: 自動 ならベースCSVの 馬場状態 の列（無ければ指定なし）
出力: past_sp_st/past_{sp,st}_{avg,max}{3,5}/{YYYYMMDD}_past_….csv（TARGET 取込用）と {元名}_{key}_merged.csv
計算の規則は shisu/past_perf_indices.py（replace・calc_entry_hensachi と同じ関数）。
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from .. import past_perf_indices as PP
from .common import RID, SOURCE_FILE, Logger, Outputs, default_log, write_outputs

NAME = "past"
LABEL = "過去SP・ST指数"


def run(base: pd.DataFrame, out_dir: Path, log: Logger = default_log, baba_turf: str = PP.BABA_AUTO,
        baba_dirt: str = PP.BABA_AUTO, csv_dir=None, result_dir=None, master_dir=None, settings_path=None) -> Outputs:
    turf, dirt = PP.norm_baba_choice(baba_turf), PP.norm_baba_choice(baba_dirt)
    history = PP.get_history(csv_dir, result_dir, master_dir, log)
    if history is None or not history.has_results:
        raise RuntimeError("結果指数がありません。result_indices/ に SP指数（SP*.csv・speed*.zip）・ST指数（st_se*.csv・st_se*.zip）を"
                           "置き、csv_files/ に過去の基本CSV（全場_タイムM.csv など）がある状態で実行してください")
    t = PP.target_frame(base)
    if not ((t["馬ID"] != "") | (t["馬名"] != "")).any():
        raise RuntimeError("ベースCSVに 馬名（または 血統登録番号）の列がありません。馬名・芝ダ・距離・年限定 のある出馬表CSV"
                           "（entries_in/ に置く形）をベースCSVに選んでください")
    if not (t["芝・ダ"] != "").any() or not t["距離"].notna().any():
        log(f"[WARN] {LABEL}: ベースCSVに 芝ダ・距離 の列が無い（空）ので、条件での絞り込みができません"
            "（その条件を使う段階を飛ばします。芝ダ・距離の両方が無ければ、どのレースも600日以内の全部の走を使います）")
    # 列の注意書きはファイルごと（縦につないだ表の列は全部のファイルの和なので、1本だけ列が無くても分かるように）
    groups = base.groupby(SOURCE_FILE, sort=False) if SOURCE_FILE in base.columns else [("ベースCSV", base)]
    for src, g in groups:
        for note in PP.column_notes(g.dropna(axis=1, how="all").columns, f"ベースCSV {src}"):
            if "馬場状態" not in note or (turf == PP.BABA_AUTO and dirt == PP.BABA_AUTO):
                log(note)
    # レース単位（16桁）の レースID(新) は 馬番 を付けた18桁で書く（16桁のままだと TARGET 取込用CSVが1レース1頭に潰れる）
    base = base.copy()
    base[RID] = t[RID].where(t[RID] != "", base[RID])
    log(f"[INFO] {LABEL}: 想定の馬場状態 {PP.baba_text(turf, dirt)}・{len(t)} 頭")
    comp = PP.compute(t, history, turf, dirt)
    for kind in PP.KINDS:
        log(f"[INFO] {LABEL}（{kind}指数）: {PP.stage_text(comp.stages[kind])}")
    res = Outputs()
    for name, key in PP.KEYS.items():
        res.extend(write_outputs(base, comp.values[name], Path(out_dir) / key, key, log, merged_suffix=key))
    try:
        p = PP.save_settings(turf, dirt, settings_path)
        res.notes.append(f"想定の馬場状態（{PP.baba_text(turf, dirt)}）を {p.name} に保存しました"
                         "（calc_entry_hensachi.py の --baba-turf / --baba-dirt を省いたときの既定になります）")
    except OSError as e:
        log(f"[WARN] {LABEL}: 想定の馬場状態を保存できませんでした（{type(e).__name__}: {e}）")
    return res
