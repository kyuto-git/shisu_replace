# CLAUDE.md — shisu_replace（競馬 外部指数の集計・照合システム）

最終更新: 2026-11-10（成績表を master 1本につき1ファイル `agg_master_*.csv.gz` に・DB の1表ずつのテーブル `foragg_*` を廃止して unified_all に一本化・aggregate の `--compact`（既定 ON）・`--resume`・並列のまとまり）／2026-11-09（screen_families の `--from-report`・選抜の既定の変更・replace の子プロセスの作り直し・lxml 6.1.3・build_db `--entries-only`）／2026-11-08（raw_csv/ の列パッチCSV（レースID(新) と前走の列だけ）を 全場_*.csv に横結合する（§3.9）・出馬表への ext_indices の自動結合・/ingest の出力を ext_indices へコピー・raw_csv/ の過去基本CSVの統合・単独の部品 場所/距離/芝(内・外)・screen_families の候補の拡大（骨格・因果トリオ・冗長の除外・複勝スプレッド）を反映。それ以前の変更は §11）

## この文書の扱い

- **コードが正**。この文書はコードに合わせて更新する。`README_RECAP.md` は要約、`Readme.txt` は TARGET 操作を含む運用メモ。
- 本番データ（`csv_files/` `raw_csv/` `ext_indices/` `agg_csv/` `master_csv/` `replace_csv/` `racing.db` `selected_families.json` `screening_report.csv` など）は `.gitignore` で除外。
  リポジトリにあるデータは出馬表側サンプル2本（`entries_in/shisu_20260531_1.csv`（入力）／`entries_out/entries_merged_hensachi.csv`（旧版の統合出力））と、`shisu/data/` のマスタ3本（コース属性・輸送距離・クワドラント）だけ。
  ★6タブの過去レースCSV（`202610.csv`）と出馬表（`test.csv`）のサンプルは 2026-11-06 に受け取って列の仕様を確認した（リポジトリには入れていない）。外部指数CSV（ext_indices/）の実ファイルは未確認で、形式は依頼文の仕様と実データ例から組んだ（§4.2）。
- **キー（結合データ）に関わる処理は `shisu/` パッケージだけで変更する**。過去側と出馬表側が同じ関数を通ることで一致を保証している（部品の値は `shisu/conditions.py`・`shisu/normalize.py`、キーの組み立ては `shisu/families.py`）。

---

## 0. 概要

TARGET frontier JV で出力した過去レースCSVに外部指数を付け、条件文字列 `結合データ`（例: `東京×芝×16-18×定量×1・2_勝×3`、`東京×ダ×1600×前走1~3着×1~5位`）ごとに集計して成績表（勝率・複勝率・掲示板率・回収値・ベイズ推定値・WEDGE）を作る。
当日の出馬表CSVの各馬について同じ規則でキーを組み立てて成績表と突き合わせ、Flask UI で「絶対軸／軸候補／消し候補／相手候補」や WEDGE を表示する。

- **過去データの取得（2026-11-06〜）**: TARGET からは地域ごとに ★6タブの**基本CSV 2種（タイムM 入り・対戦M 入り。単勝配当・複勝配当つき）**だけを出す。ほかの外部指数は `ext_indices/` に置いた外部指数CSV群を Python 側で レースID(新)・レースID(旧) で各馬に結び付ける（§3.1・§4.2）。従来の `csv_files/{地域}_{指数名}.csv`（★8タブで1指数ずつ出したもの）も読める
- **追加分の過去データ（2026-11-07〜）**: 半年〜1年ごとに出した基本CSVは `raw_csv/` に置く（または `/ingest` からアップロード）。replace が `csv_files/全場_タイムM.csv`・`全場_対戦M.csv` に統合してから master を作る（重複は後のファイル優先・最大10年分。§3.9）
- **列パッチ（2026-11-08〜）**: 基本CSVを前走データ読込なしで速く出し、前走の列は `レースID(新)` と前走の列だけの軽いCSVで別に出して `raw_csv/` に置く。raw_merge が 全場_タイムM・全場_対戦M の両方に横結合（上書き・補完）する（§3.9）
- **出馬表（2026-11-07〜）**: `entries_in/` にはマイニング値だけの出馬表CSV 1本を置けばよい。calc_entry_hensachi が ext_indices の外部指数を過去側と同じ関数で各馬に付けてから偏差値・順位を計算する（§3.3）。`/ingest` の TARGET 取込用CSVは既定で ext_indices/ にもコピーされる（§3.6）
- **成績表の持ち方（2026-11-10〜）**: aggregate は master 1本（地域×指数）につき1ファイル `agg_csv/agg_master_{地域}_{指数名}.csv.gz` に、family・スキーム・オッズ帯の成績表を縦に並べて書く（旧版は1表1ファイルで、数千 family × 33指数で数十万ファイル・140GB 超になった）。既定でコンパクト化（区分・WEDGE の上位・layer A・件数30以上の行だけ）。build_db は1表ずつのテーブルを作らず unified_all に直接入れる（§3.2・§3.4）
- **キーの型 = family**: 部品（コース・重量種別・クラス名・枠番グループ・騎手・芝・ダ・場所・距離・芝(内・外) と 22条件）＋末尾の指数の部品（指数順位 / 指数順位帯 s1・s2 / 偏差値帯）。初期セットは 49 の型（83 family・スキーム込み 129 通り。§5.3）。`screen_families.py` で全掛け合わせ候補を採点して選んだ `selected_families.json` があれば、初期セットの代わりにそれを使う（集計・DB・照合・画面）
- 順位帯（指数順位・前走着順・前走人気・前走上がり3F順で共通）は **s1 = 1 / 2~3 / 4~5 / 6~9 / 10~（排他）、s2 = 1~3 / 1~5 / 6~（重なりあり。2位は 1~3 と 1~5 の両方）**
- 合成指標 WEDGE（決定力 WIN ＋ 市場の期待を超える力 EDGE）とその年度加重 adjusted_WEDGE（§6.2）
- 外部指数の取得（Ω・吉馬6・かよちん・keibaAI のスクレイピング、ラップ6指数、安井サロン Excel）は `shisu/ingest/`・`shisu/yasui_extractor.py`。画面 `/ingest`（または `ingest.py`）で TARGET 取込用（＝ ext_indices の形式A）の日付別CSVを作る（§3.6・§3.7）

---

## 1. 構成と処理フロー

### 1.1 ディレクトリ

```
shisu_replace/
├─ shisu/                     共通パッケージ（正規化・22条件・family・キー・偏差値・分類・照合・WEDGE・外部指数の結合・指数の取得）
│   └─ data/                  マスタ: コース属性_帯つき / 輸送距離マスタ / quadrant_link（リポジトリに入れる）
├─ replace_csv_ver3.py        [A1] 過去レース（基本CSV＋ext_indices）→ master
├─ screen_families.py         [A1.5] master → family 候補の一括採点 → screening_report.csv・selected_families.json
├─ aggregate_stats_ver2.py    [A2] master → 成績表（family × スキーム × オッズ帯）
├─ calc_entry_hensachi.py     [B1] 出馬表 → 偏差値・順位・帯
├─ build_db.py                [A3/B2] CSV → racing.db（照合の事前計算 xjoin を含む）
├─ app.py                     [C] Flask UI（/ingest は shisu/ingest/web.py）
├─ ingest.py                  [D] 外部指数の取得 CLI（画面版は /ingest）
├─ tests/                     pytest（合成データで全段を通す）
├─ csv_files/   入力: 過去レース（TARGET）       ├─ entries_in/   入力: 出馬表（TARGET）
├─ raw_csv/     入力: 追加分の過去基本CSV（replace が csv_files/全場_*.csv に統合。済んだものは raw_csv/merged_done/ へ。§3.9）
├─ ext_indices/ 入力: 外部指数CSV群（§4.2）       ├─ entries_out/  偏差値付き出馬表
├─ master_csv/  1頭1行（.csv.gz。集計・選抜の入力）＋ __inner_outer__.csv.gz（前走の内外を引く表）
├─ agg_csv/     成績表 agg_master_{地域}_{指数名}.csv.gz（master 1本につき1ファイル。1行=1結合データ）＋ families.json（集計に使った family）・agg_settings.json（集計の設定。--resume 用）
├─ racing.db    SQLite                            ├─ selected_families.json / screening_report.csv（screen_families の出力）
├─ replace_csv/ processed_*（normal family のキーの確認用。--processed のときだけ）
├─ ingest_in/ ingest_out/  指数の取得の入力・出力（§3.6）
└─ foragg_csv/  旧版の集計素材（2026-11-06 から作らない。残っていても使わない。消してよい）
```

### 1.2 前工程（手作業。Readme.txt §2）

- **過去データ（基本CSV）**: TARGET レース検索 ★6タブ（頭数10以上・障害除外で抽出）→ 外部指数の欄に マイニング（タイムM）または 対戦型マイニング（対戦M）→「レース一覧★画面イメージ(CSV)」→ `csv_files/{地域}_タイムM.csv`・`csv_files/{地域}_対戦M.csv`
  - 列は `202610.csv` の形＋ `単勝配当`（単勝オッズの隣）・`複勝配当`（複勝シェアの隣）。配当の列が無くても動く（回収値が 0 になる）
  - 地域: `主５場（中山、東京、中京、京都、阪神）` / `ローカル５場（札幌、函館、福島、新潟、小倉）` / 各場名
- **外部指数**: `ext_indices/` の下にフォルダ・ファイル名で指数が分かる形で置く（§4.2。`/ingest` の出力の TARGET 取込用CSV（形式A）はそのまま置ける）
- **従来の形**: `csv_files/{地域}_{指数名}.csv`（★8タブで1指数ずつ出したもの）も読める。同じ地域×指数が ext_indices にもあれば csv_files の方を使う
- **ファイル名の指数名で処理が切り替わる**（符号反転・ZI 特例・新馬除外）。指数名は `shisu/indicators.py` の台帳で正式名に変換される。**廃止した指数**（カツラキ・HPRTV5C・成績ST指数・結果分析スピード指数（一隊）・33rap・上がり1F・上がり2F）は csv_files にあっても ext_indices にあっても読まない
- **追加分の過去データ**: 同じ ★6タブで追加の期間を出し、名前に「タイム」「マイニング」（タイムM）か「対戦」（対戦M）を入れて `raw_csv/` に置く。replace が `csv_files/全場_{種類}.csv` に統合する（§3.9）
- **列パッチ**: 前走の列（間隔・休み明け～戦目・前走場所・前芝・ダ・前距離・前走斤量・前走人気・前走着順・前走脚質・前走上り3F順・前走レース印３・前走レースID(新) など）を TARGET の前走データ読込で出すときは、`レースID(新)` とその列だけ（`指数` 列を入れない）のCSVにして `raw_csv/` に置く（名前は自由。複数に分けてもよい）。全場_タイムM・全場_対戦M の両方の同じ馬の行に当たる（§3.9）
- **出馬表**: TARGET 出馬表分析 ★4タブ → マイニング読込（外部指数1〜4 は読み込まなくてよい。読み込んだ指数は出馬表の値が使われる）→ 全選択 → CSV → `entries_in/shisu_YYYYMMDD_N.csv`（22条件に使う列: 所属・間隔・休明戦目・斤量・性・前場所・前芝ダ・前距離・前走開催・前着順・前人気・前3F順・前脚質・前レース印3・前斤量。`test.csv` の形）。ほかの外部指数は ext_indices から付く（§3.3）

### 1.3 データの流れ

```
[A] 過去レース
raw_csv/*.csv（追加分の基本CSV・列パッチ） ─ replace の最初に統合・パッチ ─> csv_files/全場_タイムM.csv・全場_対戦M.csv（§3.9）
csv_files/{地域}_タイムM.csv・{地域}_対戦M.csv（基本CSV） ＋ ext_indices/（外部指数CSV群）
csv_files/{地域}_{指数名}.csv（従来の形）
  └─ replace_csv_ver3.py ─┬─> master_csv/master_{地域}_{指数名}.csv.gz   1頭1行（キーの部品・22条件・偏差値・順位・成績）
                          ├─> master_csv/__inner_outer__.csv.gz         レースID16 → 芝(内・外)（前走の内外。出馬表側も使う）
                          └─> replace_csv/processed_*.csv               確認用（--processed のときだけ）
  └─ screen_families.py（任意・推奨）  master → 候補の family を一括採点 → screening_report.csv / selected_families.json
  └─ aggregate_stats_ver2.py  master 1本 × family（selected_families.json があればそれ、無ければ初期セット）× スキーム
                              × オッズ帯4区分 → agg_csv/agg_master_{地域}_{指数名}.csv.gz（master 1本につき1ファイル。
                              既定でコンパクト化）＋ agg_csv/families.json・agg_settings.json
  └─ build_db.py --mode foragg|both
        unified_all（全成績表の縦結合＋family/odds_mode/rank_scheme/indicator/region/テーブル名 列。1表ずつのテーブルは作らない）,
        __tables_meta__（集計表の一覧。table_name = unified_all のテーブル名）, __facets__, __families__（family の定義。照合・画面はこれを使う）

[B] 出馬表
entries_in/*.csv（マイニング値だけの出馬表1本でよい） ＋ ext_indices/（過去側と同じ外部指数CSV群）
  └─ calc_entry_hensachi.py ─> entries_out/{元名}_hensachi.csv / entries_merged_hensachi.csv（--merge-*）
  └─ build_db.py --mode entries|both（unified_all が必要）
        entries_{元名}（キー部品と22条件を正規化）, entries_all, entries_xjoin_{元名}（照合結果。__families__ の family で照合）

[C] app.py
  /              横断検索（__tables_meta__ で集計表を選び、unified_all の同じテーブル名の行を表示）
  /entries       出馬表のレース一覧（日付・場所）
  /entries/race  出馬表（各馬の順位・偏差値・帯）
  /entries/key   結合データの前方一致で unified_all を表示
  /entries/hits  条件ヒット馬（xjoin があれば読む。無ければ同じ関数でその場照合）
  /entries/wedge レース別 WEDGE 一覧（全馬 × 該当する全キー。CSV ダウンロード /entries/wedge.csv）
  /ingest        外部指数の取得（[D]）・raw_csv の統合（§3.9）

[D] 外部指数の取得（ingest.py / /ingest）
ベースCSV（TARGET: レースID(新), 日付S, 枠番, 馬番, ワーク1）
  └─ ①Ω ②吉馬6 ③かよちん ④keibaAI（スクレイピング。⑤一隊は廃止）
ラップCSV ─ ⑥ラップ6指数（手元で計算）／ 安井 Excel ─ 安井8指数
  ─> ingest_out/{実行日時}/{取得元}/{YYYYMMDD}_{名前}.csv（TARGET 取込用2列 = ext_indices の形式A）, *_merged.csv
  ─> ext_indices/{OMEGA,kichiuma,kayochin,keibaAI,yasui}/…（TARGET 取込用CSVのコピー。既定でコピーする。[A]・[B] がそのまま結び付ける）
```

### 1.4 実行コマンド（Docker。イメージ名 `csv_processor_shisu`）

```bash
docker build -t csv_processor_shisu .
docker run --rm -v "$(pwd)":/app csv_processor_shisu python replace_csv_ver3.py --clean
docker run --rm -v "$(pwd)":/app csv_processor_shisu python screen_families.py --indicators タイムM,対戦M --workers 4 --export-selected
docker run --rm -v "$(pwd)":/app csv_processor_shisu python screen_families.py --from-report --export-selected   # 採点し直さず選抜だけ
docker run --rm -v "$(pwd)":/app csv_processor_shisu python aggregate_stats_ver2.py --clean --workers 4
docker run --rm -v "$(pwd)":/app csv_processor_shisu python aggregate_stats_ver2.py --resume --workers 4   # 止まったら続きから
docker run --rm -v "$(pwd)":/app csv_processor_shisu python calc_entry_hensachi.py --merge-only --clean
docker run --rm -v "$(pwd)":/app csv_processor_shisu python build_db.py --fresh
docker run --rm -p 127.0.0.1:8000:8000 -v "$(pwd)":/app csv_processor_shisu python app.py
docker run --rm -v "$(pwd)":/app csv_processor_shisu python -m pytest
# 外部指数の取得（画面なら http://localhost:8000/ingest）
docker run --rm -v "$(pwd)":/app csv_processor_shisu python ingest.py --sources omega,kichiuma,kayochin
docker run --rm -v "$(pwd)":/app csv_processor_shisu python ingest.py --sources keibaai --keibaai-start 18952 --keibaai-end 18987
docker run --rm -v "$(pwd)":/app csv_processor_shisu python ingest.py --sources lap,yasui
```

### 1.5 再実行ルール

| 変えたもの | 再実行 |
|---|---|
| csv_files・raw_csv・ext_indices（過去データ）・キーの部品の規則（`shisu/` の normalize・conditions・course・data/） | replace → （screen）→ aggregate → build_db --mode both |
| 使う family（selected_families.json を作り直した・消した） | aggregate --clean → build_db --fresh（master はそのまま使える） |
| 集計の条件（推定方法・MIN_SAMPLE・オッズ帯の定義・WEDGE の年の最低件数・遡る年数・減衰率・窓・コンパクト化の設定など） | aggregate --clean → build_db --fresh（`--resume` は設定が前回と違えば止まる） |
| 出馬表・ext_indices（出馬表の日の外部指数）・馬体重発表後のマイニング確定値（entries_in の出馬表を上書き） | calc_entry_hensachi --merge-only --clean → build_db --entries-only（＝ `--mode entries`。古い出馬表を消すなら `--prune`） |
| 画面（app.py・`shisu/classify.py`） | app の再起動だけ |

- `build_db --mode foragg` だけ回すと xjoin（照合結果）が古いまま残るので、警告が出る。続けて `--mode entries` を回す。
- `--prune` は「今回作り直した種類」の古いテーブルだけを消す（`--mode entries --prune` で foragg_* は消えない）。
- family を入れ替えたら `aggregate --clean`（古い family の成績表を agg_csv に残さない）と `build_db --fresh`。
- 画面は `__tables_meta__` に載っている集計表だけを使う。family の名前・ラベル・照合に使う family は DB の `__families__` から読む。

---

## 2. 共通パッケージ `shisu/`

