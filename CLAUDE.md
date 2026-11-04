# CLAUDE.md — shisu_replace（競馬 外部指数の集計・照合システム）

最終更新: 2026-11-04（全ファイル精読＋サンプルCSV・合成データでの実測に基づく。コード改修は未実施）

## この文書の扱い

- **コードが正**。`README_RECAP.md`（2025-10-11版）と `Readme.txt` は運用手順として有用だが、現行コードと食い違う箇所がある（→ §6）。
- 本番データ（`csv_files/` `foragg_csv/` `replace_csv/` `racing.db` など）は `.gitignore` で除外されている。リポジトリにあるデータは **出馬表側サンプル2本だけ**:
  `entries_in/shisu_20260531_1.csv`（入力）／`entries_out/entries_merged_hensachi.csv`（統合出力）。
  過去レース側（csv_files / foragg）のサンプルは無いので、その列仕様は**コードからの推定**。
- §5 は「コード上で確認した不整合・リスク」の一覧で、改修候補。**まだ一つも直していない。**

---

## 0. 概要

TARGET frontier JV で出力した「過去レース×外部指数」CSVを、条件文字列 `結合データ`（例: `東京×芝×16-18×定量×1・2_勝×3`）ごとに集計して成績表（勝率・複勝率・掲示板率・回収値・ベイズ推定値）を作る。
当日の「出馬表×外部指数」CSVの各馬について同じ形式のキーを組み立てて成績表と突き合わせ、Flask UI で「絶対軸／軸候補／消し候補／相手候補」を表示する。

---

## 1. システム全体の処理フロー

### 1.1 前工程（手作業。Readme.txt 冒頭）

- **過去データ**: TARGET ★8タブ → 外部指数読込 → 「レース一覧★画面イメージ(CSV)」で出力（頭数10以上・障害除外で抽出）→ `csv_files/{地域}_{指数名}.csv`
  - 地域: `主５場（中山、東京、中京、京都、阪神）` / `ローカル５場（札幌、函館、福島、新潟、小倉）` / 各場名（`札幌` など）
  - 指数名: カツラキ, Gold, BC, ZI, オメガ指数, かよちん指数, keibaAI指数, 吉馬_SP能力値, 吉馬_先行力, 吉馬_SP信頼, 吉馬_SP調整, 吉馬_SP最大, 吉馬_末脚力, SAV, ST指数, 合計値, 展開, 仕上り, 先行, 追走, 持久, 持続, 瞬発, 想定前半, 想定後半, タイムM, 対戦M
  - **ファイル名で処理が切り替わる**（ZI特例・符号反転・新馬除外はファイル名で判定。DB上の指数名もファイル名から取る）。
- **出馬表**: TARGET 出馬表分析 ★4タブ → 外部指数1〜4＋マイニングを読込 → 全選択 → CSV出力 → `entries_in/shisu_YYYYMMDD_N.csv`（N=1〜5。5 はマイニング・カツラキ・持久）

### 1.2 実行順（Docker。イメージ名は Readme.txt に合わせて `csv_processor_shisu`）

```bash
docker build -t csv_processor_shisu .
# [A] 過去データ系
docker run --rm -v "$(pwd)":/app --entrypoint python csv_processor_shisu /app/replace_csv_ver3.py
docker run --rm -v "$(pwd)":/app --entrypoint python csv_processor_shisu /app/aggregate_stats_ver2.py
docker run --rm -v "$(pwd)":/app csv_processor_shisu python /app/build_db.py --mode foragg   # 既定は both
# [B] 出馬表系（[A] の unified_all が DB にあること）
docker run --rm -v "$(pwd)":/app --entrypoint python csv_processor_shisu /app/calc_entry_hensachi.py --merge-one
docker run --rm -v "$(pwd)":/app csv_processor_shisu python /app/build_db.py --mode entries
# [C] UI（http://localhost:8000）
docker run --rm -p 8000:8000 -v "$(pwd)":/app -e DB_PATH=/app/racing.db csv_processor_shisu python /app/app.py
```

### 1.3 データの流れ

```
[A] 過去レース
csv_files/*.csv (SJIS, 1行=1頭)
  └─ replace_csv_ver3.py ─┬─> replace_csv/processed_*.csv   確認用: レースID(新), 結合データ[, 指数順位帯]
                          └─> foragg_csv/foragg_{tag}_{元名}.csv   素材版: 1行=1頭 (入力1本→80本)
  └─ aggregate_stats_ver2.py  foragg_csv/foragg_*.csv を【上書き】→ 集計版: 1行=1結合データ (件数≥5)
  └─ build_db.py --mode foragg|both
        racing.db: foragg_foragg_*（CSV1本=1テーブル）, unified_all（全foragg縦結合）, __tables_meta__

[B] 出馬表
entries_in/shisu_YYYYMMDD_N.csv (cp932)
  └─ calc_entry_hensachi.py ─> entries_out/{stem}_hensachi.csv（個別） / entries_merged_hensachi.csv（--merge-*）
  └─ build_db.py --mode entries|both（unified_all を参照）
        racing.db: entries_{stem}, entries_all, entries_xjoin_{stem}（各馬×指数×family×キー と unified_all の突合結果）

[C] app.py (Flask :8000)
  /              横断検索 … foragg_* テーブルを直接読む（unified_all ではない）
  /entries       出馬表のレース一覧 … entries_*（xjoin除く）
  /entries/race  出馬表詳細
  /entries/key   unified_all を 結合データ 完全一致で表示
  /entries/hits  条件ヒット馬 … entries_xjoin_* があれば高速パス、無ければ entries_* × unified_all をその場で照合
```

### 1.4 依存関係と再実行ルール