| モジュール | 役割 |
|---|---|
| `config.py` | パス。ルートは `SHISU_BASE_DIR`（既定はリポジトリ直下）、DB は `DB_PATH`（既定 `racing.db`）、外部指数は `SHISU_EXT_DIR`（既定 `ext_indices/`）、family の選抜結果は `SHISU_FAMILIES_FILE`（既定 `selected_families.json`）、追加分の過去基本CSVは `raw_dir()`（`raw_csv/`） |
| `csvio.py` | `norm_text`（NFKC・空白除去・欠損表記→""）、CSV 読み書き（読込 cp932→utf-8-sig→utf-8、**書出し cp932**） |
| `normalize.py` | クラス名・重量種別・距離レンジ・枠番グループ・芝(内・外)・レースID/日付（`horse_race_id`）、`split_surface_distance`（距離「ダ1200」→ 芝・ダ と 距離） |
| `indicators.py` | 指数の台帳（正式名・別名・符号反転・ZI 特例・新馬除外・0=欠損）、廃止した指数（`DISCONTINUED`・`is_discontinued`）、ファイル名から地域/指数名の取り出し |
| `aggstore.py` | 成績表の保存形式: master ごとの1ファイル（`AggMasterWriter`。.part に書いてから置き換える・gzip の見出しに時刻を入れない）・行のメタ列（`ROW_META`）・コンパクト化の判定（`compact_mask`）・DB に入れる値への作り直し（`prepare_agg_frame`・`estimate_percent`。build_db から移した）・読み込み（`iter_agg_chunks`・旧来の1表1ファイルの形に分ける `read_agg_tables`） |
| `parallel.py` | 子プロセスをまとまりごとに作り直す並列実行（`run_batched`。replace・aggregate で共通。`max_tasks_per_child` は使わない） |
| `ext_indices.py` | ext_indices/ の外部指数CSVを見つけて読み（形式A〜D）、ベースCSVの各馬に値（と順位）を付ける（§4.2）。`attach_filled` は過去側（replace）と出馬表側（calc_entry_hensachi）で共通の付け方（ファイルに無い馬は 0・順位の扱い・`absent`） |
| `raw_merge.py` | raw_csv/ の追加分の基本CSVを `csv_files/全場_タイムM.csv`・`全場_対戦M.csv` に統合し、列パッチ（`レースID(新)` と補う列だけのCSV）を両方に当てる（§3.9） |
| `course.py` | `data/` のマスタ（コース属性・輸送距離）を引く。コースの属性・輸送距離・距離帯・左右・坂 |
| `conditions.py` | 22条件の導出。過去側（★6タブ）と出馬表側の列名の違いを `standardize_history` / `standardize_entries` で吸収し、同じ `derive()` を通す（§5.2） |
| `families.py` | family の定義・台帳（初期セット・selected_families.json・DB の `__families__`）・部品（`PART_TOKENS`。COURSE と単独の 場所・距離・芝(内・外) を含む）・キーの組み立て（過去側のベクトル版 `build_keys` と出馬表側の候補 `entry_keys` / `entry_key_candidates`） |
| `keys.py` | キーの窓口（families.py の再エクスポートと `all_families()`・`is_band_family()` など） |
| `hensachi.py` | レース内の偏差値・順位・偏差値帯、順位帯（新8区分。`band_labels`。前走着順・前走人気・前走上がり3F順と共通） |
| `odds.py` | オッズ4区分（normal / odds10u / odds10_50 / odds50p） |
| `naming.py` | 成績表のファイル名の規約と解析（`agg_master_{地域}_{指数名}.csv.gz`、旧来の `foragg_*.csv`。family 名は台帳から長い順に当てる。「_」の位置で切って台帳の集合と照らすので family が数千でも速い）、テーブル名（unified_all の「テーブル名」。旧来の成績表のテーブル名と同じ） |
| `finish.py` | 着順の正規化（取消・除外・失格・中止、1文字表記対応） |
| `estimates.py` | ベイズ推定（Beta 分位点・事後平均） |
| `classify.py` | 条件ヒット馬の分類（Python の1行版 `classify`・ベクトル版 `classify_frame`（aggregate のコンパクト化）・SQL 版。3つの一致はテストで確認） |
| `matching.py` | 出馬表1頭 × unified_all の完全一致照合（xjoin 構築と画面のその場照合で共通） |
| `wedge.py` | WIN・EDGE・WEDGE・年度加重 adjusted_WEDGE |
| `ingest/`・`yasui_extractor.py` | 外部指数の取得（§3.6・§3.7） |

---

## 3. 各スクリプト

### 3.1 replace_csv_ver3.py（過去レース → master）

0. `raw_csv/` の直下に未統合の CSV があれば、最初に `shisu.raw_merge.merge_raw` で `csv_files/全場_タイムM.csv`・`全場_対戦M.csv` に統合し、済んだファイルを `raw_csv/merged_done/` に移す（§3.9。`--no-raw-merge` でしない、`--raw-dir` で場所を変える）
1. 入力の決め方（`plan_jobs`）: `csv_files/*.csv` をファイル名で 地域×指数 に分ける。廃止した指数は飛ばす。地域ごとの**基本CSV**（`タイムM` を優先、無ければ `対戦M`）を決め、`ext_indices/` で見つかった各指数（§4.2）を基本CSVに結び付けて「{地域}_{指数名}」の入力として処理する。**同じ地域×指数が csv_files にあれば csv_files を使い**、ext は使わない（ログに出す）。`--no-ext` で ext_indices を見ない。基本CSVが無いのに ext_indices があれば警告
2. `normalize_history`: 列名ゆれ（芝ダ/芝・ダ、芝(内外)/芝(内・外)、頭/頭数、馬名S など）を吸収し、キー部品を `shisu.normalize` で正規化
   - `芝・ダ` 列が無い・空で `距離` が「ダ1200」「芝2000」「障3000」なら分ける（`split_surface_distance`）
   - `レースID(新)` がレース単位（16桁）なら 馬番2桁 を付けて18桁にする。日付は レースID(新) の先頭8桁（無ければ 日付S）
   - **障害レース**（芝・ダ が「障」、または トラックコード(JV) が51以上）は除く（ログに行数）
3. ext の指数（`process_ext`）: 基本CSVの全馬に `shisu.ext_indices.attach_filled`（出馬表側と共通）で値を付けて 指数 列にする。形式B（StridePlus）・C（Gold）はファイルの順位を 指数順位 にし（ファイルに無い馬はレースの最下位・同順位。TARGET と同じ）、A・D は値から順位を計算する（基本CSVの タイムM の順位は使わない）。**ファイルに値の無い馬は 0**（TARGET は外部指数を取り込んでいない馬を 0 で出すので、出馬表側・従来の ★8 の過去CSVと同じ数え方。0 を欠損とする指数（安井のランク・5ステータス）は欠損に戻る）。ログに「外部指数が付いた馬 x/y」（0 で埋める前の数）
4. `compute_indicator`（§5.5）で偏差値・順位・帯。**取消・除外の馬は計算に入れない**（TARGET は 指数 0・指数順位 0 を出す。入れると 0 が平均を動かす）。TARGET の順位に合わせて偏差値の向きを変えたときはログに出す。22条件（`shisu.conditions`。§5.2）を付ける。前走の内外は、地域ごとの基本CSV（無ければ最初のファイル）の レースID(新)・芝(内・外) から作った表で引く（`__inner_outer__.csv.gz` にも書く）
5. 頭数10以上（頭数列が無ければ警告して絞らない）の馬を `master_{地域}_{指数名}.csv.gz` に書く。除外レース（欠損2頭以上・全頭同値・有効2頭未満）・新馬除外（ZI・吉馬6種）の馬は `指数有効=0` で残す（集計では使わない）
- **family ごとの素材ファイルは作らない**（旧版は 20本×入力。今は aggregate が master からその場でキーを作る）
- オプション: `--clean`（master と processed を空に）, `--workers N`, `--processed`, `--csv-dir`, `--ext-dir`, `--no-ext`, `--master-dir`, `--raw-dir`, `--no-raw-merge`
- メモリ（2026-11-09〜）: raw_csv の統合と前走の内外の表を作ったあとに `gc.collect()` で手放す。`--workers` が2以上のとき、子プロセスは1つあたり `MAX_TASKS_PER_CHILD`（4）件ほどで作り直す（`run_parallel` → `shisu.parallel.run_batched`: ジョブを workers × 4 件ずつのまとまりに分け、まとまりごとに新しい `ProcessPoolExecutor` を作って閉じる。基本CSVのキャッシュなどがたまって Docker の OOM Killer に止められ `BrokenProcessPool` になるのを防ぐ）。`ProcessPoolExecutor(max_tasks_per_child=…)` は使わない（Python 3.11・3.12 では、子プロセスが規定の件数で終わったあとに代わりが起動されず親が止まったままになる。Docker の Python 3.11.18 で再現）。それでも子プロセスが止められたら、そのまとまりの終わっていないジョブを `[ERROR] … 子プロセスが止められました` にして次のまとまりへ進む（終了コード1）
- 目安: 実サンプル `202610.csv`（3,447行）で 0.6秒

### 3.2 aggregate_stats_ver2.py（master → 成績表）

- 使う family: `--families FILE` か `selected_families.json`（`SHISU_FAMILIES_FILE`）があればその定義、無ければ初期セット（§5.3）。**`--families`・`SHISU_FAMILIES_FILE` で指定したファイルが無ければ終了コード2**（黙って初期セットにしない）。使った定義を `agg_csv/families.json` に書く（build_db はこれを読む）。並列の子プロセスにも同じ定義を渡す
- `AGG_RANKBAND_SCHEMES`（例 `s1`）で作るスキームを絞ると、families.json もそのスキームだけになる（master ごとのファイルは丸ごと書き直すので、作らないスキームの前の行は残らない）
- master 1本（地域×指数）の `指数有効=1` の馬について、family × スキームごとに `shisu.families.build_keys` でキーを作り、オッズ帯4区分（§5.6）で集計する。**出力は master 1本につき1ファイル `agg_csv/agg_master_{地域}_{指数名}.csv.gz`**（2026-11-10〜。§4.3）: 成績表（family × スキーム × オッズ帯）を縦に並べ、`family, odds_mode, rank_scheme, indicator, region, テーブル名` の列で区別する。テーブル名は旧来の1表1ファイル `foragg_{オッズ接頭辞}{rankband_スキーム_}{family}_{地域}_{指数名}.csv` のテーブル名と同じ（xjoin・画面に出る名前は変わらない）。**0件の成績表は書かない**。1つずつ集計して書き足すので、メモリには1つの成績表しか持たない。書きかけは `.part` で、書き終わったら本来の名前に置き換える（途中で止まっても完成したファイルだけが残る）。gzip の見出しに時刻を入れないので、同じ入力・設定なら同じバイト列になる
  - 旧版は1表1ファイルだったため、数千 family × 33指数 × 4オッズ帯で数十万ファイル・140GB 超になり、Mac の Docker が止まった（ユーザーの実データ）
  - agg_csv に旧来の `foragg_*.csv` が残っていれば WARN（build_db は同じ master の agg_master があれば使わない。消すには `--clean`）
- **コンパクト化（`--compact`。既定 ON。全部の行を書くのは `--no-compact`）**: 成績表の行のうち、次のどれかを満たすものだけを書く（`shisu.aggstore.compact_mask`）
  - a) `classify` で 絶対軸・軸候補・相手候補・消し候補 のどれかになる（判定の値は build_db が DB に入れる値と同じ: 5つの率の `*_val` は文字列の括弧内の推定値。`classify_frame` で全行を一度に判定）
  - b) `WEDGE >= 0.70` または `adjusted_WEDGE >= 0.70`（`--compact-min-wedge`。`none` ならこの条件を使わない）
  - c) family の layer が A（初期セットのコース固定15種）、または `件数 >= 30`（`--compact-min-count`。`none` なら件数の条件を使わない）
  - 下限は正の数か `none`。0 以下は受け付けない（0 を「使わない」にすると、下げたのに残る行が減る逆向きの動きになるため）
  - 条件ヒット馬（4区分すべて）と WEDGE の上位の行は1行も落とさない（`tests/test_aggstore.py` で、全部の行から独立に計算した行と完全に一致すること、DB の4区分・WEDGE≧0.70 の行が全部の行の DB と同じことを確認）。落ちるのは、どの区分にも入らず WEDGE も低く件数30未満の行（レース別 WEDGE 一覧・条件ヒット馬の画面にはもともと出ても使わない行）
  - 実測（2026-11-10。残る行の割合）:
    - 実サンプル `202610.csv`（1か月分）・初期セット: 35,998行 → 13,587行（37.7%）
    - 合成の master 1本（30万行＝10年分相当）・初期セット: 91.2%（行の63%が layer A で全部残る。区分 3.7%・WEDGE≧0.70 が 18.6%）
    - 同じ master・`--space all` の X_ 候補から無作為に150 family（251通り。layer A なし）: 既定（件数≧30・WEDGE≧0.70）で 55.9%。区分は 4.8%・WEDGE≧0.70 は 16.5%（件数の少ないグループは WEDGE がたまたま高く出やすい）。下限の組み合わせ: 件数≧100・WEDGE≧0.70 で 38.5%、件数≧100・WEDGE≧1.0 で 25.9%、件数なし（`--compact-min-count none`）・WEDGE≧0.70 で 21.3%、件数なし・WEDGE≧1.0 で 6.7%、件数なし・WEDGE なし（区分だけ）で 4.8%
    - つまり既定の規則では半分ほどにしかならない（「9割が平凡」でも、平凡な行の多くが件数30以上か WEDGE≧0.70 に入る）。10分の1にするには `--compact-min-count none --compact-min-wedge 1.0` くらいまで絞る（区分と layer A の行は常に残る）
    - 大きさの目安: unified_all は1行あたり約740バイト（索引込み）、agg_master_*.csv.gz は約51バイト。4,868通り × 33指数（全部の行で約4.3億行と見積もり）なら、既定で DB 約180GB、件数なし・WEDGE≧1.0 で約21GB（合成データからの見積もり。実データでは違う）
  - 時間はほとんど変わらない（判定は書く前の成績表に1回かけるだけ）
- **並列（`--workers N`）**: master を1本ずつ子プロセスに渡し（`chunksize=1` と同じ）、N × `MASTERS_PER_CHILD`（2）本ごとに `ProcessPoolExecutor` を作り直して `gc.collect()` する（`shisu.parallel.run_batched`。replace と同じ。子プロセスが止められたらそのまとまりの残りを `[ERROR]` にして次へ）。進み具合は `[済んだ本数/全体] [OK] …` で1本ずつ出す
- **書き終わりの印と `--resume`**: master を書き始めるときに前の `agg_master_*.csv.gz` と印を消し、書き終えたら fsync して本来の名前に置き換え、印 `agg_master_*.csv.gz.done`（集計の設定のハッシュ・元の master の名前・大きさ・更新時刻・行数・成績表のファイルの大きさ）を書く。`--resume` は、印が**今の設定・今の master と合い、成績表が印のとおりの大きさで残っている** master だけを飛ばし、ほかは作り直す（違う設定で作った成績表・replace で作り直した master・途中で切れたファイルを混ぜない。設定を変えた実行が途中で止まっても、`--resume` で今の設定にそろう）。設定（family の定義・スキーム・WEDGE・推定・コンパクト化）は `agg_csv/agg_settings.json` にも書き、前回と違えばどの項目かをログに出す。`--clean` と一緒には使えない。前の実行の書きかけ `.part` は消してからやり直す
- 失敗: 子プロセスが止められた・例外の master は `[ERROR]`（終了コード1）。全部の master が失敗したときもそう出す（「replace を先に」とは出さない）。直してから `--resume` で失敗した分だけ集計できる
- 行ごとに決まる値（着順の解析・オッズ・配当・年）は master 1本につき1回だけ求め、family・オッズ帯ごとには aggregate_frame の入力に要る列だけを渡す（`with_precomputed`。結果は1行ずつ求めたときと同じ）
- 重複排除: レースID × 結合データ × 馬番（s2 の重なりで1頭が2つのキーに入るのは別のキーなので数える）
- 着順: 取消・除外（★6 の「消」「外」も）は分母外、失格・中止（「止」も）は分母に入れて着順=頭数（不明なら18）
- 率: 頻度と推定値。推定値は既定で事後分布 Beta(k+0.5, n−k+0.5) の下側10%点。**複勝率だけは頻度≤20%なら上側90%点**（消し判定を保守的にするため。`AGG_PROP_MODE=mean` でも複勝率はこの切替のまま）。scipy で厳密計算
- 回収値: 単勝・複勝は 100円均等買い。単勝適正回収値 = 100×Σ(1/オッズ×配当/100×的中)/Σ(1/オッズ)。配当の列が無ければ 0
- WIN / EDGE / WEDGE / adjusted_WEDGE（＋年数・件数・最終年）を `shisu.wedge.wedge_columns` で付ける（§6.2）。年度の窓の終わりは、オッズ帯で絞る前の master 全体の最終年
  - `--wedge-min-year-count N`（既定10）、`--wedge-max-years N`（既定10）、`--wedge-decay D`（既定0.65）、`--wedge-window data|key`（既定 data）。環境変数 `AGG_WEDGE_*` でも指定できる
- 件数 < 5 のグループは計算前に除く（出力しない）
- 集計できた master が0本なら終了コード2（replace を先に実行）
- 環境変数: `AGG_PROP_MODE`(lower|mean), `AGG_LOWER_Q`, `AGG_UPPER_Q`, `AGG_PRIOR_ALPHA/BETA`, `PLC_SWITCH_THRESHOLD_PCT`, `AGG_MIN_SAMPLE`, `AGG_RANKBAND_SCHEMES`（例 `s1` で s2 を作らない）, `AGG_WEDGE_*`。オプション `--in-dir`（既定 master_csv）, `--out-dir`, `--families`, `--clean`, `--resume`, `--workers N`, `--compact`/`--no-compact`, `--compact-min-count N|none`, `--compact-min-wedge X|none`, `--wedge-*`
- 目安（検証環境・1プロセス）: 30万行の master 1本 × 初期セット（122通りの時点で計測）= 3分19秒（合成データ。71万グループ・成績表488本・CSV 168MB。行ごとの値の前計算の前は約2.5倍かかった）。実サンプル（3,188頭）では 18秒・成績表 476本・unified_all 33,210行（うちレイヤーA（旧版の15種に当たる）は 3,847行）

### 3.3 calc_entry_hensachi.py（出馬表 → 偏差値付き出馬表）

- **ext_indices の自動結合（2026-11-07〜）**: `entries_in/` にはマイニング値だけの出馬表（`test.csv` の形）1本を置けばよい。ext_indices/ の外部指数（§4.2。形式A〜D）を、過去側と同じ `shisu.ext_indices.attach_filled` で レースID(新)・レースID(旧)（無ければ レースID(新) から作る）を使って各馬に付けてから、偏差値・順位・帯を計算する。同じ馬なら過去側（master）と順位・偏差値・帯が一致する（`tests/test_entries_ext.py`）
  - **出馬表の値を優先する。判定はファイルごと・レースごと**: そのファイルに同じ指数の列があり、そのレースに 0・空欄以外の値が1頭でもあれば、そのレースは出馬表の値と TARGET の順位を使う（全レースそうなら `[INFO] {指数}: 出馬表CSVの値を使います`）。列が無いファイル、0・空欄だけのレース（TARGET が読み込んでいない指数を 0 で出したもの）は ext の値で埋める（`出馬表に値のあるレース k/n は出馬表の値を使用` とログに出す）。別の日の出馬表が entries_in に残っていても、そのファイルの値は今日のファイルに影響しない
  - 列があるときはその列の該当レースの行を書き換え、対応する順位の列（`指数順位N` など）も ext の順位にする（列を消すと ほかの 指数順位N の対応がずれるため）。列が無ければ末尾に `{正式名}`（順位があるなら `{正式名}順位`）を足す。**末尾に足した列は 指数順位N の対応付け（§3.3 の②〜④）の枠に数えない**（台帳に無い指数（ラップ2F など）の 指数順位N を取り込まないため。無印の 指数順位 の「指数が1種類」の判定も、もとの出馬表の列だけで数える）
  - ファイルに値の無い馬は 0・順位の付け方（形式B・C はファイルの順位、ファイルに無い馬は最下位）・0=欠損の指数の扱いは過去側（§3.1 の3）と同じ。ログに「外部指数 {指数} を ext_indices から付けました（値のある馬 a/b）」
  - レースID(新)・レースID(旧) が無い出馬表には付けられない（`[WARN] 外部指数を結び付けられません`）
  - `--ext-dir`（既定 `ext_indices/`。`SHISU_EXT_DIR`）、`--no-ext`（結合しない＝従来の動き）。出馬表が複数ファイルでも、同じ ext を各ファイルに付けてから従来どおり横結合する
- 指数列は台帳で検出し正式名にする（`GoldAirplane→Gold`、`マイニング値→タイムM`、`対戦型マイニング値→対戦M`、`先行力→先行` など）
- 各指数列の直後に `X_偏差値, X_偏差値帯, X_順位, X_順位帯_s1, X_順位帯_s2` を挿入。計算は過去側と同じ `compute_indicator`。`X_順位帯_s2` は重なる帯を「・」でつないだ表示用（例 `1~3位・1~5位`。キーは帯ごとに作る）
- 順位は元ファイルの順位列を優先する。対応の付け方: ①明示列（`マイニング順位`・`対戦型マイニング順位`・`{列}順位`）→ ②指数列の**すぐ右**にある `指数順位N`（TARGET の並び）→ ③N 番目の枠（指数を読み込んでいない枠の見出し `指数N` も1枠と数える）→ ④指数が1種類なら無印 `指数順位`。どれも無ければ同値同順位で計算し `[INFO]` を出す
- `--merge-one` / `--merge-only`: レースID(新) で横結合。**空欄は後続ファイルの値で埋め、行は和集合**。DB に入れるのは統合ファイルだけにするのが推奨（`--merge-only`）
- `--clean`: entries_out の CSV を消してから出力

### 3.4 build_db.py（CSV → racing.db）

- family の定義ファイル（`SHISU_FAMILIES_FILE` など）が無い・読めないときは、DB に触る前に終了コード2
- `--mode both|foragg|entries`, `--entries-only`（＝ `--mode entries`。出馬表だけ取り込み直す。馬体重発表後のマイニング確定値の差し替えなど。`--mode foragg`・`--fresh` とは一緒に使えない）, `--fresh`, `--prune`, `--no-xjoin`, `--agg-dir`, `--allow-empty`, `--entries-dir`, `--db-path`
- foragg（2026-11-10〜）: `agg_csv/families.json` の family を台帳に入れ（無ければ selected_families.json か初期セット）、`agg_master_*.csv.gz` を20万行ずつ読み、5つの率の `*_val` を文字列の括弧内（推定値）で作り直して **unified_all に直接入れる**（1表ずつのテーブル `foragg_*` は作らない。旧版は同じ行を foragg_* と unified_all の2回持っていた）。__tables_meta__ は「テーブル名」ごとに1行（kind=foragg・n_rows は unified_all の行数・source_file は読んだファイル。その名前の実テーブルは無い）。__facets__ は読みながら集めた値。→ 索引（照合用 (結合データ, family, indicator)、テーブル名、WEDGE、adjusted_WEDGE）→ **`__families__`**（family の定義）
  - 作り直しは `unified_all__build` に入れてから、最後に1つのトランザクションで前の unified_all と入れ替える（途中で止まっても前の unified_all が残る。画面は入れ替えまで前の DB を見る）。行はファイルごとにコミットする。入れ替えのトランザクションで索引を作るので、WAL はその間だけ索引の大きさほどに膨らむ（100万行で約180MB）。最後に `wal_checkpoint(TRUNCATE)` で切り詰める
  - 前の版の DB に残っている1表ずつのテーブル `foragg_*` は入れ替えのときに消す。`DROP_TABLE_LIMIT`（5,000）本を超えるときは消さずに WARN（DROP は1本ごとにスキーマを書き直すので本数の2乗で遅い。1.6万本で約13秒、数十万本だと何時間も）。画面は使わないので表示には影響しない。消してファイルを小さくするには `--fresh`
  - 読めない成績表（途中で切れた gzip・壊れた CSV）は、そのファイルの分だけ戻して飛ばし `[ERROR]`（終了コード1。ほかのファイルは入れる。`aggregate --resume` で作り直せる）
  - 書き終わりの印（`.done`）の設定のハッシュが成績表によって違う・印が無いものがあれば WARN（違う設定の成績表の混ざり。`aggregate --resume` でそろえる）
  - 旧来の1表1ファイル（`foragg_*.csv`）も読める（互換）。同じ master の `agg_master_*.csv.gz` があれば、その master の旧来のファイルは使わない（二重に入れない）
  - 読める成績表が0本なら DB に触らず終了コード2（空にしたいときだけ `--allow-empty`）。WEDGE 列の無い旧形式の成績表は取り込まず終了コード1
  - families.json に無い family・スキームの行・ファイル（前の実行の残り）は取り込まず、行数・本数を警告する
- entries: `normalize_entries_df`（キー部品の正規化と22条件。前走の内外は `master_csv/__inner_outer__.csv.gz` で引く。**障害レース**（今芝ダ=障・レース名が 障害…・トラックコード51以上。TARGET の出馬表は 芝ダ に「ダ」「芝」と入る）は 芝・ダ を「障」にし、照合しない）→ テーブル → entries_all → xjoin（各馬 × 指数 × family × キー候補を unified_all と完全一致照合）。`--mode entries` では DB の `__families__` の family で照合する（DB を作ったときの family）
  - unified_all が無い・旧形式なら xjoin を作らず終了コード1
  - レースID(新) が無い出馬表は「日付_場 R」をレースの識別子にする。旧形式の出馬表（`X_順位帯_s1` が無い）は警告

### 3.5 app.py（Flask）

- 接続のたびに DB の `__families__` を family の台帳に反映する（DB が変わったときだけ読み、台帳を作り直す。同じ名前で部品が変わっても置き換わる。無ければ初期セット）。family の選択肢・ラベル・その場照合・`/entries/wedge` の family 絞り込みはこの family
- `/`: 条件（場所・芝・ダ・距離・芝(内・外)・地域）、family、指数、オッズ帯、プリセット（絶対軸・軸候補・相手候補・消し候補。判定は `classify` と同じ SQL）、下限（件数・WEDGE・adjusted_WEDGE）で絞り込む
  - 表示は2通り: 「集計表ごと」（最大100表・各200行）と「まとめて」（unified_all の横断ランキング、上位500行。既定の並びは adjusted_WEDGE の大きい順）
  - 「集計表ごと」の中身（2026-11-10〜）: 常に unified_all の「テーブル名」が同じ行（`WHERE "テーブル名" = ?`。並べ替えの列の索引を使わせず `+列` で並べ、同点は rowid＝成績表の行の順）。前の版の DB に1表ずつのテーブル `foragg_*` が残っていても使わない（前の版の unified_all はそのテーブルを同じ順で詰め直したものなので表示は同じ。古い中身のテーブルが残っていても影響しないことをテストで確認）
  - 並べ替えは件数・各率の *_val・平均着順・回収値・WIN・EDGE・WEDGE・adjusted_WEDGE・adjusted_WEDGE_件数。空欄は常に最後。並べ替え欄の「既定」は表示に合わせる（集計表ごと=複勝率_val、まとめて=adjusted_WEDGE）。表示列に無い列で並べたらその列も表示する
  - 横断ランキングの速さ: 絞り込みが無ければ WEDGE/adjusted_WEDGE の索引で即座に返る。他の条件で絞るときは索引を使わず全件を読んで並べる（索引を最後までたどると遅くなるため）。合成550万行で最大約1.4秒
  - 下限の入力は全角・カンマ区切りも受ける。読めない値は入力欄に残して警告
- `/entries`: 日付（最新が既定）・場所でレース一覧。リンクは `レースID(新/馬番無)`（旧リンク `ridn=場 R` も開ける）
- `/entries/hits`: 馬ごとの該当数の要約と、区分ごとの表（WEDGE・adjusted_WEDGE・年数の列あり。区分ごとに約500行まで表示。先頭から切らずに (馬番, オッズ帯) ごとの上限をそろえる（行の少ない組は全部出し、余った枠は多い組に回す）。見出しと馬ごとの該当数は全件で数え、全件は `/entries/wedge.csv`。CSV には条件ヒット馬と同じ判定の「区分」列がある）。xjoin が古い形式ならその場の照合に切り替えて、その旨を表示。並びは オッズ帯 → 馬番 → 複勝率（消しは昇順）
- `/entries/key`: WEDGE・adjusted_WEDGE・年数の列あり
- `/entries/wedge`（レース別 WEDGE 一覧。`/entries` のレース一覧・`/entries/race`・`/entries/hits` からリンク）: そのレースの全馬 × 照合で該当した全キー（条件ヒット馬と同じ照合結果。xjoin が無ければその場照合）を、**閾値で足切りせず**に並べる（ただし DB に入っている行だけ。aggregate をコンパクト化（既定）したときは、区分にも WEDGE≧0.70 にも入らず件数30未満の行は出ない。全部見るなら `--no-compact`）
  - 絞り込みは選択だけ: オッズ帯（既定「通常」。「すべて」も可）、指数、パターン（family）、馬番。並べ替え: adjusted_WEDGE（既定）・WEDGE・WIN・EDGE・件数・adjusted_WEDGE_件数・馬番。空欄は常に最後
  - 上に「馬 × 指数」の要約（その馬・指数で該当したキーの adjusted_WEDGE 最大／WEDGE 最大・該当数、馬ごとの最大・平均）。色: 1.00以上・0.80以上・0.62以上・0.50未満
  - 画面の表は先頭3000行まで。`/entries/wedge.csv` は同じ条件の全行（UTF-8 BOM 付き）
- `/ingest`: 外部指数の取得（§3.6）
- DB が未構築・旧形式なら各画面の上部に作り直しの案内を出す

### 3.6 ingest.py ／ /ingest（外部指数の取得。`shisu/ingest/`）

旧来の別コンテナのシステムを統合したもの。ベースCSVを1回渡して、選んだ取得元を順に実行する（1つ失敗しても残りは続ける）。
**⑤ 一隊（ittai）は 2026-11-05 に廃止**（サイト側の仕様変更でスクレイピングできなくなった）。画面・CLI の選択肢から外し、`--sources ittai` は理由を添えて断る（終了コード2）。番号は旧来のまま（⑥ ラップ）。

| 取得元 | 入力 | 取りに行く先・1回の単位 | 照合キー | 出力（`ingest_out/{実行日時}/`） |
|---|---|---|---|---|
| omega ① Ω指数 | ベースCSV | `keibalab.jp/db/race/{rid12}/syutsuba.html`（1レース。rid12 = 11〜14桁目と17〜18桁目を除いた12桁） | rid12 + 枠番 + 馬番 | `omega/{YYYYMMDD}_omega.csv` |
| kichiuma ② 吉馬6指数 | ベースCSV | `kichiuma.net/php/search.php?race_id=…&p=fp`（1レース。場所コード1桁目 0→7・1→8） | 11〜14桁目を除いた14桁 | `kichiuma/kichiuma_{sp_ability,senkou,sp_trust,sp_control,sp_max,sueashi}/{YYYYMMDD}_kichiuma_{key}.csv` と `{元名}_{key}_merged.csv` |
| kayochin ③ かよちん | ベースCSV | `kayochinkeiba.com/{YYMMDD}{場所英名}/`（開催日×場所。中京=cyuukyo・京都=kyouto） | (YYMMDD+場所, R, 馬番) | `kayochin/{YYYYMMDD}_kayochin.csv` |
| keibaai ④ keibaAI | ベースCSV＋開始ID・終了ID | `muryou-keiba-ai.jp/predict/2021/01/06/{連番}/`（1連番。日付・場所・R はページの meta description / title） | 14桁 | `keibaAI/{YYYYMMDD}_keibaAI.csv` |
| lap ⑥ ラップ6指数 | ラップCSV | なし（手元で計算） | — | `lap/{2F,1F,4F,33rap,rabbit_2F,rabbit_1F}/{日付S}_{名前}.csv`, `lap/pace/{実行日}_pace_of_racecoment.csv` |
| yasui 安井 Excel | Excel（＋ベースCSV） | なし | 日付 + 場所コード + R + 馬番（14桁） | §3.7 |

- 出力は各取得元で共通: (a) マージ完全版 `{元ファイル名}_merged.csv`（ベースCSVの全列。ワーク1 は取得値だけ。取れなければ空欄。指数ごとにフォルダを分ける吉馬・安井は `{元ファイル名}_{key}_merged.csv`。旧 kichiuma と同じ名前）、(b) TARGET 取込用 `{YYYYMMDD}_{名前}.csv`（日付S ごとに `レースID(新), ワーク1` の2列・cp932。同じ レースID(新) は値のある行を優先して1行。旧 dedupe_keibaAI.py の処理も含む）
- **ext_indices へのコピー（2026-11-07〜。既定でする）**: 取得元が成功したら、TARGET 取込用の日付別CSV（`{YYYYMMDD}_{名前}.csv`。`レースID(新)` の見出しで始まるものだけ。`*_merged.csv`・`*_noid.csv`・ベースCSVの無い日の `日付, 場所, …` の形は置かない）を `ext_indices/` の取得元のフォルダに同じ形でコピーする: omega → `OMEGA/`、kichiuma → `kichiuma/kichiuma_{key}/`、kayochin → `kayochin/`、keibaai → `keibaAI/`、yasui → `yasui/yasui_{key}/`。§4.2 の振り分けでそのまま見つかるので、replace（過去側）と calc_entry_hensachi（出馬表側）が自動で結び付ける。ラップ6指数は台帳に未登録（§9）なのでコピーしない。画面はチェックボックス「ext_indices/ にも置く」（既定 ON）、CLI は `--no-ext-copy` で止める。コピーしたファイルは `Outputs.ext_files`
  - 同じ名前のファイルがすでにあれば丸ごと上書きせず、`レースID(新)` ごとに足し合わせる: 今回値の取れた馬だけ新しい値にし、取れなかった馬（空欄）は前の値を残す（ログに頭数）。取得できなかった日（サイトの停止など）・別の場所や範囲で取り直したときに、前に取った値を消さない。値が1頭も無いファイルは新しく作らない。すでにあるファイルは ext_indices と同じ読み方（BOM → UTF-8 → cp932。見出しなし・2列目の見出しが ワーク1 でなくても可。同じ馬が2行あれば値のある方）で読み、形式A として読めない同じ名前のファイル（手で置いた別の形）は上書きしない（WARN）。書くときは一時ファイルから置き換える
  - ext_indices に置けなかった（フォルダの場所にファイルがある・書けない）ときは、そのファイルだけ WARN を出して残りは続ける。取得元は成功のまま（ingest_out の出力は残る）
- 値はベースの1行ごとに辞書で引く（DataFrame の merge はしない）。ページに同じ馬が2回出てもベースの行は増えない
- ⑥ の計算・丸め・列名・ファイル名は旧 process_csv.py と同じ（同梱サンプル 20261017racerap.csv の79ファイルがバイト単位で一致することを確認済み）。違いは、ラップが数として読めない行を空欄にして続けること
- 旧版からの意図的な違い: かよちんは馬番の行と指数の行のセルを位置で対にする（旧版は空のセルを詰めてから対にしていたので、途中に空欄があると馬番と値がずれた）。ベースCSVの ワーク1 に元から入っていた値は使わない
- アクセス: 前回のアクセス開始から間隔をあける（既定 Ω・かよちん 3秒、ほか2秒。1秒未満にはできない）。失敗は2回まで間隔を伸ばしてやり直す。404 はやり直さない。User-Agent は `shisu-ingest/1.0`
- 画面 `/ingest`: 取得元のチェック、keibaAI の開始ID・終了ID、取得元ごとの間隔、入力（アップロード、または `ingest_in/{base,lap,yasui}/` に置いたファイルを選ぶ）→ 実行。裏のスレッドで動き、ジョブ画面に進み具合・ログ（2秒ごとに再読込）・中止ボタン、終わったら出力の一覧（1本ずつ・ZIP でダウンロード）。ログは `ingest_out/{実行日時}/ingest.log`、アップロードは `ingest_in/uploads/{実行日時}/`
  - 同時に動かすのは1件だけ。「実行中か」の確認と実行IDの予約を同じロックの中で行う（同じ秒に2回押しても2件目は 409、ID も重ならない）。入力エラーで受け付けなかったときは予約を外し、保存したアップロードを消してからエラー画面を出す（実行ボタンは押せるまま）
  - このプロセスで動かしていない過去の実行（サーバの再起動後など）の状態は `ingest.log` の最後の「終了」行から読む。無ければ「中断（終了の記録なし）」と表示する
  - `ingest_in/` にある UTF-8 で読めない名前のファイル（Windows の zip を展開した cp932 の名前など）は選択肢に出さず、名前の付け直しを促す
  - 同じ名前のファイルを複数アップロードしても上書きしない（`lap.csv`, `lap_2.csv`）。ベースCSVのマージ完全版も名前が重なれば `_2` を付けて分ける（大文字・小文字だけ違う `base.csv` と `base.CSV` も別にする）。ラップCSVで同じ レースID(新) の行は最初の1行を使う
  - 入力CSVは cp932 → utf-8-sig → utf-8 の順に読み、必須列（レースID(新)）が見つからなければ BOM なし UTF-8（Mac の Numbers の書き出しなど）として読み直す。空のファイル・CSV として読めないファイル（列数がそろわない等）は警告して飛ばす
  - ジョブはログファイルに書けなくても（フォルダが消えた・容量不足）止まらず、どんな終わり方でも「実行中」のまま残らない
  - 画面の下に「過去基本CSV（タイムM・対戦M）の統合・列パッチ」（§3.9）: アップロード、または `raw_csv/` に置いたファイルを選んで（既定で全部に印）`POST /ingest/raw_merge` で統合する・パッチを当てる。結果（既存・追加・重複の置き換え・10年より古い行の削除・残した年、パッチは 全場_*.csv ごとの更新行数・対象列）を表示する
  - 別のサイトのページからの POST（Origin が違う・`Sec-Fetch-Site: cross-site`）は 403。Host が `localhost`・IP アドレス・環境変数 `SHISU_ALLOWED_HOSTS`（カンマ区切り）以外なら /ingest 全体を 403（DNS リバインディング対策。Mac のホスト名で開くなら `SHISU_ALLOWED_HOSTS=mymac.local` などを渡す）。ログインは無い（§9）
- CLI: `python ingest.py --sources omega,kichiuma,…|all [--base …] [--lap …] [--yasui …] [--keibaai-start N --keibaai-end M] [--interval 秒] [--no-ext-copy] [--out DIR] [--run-id ID]`。入力の既定は `ingest_in/{base,lap,yasui}/` の全ファイル。終了コード: 0 正常 / 1 失敗した取得元あり / 2 入力の不足
- テストはネットワークに出ない（偽のセッションと、旧スクレイパーの解析コードが前提にしている構造の合成 HTML。keibaAI は旧 Readme に残っていた実ページの断片をまねた）。この開発環境からは各サイトに接続できないため、実ページでの確認は未実施

### 3.7 安井サロン Excel（`shisu/yasui_extractor.py`。ingest の取得元 yasui）

- 入力: `ステータス新バージョンYYYY_M月.xlsx`（形は §4.7）。ファイル名の年・月とシート名 MMDD で日付を決める
  - シートの月がファイルの月と半年以上離れていれば年をまたいだとみなす（2025_12月 の 0104 → 2026-01-04）
  - シートの月がファイルの月と違い、その日が火〜金曜で、ファイルの月に読み替えると土・日・月曜になるときは、シート名の打ち間違いとみなして読み替え、警告を出す（実例: 2025_11月 の 1029・1030 → 11/29（土）・11/30（日）。2026_2月 の 0301（日）はそのまま）
  - ファイル名から年が読めないシートは使わない（macOS の濁点分離・アップロード名の文字化けがあっても `2025_11` の部分が残っていれば読める）