- **replace と aggregate はペアで回す**。aggregate は foragg を上書きするので、集計済みファイルに再実行しても `[SKIP]`（着順列が無い）になるだけ。集計条件を変えたら replace からやり直す。
- aggregate の前に build_db を回すと素材版（1行=1頭）がDBに入り、unified_all の成績列が NULL になる。
- `結合データ` の構成・距離レンジ・正規化を変えたら **[A][B] とも全段再実行**（キーは両側で一字一句同じ文字列でないと当たらない）。
- `build_db --mode foragg|both` は unified_all を「**今回 foragg_csv/ にあるCSVだけ**」で作り直す。古い `foragg_*` テーブルは DB に残る（`--prune` / `--fresh` で消える）。このため `/`（foragg_* を直接読む）と `/entries/hits`（unified_all）で母集団がずれることがある。
- foragg_csv/ に古いCSVが残っていれば、それも取り込まれる。
- entries_xjoin_* は作成時点の unified_all のスナップショット。foragg を作り直したら entries も再構築する。

---

## 2. 各Pythonファイルの役割と主要ロジック

### 2.1 replace_csv_ver3.py — 置換・正規化・偏差値・結合キー生成（素材出力）

- 入出力: `./csv_files/*.csv`（**相対パス**。cwd=/app 前提）→ `./replace_csv/processed_*.csv`, `./foragg_csv/foragg_*.csv`（Shift_JIS）
- `process_one_file` の処理順:
  1. `smart_read_csv`: cp932→shift_jis→utf-8-sig→utf-8→latin1 を、通常／errors=ignore／不正行スキップの3段で試す。`dtype=str`
  2. `basic_clean`: 全列文字列化＋空白除去、重量種別 `別定→定量`、クラス名のグループ化、`馬齢×(1・2_勝|準OP・OP)→1勝・OP`、コース区分 A〜D を空に、指数順位を整数文字列化（`5.0→5`）、`枠番グループ` 生成（§3.8）
  3. `apply_place_distance_rules`: 距離レンジ統合（§3.8）
  4. `apply_index_missing_policy`: 同一レース（`レースID(新/馬番無)`。無ければ `レースID(新)` の末尾2桁を落とす）で指数欠損が **2頭以上ならレースごと除外**、**1頭なら**その馬の指数順位を空にする
  5. `add_hensachi`: レース内で偏差値 = (x−平均)/母標準偏差×10+50 を四捨五入 → 偏差値帯
     - ファイル名に `ZI`: 0/欠損 → 同レース非ゼロ平均で置換、反転しない、新馬は NaN
     - ファイル名に `タイムM/想定前半/想定後半/展開`（`3F` を除去して判定、ZI以外）: 符号反転
     - 有効値<2 または std=0 → NaN
     - ファイル名に ZI か吉馬6指数（`_SHINBA_EXCLUDE_KEYS`）: 新馬は NaN
  6. **頭数≥10 のレースだけ残す**（偏差値は除外前の全頭で計算済み）
  7. `add_return_contrib`: 単勝的中/複勝的中/単勝回収寄与/複勝回収寄与 を作るが、**foragg には出力されない**（§5）
  8. 出力（`build_joined` → `export_foragg` / `export_processed`）
     - 非band 10 family（§3.9）× {全件, `odds10u_`, `odds5p_`, `odds10_50_`} = 40本
     - band 5 family × 帯スキーム {`rankband_s1`, `rankband_s2`}（環境変数 `RANKBAND_SCHEMES`、既定 `s1,s2`）× 同4種 = 40本
     - → **入力1本あたり foragg 80本、processed 81本**（代表の `processed_{元名}` = normal を追加）
     - ファイル名: `foragg_{tag}_{元名}`。tag 例: `normal`, `odds10u_rankB`, `rankband_s1_normal_band`, `odds5p_rankband_s2_rankJ_band`
- オッズフィルタは**過去レースの確定単勝オッズ**で抽出: `odds10u_` <10.0 / `odds5p_` ≥5.0 / `odds10_50_` 10以上50未満
- `export_foragg`: ZI・吉馬ファイルは新馬行を除外 → `レースID(新)`（band時は＋`指数順位帯`）で元データと merge → NaN→"" → `sanitize_df_for_sjis`（NFKC正規化・ダッシュ/チルダ統一・SJIS非対応文字の削除）→ Shift_JIS 保存
- 帯スキーム（1頭1帯、行は増えない `assign_fixed_rankband`）:
  - s1: `1~3位 / 4~6位 / 7~9位 / 10位~`
  - s2: `1~5位 / 6~10位 / 11位~`
  - 旧方式（`RANK_BANDS` で行を増殖させる `make_rankband_df`）はコメントアウト済みで未使用

### 2.2 aggregate_stats_ver2.py — 集計＋ベイズ推定（foragg を上書き）

- 入力: `/app/foragg_csv/foragg_*.csv`（**絶対パス固定**、引数なし）。`レースID(新)`（代替 `レースID(新/馬番無)`, `場 R`）・`結合データ`・`着順` が無いファイルは SKIP（＝集計済み）
- 重複排除: `[レースID, 結合データ, (指数順位帯), 馬番|馬名|馬名S の最初に存在する1列]`。馬識別列が無ければ `枠番,騎手,着順,単勝オッズ,複勝配当` で代用
- 着順: `取消|除外` → 分母から除外。`失格|中止` → 分母に含め、着順=頭数（無ければ18。1〜18にクリップ）。数値化できない値は NaN（分母には残る）
- 集計単位: `結合データ` のみで groupby
  - 件数 n、勝/2着/3着/複勝(≤3着)/掲示板(≤5着) の頻度%
  - **推定値（括弧内・`提案*`）**: 既定 `AGG_PROP_MODE=lower` → 事後分布 Beta(k+0.5, n−k+0.5) の**下側10%点**（片側90%下限）。
    **複勝率だけは頻度≤20%（`PLC_SWITCH_THRESHOLD_PCT`）なら上側90%点**を使う。`mean` にすると Jeffreys 事後平均 (k+0.5)/(n+1)
  - scipy が無ければ（Docker既定は無い）正規近似＋[0,1]クリップで代用
  - 平均着順: 失格/中止=頭数 を含めた平均
  - 単勝回収値 = Σ(1着の単勝配当)/n、複勝回収値 = Σ(3着内の複勝配当)/n（100円均等買い）
  - 単勝適正回収値 = 100×Σ(w×単勝配当/100×的中)/Σw、w=1/単勝オッズ（オッズ≤0やNaNは重み無し）。実質「実勝利数 ÷ オッズから見た期待勝利数 ×100」
  - 掲示板率は頻度のみ（推定なし）
  - `件数 < MIN_SAMPLE(5)` の行は出力しない
- 出力: 入力ファイルを**上書き**（Shift_JIS。失敗時は無指定=UTF-8 で書く）
- 環境変数: `AGG_PROP_MODE`(lower|mean), `AGG_LOWER_Q`(0.10), `AGG_UPPER_Q`(0.90), `AGG_PRIOR_ALPHA/BETA`(0.5), `PLC_SWITCH_THRESHOLD_PCT`(20), `AGG_RANKBAND_SCHEMES`(s1/s2 の選別。空=全件), `AGG_FORCE_APPEND_BAND_TO_KEY`(0/1、古いforagg互換)

### 2.3 build_db.py — SQLite 構築＋出馬表の事前突合（xjoin）

- CLI: `--mode both|foragg|entries`（既定 both）, `--fresh`（DBファイル削除）, `--prune`, `--no-xjoin`, `--foragg-dir`, `--entries-dir`, `--db-path`（既定 `/app/racing.db`。**環境変数 DB_PATH は読まない**）
- 環境変数: `XJOIN_PAGE_SIZE`(800), `XJOIN_BATCH_SIZE`(2000), `XJOIN_FAMILIES`(カンマ区切りで family 限定), `XJOIN_ONE_PER_BASE_FAM`(1でヒット上位1件のみ), `XJOIN_STRICT`(読むが未使用)
- 開始時に `PRAGMA journal_mode=WAL; synchronous=NORMAL`、`__tables_meta__` を作成し**全行削除**
- foragg 取込（`load_foragg_tables`）: `foragg_csv/*.csv` を全件（ファイル名は問わない）
  - `勝率/連対率/複勝率/2着率/3着率` から `*_val` を**作り直す**: 括弧内の数値（＝推定値）を優先、括弧が無ければ先頭の数値（`extract_bayes_percent`）。`掲示板率_val` は既にあればそのまま
  - テーブル名 = `"foragg_" + sanitize(ファイル名)` → 実際は **`foragg_foragg_{tag}_...`（接頭辞が二重）**。`sanitize` は `\w` 以外を `_` にする（日本語・全角数字は残る）。例: `foragg_foragg_rankband_s1_normal_band_主５場_中山_東京_中京_京都_阪神__カツラキ`
  - 索引: 場所, 芝・ダ, 距離, 芝(内・外), 芝(内外), 結合データ, 各 `*_val`, 指数順位帯
- `unified_all`: 毎回 DROP→CREATE（28列、§3.7）→ 各 foragg テーブルから `INSERT … SELECT`（無い列は NULL）＋索引
- entries 取込（`load_entries_tables`）: `entries_out/*.csv` → `normalize_entries_df`（§3.8）→ テーブル `entries_{sanitize(ファイル名)}`
- `entries_all`: 全 entries の主要14列を縦結合（app.py では未使用）
- `build_entries_xjoin_tables`（`--no-xjoin` でなければ）: entries テーブルごとに `entries_xjoin_{同名}` を作成
  - 指数 base = `X_偏差値` と `X_順位` が両方ある列名 X
  - 各行×base×15 family で `_build_key_candidates`（§3.9）のキー候補を作り、unified_all を**完全一致のみ**で検索
  - 対象テーブルは `instr(テーブル名, "_{family}_") > 0 AND instr(テーブル名, 指数名候補) > 0`（部分一致）。`ORDER BY 複勝率_val DESC, 連対率_val DESC LIMIT 50`
  - `INSERT OR IGNORE`（一意索引: source_table, レースID(新/馬番無), 場 R, 馬番, base, family, key, テーブル名）。`*_val` の NULL は 0.0 で保存
- `--prune`（`drop_stale_tables`）: `foragg_%`/`entries_%` のうち**今回作らなかったもの**を DROP（xjoin は対象外）。**`--mode entries --prune` だと foragg_* が全部消える**（§5）

### 2.4 calc_entry_hensachi.py — 出馬表に偏差値・順位・帯を付与

- 入出力: 既定 `/app/entries_in` → `/app/entries_out`（`--in-dir/--out-dir`、旧 `--in_dir/--out_dir` も可）
  - 既定: 個別出力 `{stem}_hensachi.csv`
  - `--merge-one`: 個別＋統合（`entries_merged_hensachi.csv`、`--merge-name` で変更可）
  - `--merge-only`: 統合のみ
- 読込: `cp932→shift_jis→utf-8-sig→utf-8`、`dtype=str, keep_default_na=False`（空欄は "" のまま）
- 互換補完: `芝ダ→芝・ダ`、`クラス→クラス名`、`外回り→芝(内・外)`（外/外回り/True/1→外、内/0/False→内、他は空）を列追加
- 指数列の検出（`INDEX_PATTERNS`、NFKC＋大文字小文字無視）と base 名（`ALIASES`）:
  `GoldAirplane/Gold指数→Gold`, `マイニング値→タイムM`, `対戦型マイニング値→対戦M`、他は列名のまま（ZI, オメガ指数, かよちん指数, keibaAI指数, 吉馬_*, 先行力, 持久力 …）
- 偏差値 `hensachi_numeric`: レース単位（`レースID(新/馬番無)` → `レースID(新)` 末尾2桁除去 → `場 R`）、母標準偏差、round(0)
  - 反転: タイムM, 展開(力), 想定前半/後半(3F), マイニング値
  - ZI: 0→非ゼロ平均、新馬 NaN
  - **std=0 なら全頭 50**（replace 側は NaN）。有効値<2 は NaN