- 取り出す値: ランク・体力・やる気（数のセル）と、5つのステータス（スピード・スタミナ・パワー・根性・賢さ）の「値」。`48⑥`→48、`-17⑫`→-17、`⑯`→値なし。丸数字は NFKC で数字に変わるので、分けてから値の部分を正規化する
- **丸数字の順位は照合に使わない**（`yasui_extract.csv` に `安井_スピード_Excel順位` などとして残すだけ）。過去側は TARGET に取り込んだ「値」から順位を出す（同値は同順位・値の無い馬は順位なし）が、Excel の丸数字は同値でも順位を分け、値の無い馬にも順位を付ける。出馬表側で丸数字を使うと過去側と順位がずれる（4ファイルでステータスごとに約1,400〜4,000頭）ので、出馬表側も値から計算する
- 出力（`yasui/`）: **吉馬6指数と同じく8指数ごとのフォルダ** `yasui_{key}/`（key = rank, speed, stamina, power, konjo, kashikosa, tairyoku, yaruki）
  - ベースCSVのある開催日: `yasui_{key}/{YYYYMMDD}_yasui_{key}.csv`（TARGET 取込用: `レースID(新), ワーク1`・cp932）と `yasui_{key}/{元ファイル名}_{key}_merged.csv`（マージ完全版）。TARGET 用は Excel にある開催日だけ書く（ベースCSVにある他の日の空のファイルは作らない）
  - ベースCSVの探し方: 指定されたベースCSV（画面で選ぶ・アップロード／CLI の `--base`、CLI は既定で `ingest_in/base/*.csv`）に無い「開催日×場所」は、`ingest_in/base/` と `entries_in/` の直下の CSV（`yasui_*.csv` は除く）を順に見て、その開催日×場所の行を持つファイルから補う（ログに使ったファイルを出す）。画面で安井 Excel だけを選んでベースCSVを忘れても、該当日の TARGET の出馬表CSVが `entries_in/` にあれば 18桁の レースID(新) が付く
    - 補ったファイルはベースCSVの列（レースID(新), 日付S, 枠番, 馬番, 馬名, 馬名S, ワーク1）だけを使う（出馬表CSVの何十列を、指定したベースのマージ完全版に混ぜない。マージ完全版は元ファイルごとに、そのファイルにある列だけを書く）
    - 候補のフォルダにある読めない CSV（列数のそろわない手書きのメモ、名前だけ .csv の Excel など）は、名前を出して警告し、候補から外す（安井の取り出しは止めない）
  - ベースCSVがどこにも無い開催日: `yasui_{key}/{YYYYMMDD}_yasui_{key}.csv` を `日付, 場所, 場所コード, R, 馬番, 馬名, ワーク1` で出し、「ベースCSVに存在しない開催日 N日（…）── 非開催日・シート名の日付の打ち間違いなどの可能性…ベースCSV（または出馬表CSV）を一緒に指定するか、ingest_in/base/・entries_in/ に置いてやり直してください」とログ・ジョブ画面に出す（`no_base_day_message`。レースID(新) が無いので TARGET にはそのまま取り込めず、ext_indices にもコピーしない。Excel には回・日が無いので18桁は作れない）
  - ベースCSVのある開催日でも、ある場所の行がどこにも無い馬は TARGET 取込用CSVに入らないので、`yasui_{key}/{YYYYMMDD}_yasui_{key}_noid.csv`（上と同じ 日付・場所・R・馬番 の形）に出し、件数と開催日・場所を案内する
  - `yasui_extract.csv`（全行。照合できた行は レースID(新) 付き）
  - **出馬表用CSV（`entries_in/yasui_{YYYYMMDD}.csv`）は 2026-11-07 に廃止**（`--yasui-to-entries`・画面のチェックも削除）。TARGET 取込用CSVが ext_indices/yasui/ にコピーされ（§3.6）、calc_entry_hensachi が出馬表の各馬に付ける（§3.3。ファイルに無い馬は 0 で、過去側と同じ数え方）
  - 警告は2種類に分けて出す: ①「ベースCSVに存在しない開催日 N日（12/06(土) 3頭、…）── 非開催日・シート名の日付の打ち間違いなどの可能性」（その日は 日付・場所… の形で出す）、②「ベースCSVに該当行が無い馬 N頭（11/01(土) 東京 12頭、…）── 別の月のシートの混入・少頭数・ベースCSVの収録期間外などの可能性。TARGET 取込用CSVに入れず `{日付}_yasui_{key}_noid.csv` に分けた」
  - ランク・5ステータスは台帳で `zero_missing`（0 = 欠損。§5.4・§5.5）。過去側で TARGET が 0 を出しても、出馬表側で 0 でも空欄でも、両側で欠損として数え、順位は TARGET の 指数順位 を使わずに値から計算する（TARGET の順位は 0 の馬も数えているため）。値が本当に 0 の馬は4ファイルで 0.1〜0.3%（ランク 9頭）で、これは欠損扱いになる。体力・やる気は約14%が 0 の普通の値なので 0 のまま比べる（Excel に無い馬は両側とも 0。ext_indices から読む過去側も、ファイルに値の無い馬を 0 にする。§3.1）
  - 出馬表側は ext_indices から付けた値で、順位は8指数とも値から計算する（形式A）
- 照合できた馬の枠番・馬名（ベースに 馬名 / 馬名S 列があれば）がベースと違えば件数を警告する（シートの日付違いの検出用）

### 3.8 screen_families.py（family 候補の一括採点と選抜）

DB を作らずにメモリ上で、family の候補を master ごとに**本番と同じ関数**（`build_keys` → `aggregate_frame` → `build_db.prepare_agg_frame` → `classify`）で集計・判定し、master を通して合計する。

- 候補（`--space`）= 骨格（舞台）× 条件 × [指数帯]（2026-11-07〜）:
  - 条件の候補（POOL。28種）は 重量種別・クラス名・枠番グループ・騎手 と 22条件の24列。[指数帯] は 指数順位帯（s1・s2）と 偏差値帯
  - 骨格: s0 コース不問 / s1 芝・ダ / s2 場所×芝・ダ / s3 芝・ダ×距離 / s4 芝・ダ×距離帯 / s5 場所×芝・ダ×距離帯 / s6 COURSE / s6n COURSE×重量種別×クラス名（normal の舞台）。場所・距離・芝(内・外) は単独の部品（§5.1）
  - 因果トリオ（s0）: ① 騎手 × ローテ（間隔帯・叩き・斤量帯・斤量増減・距離増減・輸送距離）× 前走（前走着順・前走人気・前走上り3F順・前走脚質・前走R印3・前走クワドラント）、② コースの物理属性 × (前走R印3／前走脚質) × (前走着順／前走上り3F順)、③ 前走人気 × 前走着順 × 今走の条件、④ 枠番グループ × (初角帯／F係数帯) × 前走脚質
  - `initial`: 初期セット（49の型・83 family・129通り）だけ
  - `default`（既定）: 初期セット ＋ s0 の単一条件・2条件ペア・因果トリオ ＋ s2〜s6・s6n × 単一条件（633テンプレート → 1,289 family・2,098通り）
  - `all`: ＋ s1 ×（ペア・トリオ）＋ s2・s3・s4・s6 × ペア ＋ s5 × 主要条件（MAJOR）のペア（2,371テンプレート → 4,635 family・7,557通り）
  - **冗長の除外**（`redundant()`。同じグループにしかならない組み合わせ）: COURSE とコースの物理属性（R値帯・直線帯・初角帯・CFS帯・F係数帯・今走クワドラント・左右・坂・距離帯）、場所 と 左右・坂、距離 と 距離帯、左右遷移 と 左右。部品の集合が同じ候補（並びだけ違う）は1つにまとめる（初期セットを優先）
  - 新しい組み合わせは `X_{部品の略号}`（偏差値帯は末尾 `_hen`。場所 `plc`・距離 `dist`・芝(内・外) `io` など）の名前になる
  - 依頼の目安との差: s2 × 単一条件は 26（左右・坂だけを除くので 28−2）、s2 × ペアは 325（26C2）、s3・s4 × ペアは 350（左右遷移＋左右の1組を除く）
- `screening_report.csv`（1行 = 候補 × スキーム）:
  - 基準A: `groups`（件数5以上のグループ数）/ `groups_ge10` / `coverage`（件数10以上のグループに入る馬の割合）/ `adj_groups`（adjusted_WEDGE が付いた数）/ `wedge_top`・`wedge_bottom`・`wedge_spread`、`adj_wedge_top`・`adj_wedge_bottom`・`adj_wedge_spread`（adjusted_WEDGE が付いたグループだけ）、`fuku_top`・`fuku_bottom`・`fuku_spread`（上位帯と下位帯の件数加重平均とその差。複勝率は頻度の%。上位帯・下位帯 = s1: 1位 / 10位~、s2: 1~3位 / 6位~、偏差値帯: 71以上 / 40以下、指数順位: 1 / 10以上）/ `monotonic`（隣り合う帯で上の帯の平均 WEDGE が高い割合）
  - 基準B: `abs_*`・`axis_*`・`opp_*`・`keshi_*`（オッズ帯4区分ごとと合計 `*_total`）＝ classify で絶対軸・軸候補・相手候補・消し候補になるキーの数（本番の DB を同じ family で作って判定した数と一致することをテストで確認）
  - `select_A`・`select_B`・`selected`・`reason`
- 選抜（`--export-selected [FILE]`。既定の書き先 `selected_families.json`。2026-11-09 に既定を変更）:
  - ① 基準B: 絶対軸＋軸候補のキーが `--min-axis`（既定25）個以上。`--always-select-abs`（既定 ON。`--no-always-select-abs` で切る）なら、絶対軸のキーが1つでもある候補は `--min-axis` に関係なく選ぶ
  - ② 基準A: `groups_ge10 >= --min-groups`（10）かつ `coverage >= --min-coverage`（0.2）かつ `fuku_spread >= --min-fuku-spread`（20.0 ＝ 複勝率の差20%ポイント）かつ `wedge_spread >= --min-spread`（0.10）を満たすもののうち、wedge_spread の大きい順に `--top-a`（既定100）個、または adj_wedge_spread の大きい順に `--top-a` 個（どちらかに入れば選ぶ。同じ値は name・scheme の順で、レポートの行の並びで選抜が変わらない）
  - `reason` 列: `B:軸が出る`（①の下限）・`B:絶対軸`（絶対軸だけで選ばれた）・`A:識別力`（wedge_spread 上位）・`A:識別力(adj)`（adj_wedge_spread 上位だけ）
  - 値の無い（NaN の）指標は満たさないとみなす。指数順位帯の family は選ばれたスキームだけを持つ（`"schemes": ["s2"]` など）。使った基準は selected_families.json の `criteria` に残す
  - 変更の理由: 旧既定（`--min-axis 1`・`--top-a 30`）では `--space all` の 7,557通りのうち 4,868通り（3,531 family）が選ばれ、全33指数の本集計が50時間を超えた（ユーザーの実データ）。約3分の1（約1,600通り・約1,200 family）に絞る目安で既定を決めた。選ばれる数はデータで変わるので `--from-report` で確かめて調整する
- **`--from-report [REPORT_CSV]`**（2026-11-09〜）: master を採点し直さず（`--space all` で約5時間かかる処理をしない）、採点済みのレポート（省略時は `--report` の値。既定 `screening_report.csv`）を読んで `select()` だけをかけ直し、`--report` に書き直す（`--report` を指定しなければ読んだファイルに書き戻す。`--from-report old_report.csv` で既定の screening_report.csv を上書きしない）（選抜の列 `select_A`・`select_B`・`selected`・`reason` を作り直す）。`--export-selected` なら selected_families.json も書く（family はレポートの `name`・`parts`・`tail`・`layer` から作り直す）。数秒。master_csv は読まない。レポートが無い・形が違えば終了コード2
- `--indicators`・`--regions` で master を絞る（全角・別名でもファイル名と同じ形にそろえて比べる）。`--workers N` で master を並列に。`--max-candidates` は試し用
- 採点できない master が1本でもあれば、レポートは書くが選抜（selected_families.json）は書かず終了コード1
- 目安（検証環境・1プロセス、30万行の master 1本、オッズ帯4区分）: 1通りあたり約2.4秒 → `initial`（129通り）約5分、`default`（2,098通り）約1.4時間、`all`（7,557通り）約5時間。master が多い（2地域 × 約34指数 = 約68本）と `default` は1プロセスで4日近くかかるので、`--indicators タイムM,対戦M` など代表の指数で選ぶか `--workers` を使う。メモリは1プロセス 0.5GB 前後（30万行）
- selected_families.json は family の定義（部品と tail）だけを持ち、指数・地域に依らない。全指数・全地域の集計（aggregate）に使われる

### 3.9 raw_csv/ の統合（`shisu/raw_merge.py`。replace の最初・/ingest の画面）

半年〜1年に1回、TARGET ★6タブで追加分の基本CSVを出したら `raw_csv/` に置く（または `/ingest` 画面からアップロード）。

- ファイルの扱いは中身と名前で決める（`file_role`）: ① `レースID(新)` があって `指数` 列の無いCSVは、名前に関係なく**列パッチ**（下）。② それ以外は名前（NFKC・小文字）で種類を決める: 「対戦」「match」→ 対戦M、「タイム」「time」「マイニング」「mining」→ タイムM（「対戦型マイニング」はマイニングも含むので、対戦を先に見る）。③ どちらでもない（指数 列があって名前で種類が分からない）ファイルは触らず、名前の付け方を WARN で案内する（raw_csv に残る。基本CSVを列パッチと取り違えると新しいレースの行が入らないので、名前の無い基本CSVはパッチにしない）
- 既存の `csv_files/全場_{種類}.csv` と raw_csv のファイル（**更新日時の古い順**、同じなら名前順）を縦につなぎ、`レースID(新)` が同じ馬の行は**後のファイルの行**を使う（追加で出し直したものが優先）。レース単位（16桁）の ID は 馬番 を付けて馬単位で比べる（そのままだと1レースが1頭に潰れる）。レースID(新) の無い行・16桁で馬番の読めない行は残す。列は全ファイルの和（無い列は空）。書き出しは一時ファイルに書いてから置き換える（途中で止まっても既存の 全場_*.csv を壊さない）
- **最大10年分**: 基準年（既定はデータの中の最新年。`merge_raw(ref_year=…)` で指定もできる）の9年前以降を残す。例: 2026年が基準なら 2017〜2026年を残し、2016年以前は消す。年は レースID(新) の先頭4桁（無ければ 日付S）。年の分からない行は残して WARN
- **列パッチ**（2026-11-08〜。`apply_patches`）: TARGET の前走データ読込は時間がかかるので、基本CSVは読み込まずに出し、前走の列だけを `レースID(新)` と一緒に別のCSVで出して raw_csv に置く使い方
  - 基本CSVの統合のあとに当てる（同じ回に置いた基本CSVの行にも当たる）。パッチが複数ファイルでも全部読み、更新日時の古い順につなぐ
  - キーは `レースID(新)`（18桁。16桁ならパッチの 馬番 を付ける。18桁にならない行は当てない）。`csv_files/全場_タイムM.csv`・`全場_対戦M.csv` の**両方**の同じ馬の行に、パッチにある列（`レースID(新)` と見出しの無い列 `Unnamed:*` 以外）の**空でない値**を上書き・補完する（パッチの空欄では上書きしない。同じ馬・同じ列が複数のパッチにあれば後のファイルの値。全場_*.csv に無い列は足す。見出しの前後の空白・全角半角のゆれ（`前走レース印3` と `前走レース印３` など）は 全場_*.csv の列に合わせる。`指数`・`指数順位` はタイムM・対戦M で値が違うので、パッチにあっても書き換えない）。書き出しは一時ファイルから置き換える
  - ログ・画面に「前走・追加列パッチを反映しました: 全場_タイムM.csv 更新 N 行・全場_対戦M.csv 更新 M 行（パッチ k 本 r 行・全場_*.csv に無い u 行。対象列: 間隔・…）」。更新行数は値が変わった行、対象列は値が変わった列
  - **パッチのファイルごとに**、全部の行が 全場_タイムM.csv・全場_対戦M.csv の**両方**に当たったものだけ merged_done/ に移す。片方の 全場_*.csv が無い（対戦M の基本CSVをまだ統合していない など）・そこに無い行がある（その期間の基本CSVがまだ）パッチは、当たった分は反映して raw_csv に残し、ファイル名と行数を WARN に出す（次に回すとまた当てる。同じ値なので書き換えは起きない。その基本CSVを統合した回に全部当たって移る）。基本CSVに無いレースの行が残るだけなら、そのファイルは手で merged_done/ に移すか消す
  - 当てる先は `全場_*.csv` の2本だけ（地域別の基本CSV `主５場_タイムM.csv` などには当てない）
- 書き出しは cp932。統合したファイルは `raw_csv/merged_done/` に移す（同じ名前があれば `_2` などを付ける。次回また統合しない）。読めないファイル・`レースID(新)` 列の無いファイルは WARN を出して raw_csv に残す
- replace_csv_ver3.py は raw_csv/ の直下に CSV があれば、master を作る前にこれを実行する（`--no-raw-merge` でしない）。`--raw-dir` の既定は `--csv-dir` と同じ階層の `raw_csv`（ふつうはリポジトリ直下の raw_csv。`--csv-dir` を別の場所にして試しに回しても、本物の raw_csv を取り込んで merged_done に移さない）。統合先は `全場_{種類}.csv` なので、地域を分けた基本CSV（`主５場_タイムM.csv` など）とは別の地域「全場」として集計される
- 画面（`/ingest` の下の欄）: アップロードは名前のパス部分を除いて `raw_csv/` に保存し（同じ名前は `_2`）、中身と名前で扱いを決める（基本CSV・列パッチのどちらでもないファイルは消して 400）。選べるのは raw_csv にあるファイルだけ。同時に1件（2件目は 409）、別サイトからの POST は 403

---

## 4. データ仕様

### 4.1 csv_files/*.csv（過去レース）

基本CSV（★6タブ。`202610.csv` の形）で使う列:

| 区分 | 列 |
|---|---|
| 必須 | `レースID(新)`（18桁: YYYYMMDD+場2+回2+日2+R2+馬番2）, `場所`, `距離`（「ダ1200」の形、または `芝・ダ` 列と数の距離）, `頭数`, `指数`, `着順`, `単勝オッズ` |
| あれば使う | `単勝配当`, `複勝配当`（無ければ回収値 0）, `指数順位`, `レースID(旧)`（形式B・D の照合。無ければ レースID(新) から作る）, `日付S`, `重量種別`, `クラス名`, `芝(内・外)`, `枠番`, `馬番`, `騎手`, `馬名`, `トラックコード(JV)`（51以上は障害） |
| 22条件 | `所属`（(美)/(栗)。[地]・[外] は空）, `間隔`（週数）, `休み明け～戦目`, `斤量`, `前走斤量`, `性別限定`, `前走場所`, `前芝・ダ`, `前距離`, `前走着順`（全角も可）, `前走人気`, `前走上り3F順`, `前走脚質`, `前走レース印３`, `前走レースID(新)` |

従来の ★8タブの形（`芝・ダ` 列が独立）もそのまま読める。

### 4.2 ext_indices/（外部指数CSV群。`shisu/ext_indices.py`）

ext_indices/ の下（階層は自由）のファイルを、フォルダ名・ファイル名（NFKC・小文字・`-`→`_` にして比べる）で指数に振り分ける。形は指数ごとに決まっている。