- 順位 `X_順位`: 元ファイルの順位列を優先（明示列 `マイニング順位` 等 → `指数順位N` を指数列の出現順に位置対応 → 指数1種類のときのみ無印 `指数順位`）。無ければ再計算（同値同順位 `method=min`）
- 帯: `X_偏差値帯`（§3.8 と同じ6区分）、`X_順位帯` = `1~2位 / 3~5位 / 6~9位 / 10位~`（**過去側の s1/s2 と区切りが違う** §5）
- 出力列の並び: 各指数列の直後に `X_偏差値, X_偏差値帯, X_順位, X_順位帯` を挿入。Shift_JIS（サニタイズなし）
- 統合 `merge_on_key`: 先頭ファイルの `レースID(新)`（→ `レースID(新/馬番無)` → `場 R`）を index にして後続ファイルの列を足す。**先頭ファイルにある列は NaN のときだけ後続で埋める**（"" は埋めない §5）。後続にしか無い行は落ちる

### 2.5 app.py — Flask UI

- `DB_PATH`（既定 `/app/racing.db`）、`PORT`（既定 8000）、`0.0.0.0` で起動。テンプレートは全て `render_template_string` のインライン HTML
- **`/` 横断検索**: `foragg_%` テーブルを1本ずつ直接 SELECT（各200行まで）
  - 絞込: 場所・芝・ダ・距離・芝(内・外)（選択肢は全foraggテーブルを DISTINCT 走査して作る＝テーブル数が多いと重い）
  - family（`detect_family_and_flags`）／指数（`detect_indicator` = テーブル名の最後の `__` 以降）／オッズ（`_mode_label_from_tname`: both/normal/odds5p/odds10_50/odds10u）
  - プリセット: 軸候補 = `複勝率_val≥80 OR 連対率_val≥60`、消し候補 = `複勝率_val<5`（**件数条件なし**）
  - `*_val` 列見出しクリックでソート（既定 `複勝率_val DESC`）
- **`/entries`**: entries テーブル選択（xjoin を除く `entries_%`。名前順の先頭＝**`entries_all` が既定になる**）、日付列（日付/開催日/RaceDate/年月日。無ければ非表示。あれば最新日を既定）、場所。各レースの先頭行を `COALESCE("場 R","レースID(新)","レースID(新/馬番無)")` 単位で表示
- **`/entries/race`**: 1レースの全馬。各指数の順位/偏差値と、base キー `結合データ`（場所×芝・ダ×距離×内外）へのリンク
- **`/entries/key`**: unified_all を `結合データ` 完全一致で最大200行表示（base キーは集計キーより短いので、通常は該当なし）
- **`/entries/hits`**（条件ヒット馬）:
  - 高速パス: `entries_xjoin_{…}` があれば `"場 R" = ridn`（→空白除去→末尾R除去 の順で再試行）で取得
  - 通常パス（xjoin無し）: entries の行から `_build_key_candidates` → `_query_unified_for_key`。①完全一致 → ②帯記号ゆらぎ置換で完全一致 → ③ `結合データ LIKE key%` → ④ `LIKE 場所×芝・ダ×距離%`（**緩いフォールバック** §5）
  - 分類 `classify`（keshi → abs → axis → opp の順で最初に該当したもの1つ。% 閾値は `_detect_scale` で 0–1 格納なら×0.01）:

    | 区分 | 条件（`fv`=複勝率_val, `rv`=連対率_val, `top5`=掲示板率_val） |
    |---|---|
    | (3) 消し | (件数≥20 かつ fv<5 かつ 掲示板率あり かつ top5<10) または (odds10u かつ 件数≥20 かつ fv<20) |
    | (1) 絶対軸 | 件数≥10 かつ **連対率文字列の先頭数値（＝頻度）**≥80 かつ (複勝回収値≥100 または 単勝適正回収値≥100) |
    | (2) 軸候補 | 件数≥10 かつ 掲示板率あり かつ [fv≥80&top5≥90 / rv≥60&top5≥90 / fv≥70&top5≥80&複勝回収≥100 / rv≥50&top5≥80&複勝回収≥100] のいずれか |
    | (4) 相手 | 件数≥30 かつ 掲示板率あり かつ [fv≥60&top5≥70 / rv≥50&top5≥70 / fv≥33&複勝回収≥150&top5≥50] のいずれか |

  - fv/rv は DB の `*_val` ＝**推定値**（下側10%点。複勝率は頻度≤20%なら上側90%点）。つまり軸は保守側の下限、消しは上限で判定している。top5 と絶対軸の連対率は**頻度**
  - 表示順: オッズモード（通常→odds10u→odds5p→odds10_50）→ 馬番昇順 → 複勝率（消しは昇順、他は降順）
  - オッズモードは「過去にその確定オッズ帯だった馬の成績」。当日の馬のオッズとは無関係に全馬へ表示される
- `/healthz`: DB 接続確認
- 定数のうち `AXIS_CNT_MIN_C`, `AXIS_FV_C`, `KESHI_CNT_MIN` は未使用（README_RECAP の「件数20 & 複勝67%」は現行では効いていない）

### 2.6 旧版（参照のみ。現行フローでは使わない）

- `replace_csv.py`（ver1）: `/app` 絶対パス、processed_* 10種のみ。キー順が `場所×重量種別×クラス名×(内外)×芝・ダ×距離×…` で **ver3 と非互換**。重量種別 `別定/ハンデ→別ハ`
- `aggregate_stats.py`（旧集計。docstring の名前は ver2 だが中身は旧版）: Jeffreys 事後平均、提案* = Laplace(Beta(1,1))、単勝≥3.0 の `foragg_odds3p_*` を別出力
- `replace_csv_ver2.py` は Readme.txt に説明があるが、リポジトリには無い

---

## 3. データ仕様

### 3.1 `csv_files/*.csv`（過去レース入力。サンプル無し、コードからの推定）

| 区分 | 列 |
|---|---|
| 必須相当 | `レースID(新)`, `場所`, `芝・ダ`, `距離`, `頭数`, `指数`, `指数順位`, `着順`, `単勝オッズ`, `単勝配当`, `複勝配当` |
| 任意 | `レースID(新/馬番無)`, `重量種別`, `クラス名`, `芝(内・外)` または `芝(内外)`, `コース区分`, `枠番`, `騎手`, `馬名`/`馬名S` |

- 1ファイル=1指数。指数名はファイル名末尾から判定（列は常に `指数` / `指数順位`）
- `レースID(新)`: 18桁 `YYYYMMDD`+場コード2+回2+日2+R2+馬番2（出馬表サンプルと同形式と推定。例 `202605310502120101`）

### 3.2 foragg 素材版（replace 直後、1行=1頭）

`レースID(新), 結合データ, [指数順位帯], 着順, 単勝オッズ, 単勝配当, 複勝配当,` ＋存在すれば `場所, 芝・ダ, 距離, 芝(内・外), 馬名, 馬名S, 頭数, 頭`
（`馬番` と `芝(内外)` はカンマ抜けで入らない §5）

### 3.3 foragg 集計版（aggregate 後、1行=1結合データ）

| 列 | 型・形式 |
|---|---|
| 結合データ | 文字列（§3.9） |
| 件数 | int（≥5） |
| 勝率, 連対率, 複勝率, 2着率, 3着率 | `"45.00%（47.10%）"`＝頻度%（推定%）。括弧は全角 |
| 掲示板率 | `"80.00%"`（頻度のみ） |
| 勝率_val, 連対率_val, 複勝率_val, 2着率_val, 3着率_val, 掲示板率_val | float（0–100）。**CSVでは頻度** |
| 平均着順, 単勝回収値, 複勝回収値, 単勝適正回収値 | float（小数2桁） |
| 提案勝率, 提案連対率, 提案複勝率, 提案2着率, 提案3着率 | `"47.10%"`（推定値のみ） |
| 場所, 芝・ダ, 距離, 芝(内・外), 芝(内外) | グループ内で最初の非空値 |

列順は `結合データ, 件数, 勝率_val, 勝率, 連対率_val, 連対率, 複勝率_val, 複勝率, 2着率_val, 掲示板率_val, 掲示板率, 2着率, 3着率_val, 3着率, 平均着順, …`

### 3.4 `processed_*.csv`

`レースID(新), 結合データ[, 指数順位帯]`（確認用。下流では未使用）

### 3.5 `entries_in/shisu_YYYYMMDD_N.csv`（サンプル: 348行×27列、2026-05-31 東京12R＋京都12R）

`レースID(新), 場 R, Ｒ, 場所, 芝ダ, 距離, 外回り, クラス, レース名, 年限定, 頭, 枠番, 馬番, 馬名S, 騎手,` ＋指数ブロック
（サンプル _1: `GoldAirplane, 指数順位1, ZI, 指数順位2, オメガ指数, 指数順位3, かよちん指数, 指数順位4, マイニング値, マイニング順位, 対戦型マイニング値, 対戦型マイニング順位`）

- cp932。`場 R` は `東1` `京12` 形式。`外回り` は `外` か空。`年限定` は `３歳` `４歳以上`（全角）。`クラス` は `未勝利, 1勝, 2勝, 3勝, OP(L), ｵｰﾌﾟﾝ, Ｇ１, Ｇ２` など
- 過去側と列名が違う: `芝ダ`/`頭`/`クラス`（過去側は `芝・ダ`/`頭数`/`クラス名`）。重量種別・芝(内・外) の列は無い
- サンプル _1 ではマイニング値・対戦型マイニング値が全行空

### 3.6 `entries_out/*_hensachi.csv`（サンプル: 統合版 348行×92列）

入力列＋各指数の `X_偏差値`（float）, `X_偏差値帯`, `X_順位`, `X_順位帯` ＋末尾に `芝・ダ, クラス名, 芝(内・外)`。統合版は全ファイルの列の和集合（例: keibaAI指数, 吉馬_先行力, 吉馬_末脚力, 吉馬_SP能力値/信頼/調整/最大, 判別できない `指数2/3/4`（全空））。
サンプルでの実測:
- `タイムM_偏差値` 348件・`対戦M_偏差値` 338件は埋まっているが、`タイムM_偏差値帯`・`タイムM_順位帯`・`対戦M_偏差値帯`・`対戦M_順位帯`・`マイニング値` は**0件**（統合の埋め漏れ §5）
- `keibaAI指数` が全行 `0.00` → 全頭 `偏差値50 / 41~50 / 順位1 / 1~2位`

### 3.7 DB（racing.db）テーブル

| テーブル | 作成元 | 内容 |
|---|---|---|
| `__tables_meta__` | build_db | table_name(PK), source_file。実行のたびに全削除して今回分だけ入れる |
| `foragg_foragg_*` | build_db（foragg CSV 1本=1テーブル） | §3.3 の列。`*_val`（5率）は**推定値で上書き**、掲示板率_val は頻度のまま |
| `unified_all` | build_db | 場所, 芝・ダ, 距離, 芝(内・外), 結合データ, 件数(INT), 勝率, 連対率, 複勝率, 2着率, 3着率, 掲示板率, 平均着順, 単勝回収値, 複勝回収値, 単勝適正回収値(REAL), 提案勝率〜提案3着率, 勝率_val〜3着率_val, 掲示板率_val(REAL), テーブル名 の28列。`芝(内外)` と `指数順位帯` は持たない |
| `entries_{stem}` | build_db（entries_out CSV 1本=1テーブル） | §3.6 の列（全て文字列化）＋ `距離_raw, 重量種別, 枠番グループ, 結合データ(base)`。`芝ダ/頭/クラス` は `芝・ダ/頭数/クラス名` に統合 |
| `entries_all` | build_db | レースID(新/馬番無), レースID(新), 場 R, 場所, 芝・ダ, 距離, 芝(内・外), クラス名, 重量種別, 頭数, 馬番, 馬名, 騎手, 結合データ |
| `entries_xjoin_{stem}` | build_db | source_table, レースID(新/馬番無), 場 R, 馬番, 枠番, 枠番グループ, 騎手, 馬名, base, family, key, like_prefix, テーブル名, 件数, 勝率_val, 勝率, 連対率_val, 連対率, 複勝率_val, 複勝率, 2着率_val, 2着率, 3着率_val, 3着率, 掲示板率_val, 掲示板率, 平均着順, 単勝回収値, 複勝回収値, 単勝適正回収値, hit_key(=一致した結合データ), odds_tag(normal/odds10u/odds5p/odds10_50) |