| 形式 | 中身 | 指数（置き方の例） | 照合 |
|---|---|---|---|
| A 馬単位・新18桁 | `レースID(新), 指数値`（見出しは有っても無くても良い） | オメガ指数（`OMEGA/`・`*_omega.csv`）、かよちん指数（`kayochin/`）、keibaAI指数（`keibaAI/`）、吉馬6指数（`kichiuma_sp_ability/`・`kichiuma-sp_ability/`・`kichiuma/kichiuma_sp_ability/` など）、安井8指数（`yasui_rank/` など。`/ingest` の出力をそのまま） | レースID(新)。順位は値から計算 |
| B 馬単位・旧10桁 | `旧ID, 指数値, 順位`（見出しなし。例 `0520380101,54,6`） | `StridePlus/`（大小文字・`Stride_Plus` も可）の下でファイル名の接頭辞: `sav` SAV / `st` ST指数（`st_se` は成績ST指数＝廃止）/ `gokei` 合計値 / `tenkai` 展開 / `siagari` 仕上り / `senko` 先行 / `tsuiso` 追走 / `jikyu` 持久 / `jizoku` 持続 / `shunpatsu` 瞬発 / `mae_stime` 想定前半 / `ato_stime` 想定後半 | 旧10桁。順位はファイルの値 |
| C レース単位・12桁 | `ID12, 指数1, 順位1, 指数2, 順位2, …`（馬番1から。例 `202609260601,64,1,13,13,…`） | Gold（`GoldAirplane/`・`gold*.csv`） | 12桁（YYYYMMDD+場2+R2）＋馬番。順位はファイルの値 |
| D レース単位・旧8桁 | `ID8, 指数1, 指数2, …`（馬番1から値だけ） | BC（`BCindex/`・`BC*.csv`。先頭の `RX` を除く）、ZI（`ZI_DATA/`・`ZI*.CSV`） | 旧8桁＋馬番。順位は値から計算 |

- ID: 旧10桁 = 場所2 + 年下2桁 + 回1 + 日1（10日=A, 11日=B, 12日=C）+ R2 + 馬番2。先頭の0が落ちて9桁（旧8桁なら7桁）になったものは0を補う。ベースCSVに レースID(旧) が無い・空なら レースID(新) から作る
- 文字コード: BOM があれば utf-8-sig、無ければ utf-8（先頭64KB で判定）→ cp932
- 同じ指数のファイルが複数あれば相対パスの順に読み、後のファイルの値を優先（空の値で上書きしない）。上書きの件数をログに出す
- ファイルに値の無い馬は 0 として扱う（§3.1。TARGET と同じ）。ファイルに無い馬の順位はレースの最下位・同順位（形式・順位を値から作るかどうか・指数の向きに関係なく。compute_indicator の last）
- 形式B は3列目（順位）が要る。先頭50行のどの行にも3列目の欄が無い（2列だけの）ファイルは使わない（使わないファイルの WARN に出る）。欄はあって空なら、そのレースは値から順位を作る（レースごと。同じ指数の他のファイルに順位があっても。replace が WARN を出す）
- 0 を欠損とする指数（想定前半・想定後半・安井のランク・5ステータス）では、ファイルに無い馬の 0 は欠損になる（順位なし。1レースに2頭以上ならそのレースは使わない）
- 使わないファイル: `*_merged.csv`・`yasui_extract.csv`・`*_noid.csv`・CSV でないもの・`entries_in/` の下、廃止した指数（カツラキ・HPRTV5C・成績ST指数（`st_se`）・一隊・33rap・上がり1F/2F。種類ごとに1行ログ）、どの指数か分からないもの・形が合わないもの（先頭5本を WARN）。フォルダ名とファイル名で指数が食い違うものは使わない
- 形式Aの見分け: 1行目が18桁の数字か `レースID(新)` の見出しで、列が2つ以下（`日付,場所,…` の安井の「ベースCSVが無い日」の出力は使わない）

### 4.3 agg_csv（成績表、1行=1結合データ）

- `agg_master_{地域}_{指数名}.csv.gz`（2026-11-10〜。master 1本につき1ファイル・cp932・gzip）: 下の表の列 ＋ `family, odds_mode, rank_scheme, indicator, region, テーブル名`。1つの成績表（family × スキーム × オッズ帯）の行は続けて並び、中の並びは結合データの昇順。テーブル名 = 旧来のファイル名（拡張子なし）を SQLite 用に安全化したもの
- 旧来の `foragg_{オッズ接頭辞}{rankband_スキーム_}{family}_{地域}_{指数名}.csv`（2026-11-09 まで。1表1ファイル）も build_db が読める。`shisu.aggstore.read_agg_tables` は両方を旧来の1表1ファイルの形（{ファイル名: 成績表}）に分けて返す（確認・テスト用）
- `agg_master_{地域}_{指数名}.csv.gz.done`: 書き終わりの印（JSON: settings（設定のハッシュ）, master, master_size, master_mtime_ns, rows, bytes）。`--resume` と build_db の混ざりの確認が見る
- `agg_settings.json`: 集計の設定（family の定義・スキーム・WEDGE・推定・コンパクト化）。前回と違えばログに出す

| 列 | 内容 |
|---|---|
| 結合データ, 件数 | キーと件数（≥5） |
| 勝率, 連対率, 複勝率, 2着率, 3着率 | `"45.00%（47.10%）"` ＝ 頻度%（推定%） |
| *_val（上の5つ）, 掲示板率_val | **CSV では頻度%**（0–100） |
| 掲示板率 | `"80.00%"`（頻度のみ） |
| 平均着順, 単勝回収値, 複勝回収値, 単勝適正回収値 | 数値 |
| WIN, EDGE, WEDGE, adjusted_WEDGE | 数値（小数4桁）。§6.2。adjusted_WEDGE は条件を満たす年が無ければ空 |
| adjusted_WEDGE_年数, adjusted_WEDGE_件数, adjusted_WEDGE_最終年 | adjusted_WEDGE に使った年の数・件数の合計・最も新しい年 |
| 提案勝率〜提案3着率 | 推定%のみ |
| 場所, 芝・ダ, 距離, 芝(内・外) | グループ内で最初の非空値。**family がその部品で絞っているときだけ**（コースを含む family は4つとも、ほかは parts にある 場所・芝・ダ・距離・芝(内・外) だけ。例: 場所×芝・ダ の family は 場所・芝・ダ。ほかは空）。画面の場所・芝・ダ・距離の絞り込みは、その部品を持たない family の行を出さない |

`agg_csv/families.json`: `{"version": 1, "families": [{"name", "parts", "tail", "label", "layer", "schemes"?}], "source"}`

### 4.4 master_csv（1頭1行）

`レースID(新), レースID(新/馬番無), 日付, year, 地域, 指数名, 場所, 芝・ダ, 距離_raw, 距離, 芝(内・外), 重量種別, クラス名, 頭数, 枠番, 枠番グループ, 馬番, 馬名, 騎手, 指数, 指数順位_元, 順位, 偏差値, 偏差値帯, 順位帯_s1, 順位帯_s2, 指数有効(0/1), 着順, 単勝オッズ, オッズ帯, 単勝配当, 複勝配当` ＋ 22条件の24列（§5.2）
（頭数10以上の全馬。障害は除く。除外レースの馬も `指数有効=0` で残す。`順位帯_s2` は表示用に「・」でつないだもの）

`__inner_outer__.csv.gz`: `レースID16, 芝(内・外)`（前走の内外を引く表。aggregate・screen は読まない）

### 4.5 entries_in / entries_out

- 入力（サンプル 348行×27列）: `レースID(新), 場 R, Ｒ, 場所, 芝ダ, 距離, 外回り, クラス, レース名, 年限定, 頭, 枠番, 馬番, 馬名S, 騎手,` ＋指数ブロック（`GoldAirplane, 指数順位1, ZI, 指数順位2, …, マイニング値, マイニング順位, 対戦型マイニング値, 対戦型マイニング順位`）
- 出力: 入力列 ＋ 各指数の `X_偏差値, X_偏差値帯, X_順位, X_順位帯_s1, X_順位帯_s2`（X は正式名）
- 同梱の `entries_out/entries_merged_hensachi.csv` は**旧版の出力**（列名・帯の定義が新版と違う）

### 4.5.1 指数取得の入力（ingest_in/）

| ファイル | 列 |
|---|---|
| ベースCSV（TARGET「レース一覧★画面イメージ」/ 出馬表分析★5タブ） | `レースID(新)`（18桁）, `日付S`（YYYYMMDD。空なら レースID の先頭8桁）, `枠番`, `馬番`, `ワーク1`（ほかの列はマージ完全版にそのまま残る） |
| ラップCSV（レース検索★5タブ・ワーク1 にレースのラップ） | `レースID(新), 日付S, 場所, 芝・ダ, 距離, コース区分, 馬場状態, クラス名, 年齢限定, 着順, 着差, 補正, レース印２, 1角, 2角, 3角, 4角, 人気, 単勝オッズ, ワーク1`（例 `" 7.1-11.0-11.7-12.0-12.2-12.3-12.4"`） |

### 4.7 安井サロン Excel（ステータス新バージョンYYYY_M月.xlsx）

2025年11月〜2026年2月の4ファイル（約1.6万頭）で確認した形。シート = 開催日（MMDD）、1行目 空・2行目 大分類（レース情報／基本情報／ステータス／状態／馬場適性／距離適性／脚質適性／スキル）・3行目 列名・4行目以降 1頭1行。A列（0）は空。列は3行目の見出しで探し、見つからなければ下の位置を使う。

| 列（0始まり） | 見出し | 内容 | 今の扱い |
|---|---|---|---|
| 1 | 場 | 東京・中山 など | 取り出す（場所コードに変換） |
| 2 | レース | R（数） | 取り出す |
| 3 | 条件 | `３歳未勝利　ダート1200ｍ` | 取り出す（表示用） |
| 4 / 5 | 枠番 / 馬番 | `1` / `01`（文字列） | 取り出す |
| 6〜8 | 状態 / 展開 / 能力 | ◎○▲△ または空 | **未使用**（定性情報。今後、予想コメント・特徴タグとして画面に出す候補） |
| 9 | ランク | 数（34, 40 など。新馬などは空） | `安井_ランク` |
| 10 | （見出しなし） | ランクの評価 `A+`〜`G` | **未使用**（定性情報の候補） |
| 11 | 馬名 | | 取り出す（照合の確認用） |
| 12 / 14 / 16 / 18 / 20 | スピード / スタミナ / パワー / 根性 / 賢さ | `48⑥`（値＋レース内順位の丸数字）。`-17⑫` の負数、`⑯` だけ（値なし）もある。丸数字は同値でも順位を分け、値の無い馬にも付く | `安井_スピード` など（値）。丸数字は `yasui_extract.csv` の `安井_スピード_Excel順位` などに残すだけ（照合に使わない） |
| 13 / 15 / 17 / 19 / 21 | （見出しなし） | 各ステータスの評価 `A+`〜`G` | **未使用**（定性情報の候補） |
| 22 | 体力 | 1〜5 | `安井_体力` |
| 23 | やる気 | 0〜5 | `安井_やる気` |
| 24〜25 | 馬場適性: 芝 / ダート | `S`・`A`〜`G` | **未使用**（定性情報の候補） |
| 26〜29 | 距離適性: 短距離 / マイル / 中距離 / 長距離 | `S`・`A`〜`G` | **未使用**（定性情報の候補） |
| 30〜33 | 脚質適性: 逃げ / 先行 / 差し / 追込 | `A`〜`G` | **未使用**（定性情報の候補） |
| 34〜47 | スキル: 直線 / コーナー / 根幹距離 / 非根幹距離 / 良馬場 / 道悪 / 右回り / 左回り / 内枠 / 外枠 / 急坂 / 平坦 / 夏 / 冬 | `○` または空 | **未使用**（特徴タグの候補） |

- 値の欠け: ランクは約13%の馬で空（ほぼ新馬戦）。ステータスの値が空（丸数字だけ、または空欄）の馬はスピード〜根性で約16%、賢さで約22%。§5.5 の規則（1レースに欠損2頭以上ならそのレースはその指数で使わない）で、スピード〜根性は約18%、賢さは約39%のレースが偏差値・順位の対象外になる（4ファイルでの実測）
- 定性情報（未使用の列）を使うときは、`yasui_extractor._record` で同じ行から読めばよい（列位置は `_column_positions` の見出し探索に足す）

### 4.6 DB（racing.db）

| テーブル | 内容 |
|---|---|
| `__tables_meta__` | table_name, source_file, kind(foragg/entries/xjoin), family, odds_mode, rank_scheme, indicator, region, n_rows。kind=foragg の table_name は unified_all の「テーブル名」（実テーブルは無い）。索引 kind |
| `unified_all` | 全成績表の縦結合（場所, 芝・ダ, 距離, 芝(内・外), 結合データ, 成績列, *_val（5つの率は推定値に置換）, WIN〜adjusted_WEDGE_最終年, family, odds_mode, rank_scheme, indicator, region, テーブル名）。索引 (結合データ, family, indicator)・テーブル名・WEDGE・adjusted_WEDGE |
| （`foragg_*`） | 2026-11-09 までの build_db が作った成績表1本=1テーブル。今は作らない（前の DB に残っていれば foragg の作り直しで消す。画面はあれば読む） |
| `__facets__` | 画面の選択肢（col, val） |
| `__families__` | family の定義（name, parts（JSON）, tail, label, layer, schemes）。aggregate の `agg_csv/families.json` から。照合・画面はこれを使う |
| `entries_*` | 出馬表（元の列 ＋ 正規化したキー部品 `場所/芝・ダ/距離/芝(内・外)/重量種別/クラス名/枠番グループ/騎手`、22条件の列、`日付`、`レースID(新/馬番無)`、`距離_raw`、`クラス名_raw`） |
| `entries_all` | 全出馬表の主要列の縦結合（画面では使わない） |
| `entries_xjoin_*` | 照合結果: source_table, 日付, レースID(新), レースID(新/馬番無), 場 R, 馬番, 枠番, 枠番グループ, 騎手, 馬名, base(指数), key, テーブル名, family, odds_mode, rank_scheme, indicator, region, 成績列（WEDGE 系を含む） |

`*_val` の意味:

| 列 | agg_csv | DB・画面の判定 |
|---|---|---|
| 勝率_val〜3着率_val | 頻度% | 推定%（括弧内） |
| 掲示板率_val | 頻度% | 頻度% |

率はすべて 0–100。画面側で 0–1 への換算はしない。

---

## 5. キーの規則（`shisu/`）

### 5.1 構成

キー = family の部品の値を `×` でつないだもの ＋ 末尾の指数の部品。**部品が1つでも空ならキーを作らない**。

- 部品（`shisu/families.py` の `PART_TOKENS`）: `COURSE`（`場所×芝・ダ×距離×[芝(内・外)]`。芝(内・外) は空なら省く）、`重量種別`、`クラス名`、`枠番グループ`、`騎手`、`芝・ダ`、単独の `場所`・`距離`（レンジ統合後の値。例 `16-18`）・`芝(内・外)`（2026-11-07〜）、22条件の列（§5.2）
  - 単独の 芝(内・外) は空なら（ほかの部品と同じく）キーを作らない（過去側でダート・内外の区別の無い芝はキーにならない）。出馬表側は 芝 のときだけ作り、値が分かればその値、分からなければコース属性マスタでその場所・距離（`距離_raw`）にある内外の値だけを試す（`course.inner_values`。例: 阪神芝1600 → 外、中山芝2000 → 内、東京芝 → 作らない）
  - COURSE と 芝・ダ・場所・距離・芝(内・外) を同じ family に入れると読み込みで弾く（COURSE が含んでいる）
- 末尾（tail）: `RANK`（指数順位そのもの）/ `RBAND`（指数順位帯。スキーム s1・s2 の2通りの集計表になる）/ `HBAND`（偏差値帯6種）
- 帯にする条件（前走着順・前走人気・前走上り3F順）は、キーを作るときに帯の名前にする。帯のスキームは family のスキームと同じ。これらを含む family は、末尾が偏差値帯・指数順位でも s1・s2 の2つの集計表になる（`Family.uses_schemes`）
- s2 は帯が重なるので、1頭から2つ以上のキーができる（指数2位・前走2着なら `…×前走1~3着×1~3位`・`…×前走1~3着×1~5位`・`…×前走1~5着×1~3位`・`…×前走1~5着×1~5位`）。過去側（集計）も出馬表側（照合）も全部作る
- 出馬表側は 芝(内・外) が分かれば `[値, 空]`、分からなければ `[空, 内, 外, 内外]` を試す。RBAND の family は s1・s2 両方で試す
- 例: `東京×ダ×1600×定量×1・2_勝×4`（normal）、`東京×ダ×1600×前走2~3着×4~5位`（D_prevfin, s1）、`髙杉吏麒×前走上り1~3位×1~5位`（B_jk_prevagari, s2）、`芝×芝マイル×R値帯の値×61~65`（C_sd_dist_r_hen）

### 5.2 部品の値（正規化と22条件）

- **文字**: すべて NFKC ＋ 空白除去（`Ｇ１→G1`、`Ｃ．ルメール→C.ルメール`、`ｵｰﾌﾟﾝ→オープン`）。書き出しは cp932（`髙` などを保持）
- **クラス名**: `500万/1000万/1勝/2勝→1・2_勝`、`1600万/3勝/オープン/OP/OP(L)/L→準OP・OP`、`G2/G3/重賞→重賞`、`G1/GI→G1`、未勝利・新馬はそのまま。`馬齢×(1・2_勝|準OP・OP)→1勝・OP`
- **重量種別**: 過去側は TARGET の列（`別定→定量`。値は 馬齢/定量/ハンデ）。出馬表側は `重量種別` 列があればそれ、無ければ推定:
  レース名のハンデ記号（`むらさＨ･3勝`・`目黒記念ＨG2`・`安土城Ｈ(L)`・`北陸ＳＨ･3勝`（ＳＨ・ＣＨ・ＴＨ）。`NHKマイルC`・`HTB賞` は対象外）→ハンデ、年限定に「上」→定量、2・3歳限定のオープン・リステッド（準OP・OP）→定量（TARGET では別定）、その他の2・3歳限定（新馬・未勝利・1勝・重賞・G1。菊花賞・秋華賞も TARGET は 馬齢）→馬齢、年限定が空→不明（重量種別を含むキーは作らない）。★6 サンプルの264レースで TARGET の 重量種別 と全件一致
- **距離**: `1600m`・`1600.0`・`ダ1600`→`1600`。レンジ統合（福島芝1700/1800→17-18、新潟芝 内1200/1400→12-14・外1600/1800→16-18・内2000/2200/2400→20-24・外3000/3200→30-32、中山ダ2400/2500→24-25、東京芝1600/1800→16-18・2300/2400→23-24・ダ1300/1400→13-14、中京ダ1800/1900→18-19、京都芝 内1400/1600→14-16・外1400/1600/1800→14-18・2000→2000×`内外`・外2200/2400→22-24・外3000/3200→30-32・ダ1800/1900→18-19、阪神芝1600/1800→16-18・2400/2600→24-26・3000/3200→30-32、小倉芝1700/1800→17-18）。コース属性・距離帯・距離増減は統合前の距離（`距離_raw`）で引く
- **芝(内・外)**: 過去側は TARGET の列。出馬表側は `外回り` 列が外→`外`、京都・新潟の芝でそれ以外→`内`、他は不明（空）
- **枠番グループ**: 10–13頭 枠1–5内/6–8外、14–16頭 1–4内/5–8外、17–18頭 1–3内/4–6中/7–8外、それ以外は空（両側共通）

**22条件**（`shisu/conditions.py`。列名 → 値。分からなければ空）:

| 列 | 値 | 元の列（過去側 ★6 / 出馬表） |
|---|---|---|
| 時期 | `12-2月` / `3-5月` / `6-9月` / `10-11月` | 日付（レースID(新)） |
| 輸送距離 | `110km以内` / `111-350` / `351-500` / `501-600` / `600km超` / `滞在`（輸送距離マスタ。地方・外国は空） | 所属 `(美)`・`(栗)` / 所属 `美浦`・`栗東`（2列目 `所属.1` も見る）× 場所 |
| 間隔帯 | `連闘` / `中2-3` / `中4-6` / `中7-9` / `中10-20` / `中21週以上` | 間隔（TARGET の週数。両側とも 1=連闘、2=中1週…。出馬表の `連闘`=1、`初走`=空）。値 w で 2〜3→中2-3、4〜6→中4-6 … と分ける |
| 叩き | `休み明け(1戦)` / `叩き2戦` / `叩き3戦` / `叩き4戦` / `叩き5戦以上` | 休み明け～戦目 / 休明戦目 |
| R値帯・直線帯・初角帯・CFS帯・F係数帯・今走クワドラント | コース属性マスタの帯（R値帯 `200以上`〜`100-120`、直線帯 `短(-299)`/`中(300-399)`/`長(400-)`、初角帯 `かなり短い(-149)`〜`長い(400-)`、CFS帯・F係数帯 `高`/`中`/`低`、クワドラント `S/H/T/O/ST/OT/SH/OH/N`） | (場所, 芝・ダ, 距離_raw, 芝(内・外)) で引く。内外が空なら (場所, 芝・ダ, 距離) で一意に決まるときだけ引く（京都芝1400/1600・新潟芝2000 は内外が要る） |
| 左右 | `左`（東京・中京・新潟）/ `右` | 場所 |
| 左右遷移 | `左→左` / `右→左` / `左→右` / `右→右`（前走が JRA 以外なら空） | 前走場所 → 場所 |
| 距離帯 | `芝短`(1000-1399) / `芝マイル`(1400-1799) / `芝中`(1800-2299) / `芝クラシック`(2300-2599) / `芝長`(2600-) / `ダ短`(-1399) / `ダマイル`(1400-1799) / `ダ中`(1800-2099) / `ダ長`(2100-) | 芝・ダ × 距離_raw |
| 坂 | `坂あり`（東京・中山・阪神・福島・中京）/ `坂なし` | 場所 |
| 前走クワドラント | 前走のコースのクワドラント | 前走場所・前芝・ダ・前距離（＋内外。内外が要るコースは 前走レースID の先頭16桁で `__inner_outer__` を引く。出馬表は `前走開催`（例 `260816 2新8  1R`。日が2桁だと固定幅で R が切れて `260712 1函10 11`）から同じ16桁を作る）。引けなければ空 |
| 斤量帯 | `51以下` / `51.5-53` / `53.5-55` / `55.5-57` / `57.5-59` / `59.5以上` | 斤量（減量記号 ◇△▲★☆ を除く） |
| 斤量増減 | `同じ` / `+0.5~+1.5` / `+2kg以上` / `-0.5~-1.5` / `-2kg以下` | 斤量 − 前走斤量（出馬表で 前斤量 が無ければ 斤量差） |
| 牝馬限定 | `牝馬限定` / `混合` | 両側同じ規則: 性別限定 の列があればそれ（`牝`）。無ければ、レース名に「牝」か、そのレースの全馬の性（性・性別）が牝。どれも無ければ空 |
| 距離増減 | `同距離` / `延長200` / `延長400` / `延長400超` / `短縮200` / `短縮400` / `短縮400超` | 距離_raw − 前距離（200以内・400以内・それ超） |
| 前走着順・前走人気・前走上り3F順 | 1以上の整数（キーでは帯 `前走2~3着`・`前走1~3人気`・`前走上り6位~` など。上り3F順の 0 は空） | 前走着順（`３`）/ 前着順（`３着`）、前走人気 / 前人気、前走上り3F順 / 前3F順 |
| 前走脚質 | `逃げ` / `先行` / `中団` / `後方`（ﾏｸﾘ・不明は空） | 前走脚質 / 前脚質（`逃`/`先`/`中`/`後`） |
| 前走R印3 | `HHH~HH` / `H` / `M` / `S` / `SS~SSS` | 前走レース印３ / 前レース印3 |

- 過去側と出馬表側で同じ馬なら 22条件の値がすべて一致することを、同じ前走を両側の列名で作ったデータでテストしている（`tests/test_conditions.py`）

### 5.3 family の台帳（`shisu/families.py`）

- `Family(name, parts, tail, label, layer, only)`。`only` は RBAND で使うスキームを絞る（selected_families.json の `"schemes"`）
- **初期セット**（`DEFAULT_TEMPLATES`。49の型）:
  - A（コース固定 15）: `normal`, `rankB`, `rankJ`, `rankBJ`, `normalJ`（指数順位）、その `*_band`（指数順位帯）、`hensachiA/B/J/BJ/AJ`（偏差値帯）
  - B（コース不問・騎手×条件 9）: `B_jk_prevfin`（騎手×前走着順）, `B_jk_prevpop`, `B_jk_prevagari`, `B_jk_prevstyle`, `B_jk_distchg`, `B_jk_interval`, `B_jk_tataki`, `B_jk_wtchg`, `B_sd_frame_jk`（芝・ダ×枠番グループ×騎手）
  - C（全国のコース属性 10）: `C_sd_dist_cfs`（芝・ダ×距離帯×CFS帯）, `C_sd_dist_f`, `C_sd_dist_r`, `C_sd_straight_slope`（芝・ダ×直線帯×坂）, `C_sd_corner_frame`（×初角帯×枠番グループ）, `C_sd_f_style`, `C_sd_straight_agari`, `C_sd_r3_style`, `C_sd_distchg_style`, `C_quad_prevquad`（今走クワドラント×前走クワドラント）
  - D（コース×条件 15）: `D_season`, `D_transport`, `D_interval`, `D_tataki`, `D_lr`（左右遷移）, `D_prevquad`, `D_weight`（斤量帯）, `D_wtchg`, `D_mare`, `D_distchg`, `D_prevfin`, `D_prevpop`, `D_prevstyle`, `D_prevagari`, `D_r3`
  - B〜D は指数順位帯（名前そのまま、s1・s2）と偏差値帯（名前 + `_hen`）の2つの family になる。前走着順・前走人気・前走上り3F順を含む偏差値帯の family（B_jk_prevfin_hen など7種）も、前走の帯のスキームで s1・s2 の2つになる → **83 family・129通り**
- **置き換え**: `configure()` が `selected_families.json`（`SHISU_FAMILIES_FILE`）を読めば、初期セットの代わりにその family だけを使う。任意の部品1〜（実用上）3個 ＋ tail の family を書ける:
  ```json
  {"families": [{"name": "X_jk_pfin_season", "parts": ["騎手", "前走着順", "時期"], "tail": "RBAND", "schemes": ["s2"]},
                {"name": "X_crs_mare_hen", "parts": ["COURSE", "牝馬限定"], "tail": "HBAND"}]}
  ```
  名前は英字で始まる英数字と `_`（61文字まで）。オッズ帯の接頭辞（`odds10u_` など）・`rankband_` で始まる名前、知らない部品・tail・同じ部品の重複、COURSE と 芝・ダ・場所・距離・芝(内・外) の同居、スキームを持たない family の `schemes` は読み込みで弾く。selected_families.json・DB の定義が正（初期セットと同じ名前を別の部品で使えば置き換わる）
- 使う family の受け渡し: aggregate → `agg_csv/families.json` → build_db → DB `__families__` → 照合（xjoin）・画面。成績表のファイル名・DB の family 列はこの名前（名前の前方一致は長い順に当てるので `D_prevfin` と `D_prevfin_hen` も区別できる）

### 5.4 指数の台帳（`shisu/indicators.py`）

| 正式名 | 別名 | 特例 |
|---|---|---|
| Gold | GoldAirplane, Gold指数 | |
| BC | BCindex | |
| ZI | ZI指数 | 0/欠損→同レース非ゼロ平均、新馬除外 |
| 吉馬_SP能力値・吉馬_先行力・吉馬_SP信頼・吉馬_SP調整・吉馬_SP最大・吉馬_末脚力 | | 新馬除外 |
| 展開 | 展開力 | 符号反転 |
| 先行・追走・持久・持続・瞬発 | 先行力・追走力・持久力・持続力・瞬発力 | |
| 想定前半・想定後半 | 想定前半3F・想定後半3F | 符号反転、0=欠損（3Fの秒数なので 0 はありえない。外部指数の無い馬に TARGET が出す 0 を最高値にしない） |
| タイムM | マイニング値, タイム指数, TimeM | 大きいほど良い（★6 の過去CSV 255レース全部で TARGET の 指数順位1 がレースの最大値。旧版は符号反転していた） |
| 対戦M | 対戦型マイニング値, MatchM | |
| 安井_ランク・安井_スピード・安井_スタミナ・安井_パワー・安井_根性・安井_賢さ | | 安井サロン Excel（§3.7）。0=欠損（zero_missing）・順位は値から計算 |
| 安井_体力・安井_やる気 | | 安井サロン Excel（§3.7）。0 も普通の値 |
| ほか | オメガ指数(Ω指数), かよちん指数, keibaAI指数, SAV, ST指数(ST), 合計値, 仕上り(仕上がり), 圧勝力 | |
| （廃止） | カツラキ, HPRTV5C, 成績ST指数, 結果分析スピード指数・一隊, 33rap, 上がり1F・上がり2F | 読まない |

照合は DB の `indicator` 列の**完全一致**（`先行` と `吉馬_先行力` は別物）。

### 5.5 偏差値・順位・帯（`compute_indicator`、両側共通）

0. 台帳で `zero_missing` の指数（安井のランク・5ステータス、想定前半・想定後半）は、0 を欠損にし、元の順位列（TARGET の 指数順位・StridePlus の3列目）を使わずに値から順位を付け直す（同値は同順位。TARGET の順位は 0 の馬も数えているため。両側とも同じ）
1. 同一レースで指数欠損2頭以上 → このレースはこの指数で使わない。1頭 → その馬の順位は空
2. ZI: 0/欠損を同レース非ゼロ平均で埋める
3. 符号反転・新馬除外（台帳どおり）
4. 有効2頭未満、または全頭同値（全頭0の未取得など）→ このレースは使わない
5. 偏差値 = (x−平均)/母標準偏差×10+50 を四捨五入。帯: `71以上 / 66~70 / 61~65 / 51~60 / 41~50 / 40以下`
6. 順位は元の順位列を優先（無ければ同値同順位）。**偏差値の向きは順位に合わせる**: 元の順位があれば、1位の馬がレースの最大値か最小値かで向きを決める（ファイル全体の多数決。台帳の「符号反転」は順位が無いときの既定。台帳は TARGET の向きに合わせてあるので、ふつうは同じになる）。ZI の順位を値から作るときは 0 の馬を最下位（同順位）にする（TARGET と同じ）
7. **順位帯（新8区分。`shisu/hensachi.py` の `RANK_SCHEMES`・`band_labels`）**: s1 = `1位` / `2~3位` / `4~5位` / `6~9位` / `10位~`（排他）、s2 = `1~3位` / `1~5位` / `6位~`（重なりあり）。前走着順（`前走1着`・`前走2~3着`…）・前走人気（`前走1人気`…）・前走上がり3F順（`前走上り1位`…）も同じ区切り
- 指数値・順位は NFKC・空白除去してから数値化する（全角数字を欠損にしない）

### 5.6 オッズ帯（`shisu/odds.py`）

aggregate が master を過去レースの**確定単勝オッズ**で絞って集計する: `normal`（なし）/ `odds10u`（10倍未満）/ `odds10_50`（10以上50未満）/ `odds50p`（50倍以上）。成績表のファイル名の接頭辞は `odds10u_` / `odds10_50_` / `odds50p_`。オッズ欠損の馬は normal にだけ入る。
画面の「オッズ帯」は「過去にそのオッズ帯だった馬の成績」で、当日の馬のオッズとは無関係に全馬へ表示される。

---

## 6. 評価指標

### 6.1 条件ヒット馬の分類（`shisu/classify.py`）

値: `fv`=複勝率_val（推定）, `rv`=連対率_val（推定）, `top5`=掲示板率_val（頻度）, `frc`=複勝回収値, `tra`=単勝適正回収値, `cnt`=件数。欠損は「満たさない」。
判定順 **消し → 絶対軸 → 軸候補 → 相手** で、最初に当てはまった区分を1つだけ付ける。

| 区分 | 条件 |
|---|---|
| 消し候補 | (cnt≥20 かつ fv<5 かつ top5<10) または (odds10u かつ cnt≥20 かつ fv<20) |
| 絶対軸 | cnt≥10 かつ rv≥80 かつ (frc≥100 または tra≥100) |
| 軸候補 | cnt≥10 かつ [fv≥80&top5≥90 / rv≥60&top5≥90 / fv≥70&top5≥80&frc≥100 / rv≥50&top5≥80&frc≥100] のいずれか |
| 相手候補 | cnt≥30 かつ [fv≥60&top5≥70 / rv≥50&top5≥70 / fv≥33&frc≥150&top5≥50] のいずれか |

推定値は「軸は下限、消しは上限」で判定する設計（複勝率の推定は頻度20%以下なら上側90%点）。閾値は `classify.py` の定数で変える。`/` のプリセットも同じ定義の SQL を使う。WEDGE はこの分類には使っていない（表示と並べ替え・絞り込み用）。

### 6.2 WEDGE（`shisu/wedge.py`、aggregate が成績表の列として計算）

グループ（結合データ）ごとに、aggregate の分母と同じ馬（取消・除外を除く。失格・中止は着順=頭数）で計算する。

- **WIN**（決定力）= (1着数×3.0 + 2着数×1.5 + 3着数×1.0) / (件数×3.0)
- **EDGE**（市場の期待を超える力）= 成功数 / オッズのある件数。単勝オッズ（100倍超は100倍にクリップ）の帯ごとに、着順が次以内なら成功:
  1.0〜1.4倍→1着 / 1.5〜2.9→1着 / 3.0〜4.9→2着 / 5.0〜9.9→3着 / 10.0〜19.9→4着 / 20.0〜49.9→6着 / 50.0〜100.0→8着。オッズが無い・1倍未満の馬は EDGE の分母から外す。着順が数値にならない馬は WIN と同じく分母に入れて失敗扱い
- **WEDGE** = WIN + EDGE（目安: 1.00以上=トップ級、0.62前後=平均、0.50未満=下位）
- **adjusted_WEDGE**（年度加重）:
  1. 年（master の `year`。1900〜2100 の整数だけ）ごとに WEDGE を出す
  2. 窓 = 終わりの年から遡る10年（`--wedge-max-years`）。終わりの年は既定（`data`）で**master 全体の最終年**（＝データとしての「今」）。`--wedge-window key` なら結合データ自身の最終年。指数によって過去データは3〜10年分と幅があるので、窓の中の年は**すべて**使う
  3. 件数が10未満（`--wedge-min-year-count`）の年は除く（1走だけのマグレ当たりなどで歪むのを防ぐ）
  4. 残った年に指数減衰のウェイト `w = decay ** (最終年 − 年)` を掛けて合計1に正規化し、加重平均する（`--wedge-decay`、既定0.65＝半減期 約1.6年）。年の差は暦で数えるので、開催の無い年・件数不足の年をはさむと、それより古い年はその分軽くなる
     - 例: 5年そろえば ≒ `[0.07, 0.11, 0.17, 0.26, 0.40]`（Phase 3 の固定ウェイト `[0.06, 0.11, 0.18, 0.25, 0.40]` とほぼ同じ）、4年 ≒ `[0.12, 0.18, 0.28, 0.43]`、10年 ≒ 新しい年から 0.35, 0.23, 0.15, 0.10, 0.06, …, 0.007（古い5年の合計 約1割）、1年 → 1.0
  5. 残った年が無ければ空。使った年数・件数の合計・最終年を `adjusted_WEDGE_年数/件数/最終年` に出す（年数が1〜2しかない値は、年数・件数の列で確かめて使う）
- オッズ帯別の成績表（odds10u など）でも同じ EDGE 表を使う。窓の終わりは4本とも同じ（master 全体の最終年）
- 正しさの確認: テストで、ベクトル版 `wedge_columns` と仕様どおりの1グループ版（`wedge_from_frame`・`adjusted_wedge`）をランダムデータと手計算の例で照合している

---

## 7. Docker・実行環境

- `Dockerfile`: `python:3.11.18-slim-bookworm`（`--build-arg PYTHON_IMAGE=...` で差し替え可）、ja_JP.UTF-8、sqlite3 CLI、`requirements-dev.txt`（実行用＋pytest）を固定版でインストール
- `requirements.txt`: pandas 2.3.3 / numpy 2.3.5 / scipy 1.16.3 / Flask 3.1.3 / requests 2.33.1 / beautifulsoup4 4.14.2 / lxml 6.1.3 / openpyxl 3.1.5（依存も固定）。lxml は Ω・吉馬・かよちんの HTML 解析（`BeautifulSoup(html, "lxml")`）で使う。2026-11-09 に Dependabot のアラート3件を解消: lxml 6.0.2 → 6.1.3（High。CVE-2026-41066 / GHSA-vfmq-68hx-4jfw。XML の外部実体で手元のファイルを読まれる。6.1.0 で修正）、Flask 3.1.2 → 3.1.3（CVE-2026-27205 / GHSA-68rp-wp8r-4726。session のキーだけを見たとき `Vary: Cookie` が付かない。キャッシュするプロキシの裏で問題になる）、requests 2.32.5 → 2.33.1（CVE-2026-25645 / GHSA-gc5v-m9x4-r6x2。`extract_zipped_paths()` の一時ファイル名が予測できる。このコードは呼んでいない）。固定版は pip-audit で既知の脆弱性なしを確認した。テストは pandas 2.3.3 と 3.0.5 の両方、Docker（Python 3.11.18）で通ることを確認済み
- Dockerfile は `ingest_in/{base,lap,yasui}`・`ingest_out` も作る。`/ingest` は `docker run -p 8000:8000 …  python app.py` の画面から使う（スクレイピングはコンテナからインターネットに出られることが前提）
- パス: 全スクリプトが `shisu/config.py` を使う（既定はリポジトリ直下。Docker では /app）。`SHISU_BASE_DIR`・`DB_PATH` で変更可。Docker 外でもそのまま動く
- 運用は `-v "$(pwd)":/app` でホストを丸ごとマウント。コード変更は再ビルド不要、`requirements*.txt` を変えたら再ビルド
- SQLite 3.30 以上（`NULLS LAST` を使用）。bookworm は 3.40

---

## 8. テスト

`python -m pytest`（Docker: `docker run --rm -v "$(pwd)":/app csv_processor_shisu python -m pytest`）

- `tests/test_core.py`: 正規化・台帳・偏差値の規則・順位帯（新8区分。s1 は排他、s2 は 1〜3位が2帯）・キー（**全129通りで、過去側のベクトル版 `build_keys` と出馬表側の `entry_keys` が同じキーを作る**こと（内外の候補を除けば完全一致）、照合の窓口 `entry_key_candidates` に過去側のキーが必ずあること）・family の数（49の型・83 family・129通り）・ファイル名（family 名の前方一致）・オッズ・着順・分類（境界値、Python 版と SQL 版の一致）・WEDGE
- `tests/test_conditions.py`: 22条件の規則（時期・間隔・叩き・斤量・増減・距離帯・輸送距離・左右・坂・コース属性マスタ・脚質・R印3・前走の内外の引き方）と、**同じ馬・同じ前走を ★6タブの列名と出馬表の列名で作ったとき、22条件の値がすべて一致し、全129通りのキーが出馬表側の照合候補（`matching.entry_fields` 経由）に入る**こと
- `tests/test_replace_ext.py`: 基本CSV（距離「芝1600」の形・配当なし・9桁の旧ID・全角の着順）＋ ext_indices の4形式で、各指数の値が正しい馬に付くこと（形式B・C の順位）、csv_files の同名の指数が優先されること、廃止した指数を読まないこと、障害（距離「障」・トラックコード51以上）を除くこと、配当なしでも集計できること、`--no-ext`
- `tests/test_ext_indices.py`: ext_indices の ID 変換（16進の日・9桁/7桁・RX）、フォルダ・ファイル名の振り分け（StridePlus の接頭辞・st_se・吉馬・安井・食い違い）、使わないファイル、文字コード、4形式の読み取りと attach、後のファイルの優先、速さ（20万行）
- `tests/test_screen_families.py`: screening_report.csv の列・行数、**基準B の該当キー数が本番（aggregate → build_db → classify）と完全一致**すること、件数10以上のグループ数の一致、選抜と selected_families.json、候補の作り方、**selected_families.json で family を置き換えたとき aggregate・build_db（`__families__`・unified_all・xjoin）・画面がその family だけを使う**こと（スキームの絞り込みも）、不正な定義を弾くこと
- `tests/test_scripts.py`: 各スクリプトの関数単位（指数順位N の対応付け、旧形式の順位 `13.0`、頭数列なし、mean モード、--clean など）
- `tests/test_pipeline.py`: 合成データ（`tests/synth.py`。空欄の内外・髙・Ｇ１・全頭0・欠損2頭、★6タブ・出馬表の前走の列を含む）で replace → aggregate → calc → build_db → app を通し、旧版の不具合が再発しないこと、xjoin とその場照合の分類結果が一致すること、`--mode entries --prune` で foragg_* が消えないこと、`/entries/wedge` が閾値なしで全キーを出すこと
- `tests/test_band_regression.py`: 旧版の band 系の件数・勝率の膨張（§11）の再発防止（新8区分・master からのキーで）。レース単位（16桁）のIDでも馬単位と成績表が完全に一致すること、どの family でも1頭が同じキーに2回入らないこと（s2 でも1頭2キーまで）
- `tests/test_verified_fixes.py`: 検証ワークフローで再現した不具合の再発防止（★6 の「外」「消」、取消の馬の 0、偏差値の向き、ZI の順位、外部指数の値の無い馬、出馬表の障害、前走開催、牝馬限定、廃止名の括弧、family の予約名・定義の置き換え・指定ファイルが無い・スキームの絞り込み・成績表のコース列・古い成績表・画面の同期と wedge の絞り込み、screen の引数・終了コード）
- `tests/test_ingest.py`: 指数取得の各取得元、ラップ6指数（★6 の「ダ1200」形の距離も）、安井 Excel（警告の2種類・出馬表用CSVを作らないこと）、/ingest 画面、**ext_indices へのコピー**（既定 ON・外すとしない・CLI の `--no-ext-copy`・コピー先が §4.2 の振り分けで正しい指数として見つかること・安井の 0=欠損が過去側と出馬表側で一致すること・同じ名前のファイルは馬ごとに足し合わせ、値の無いファイルを置かず、形の違うファイルを上書きしないこと・置けなくても取得元は成功のまま）
- `tests/test_entries_ext.py`: 出馬表1本（マイニング値だけ）に ext_indices の指数（オメガ・ST指数・Gold・BC・ZI）が付き、**master と順位・偏差値・偏差値帯・順位帯が一致する**こと、出馬表に値のある指数は出馬表の値を使うこと、0 だけの列（と右の 指数順位1）を ext で上書きすること、**優先がファイル・レースごと**（別のファイルに値があっても・0 だけのレースは ext から付き、過去側と同じ順位）、台帳に無い指数の 指数順位N を足した列に回さないこと、`--no-ext`・レースID の無い出馬表
- `tests/test_raw_merge.py`: ファイル名からの種類、**列パッチ**（中身での見分け・2本のパッチを 全場_タイムM・全場_対戦M の両方に当てる・後のファイル優先・空欄で上書きしない・無い列を足す・16桁の ID＋馬番・18桁でない行・当てる先が無い／当たらない行のあるパッチは残す・基本CSVと同じ回・あとから基本CSVを統合すると当たって移る・片方の 全場_*.csv がまだ無いときは残す・ファイルごとに移すかを決める・見出しのゆれ・指数順位を書き換えない・replace の master の22条件に入る・画面からのアップロード）、重複の置き換え（後のファイル優先）・10年の窓・merged_done への移動（同じ名前は `_2`）・種類の分からないファイルと読めないファイルを残すこと、16桁の ID でも馬ごとに比べること、書き込みの途中失敗で既存の 全場_*.csv を壊さないこと、`--raw-dir` の既定が `--csv-dir` の隣になること、replace が master の前に統合すること（`--no-raw-merge`）、/ingest のアップロードと選択・パスの除去・不正な名前・別サイトからの POST
- `tests/test_screen_families.py` には候補の数（骨格・因果トリオ）・冗長の除外・単独の 場所/距離/芝(内・外) を含む family の過去側と出馬表側のキーの一致（単独の 芝(内・外) はダート・内外の区別の無い芝で両側ともキーを作らない）・成績表のコース列・select の複勝スプレッド・adj_wedge_* も
- `tests/test_aggstore.py`（2026-11-10）: master ごとの1ファイルとメタ列・テーブル名、`classify_frame` と1行版の一致（境界値・欠損・4オッズ帯）、旧来のファイル名の速い解析が前の当て方と同じこと、**コンパクト化で残る行が仕様の条件から独立に計算した行と完全に一致**すること・DB の4区分と WEDGE≧0.70 の行が全部の行の DB と同じこと・layer A は全部残ること・下限のオプション、`--resume`（済んだ master を触らない・書きかけを消す・設定を変えた実行が途中で止まっても今の設定にそろう・途中で切れたファイルと作り直した master は作り直す・--clean と一緒なら止める）、全部の master が失敗したときの終了コードとメッセージ、下限の `none` と 0 以下を弾くこと、並列のまとまり（子プロセスを作り直しても1プロセスと同じバイト列）、build_db が旧来の1表1ファイルからでも同じ unified_all・__tables_meta__ を作ること・agg_master と重なる旧来のファイルを使わないこと・前の版の1表ずつのテーブルを消すこと（多すぎれば残して WARN）・読める成績表が無ければ DB を変えないこと・途中で切れたファイルを飛ばして [ERROR]・違う設定の混ざりの WARN、__facets__、**「集計表ごと」の画面が、前の版の DB（1表ずつのテーブルあり・古い中身のテーブルあり）でも新しい DB でも同じ HTML**になること
- `tests/test_screen_families.py` に選抜の新しい規則（基準B の絶対軸・基準A の adj 上位）と `--from-report`（採点し直さず同じ選抜になる・基準を変えると減る・基準が JSON に残る・読んだファイルに書き戻す・ファイル名なしの --from-report に --report を付けると --report を読む・レポートが無い/形が違うと終了コード2）・同じ値の並びに依らない選抜、`tests/test_pipeline.py` に `build_db --entries-only`（`--fresh`・`--mode foragg` と一緒なら弾く）と replace の `--workers 2`（まとまりごとに子プロセスを作り直しても1プロセスと同じ master・子プロセスが止められてもそのまとまりを [ERROR] にして残りを続ける）
- 件数: 464（pandas 2.3.3・3.0.5・Docker（Python 3.11）で全件パス）

---

## 9. 既知の制約・要確認事項

1. **集計量と DB の大きさ**: 成績表の行数は family の数にほぼ比例する。実サンプル `202610.csv`（1か月・3,188頭）・初期セットで、全部の行は 35,998行・コンパクト化（既定）で 13,587行。2026-11-10 から DB は unified_all だけに持つ（前は1表ずつのテーブルにも同じ行を持っていた）。数千 family を全指数で集計するときは、コンパクト化のまま回す。**既定の規則（件数≧30・WEDGE≧0.70）では行は半分ほどにしかならない**（§3.2 の実測。4,868通り × 33指数で DB 約180GB の見積もり）。ディスクが足りなければ `--compact-min-count none --compact-min-wedge 1.0`（同 約21GB）などで絞る。**集計の時間は family の数に比例し、コンパクト化では減らない**（30万行の master 1本 × 129通りで約3分 → 4,868通りなら1本あたり約2時間・33指数で1プロセス約3日。`--workers` と `--resume` で分けて回す）。時間が足りなければ screen_families.py で family を絞る
2. **screen_families の時間**: 30万行の master 1本で1通り約2.4秒（`default` 2,098通りで約1.4時間、`all` 7,557通りで約5時間）。全 master で `default` は1プロセスで4日近く。代表の指数（`--indicators タイムM,対戦M`）で選ぶか `--workers` で並列にする
3. **外部指数CSV（ext_indices/）は実ファイルで未確認**: 形式A〜D とフォルダ名・ファイル名の規則は依頼文の仕様と実データ例から組んだ。最初の実行では replace のログ（`外部指数が付いた馬 x/y`・使わないファイルの WARN）を確認する。形式Aは `/ingest` の TARGET 取込用CSVと同じ形
4. **間隔帯**: TARGET の「間隔」を両側とも週数（1=連闘、2=中1週）とみなし、値 2〜3 を `中2-3` のように分けている（「中N週」の N ではなく TARGET の値そのもの）。出馬表サンプル（`test.csv`）の「2」が前走から14〜15日だったことからの判断。中N週の N で区切りたい場合は `conditions.interval_band` を1つずらす
5. **牝馬限定（出馬表側）**: 出馬表に `性別限定` 列が無いので、レース名に「牝」がある・そのレースの全馬が牝のときに `牝馬限定` とする。過去側は ★6 の `性別限定` 列。牝馬限定戦に牡馬が1頭でも入る（実際には無い）・レース名に「牝」を含む混合戦があればずれる
6. **前走脚質**: 出馬表の `マ`（マクリ）・過去側の `ﾏｸﾘ` は4区分（逃げ・先行・中団・後方）に入らないので空（その馬は前走脚質を含むキーを作らない）
7. **前走クワドラント・左右遷移・輸送距離**: 前走が地方・海外、コース属性マスタに無いコース（その場に無い距離など）、内外が分からない京都芝1400/1600・新潟芝2000 で前走レースID が過去データに無い馬は空。所属が地方・外国の馬の輸送距離は空
8. **重量種別の推定**（出馬表側）: ★6 サンプルでは全件一致したが、2・3歳限定の別定・ハンデの重賞（例: ラジオNIKKEI賞）は過去側とずれる。TARGET の出馬表出力に `重量種別` 列を追加できれば、その列が優先されて解消する
9. **芝(内・外)**: 出馬表から分かるのは京都・新潟の内外（外回り列）だけ。他場は候補を全部試すので、過去側に「空」と「内/外」の両方のグループがあると同じ馬に2行ヒットすることがある [要実データ]
10. **9頭以下の出馬表**: 過去側は10頭以上だけを集計している。出馬表側は頭数で絞らないので、少頭数レースの順位・偏差値は多頭数の成績と照合される（枠番グループ系は付かない）
11. 地域の重複: 主5場ファイルと各場ファイルの両方があれば、同じキーが両方の表でヒットする（地域列で区別して表示）
12. 同梱の `entries_out/entries_merged_hensachi.csv` は旧版の出力で、新版の列（`X_順位帯_s1` など）は無い。再生成して使う
13. **xjoin の容量**: 1頭×1指数あたり family × キー候補 × 最大50行を保存する。family を絞る（selected_families.json）と小さくなる。コンパクト化した DB では、平凡な行（区分外・WEDGE<0.70・件数<30）とは照合されないので、そのぶん小さい
14. **adjusted_WEDGE の付きやすさ**: 「年10件以上」は細かいキーほど満たしにくい。aggregate の最後のログで割合を見る。値の信頼度は `adjusted_WEDGE_年数`・`adjusted_WEDGE_件数` で確認する
15. **WEDGE の小標本**: 全期間の WEDGE には件数の下限が無い（成績表の件数≥5 だけ）。横断ランキングでは「件数 下限」と併用する
16. **外部指数の取得（スクレイピング）は実ページで未確認**（この開発環境から各サイトに接続できない）
17. **安井 Excel の値の欠け**: §4.7。両側の一致は「TARGET は取り込んでいない馬を 0 で出す」前提 [要実データ]
18. **ラップ6指数は台帳に未登録**: 集計に使うときに `shisu/indicators.py` に1行足す（向きを決めて reverse を付ける）
19. **展開（StridePlus）の 0**: 「小さいほど良い」指数なので、外部指数ファイルに無い馬の 0 は最高値として数えられる（出馬表側の TARGET の 0 と同じなので両側は一致する）。展開の値に 0 がありえないなら台帳で zero_missing にする [要実データ]
20. **画面にログインが無い**: `docker run -p 127.0.0.1:8000:8000 …` で起動する
21. サンプルで分かったこと: `202610.csv` には `単勝配当`・`複勝配当` の列が無い（本番では足す前提。無いと回収値は 0）。`test.csv` のマイニング値・対戦型マイニング値は空だった（照合の確認は値を入れて行った）
22. **raw_csv の統合先は「全場」**: `csv_files/全場_タイムM.csv`・`全場_対戦M.csv` に統合する。同じ csv_files に地域別の基本CSV（`主５場_タイムM.csv` など）もあれば、全場と地域別が別の地域として両方集計される（11 と同じく同じキーが両方の表でヒットする）。全場で運用するなら地域別のファイルは置かない。10年の基準年は既定でデータの中の最新年（実行日の年ではない）
23. **出馬表に値のある指数の判定**: ファイル・レースごと。そのレースに 0・空欄以外の値が1頭でもあれば、その指数はそのレースで ext_indices を使わない（TARGET で読み込んだ指数は TARGET の値を使う。読み込んだが一部の馬だけに値がある場合も、残りの馬は 0 のまま。過去側の ext は値の無い馬を 0 にするので数え方は同じ）
24. **ext_indices へのコピーは馬ごとの足し合わせ**: /ingest は同じ名前の `{YYYYMMDD}_{名前}.csv` に、今回値の取れた馬だけを新しい値で書き込み、取れなかった馬は前の値を残す。値を消したい（誤った値を取り込んだ）ときは ext_indices のファイルを手で消してから取り直す
25. **列パッチと基本CSVの出し直しの重なり**: 基本CSVの統合は「同じ馬は後のファイルの行」（§3.9）なので、前走を読み込まずに出し直した基本CSVが、パッチを当て済みの期間と重なると、その行の前走の列は空に戻る。重なる期間を出し直したときは、その期間の列パッチも raw_csv に置き直す（同じ回に置けば、統合のあとに当たる）
26. **コンパクト化で落ちる行**（aggregate の既定）: 区分（4つ）にも WEDGE・adjusted_WEDGE≧0.70 にも入らず、件数30未満で、layer A でもない行は agg_csv にも DB にも入らない。条件ヒット馬・横断検索のプリセット・WEDGE の上位には影響しないが、レース別 WEDGE 一覧・`/entries/key`・「集計表ごと」でその行は見えない。全部の行が要るときは `aggregate --clean --no-compact` → `build_db --fresh`。screen_families（採点）はメモリ上で全部の行を使うので影響しない

---

## 10. 今後

- DB の大きさ: unified_all の率の列は文字列（`45.00%（47.10%）`）と数値（*_val）の両方を持っている。数値だけにして画面で文字列を作れば、さらに小さくできる（2026-11-10 に1表ずつのテーブル foragg_* の二重持ちは解消した）
- 集計の時間: family の数に比例する。同じキーの部品を共有する family（例: 同じ parts で tail だけ違う）でグループ化を共有すれば短くできる
- 条件ヒット馬の分類や `/entries/hits` の並びに adjusted_WEDGE を使うか、年別 WEDGE の推移を画面で見せるか
- 指数の組み合わせ・傾向分析（master_csv を入力に、レース単位で「指数1位が3着内のとき残り2頭の順位帯」などを集計）
- 安井 Excel の定性情報（§4.7 の未使用列）を予想コメント・特徴タグとして使う

---

## 11. 変更履歴

### 2026-11-10 成績表の持ち方（master ごとの1ファイル・unified_all への一本化・コンパクト化）
- 背景: 16GB RAM・500GB SSD の MacBook Air で 1,655〜4,868通りの family × 33指数 × 4オッズ帯を集計したら、agg_csv に数十万個の CSV（140GB 超・数億行）ができて SSD とメモリが尽き、Docker エンジンが止まった（ユーザーの実データ）
- aggregate: 出力を master 1本につき1ファイル `agg_master_{地域}_{指数名}.csv.gz` に（family, odds_mode, rank_scheme, indicator, region, テーブル名 の列。テーブル名は旧来と同じ）。成績表を1つずつ書き足す（メモリに持つのは1つだけ）。`--compact`（既定 ON。`--no-compact`・`--compact-min-count`・`--compact-min-wedge`）で区分・WEDGE の上位・layer A・件数30以上の行だけを書く。並列は master を1本ずつ渡し、workers × 2 本ごとに子プロセスを作り直す（`shisu/parallel.py`。replace と共通）。`--resume`（agg_settings.json で設定を確かめて、済んだ master を飛ばす）。進み具合を1本ずつ出す
- build_db: 1表ずつのテーブル `foragg_*` を作らず、agg_master（と旧来の foragg_*.csv）を少しずつ読んで unified_all に直接入れる。`unified_all__build` に作ってから入れ替える。__tables_meta__ はテーブル名ごと、__facets__ は読みながら集める。前の版の1表ずつのテーブルは消す
- app: 「集計表ごと」を unified_all の同じテーブル名の行から（1表ずつのテーブルがあればそこから。同じ結果）
- shisu: `aggstore.py`（保存形式・コンパクト化・読み込み。`prepare_agg_frame`・`estimate_percent` を build_db から移した）、`parallel.py`、`classify.classify_frame`（ベクトル版）、`naming` に agg_master の名前と速い旧来名の解析
- 独立レビュー（再現つき）の指摘を反映: `--resume` が設定を変えた実行の途中で止まったあとに前の設定の成績表を飛ばして混ぜた（agg_settings.json を毎回書き直していたため）→ 成績表ごとの書き終わりの印（設定のハッシュ・master の大きさと更新時刻・ファイルの大きさ）で判定し、合わないものは作り直す。書き終わりに fsync。途中で切れた成績表で build_db が例外で止まった → そのファイルを飛ばして [ERROR]。全部の master が失敗したときに「replace を先に」と出た。`--compact-min-count 0` で下げたのに残る行が減った → `none` で外し、0 以下は弾く。前の版の DB の1表ずつのテーブルが数十万本あると消すのに何時間もかかる → 画面は常に unified_all から読み、5,000本を超えたら消さずに WARN。WAL の記述の訂正と切り詰め
- テスト: `tests/test_aggstore.py` を追加。個別の成績表ファイルを読んでいたテストは `read_agg_tables` で同じ形に分けて読む（件数を厳密に比べるテストは `--no-compact`）。pipeline の fixture は既定のコンパクト化で全段を通す。テスト 464件