`*_val` の意味の違い（取り違えやすい）:

| 列 | foragg CSV（aggregate 出力） | DB（build_db 後）・UI判定 |
|---|---|---|
| 勝率_val〜3着率_val | 頻度% | 括弧内の推定%（括弧が無ければ頻度%） |
| 掲示板率_val | 頻度% | 頻度%（そのまま） |

### 3.8 正規化ルール（過去側 replace_csv_ver3 ／ 出馬表側 build_db.normalize_entries_df）

- **クラス名**（両側共通）: `500万,1000万,1勝,2勝→1・2_勝` / `1600万,3勝,ｵｰﾌﾟﾝ,OP(L)→準OP・OP` / `Ｇ２,Ｇ３→重賞` / `馬齢×(1・2_勝|準OP・OP)→1勝・OP`。未勝利・新馬・Ｇ１ はそのまま。
  過去側は出力時に NFKC がかかるので `Ｇ１→G1`、出馬表側は `Ｇ１` のまま（§5）
- **重量種別**: 過去側＝TARGETの列（`別定→定量`。値は 馬齢/定量/ハンデ）。出馬表側＝推定（`レース名` に `Ｈ`/`H` → ハンデ、それ以外で `年限定` に `上` が無い → 馬齢、ある → 定量）
- **芝(内・外)**: 過去側＝TARGETの列をそのまま（京都芝2000 は `内外` に統合）。出馬表側＝**京都・新潟だけ** `外回り=="外"→外`、それ以外は `内`（ダートも `内`）。他場は空
- **距離レンジ**（両側同一。replace_csv_ver3 と build_db に二重定義）:
  福島芝1700/1800→17-18、新潟芝 内1200/1400→12-14・外1600/1800→16-18・内2000/2200/2400→20-24・外3000/3200→30-32、中山ダ2400/2500→24-25、東京芝1600/1800→16-18・2300/2400→23-24・ダ1300/1400→13-14、中京ダ1800/1900→18-19、京都芝 内1400/1600→14-16・外1400/1600/1800→14-18・2000(内/外)→2000×`内外`・外2200/2400→22-24・外3000/3200→30-32・ダ1800/1900→18-19、阪神芝1600/1800→16-18・2400/2600→24-26・3000/3200→30-32、小倉芝1700/1800→17-18（札幌芝・中京芝はコメントアウト）。出馬表側は先に `m` を除去
- **枠番グループ**: 10–13頭 枠1–5内/6–8外、14–16頭 1–4内/5–8外、17–18頭 1–3内/4–6中/7–8外。過去側は9頭以下を空、出馬表側は9頭以下・不明で 1–2内/3–6中/7–8外
- **偏差値帯**（両側同一ラベル、ASCII `~`）: `71以上 / 66~70 / 61~65 / 51~60 / 41~50 / 40以下`
- **順位帯**: 過去側 s1 `1~3位/4~6位/7~9位/10位~`、s2 `1~5位/6~10位/11位~`。出馬表側 `1~2位/3~5位/6~9位/10位~`
- **新馬**: 過去側は ZI・吉馬6指数のファイルで新馬行を foragg から除外。出馬表側は ZI のみ偏差値/順位を NaN
- **頭数**: 過去側は10頭以上のみ。出馬表側は制限なし

### 3.9 結合データ（family 別キー構成）

共通の先頭: `場所×芝・ダ×距離×芝(内・外)`（空要素は詰める。区切りは `×`）

| family | 後続 | family | 後続 |
|---|---|---|---|
| normal | 重量種別×クラス名×指数順位 | normal_band | 重量種別×クラス名×指数順位帯 |
| rankB | 枠番グループ×指数順位 | rankB_band | 枠番グループ×指数順位帯 |
| rankJ | 騎手×指数順位 | rankJ_band | 騎手×指数順位帯 |
| rankBJ | 枠番グループ×騎手×指数順位 | rankBJ_band | 枠番グループ×騎手×指数順位帯 |
| normalJ | 重量種別×クラス名×騎手×指数順位 | normalJ_band | 重量種別×クラス名×騎手×指数順位帯 |
| hensachiA | 重量種別×クラス名×偏差値帯 | hensachiB | 枠番グループ×偏差値帯 |
| hensachiJ | 騎手×偏差値帯 | hensachiBJ | 枠番グループ×騎手×偏差値帯 |
| hensachiAJ | 重量種別×クラス名×騎手×偏差値帯 | | |

- 過去側の生成は `replace_csv_ver3.build_joined`。出馬表側の候補生成は `build_db._build_key_candidates` と `app._build_key_candidates`（**同じ処理が2か所にある**）
  - 順位: `13`, `13位`（元が小数なら `13.0` も）
  - 偏差値帯・順位帯: `~` `-` `〜` の3表記（順位帯は `位` の有無も）
  - 内外: 値があれば `[値, 空]`、無ければ `[空, 内, 外, 内外]` を全部試す
- 例（サンプル 東1 ZI 13位）: `東京×ダ×1600×馬齢×未勝利×13`（normal）、`東京×ダ×1600×内枠×13`（rankB）、`東京×ダ×1600×馬齢×未勝利×40以下`（hensachiA）