### 2026-11-09 選抜のやり直し・メモリ・lxml・Readme.txt
- screen_families: `--from-report [CSV]`（採点済みの screening_report.csv から選抜だけやり直す。数秒）。選抜の既定を `--min-axis 25`・`--top-a 100` に。基準B は絶対軸が1つでも出る候補を必ず選ぶ（`--always-select-abs`）、基準A は wedge_spread 上位か adj_wedge_spread 上位
- replace_csv_ver3: raw_csv の統合・内外の表のあとに `gc.collect()`、子プロセスを workers × 4 件のまとまりごとに作り直す（BrokenProcessPool 対策。`max_tasks_per_child` は Python 3.11・3.12 で止まるので使わない）
- build_db: `--entries-only`（＝ `--mode entries`）
- lxml 6.0.2 → 6.1.3（Dependabot の脆弱性アラート。lxml は BeautifulSoup の解析で使うので削除せず更新）。同じく Dependabot に出ていた Flask 3.1.2 → 3.1.3・requests 2.32.5 → 2.33.1（§7）
- Readme.txt を最新の運用（ext_indices への自動コピー・安井出馬表用CSVの廃止・出馬表1本への外部指数の自動結合・raw_csv の基本CSVと列パッチ・`--from-report`・馬体重発表後の差し替え）に更新
- 検証（反証役が再現）の指摘を反映: `ProcessPoolExecutor(max_tasks_per_child=4)` が Docker の Python 3.11.18 で workers × 4 件を超えると止まる（テストはジョブ3件で気づけなかった）→ まとまりごとの作り直しに変更、`--from-report old.csv` が既定の screening_report.csv を上書きしていた → 読んだファイルに書き戻す。あわせて、選抜の同じ値の並びを決める・`--entries-only` と `--fresh`／`--mode foragg` を弾く
- 2回目の検証（コミット済みの範囲。build_db・文書と Flask・requests の影響）の指摘: Readme.txt 5章の「別の名前で残したいときは --report new_report.csv を付ける」が、ファイル名なしの `--from-report` では --report のファイルを読むコードと逆だった → `--from-report screening_report.csv --report new_report.csv` と書き、注意を足した。Flask 3.1.3・requests 2.33.1 は、変更点の突き合わせ・画面の応答ヘッダの比較・文字コード14通りの取得の比較で動きの違いなし
- テスト 447件

### 2026-11-08 列パッチ（前走の列の横結合）・前走の自動補完の取り消し
- 前走の列を 血統登録番号 の履歴から作る機能（b3bf845・5615360）を取り消した（地方・海外帰りなどで前走がずれるため。TARGET の前走の列をそのまま使う 63b7461 の動きに戻した）
- raw_csv/ の列パッチ: `レースID(新)` があって `指数` 列の無いCSV（前走の列だけを出した軽いCSV）を、全場_タイムM・全場_対戦M の両方に レースID(新) で横結合（空でない値で上書き・補完）する。複数ファイル可・後のファイル優先・無い列は足す。基本CSVの統合のあとに当て、merged_done/ に移す。replace の自動統合と /ingest の画面の両方
- 検証（2つの観点で探して反証役が再現）の指摘を反映: 片方の 全場_*.csv にしか当たらなかったパッチ・同じ回のほかのパッチが当たったときの当たらないパッチまで merged_done/ に移していた → ファイルごとに、両方の 全場_*.csv に全部の行が当たったものだけ移す。あわせて見出しのゆれを 全場_*.csv の列に合わせ、指数順位 を書き換えない
- テスト: test_raw_merge.py に列パッチの10件

### 2026-11-07 日常運用の自動化とスクリーニングの強化
- 出馬表: `entries_in/` はマイニング値だけの出馬表1本でよく、calc_entry_hensachi が ext_indices の外部指数（形式A〜D）を過去側と同じ `ext_indices.attach_filled` で付けてから偏差値・順位を計算する。出馬表に値のある指数は出馬表を優先、0 だけの列は ext で上書き。`--ext-dir`・`--no-ext`
- /ingest・ingest.py: TARGET 取込用CSVを ext_indices/ の取得元のフォルダにコピー（既定 ON。`--no-ext-copy`）。安井の出馬表用CSV（`entries_in/yasui_*.csv`・`--yasui-to-entries`）を廃止。安井の警告を「ベースCSVに存在しない開催日」と「ベースCSVに該当行が無い馬（_noid.csv）」に分けた
- raw_csv/: 追加分の基本CSVを `csv_files/全場_タイムM.csv`・`全場_対戦M.csv` に統合（レースID(新) で重複排除・後のファイル優先・最大10年分・merged_done/ へ移動）。replace の最初に自動で実行、/ingest の画面からも。`.gitignore`・`.dockerignore`・Dockerfile に raw_csv
- ラップ6指数: ★6 の形（芝・ダ 列が無く 距離が「ダ1200」）も読む
- family の部品に単独の 場所・距離・芝(内・外) を追加（COURSE は残す。COURSE との同居は弾く）。成績表のコース列は parts にある部品だけ
- screen_families: 骨格（s0〜s6・s6n）× 条件28種 × 因果トリオで候補を広げ（default 2,098通り・all 7,557通り）、同じグループにしかならない組み合わせを除く。レポートに adj_wedge_top/bottom/spread、基準A に `--min-fuku-spread`（既定20.0）
- テスト: test_entries_ext・test_raw_merge を追加、test_ingest・test_screen_families を更新
- 検証（5つの観点で探して、反証役が再現）の指摘を反映: 出馬表の値の優先をファイル・レースごとに（別のファイル・別の日の出馬表や、Gold を一部のレースにしか読み込んでいない出馬表で ext が付かなかった）、末尾に足した ext の列が 指数順位N の枠に数えられて台帳に無い指数の順位を取り込んでいた、raw_csv の統合で16桁の ID だと1レースが1頭に潰れた・`--csv-dir` を変えても本物の raw_csv を取り込んで移していた・書き込みの途中失敗で 全場_*.csv が切り詰められた、ext_indices へのコピーが丸ごと上書きで取得できなかった日の空欄で前の値を消していた（コピーの失敗で取得元が ERROR になっていた。2回目の検証で、BOM の無い UTF-8 の既存ファイルを読めずに置かなかった点も）、単独の 芝(内・外) で出馬表側がダート・内外の区別の無い芝でも 内/外/内外 を試して誤ってヒットした

### 2026-11-06 過去データの取得の変更・新8区分の順位帯・22条件・family の台帳・screen_families.py
- 過去データは ★6タブの基本CSV（タイムM・対戦M。単勝配当・複勝配当つき）＋ `ext_indices/` の外部指数CSV群（形式A〜D）に。`shisu/ext_indices.py` で レースID(新)・レースID(旧)（16進の日・先頭0落ち）から各馬に値を付ける。従来の `{地域}_{指数名}.csv` も読み、同名なら csv_files を優先。距離「ダ1200」の分割、配当なし、障害（トラックコード51以上）の除外。カツラキ・HPRTV5C・成績ST指数・一隊・33rap・上がり1F/2F を廃止
- 順位帯を新8区分（s1 = 1 / 2~3 / 4~5 / 6~9 / 10~、s2 = 1~3 / 1~5 / 6~ の重なりあり）に。前走着順・前走人気・前走上がり3F順も同じ区切り
- 22条件（`shisu/conditions.py`・`shisu/course.py`・`shisu/data/` のマスタ）を過去側・出馬表側で同じ関数で導出
- family を台帳化（`shisu/families.py`）。初期セット49の型（83 family・129通り）。`selected_families.json` があればそれに置き換えて集計・DB（`__families__`）・照合・画面
- replace は family ごとの素材を作らず master だけを書く。aggregate は master からその場でキーを作る（行ごとの値は master 1本につき1回だけ求める）
- `screen_families.py`: 候補（初期セット・単一条件・2条件ペア・コース/距離帯×1〜2条件）を本番と同じ関数で採点し、基準A（件数・カバー率・スプレッド・単調性）と基準B（絶対軸・軸候補・相手・消しのキー数）で `screening_report.csv`、`--export-selected` で `selected_families.json`
- テスト: test_conditions・test_replace_ext・test_ext_indices・test_screen_families を追加。既存のテストを新しい構成（master・新8区分・family の台帳）に合わせた

### 2026-11-06 検証（5つの観点で探して、指摘ごとに反証役が再現）の指摘を反映
- 出馬表側の重量種別の推定: ＳＨ（ステークス＋ハンデ）を拾う、3歳G1 は 馬齢、2・3歳のオープン・リステッドは 定量（★6 の264レースで TARGET と全件一致）
- 出馬表の障害レース（芝ダ は「ダ」、今芝ダ=障）を照合しない。前走開催の末尾 R が切れた形（日が2桁）も読む。牝馬限定の規則を両側で共通に
- ★6 の着順「外」（除外）・「消」（取消）を分母に入れない。取消・除外の馬（指数 0）を偏差値・順位の計算から外す
- 偏差値の向きを TARGET の順位に合わせる（★6 の タイムM は値の大きい馬が1位なのに、台帳の符号反転で偏差値が逆向きになっていた）
- ZI の順位を値から作るとき 0 の馬を最下位に。外部指数のファイルに値の無い馬は 0（TARGET と同じ数え方）
- 前走着順などを含む偏差値帯の family も s1・s2 の両方で（122→129通り）
- family の予約名、同じ名前の定義の置き換え（画面が 500 にならない）、`--families` の指定ファイルが無ければエラー、AGG_RANKBAND_SCHEMES のときの古い成績表の削除と families.json、build_db が今の family に無い成績表を取り込まない、成績表のコース列は family が絞っている部品だけ、`/entries/wedge` の family 絞り込み、screen の引数の正規化と失敗時の終了コード、廃止名の括弧つき表記
- 2回目の検証（3つの観点）の指摘: 出馬表の初出走馬の 斤量増減 が NaN から「-0.5~-1.5」になっていた、タイムM の台帳の向きを「大きいほど良い」に（順位の無い側と向きがずれないように）、想定前半・想定後半の 0 を欠損に、形式B・C でファイルに無い馬を最下位に、`/entries/hits` の表示を区分ごと500行まで、build_db は family の定義ファイルが無ければ DB を消す前に終了コード2
- 3回目の検証（2つの観点。実データの全段の照合は 33指数・22条件・過去側のキー 1,120万件で両側のずれ0件）の指摘: `/entries/hits` の表示上限を馬・オッズ帯ごとに均等に（先頭から切ると後ろの馬が消えていた）・CSV に「区分」列、family の定義の形（文字列だけの要素・parts が数など）を ValueError に（build_db・aggregate は終了コード2）、形式B の順位の列が無いファイルを使わない、想定前半・想定後半の順位は値から付け直すことを明記
- 4回目の検証（3回目の差分だけ）の指摘: 表示上限の枠を行の少ない組から多い組へ回す、形式B の確認で先頭の空の行を理由にファイルを捨てない（3列目の欄が無い本当の2列のファイルだけ外す）、family の name・tail を必須の文字列に（null を "None" にしない）、文書の訂正
- 5回目の検証（4回目の差分だけ）の指摘: 順位の欄が空のファイルと順位のあるファイルが同じ指数で混ざると、空のほうのレースに順位が付かなかった → 元の順位が1頭も無いレースは値から順位を付ける（compute_indicator。両側共通）
- 6回目の検証の指摘: 値から順位を作るレースで、ファイルに無い馬（0）が「小さいほど良い」展開で1位になっていた → ファイルに無い馬は値から順位を作るときも最下位（compute_indicator の last）
- テスト 402件（`tests/test_verified_fixes.py` を追加）

### 2026-11-05 一隊の廃止・安井 Excel の指数別出力
- ⑤ 一隊（ittai）を廃止（サイト側の仕様変更）。`shisu/ingest/ittai.py` を削除し、画面・CLI・`SOURCES`/`ORDER` から外した。`--sources ittai` は理由を添えて断る
- 安井 Excel の出力を吉馬6指数と同じ8指数ごとのフォルダに統一: `yasui_{key}/{YYYYMMDD}_yasui_{key}.csv` と `{元名}_{key}_merged.csv`（吉馬のマージ完全版も旧版と同じ `{元名}_{key}_merged.csv` に戻した）。ベースCSVが指定されない開催日は `ingest_in/base/`・`entries_in/` から探し、どこにも無い開催日も日付・場所・R・馬番つきの指数別CSVを出して案内する（ジョブ画面にも表示）。テスト 225件
- 検証（3つの観点で探して反証役が再現）の指摘を反映: 候補フォルダの読めない CSV 1本で安井の取り出し全体が止まる、自分の出力 yasui_*.csv を候補にしないことのテストが効いていない。あわせて、ベースCSVの補い方を開催日単位から「開催日×場所」単位に、補ったファイルの余計な列をマージ完全版に混ぜない、行の無い場所の馬は `_noid.csv` に出す、自動で探すつなぎ（entries_in/）と画面の案内表示のテストを追加。テスト 228件

### 2026-11-05 WEDGE の年度加重・レース別 WEDGE 一覧・band 系の再発防止・外部指数の取得の統合
- adjusted_WEDGE: 年の最低件数 30→10、固定5年 → 窓（既定10年）の中の有効な年すべてに指数減衰ウェイト（既定0.65、合計1に正規化）。`--wedge-max-years`・`--wedge-decay` を追加
- `/entries/wedge`（＋CSV）: レースの全馬 × 該当する全キーの WEDGE / adjusted_WEDGE を閾値なしで一覧。`/entries`・`/entries/race`・`/entries/hits` からリンク
- band 系の件数・勝率の膨張（旧 `export_foragg()` が `out.merge(base_df, on=["レースID(新)", "指数順位帯"])` で列を付けていたため、レース単位のIDだと同じレース・同じ順位帯の別の馬が直積で混入。例「中山×ダ×1200×ミナリク×1~2位」本来4件→22件、他の馬の1着が混入）: 今の replace は merge しない構造であることを確認し、テストで固定。あわせて、レース単位（16桁）のIDは馬番を付けて18桁にし、レースの識別子から R が消える不具合（16桁から末尾2桁を落としていた）を直した。馬の列が無いときに別の馬を重複排除でまとめないようにした
- 外部指数の取得を `shisu/ingest/`・`ingest.py`・`/ingest` に統合（Ω・吉馬6・かよちん・keibaAI・一隊（同日廃止）・ラップ6指数）。安井サロン Excel の抽出（`shisu/yasui_extractor.py`）と台帳への `安井_*` 8指数の登録
- 依存に requests・beautifulsoup4・lxml・openpyxl を固定版で追加
- 独立レビューの指摘を反映: 安井の丸数字の順位を出馬表側で使わない（過去側と順位がずれる）・出馬表用CSVはベースの全馬、/ingest の同時実行の予約・ジョブが「実行中」のまま残らない・同名アップロード・別サイトからの POST・入力エラー時のアップロード削除、/entries/wedge の予約引数、window=key の窓の終わり（WEDGE の出る年で決める）、かよちんの「11R …」の前方一致。テスト 217件
- 2回目の検証（5つの観点で探して、指摘ごとに反証役が再現）で確認された指摘を反映: エラー画面の実行ボタンが押せない・受け付けなかった実行の表示、大文字・小文字だけ違うベースCSVのマージ完全版の上書き、TARGET が値の無い馬を 0 で出すことへの対応（安井のランク・5ステータスを 0=欠損に。出馬表用CSVの無い馬は 0）、再起動で止まった実行を「完了」と表示、DNS リバインディング（Host の確認）、UTF-8 でない入力ファイル名で 500。あわせて BOM なし UTF-8 と空のファイルの入力を受けるように。テスト 223件

### 2026-11-04 Phase 3（WEDGE）
- 独立レビュー（独自実装との全列照合でずれ0件）の指摘を反映: 着順不明の馬を EDGE でも失敗扱い、WEDGE 列の無い旧成績表を取り込まない（旧 xjoin はその場照合に切替）、絞り込み付き横断ランキングで並べ替え用索引を使わない（最大14秒→約1.4秒）、並べ替え欄の既定、下限入力の全角・カンマ対応、表示列に無い列での並べ替え、年度窓の終わりのログ、年の値の検査。テスト 162件
- aggregate が成績表に WIN・EDGE・WEDGE・adjusted_WEDGE（＋年数・件数・最終年）を出す。年の最低件数30・直近5年の窓（素材全体の最終年まで）・仕様どおりのウェイト
- build_db: unified_all・foragg_*・xjoin に WEDGE 列。WEDGE と adjusted_WEDGE に索引。WEDGE 列の無い DB は旧形式として作り直しを促す
- app: 横断検索に WEDGE 系の並べ替え・下限フィルタ・全集計表の横断ランキング（まとめて表示）。条件ヒット馬・キー詳細に WEDGE 列
- テスト 156件（WEDGE の照合・手計算例・境界・DB・画面）

### 2026-11-04 Phase 1・2（旧 CLAUDE.md §5 の課題への対応）
- キー生成を `shisu/` に一本化（過去側・出馬表側が同じ関数を使用）
  - 空欄で `nan` 混入・空キーになる不具合を解消（部品が空ならキーを作らない）
  - 順位帯を過去側の s1/s2 に統一（出馬表側も両方を計算）
  - `--merge-one` で帯が抜ける不具合を解消（空欄も後続で埋める、行は和集合）
  - 全頭同値（全頭0）・欠損2頭以上のレースは両側とも除外
  - NFKC を両側に適用（`Ｇ１`/`G1` 不一致を解消）。出力を cp932 にして `髙` などの消失を解消
  - 重量種別の推定を改善（NHKマイルC 等の誤ハンデ判定、3歳G1）
  - 指数名を台帳で正式名に統一し、DB の indicator 列で完全一致（`先行力`→`先行`、`吉馬_先行力` と区別）
- DB・画面
  - テーブル名の二重接頭辞を解消。family・オッズ帯・指数などは __tables_meta__/unified_all の列で管理し、`/` の絞り込みが機能するように
  - 通常パスの前方一致フォールバックを廃止。xjoin とその場照合は同じ関数（完全一致）で、分類結果が一致
  - xjoin に日付・レースIDを持たせ、レースIDで絞り込み（存在しない列への参照を解消）
  - `--prune` を今回作り直した種類に限定（`--mode entries --prune` で foragg_* が消えない）。古い xjoin も掃除
  - 0〜1 スケールの自動判定を廃止（0.8% を 80% と誤読する可能性を排除）。率は 0–100 で統一
  - `/entries` の既定テーブルが entries_all になる問題、ラジオボタン重複を解消
- 集計素材は上書きせず `agg_csv/` に出力。`AGG_OPTIONAL_COLS` のカンマ抜け（馬番が入らない）を解消
- オッズ帯を normal / odds10u / odds10_50 / odds50p に変更（odds5p 廃止）。絞り込みは aggregate で行い、素材は20本・gzip に（30万行の入力で約2GB→83MB）。processed_* は `--processed` のときだけ
- 分類を `classify()` に一本化。絶対軸は 連対率_val を使用。`/` のプリセットも同じ判定
- 旧版スクリプト（replace_csv.py・aggregate_stats.py）を削除。Dockerfile の版固定・scipy 追加・パス統一
- Phase 3 準備: 素材に 日付・year、master_csv、`shisu/wedge.py`
- 多視点レビュー（キー一致・集計・DB/画面・要件・規模）での指摘を反映: 指数順位N の対応付け（すぐ右／枠番号）、出馬表側の値の NFKC、旧形式の出馬表（順位 `13.0`）と旧形式 DB の検出、成績表0本で DB を消さない、レースID 無しの出馬表、並べ替えリンクの予約引数、xjoin キャッシュの上限、aggregate の早期絞り込み、頭数列なしの扱い、`AGG_PROP_MODE=mean` でも複勝率は分位点切替（旧挙動）