---

## 4. Dockerfile と実行環境

- ベース `python:3.11-slim`。`locales`（ja_JP.UTF-8）＋ `sqlite3` CLI。`pip install pandas numpy flask`（**バージョン未固定**、scipy なし）
- `COPY . .` するが `.dockerignore` でデータ・DB・CSV は除外。`/app/csv_files, replace_csv, foragg_csv` を mkdir（entries_in は作らない＝マウント前提）
- `EXPOSE 8000`、`CMD ["python","app.py"]`（ENTRYPOINT ではないので `docker run IMAGE python /app/x.py` でも `--entrypoint python IMAGE /app/x.py` でも動く）。Dockerfile 後半はコメントアウトされた旧版
- 運用は `-v "$(pwd)":/app` でホストのディレクトリを丸ごとマウント → **コード変更は再ビルド不要**、依存ライブラリ変更時のみ再ビルド
- ホストは Mac（Readme の `cd Downloads/self-dev/docker/shisu/docker_shisu_replace`。Datasette を `--platform linux/amd64` で起動している点から Apple Silicon と推定）

注意点:
- **パス前提が混在**: replace_csv_ver3 は `./`（cwd=/app 前提）、aggregate_stats_ver2・calc_entry_hensachi（既定）・build_db（既定）・app（既定）は `/app` 絶対パス。Docker 外で動かすなら build_db/calc は引数、aggregate はコード修正が必要
- **pandas のバージョンで結果が変わる**（実測: pandas 2.3.3 / 3.0.5）。再ビルドすると最新版（現在 3.x）が入る。空欄セルの扱いが変わり、`結合データ` が `…×nan×…` から空文字に変わる（§5-1）。固定するのが安全
- scipy が無いので aggregate の推定値は正規近似（小標本では Beta 分位点とずれる）
- Python 3.10 以上が必要（calc_entry_hensachi が `int | None` 型注釈を使う）
- SQLite の「存在しない列名をダブルクォートで書くと文字列リテラル扱い」（DQS）に依存している SQL がある（xjoin 高速パスの `COALESCE("レースID(新)",…)`、`/entries` の `"レース名"` など）。DQS を無効にした SQLite ビルドではエラーになる
- データ量: Readme の実績で unified_all ≈ 577万行。xjoin 構築は 行×指数×15family×キー候補 回のクエリになり重い
- 補助ツール: Datasette（:8001）でDB閲覧可（Readme.txt 参照）。テーブル削除用ワンライナーも Readme.txt にある

---

## 5. 既知の不整合・リスク（未修正。改修時の候補）

根拠: [実測]＝本セッションで実行して確認、[サンプル]＝同梱CSVで確認、[コード]＝コード読解、[要実データ]＝本番データで要確認

**照合結果に直接効くもの**
1. **欠損セルで結合データが壊れる** [実測]。replace の `basic_clean` が `astype(str)` するため、pandas 2.x では空欄が `nan` 文字列になりキーに混入（`東京×ダ×1600×nan×定量×1・2_勝×1`）、pandas 3.x ではキー全体が空文字になる。出馬表側のキーに `nan` は出ないので、該当行は二度とヒットしない。ダートなど `芝(内・外)` が空の行がどれだけあるかは [要実データ]（§7 の確認クエリ）
2. **順位帯の区切りが過去側と出馬表側で違う** [コード]。出馬表 `1~2/3~5/6~9/10位~` に対して、過去側は s1 `1~3/4~6/7~9/10位~`、s2 `1~5/6~10/11位~`。一致するのは s1 の `10位~` だけなので、band 系5 family は実質「10位以下」しかヒットしない
3. **`--merge-one/--merge-only` の統合で帯が抜ける** [サンプル]。`merge_on_key` は先頭ファイルの値が NaN のときだけ後続で埋めるが、帯列は空でも `""`（NaN ではない）なので埋まらない。サンプルではタイムM・対戦M の `_偏差値` は埋まって `_偏差値帯` は0件 → hensachi 系 family のキーが作られない
4. **全頭0の指数がそのまま照合される** [サンプル]。keibaAI指数が全行0.00 → std=0 で全頭偏差値50・順位1 → 「1位」「41~50」の過去成績が全馬に付く
5. **クラス名 `Ｇ１` の全角/半角不一致** [実測]。過去側は出力時の NFKC で `G1`、出馬表側は `Ｇ１` のまま → クラスを含む family（normal / normalJ / hensachiA / hensachiAJ / *_band）で G1 は当たらない
6. **重量種別の推定ずれ** [サンプル]。出馬表側は レース名・年限定 から推定するので、例えば東京優駿（3歳G1・定量）が `馬齢` になる。3歳の別定戦なども過去側（定量）と食い違う
7. **指数名の対応が部分一致で、表記も揃っていない** [コード／要実データ]。xjoin は `instr`、通常パスは `LIKE %指数名%` でテーブルを選ぶ。出馬表側の指数列検出（`INDEX_PATTERNS`）は `先行力/追走力/持久力/持続力/瞬発力`（力付き）だけで、過去側ファイル名は `先行/追走/持久/持続/瞬発`。列名が力付きなら `先行力` は `吉馬_先行力` のテーブルに誤って当たり、`持久力` は `持久` に当たらない。力無しなら列が検出されない。family も `_normal_` が `_normal_band_` を含む（キー形式が違うので実害は小さい）
8. **xjoin 無しの通常パスは前方一致で誤ヒットする** [コード]。`LIKE key%` だと順位1が10〜18位にも一致し、最後の `場所×芝・ダ×距離%` は条件が全く違う行の上位50件を返す。xjoin 高速パスは完全一致だけなので、**2つのパスで結果が変わる**

**機能・運用面**
9. **テーブル名の接頭辞が二重（`foragg_foragg_…`）** [実測]。app の `detect_family_and_flags` は全テーブルを family=`foragg` と判定 → `/` の family フィルタが機能しない。会場別ファイル（`札幌_ZI` など）は `__` が無いので指数名が「不明」
10. **`build_db.py --mode entries --prune` で foragg_* が全部消える** [コード]。Readme.txt に「古いテーブル掃除」として載っているコマンド。unified_all は残るので hits は動くが、`/` は空になる。entries_all も消える
11. **`AGG_OPTIONAL_COLS` のカンマ抜け** [実測]。`"芝(内外)" "馬番"` が `"芝(内外)馬番"` に連結され、foragg に `馬番`・`芝(内外)` が入らない。aggregate の重複排除は `馬名` 頼りになる
12. `add_return_contrib` が作る的中・回収寄与列は foragg に出力されない（docstring と食い違い）[コード]
13. 着順の1文字表記（`取`/`除`/`中`/`失`）は ver2 の正規表現（`取消|除外`, `失格|中止`）に当たらない。TARGET がこの表記なら取消馬が分母に残る [要実データ]
14. `/entries` の既定テーブルが `entries_all`（名前順で先頭）。`レース名` 列が無いので DQS によりレース名欄に文字列「レース名」が出る [コード]
15. entries_xjoin_* に日付列が無い。1テーブルに複数日が入っていると、同じ `場 R` の馬が混ざる [コード]
16. `/entries/key` は base キー（場所×芝・ダ×距離×内外）で完全一致検索するので、通常は該当なし [コード]
17. 偏差値計算の差: std=0 は過去側 NaN／出馬表側 50、吉馬の新馬除外は過去側のみ、指数欠損ポリシーは過去側のみ [コード]
18. `/` の初期表示で全 foragg テーブル×4列を DISTINCT 走査して重い。オッズ選択のラジオボタンが2段重複している [コード]
19. `sanitize_df_for_sjis` は `〜`(U+301C) を `～`(U+FF5E) に変換するが shift_jis で書けないため削除される（帯ラベルは ASCII `~` なので現状は影響なし）[実測]
20. app.py の `classify` で odds10u 用に計算している `f_pct_10u` は未使用 [コード]

---

## 6. README_RECAP.md / Readme.txt との差分（コードが正）

| ドキュメントの記述 | 現行コード |
|---|---|
| 推定値は Jeffreys 事後平均 (k+0.5)/(n+1) | 既定は事後分布の下側10%点。複勝率は頻度≤20%なら上側90%点（`AGG_PROP_MODE=mean` で事後平均） |
| `*_val` はベイズ値 | CSV では頻度、DB では推定値（掲示板率_val は両方頻度） |
| オッズ派生は odds3p / `both/normal/odds3p/odds10u` | odds10u（<10）/ odds5p（≥5）/ odds10_50（10–50）。odds3p は旧 aggregate_stats.py のみ |
| 条件ヒット馬は 軸=複勝≥80 or 連対≥60 or (件数≥20&複勝≥67)、消し=件数≥20&複勝<5 | abs/axis/keshi/opp の4区分（§2.5の表）。件数20&67% の条件は未使用 |
| 並び順 通常→odds10u→odds3p | 通常→odds10u→odds5p→odds10_50 |
| calc_entry_hensachi が `66-70/66~70/66〜70` の3表記を出力 | 出力は `~` のみ。3表記はキー照合時に生成 |
| 重量種別 別定/ハンデ→別ハ（Readme.txt の ver1 説明） | ver3 は 別定→定量、ハンデはそのまま |
| キー順 場所×重量種別×クラス名×(内外)×芝ダ×距離×… | ver3 は 場所×芝・ダ×距離×(内外)×… |
| unified_all の代表列に 芝(内外) | unified_all は 芝(内・外) のみ。掲示板率・掲示板率_val が追加 |
| テーブル名は英数とアンダースコアのみ | 日本語も残る（`\w` 判定）。接頭辞は二重 |
| Docker イメージ名 `csv_processor`（README_RECAP） | Readme.txt の実運用は `csv_processor_shisu` |

---

## 7. 今後の作業ルール・確認方法

- 改修時は**両側を同時に**直す。キー関連のロジックは次に分散している:
  - 過去側: `replace_csv_ver3.py`（`basic_clean`, `apply_place_distance_rules`, `add_hensachi`, `build_joined`, `RANKBAND_SCHEMES`）
  - 出馬表側: `calc_entry_hensachi.py`（偏差値・順位・帯）、`build_db.py`（`normalize_entries_df`, `apply_place_distance_rules_inplace`, `_build_key_candidates`, `IND_ALIASES`）、`app.py`（`_build_key_candidates`, `IND_ALIASES`, `_query_unified_for_key`）
  - `IND_ALIASES` は build_db と app で微妙に違う（app の Gold に `Gold_Airplane` が無い）
- キー仕様を変えたら全段再実行（replace → aggregate → build_db --mode both）。既存 DB と混ぜない（`--fresh` 推奨）
- 文字コード: 出力はすべて Shift_JIS。読込は cp932 優先。SJIS で書けない文字は replace 側では削除、calc 側では例外になる
- このクラウド環境には本番データも Flask も無い。サンプル2本と合成データで関数単位に検証する（flask はスタブで import 可能）
- 確認用クエリ（ホストのプロジェクト直下で）:

```bash
docker run --rm -v "$(pwd)":/app csv_processor_shisu sqlite3 /app/racing.db "SELECT COUNT(*) FROM unified_all WHERE \"結合データ\" LIKE '%nan%' OR \"結合データ\" = '';"
```

```bash
docker run --rm -v "$(pwd)":/app csv_processor_shisu sqlite3 /app/racing.db "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'foragg_%' LIMIT 5;"
```

```bash
docker run --rm -v "$(pwd)":/app csv_processor_shisu python -c "import pandas, numpy, flask; print(pandas.__version__, numpy.__version__, flask.__version__)"
```
