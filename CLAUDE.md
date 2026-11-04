# CLAUDE.md — shisu_replace（競馬 外部指数の集計・照合システム）

最終更新: 2026-11-04（Phase 1 バグ修正・リファクタリング／Phase 2 オッズ4区分・抽出条件変更／Phase 3 WEDGE を反映）

## この文書の扱い

- **コードが正**。この文書はコードに合わせて更新する。`README_RECAP.md` は要約、`Readme.txt` は TARGET 操作を含む運用メモ。
- 本番データ（`csv_files/` `foragg_csv/` `agg_csv/` `master_csv/` `replace_csv/` `racing.db` など）は `.gitignore` で除外。
  リポジトリにあるデータは出馬表側サンプル2本だけ: `entries_in/shisu_20260531_1.csv`（入力）／`entries_out/entries_merged_hensachi.csv`（旧版の統合出力）。
  過去レース側の CSV サンプルは無いので、その列仕様は**コードからの推定**（§4.1）。
- **キー（結合データ）に関わる処理は `shisu/` パッケージだけで変更する**。過去側と出馬表側が同じ関数を通ることで一致を保証している。

---

## 0. 概要

TARGET frontier JV で出力した「過去レース×外部指数」CSVを、条件文字列 `結合データ`（例: `東京×芝×16-18×定量×1・2_勝×3`）ごとに集計して成績表（勝率・複勝率・掲示板率・回収値・ベイズ推定値）を作る。
当日の「出馬表×外部指数」CSVの各馬について同じ規則でキーを組み立てて成績表と突き合わせ、Flask UI で「絶対軸／軸候補／消し候補／相手候補」を表示する。
成績表には合成指標 WEDGE（決定力 WIN ＋ 市場の期待を超える力 EDGE）と、その直近5年の年度加重 adjusted_WEDGE も持たせ、「今まさに旬の指数条件」を横断検索で並べられる（§6.2）。

将来は別リポジトリ（`docker-lasthalon-calculate`、`docker-keiba-omega` / `docker-keibaAI-scraping` / `docker-kichiuma-scraping` / `docker-kayochin-get` / `docker-ittai-scraper`）と統合する予定。統合時は `shisu/` を中核ライブラリとして使い、新しい指数は `shisu/indicators.py` の台帳に1行足して取り込む想定。

---

## 1. 構成と処理フロー

### 1.1 ディレクトリ

```
shisu_replace/
├─ shisu/                     共通パッケージ（正規化・キー・偏差値・分類・照合・WEDGE）
├─ replace_csv_ver3.py        [A1] 過去レース → 集計素材
├─ aggregate_stats_ver2.py    [A2] 素材 → 成績表
├─ calc_entry_hensachi.py     [B1] 出馬表 → 偏差値・順位・帯
├─ build_db.py                [A3/B2] CSV → racing.db（照合の事前計算 xjoin を含む）
├─ app.py                     [C] Flask UI
├─ tests/                     pytest（合成データで全段を通す）
├─ requirements*.txt          固定版の依存（Dockerfile が使う）
├─ csv_files/   入力: 過去レース（TARGET）     ├─ entries_in/   入力: 出馬表（TARGET）
├─ foragg_csv/  素材（1行=1頭×1キー、.csv.gz）  ├─ entries_out/  偏差値付き出馬表
├─ agg_csv/     成績表（1行=1結合データ）       ├─ racing.db     SQLite
├─ master_csv/  正規化済み 1頭1行（.csv.gz。WEDGE・組合せ分析用）
└─ replace_csv/ processed_*（結合データ確認・TARGET 取込用。--processed のときだけ）
```

### 1.2 前工程（手作業。Readme.txt 冒頭）

- **過去データ**: TARGET ★8タブ → 外部指数読込 → 「レース一覧★画面イメージ(CSV)」で出力（頭数10以上・障害除外で抽出）→ `csv_files/{地域}_{指数名}.csv`
  - 地域: `主５場（中山、東京、中京、京都、阪神）` / `ローカル５場（札幌、函館、福島、新潟、小倉）` / 各場名
  - **ファイル名の指数名で処理が切り替わる**（符号反転・ZI 特例・新馬除外）。指数名は `shisu/indicators.py` の台帳で正式名に変換される
- **出馬表**: TARGET 出馬表分析 ★4タブ → 外部指数1〜4＋マイニング読込 → 全選択 → CSV → `entries_in/shisu_YYYYMMDD_N.csv`

### 1.3 データの流れ

```
[A] 過去レース
csv_files/*.csv
  └─ replace_csv_ver3.py ─┬─> foragg_csv/foragg_{tag}_{元名}.csv.gz  素材（入力1本→20本。オッズでは絞らない）
                          ├─> master_csv/master_{元名}.csv.gz        正規化済み 1頭1行
                          └─> replace_csv/processed_*.csv            確認用（--processed のときだけ）
  └─ aggregate_stats_ver2.py  素材1本 → オッズ帯4区分の成績表4本（agg_csv/）。素材は上書きしない
  └─ build_db.py --mode foragg|both
        foragg_*（成績表1本=1テーブル）, unified_all（縦結合＋family/odds_mode/rank_scheme/indicator/region 列）,
        __tables_meta__（テーブルの属性）, __facets__（画面の選択肢）

[B] 出馬表
entries_in/*.csv
  └─ calc_entry_hensachi.py ─> entries_out/{元名}_hensachi.csv / entries_merged_hensachi.csv（--merge-*）
  └─ build_db.py --mode entries|both（unified_all が必要）
        entries_{元名}（キー部品を正規化）, entries_all, entries_xjoin_{元名}（照合結果）

[C] app.py
  /              横断検索（__tables_meta__ で集計表を選び、各 foragg_* を表示）
  /entries       出馬表のレース一覧（日付・場所）
  /entries/race  出馬表（各馬の順位・偏差値・帯）
  /entries/key   結合データの前方一致で unified_all を表示
  /entries/hits  条件ヒット馬（xjoin があれば読む。無ければ同じ関数でその場照合）
```

### 1.4 実行コマンド（Docker。イメージ名 `csv_processor_shisu`）

```bash
docker build -t csv_processor_shisu .
docker run --rm -v "$(pwd)":/app csv_processor_shisu python replace_csv_ver3.py --clean
docker run --rm -v "$(pwd)":/app csv_processor_shisu python aggregate_stats_ver2.py --clean
docker run --rm -v "$(pwd)":/app csv_processor_shisu python calc_entry_hensachi.py --merge-only --clean
docker run --rm -v "$(pwd)":/app csv_processor_shisu python build_db.py --fresh
docker run --rm -p 8000:8000 -v "$(pwd)":/app csv_processor_shisu python app.py
docker run --rm -v "$(pwd)":/app csv_processor_shisu python -m pytest
```

### 1.5 再実行ルール

| 変えたもの | 再実行 |
|---|---|
| csv_files（過去データ）・キーの規則（`shisu/`） | replace → aggregate → build_db --mode both |
| 集計の条件（推定方法・MIN_SAMPLE・オッズ帯の定義・WEDGE の年の最低件数や窓など） | aggregate → build_db --mode both（素材はそのまま使える） |
| 出馬表 | calc_entry_hensachi --merge-only --clean → build_db --mode entries --prune |
| 画面（app.py・`shisu/classify.py`） | app の再起動だけ |

- `build_db --mode foragg` だけ回すと xjoin（照合結果）が古いまま残るので、警告が出る。続けて `--mode entries` を回す。
- `--prune` は「今回作り直した種類」の古いテーブルだけを消す（`--mode entries --prune` で foragg_* は消えない）。
- 画面は `__tables_meta__` に載っている集計表だけを使う。古い foragg_* テーブルが DB に残っていても表示・照合には出ない。

---

## 2. 共通パッケージ `shisu/`

| モジュール | 役割 |
|---|---|
| `config.py` | パス。ルートは `SHISU_BASE_DIR`（既定はリポジトリ直下）、DB は `DB_PATH`（既定 `racing.db`） |
| `csvio.py` | `norm_text`（NFKC・空白除去・欠損表記→""）、CSV 読み書き（読込 cp932→utf-8-sig→utf-8、**書出し cp932**） |
| `normalize.py` | クラス名・重量種別・距離レンジ・枠番グループ・芝(内・外)・レースID/日付 |
| `indicators.py` | 指数の台帳（正式名・別名・符号反転・ZI 特例・新馬除外）、ファイル名から地域/指数名の取り出し |
| `hensachi.py` | レース内の偏差値・順位・偏差値帯・順位帯（s1/s2） |
| `keys.py` | 結合データの組み立て（15 family）。過去側のベクトル版と出馬表側の候補生成 |
| `odds.py` | オッズ4区分（normal / odds10u / odds10_50 / odds50p） |
| `naming.py` | 素材ファイル名の規約と解析、テーブル名 |
| `finish.py` | 着順の正規化（取消・除外・失格・中止、1文字表記対応） |
| `estimates.py` | ベイズ推定（Beta 分位点・事後平均） |
| `classify.py` | 条件ヒット馬の分類（Python 版と SQL 版。両者の一致はテストで確認） |
| `matching.py` | 出馬表1頭 × unified_all の完全一致照合（xjoin 構築と画面のその場照合で共通） |
| `wedge.py` | WIN・EDGE・WEDGE・年度加重 adjusted_WEDGE（成績表用のベクトル版 `wedge_columns` と、照合用の素直な1グループ版） |

---

## 3. 各スクリプト

### 3.1 replace_csv_ver3.py（過去レース → 素材）

1. `read_csv_any`（全列文字列、空欄は ""）→ `normalize_history`: 列名ゆれ（芝ダ/芝・ダ、芝(内外)/芝(内・外)、頭/頭数、馬名S など）を吸収し、キー部品を `shisu.normalize` で正規化
2. `compute_indicator`（§5.4）で偏差値・順位・帯。元の `指数順位` 列があれば順位はそれを使う
3. 頭数10以上（頭数列が無ければ警告して絞らない）、かつ「除外レース」（欠損2頭以上・全頭同値・有効2頭未満）と新馬除外（ZI・吉馬6種）を除いた行を素材にする
4. 非band 10 family ＋ 帯スキーム（s1, s2）× band 5 family ＝ 20本を gzip で書く。キーが作れない行（部品が空）は入れない。**オッズ帯では絞らない**（aggregate で絞る。素材の容量が1/4になり、オッズ帯の定義変更が aggregate の再実行だけで済む）
- オプション: `--clean`（出力先を空に）, `--workers N`（並列）, `--processed`（processed_* も出す）, `--no-master`, 環境変数 `RANKBAND_SCHEMES`（例 `s1`）
- 目安（合成データ30万行の入力1本、検証環境で計測）: 51秒、素材 83MB・master 6MB。オッズ帯ごとに素材を書く旧来の方式で素材を残すと約2GB になる（旧版は aggregate が素材を上書きして縮めていた）

### 3.2 aggregate_stats_ver2.py（素材 → 成績表）

- 入力 `foragg_csv/foragg_*.csv.gz`。素材1本からオッズ帯4区分（§5.5）の成績表を `agg_csv/foragg_{オッズ接頭辞}{tag}_{元名}.csv` に書く。ファイル名が規約外は SKIP
- 重複排除: レースID × 結合データ（× 指数順位帯）× 馬番
- 着順: 取消・除外は分母外、失格・中止は分母に入れて着順=頭数（不明なら18）
- 率: 頻度と推定値。推定値は既定で事後分布 Beta(k+0.5, n−k+0.5) の下側10%点。**複勝率だけは頻度≤20%なら上側90%点**（消し判定を保守的にするため。`AGG_PROP_MODE=mean` でも複勝率はこの切替のまま）。scipy で厳密計算
- 回収値: 単勝・複勝は 100円均等買い。単勝適正回収値 = 100×Σ(1/オッズ×配当/100×的中)/Σ(1/オッズ)
- WIN / EDGE / WEDGE / adjusted_WEDGE（＋年数・件数・最終年）を `shisu.wedge.wedge_columns` で付ける（§6.2）。年度の窓の終わりは、オッズ帯で絞る前の素材ファイル全体の最終年
  - `--wedge-min-year-count N`（既定30、環境変数 `AGG_WEDGE_MIN_YEAR_COUNT`）、`--wedge-window data|key`（既定 data、`AGG_WEDGE_WINDOW`）
  - 実行の最後に「adjusted_WEDGE が付いたグループ: x/y」と年度窓の終わりの年を出す（年30件の条件は細かいキーほど満たしにくいので、本番データで割合を確認する。窓の終わりが素材によって違えば警告）
- 件数 < 5 のグループは計算前に除く（出力しない）
- 集計できた素材が0本なら終了コード2（replace を先に実行）
- 環境変数: `AGG_PROP_MODE`(lower|mean), `AGG_LOWER_Q`, `AGG_UPPER_Q`, `AGG_PRIOR_ALPHA/BETA`, `PLC_SWITCH_THRESHOLD_PCT`, `AGG_MIN_SAMPLE`, `AGG_RANKBAND_SCHEMES`, `AGG_WEDGE_MIN_YEAR_COUNT`, `AGG_WEDGE_WINDOW`。オプション `--clean`, `--workers N`, `--wedge-min-year-count`, `--wedge-window`
- 目安（合成データ30万行の入力1本＝素材20本）: 54秒（WEDGE 追加前は44秒）

### 3.3 calc_entry_hensachi.py（出馬表 → 偏差値付き出馬表）

- 指数列は台帳で検出し正式名にする（`GoldAirplane→Gold`、`マイニング値→タイムM`、`対戦型マイニング値→対戦M`、`先行力→先行` など）
- 各指数列の直後に `X_偏差値, X_偏差値帯, X_順位, X_順位帯_s1, X_順位帯_s2` を挿入。計算は過去側と同じ `compute_indicator`
- 順位は元ファイルの順位列を優先する。対応の付け方: ①明示列（`マイニング順位`・`対戦型マイニング順位`・`{列}順位`）→ ②指数列の**すぐ右**にある `指数順位N`（TARGET の並び）→ ③N 番目の枠（指数を読み込んでいない枠の見出し `指数N` も1枠と数える）→ ④指数が1種類なら無印 `指数順位`。どれも無ければ同値同順位で計算し `[INFO]` を出す
- `--merge-one` / `--merge-only`: レースID(新) で横結合。**空欄は後続ファイルの値で埋め、行は和集合**。DB に入れるのは統合ファイルだけにするのが推奨（`--merge-only`。個別ファイルも入れると xjoin が二重に作られる）
- `--clean`: entries_out の CSV を消してから出力（古い出馬表が DB に入らないように）

### 3.4 build_db.py（CSV → racing.db）

- `--mode both|foragg|entries`, `--fresh`, `--prune`, `--no-xjoin`, `--agg-dir`, `--allow-empty`, `--entries-dir`, `--db-path`（既定は `DB_PATH` か `racing.db`）
- foragg: 成績表を読み、5つの率の `*_val` を文字列の括弧内（推定値）で作り直す（WEDGE 系の列は数値化してそのまま）→ テーブル（名前はファイル名そのまま。接頭辞 `foragg_` は1つ）→ unified_all（索引: 照合用 (結合データ, family, indicator)、テーブル名、WEDGE、adjusted_WEDGE）→ __facets__
  - 読める成績表が0本なら DB に触らず終了コード2（空にしたいときだけ `--allow-empty`）
  - WEDGE 列の無い旧形式の成績表（Phase 3 より前の aggregate の出力）は取り込まず、終了コード1（`aggregate_stats_ver2.py --clean` をやり直す）
- entries: `normalize_entries_df`（§5）→ テーブル → entries_all → xjoin（各馬 × 指数 × 15 family × キー候補を unified_all と完全一致照合。照合結果のキャッシュは2万件で空にする）
  - unified_all が無い・旧形式（family/indicator/WEDGE などの列が無い。Phase 2 で作った DB も含む）なら xjoin を作らず終了コード1
  - レースID(新) が無い出馬表は「日付_場 R」をレースの識別子にする。旧形式の出馬表（`X_順位帯_s1` が無い）は警告。順位 `13.0` は `13` として扱う

### 3.5 app.py（Flask）

- `/`: 条件（場所・芝・ダ・距離・芝(内・外)・地域）、family、指数、オッズ帯、プリセット（絶対軸・軸候補・相手候補・消し候補。判定は `classify` と同じ SQL）、下限（件数・WEDGE・adjusted_WEDGE）で絞り込む
  - 表示は2通り: 「集計表ごと」（最大100表・各200行）と「まとめて」（unified_all の横断ランキング、上位500行。既定の並びは adjusted_WEDGE の大きい順）
  - 並べ替えは件数・各率の *_val・平均着順・回収値・WIN・EDGE・WEDGE・adjusted_WEDGE・adjusted_WEDGE_件数。空欄は常に最後。並べ替え欄の「既定」は表示に合わせる（集計表ごと=複勝率_val、まとめて=adjusted_WEDGE）。表示列に無い列で並べたらその列も表示する
  - 横断ランキングの速さ: 絞り込みが無ければ WEDGE/adjusted_WEDGE の索引で即座に返る。他の条件で絞るときは索引を使わず全件を読んで並べる（索引を最後までたどると遅くなるため）。合成550万行で最大約1.4秒
  - 下限の入力は全角・カンマ区切りも受ける。読めない値は入力欄に残して警告
- `/entries`: 日付（最新が既定）・場所でレース一覧。リンクは `レースID(新/馬番無)`（旧リンク `ridn=場 R` も開ける）
- `/entries/hits`: 馬ごとの該当数の要約と、区分ごとの表（WEDGE・adjusted_WEDGE・年数の列あり）。xjoin が古い形式（WEDGE 列なし）ならその場の照合に切り替えて、その旨を表示。照合経路（xjoin / on-the-fly）を表示。並びは オッズ帯（通常→10倍未満→10〜50倍→50倍以上）→ 馬番 → 複勝率（消しは昇順）
- `/entries/key`: WEDGE・adjusted_WEDGE・年数の列あり
- DB が未構築・旧形式なら各画面の上部に作り直しの案内を出す（旧形式の DB でもエラーにしない）

---

## 4. データ仕様

### 4.1 csv_files/*.csv（過去レース。サンプル無し・推定）

| 区分 | 列 |
|---|---|
| 必須 | `レースID(新)`（18桁: YYYYMMDD+場2+回2+日2+R2+馬番2）, `場所`, `芝・ダ`, `距離`, `頭数`, `指数`, `着順`, `単勝オッズ`, `単勝配当`, `複勝配当` |
| 任意 | `レースID(新/馬番無)`, `指数順位`, `重量種別`, `クラス名`, `芝(内・外)`/`芝(内外)`, `枠番`, `馬番`, `騎手`, `馬名`/`馬名S` |

### 4.2 foragg_csv（素材、1行=1頭×1キー、cp932 の CSV を gzip）

`レースID(新), 結合データ, [指数順位帯], 着順, 単勝オッズ, 単勝配当, 複勝配当, 場所, 芝・ダ, 距離, 芝(内・外), 馬番, 馬名, 頭数, 日付, year`
（ファイル名にオッズ接頭辞は付かない。中身を見るときは `gzcat` か pandas の `read_csv`）

### 4.3 agg_csv（成績表、1行=1結合データ）

| 列 | 内容 |
|---|---|
| 結合データ, 件数 | キーと件数（≥5） |
| 勝率, 連対率, 複勝率, 2着率, 3着率 | `"45.00%（47.10%）"` ＝ 頻度%（推定%） |
| *_val（上の5つ）, 掲示板率_val | **CSV では頻度%**（0–100） |
| 掲示板率 | `"80.00%"`（頻度のみ） |
| 平均着順, 単勝回収値, 複勝回収値, 単勝適正回収値 | 数値 |
| WIN, EDGE, WEDGE, adjusted_WEDGE | 数値（小数4桁）。§6.2。adjusted_WEDGE は条件を満たす年が無ければ空 |
| adjusted_WEDGE_年数, adjusted_WEDGE_件数, adjusted_WEDGE_最終年 | adjusted_WEDGE に使った年の数・件数の合計・最も新しい年（整数。使った年が無ければ 0 / 0 / 空） |
| 提案勝率〜提案3着率 | 推定%のみ |
| 場所, 芝・ダ, 距離, 芝(内・外) | グループ内で最初の非空値 |

### 4.4 master_csv（1頭1行）

`レースID(新), レースID(新/馬番無), 日付, year, 地域, 指数名, 場所, 芝・ダ, 距離_raw, 距離, 芝(内・外), 重量種別, クラス名, 頭数, 枠番, 枠番グループ, 馬番, 馬名, 騎手, 指数, 指数順位_元, 順位, 偏差値, 偏差値帯, 順位帯_s1, 順位帯_s2, 指数有効(0/1), 着順, 単勝オッズ, オッズ帯, 単勝配当, 複勝配当`
（頭数10以上の全馬。除外レースの馬も `指数有効=0` で残す）

### 4.5 entries_in / entries_out

- 入力（サンプル 348行×27列）: `レースID(新), 場 R, Ｒ, 場所, 芝ダ, 距離, 外回り, クラス, レース名, 年限定, 頭, 枠番, 馬番, 馬名S, 騎手,` ＋指数ブロック（`GoldAirplane, 指数順位1, ZI, 指数順位2, …, マイニング値, マイニング順位, 対戦型マイニング値, 対戦型マイニング順位`）
- 出力: 入力列 ＋ 各指数の `X_偏差値, X_偏差値帯, X_順位, X_順位帯_s1, X_順位帯_s2`（X は正式名）
- 同梱の `entries_out/entries_merged_hensachi.csv` は**旧版の出力**（列名・帯の定義が新版と違う）

### 4.6 DB（racing.db）

| テーブル | 内容 |
|---|---|
| `__tables_meta__` | table_name, source_file, kind(foragg/entries/xjoin), family, odds_mode, rank_scheme, indicator, region, n_rows |
| `foragg_*` | 成績表1本=1テーブル（§4.3 の列。5つの率の *_val は推定値に置換） |
| `unified_all` | 全 foragg_* の縦結合（場所, 芝・ダ, 距離, 芝(内・外), 結合データ, 成績列, *_val, WIN〜adjusted_WEDGE_最終年, family, odds_mode, rank_scheme, indicator, region, テーブル名）。索引 (結合データ, family, indicator)・テーブル名・WEDGE・adjusted_WEDGE |
| `__facets__` | 画面の選択肢（col, val） |
| `entries_*` | 出馬表（元の列 ＋ 正規化したキー部品 `場所/芝・ダ/距離/芝(内・外)/重量種別/クラス名/枠番グループ/騎手`、`日付`、`レースID(新/馬番無)`、`距離_raw`、`クラス名_raw`） |
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

`場所×芝・ダ×距離×[芝(内・外)]×（family の後続部品）`。芝(内・外) は空なら省く。**それ以外の部品が1つでも空ならキーを作らない**。

| family | 後続 | family | 後続 |
|---|---|---|---|
| normal | 重量種別×クラス名×指数順位 | normal_band | 重量種別×クラス名×順位帯 |
| rankB | 枠番グループ×指数順位 | rankB_band | 枠番グループ×順位帯 |
| rankJ | 騎手×指数順位 | rankJ_band | 騎手×順位帯 |
| rankBJ | 枠番グループ×騎手×指数順位 | rankBJ_band | 枠番グループ×騎手×順位帯 |
| normalJ | 重量種別×クラス名×騎手×指数順位 | normalJ_band | 重量種別×クラス名×騎手×順位帯 |
| hensachiA | 重量種別×クラス名×偏差値帯 | hensachiB | 枠番グループ×偏差値帯 |
| hensachiJ | 騎手×偏差値帯 | hensachiBJ | 枠番グループ×騎手×偏差値帯 |
| hensachiAJ | 重量種別×クラス名×騎手×偏差値帯 | | |

出馬表側は 芝(内・外) が分かれば `[値, 空]`、分からなければ `[空, 内, 外, 内外]` を試す。band family は s1・s2 両方の帯で試す。

### 5.2 正規化

- **文字**: すべて NFKC ＋ 空白除去（`Ｇ１→G1`、`Ｃ．ルメール→C.ルメール`、`ｵｰﾌﾟﾝ→オープン`）。書き出しは cp932（`髙` などを保持）
- **クラス名**: `500万/1000万/1勝/2勝→1・2_勝`、`1600万/3勝/オープン/OP/OP(L)/L→準OP・OP`、`G2/G3/重賞→重賞`、`G1/GI→G1`、未勝利・新馬はそのまま。`馬齢×(1・2_勝|準OP・OP)→1勝・OP`
- **重量種別**: 過去側は TARGET の列（`別定→定量`。値は 馬齢/定量/ハンデ）。出馬表側は `重量種別` 列があればそれ、無ければ推定:
  レース名のハンデ記号（`むらさＨ･3勝`・`目黒記念ＨG2`・`安土城Ｈ(L)`。`NHKマイルC`・`HTB賞` は対象外）→ハンデ、年限定に「上」→定量、3歳限定G1→定量、その他の2・3歳限定→馬齢、年限定が空→不明（重量種別を含むキーは作らない）
- **距離**: `1600m`・`1600.0`→`1600`。レンジ統合（福島芝1700/1800→17-18、新潟芝 内1200/1400→12-14・外1600/1800→16-18・内2000/2200/2400→20-24・外3000/3200→30-32、中山ダ2400/2500→24-25、東京芝1600/1800→16-18・2300/2400→23-24・ダ1300/1400→13-14、中京ダ1800/1900→18-19、京都芝 内1400/1600→14-16・外1400/1600/1800→14-18・2000→2000×`内外`・外2200/2400→22-24・外3000/3200→30-32・ダ1800/1900→18-19、阪神芝1600/1800→16-18・2400/2600→24-26・3000/3200→30-32、小倉芝1700/1800→17-18）
- **芝(内・外)**: 過去側は TARGET の列。出馬表側は `外回り` 列が外→`外`、京都・新潟の芝でそれ以外→`内`、他は不明（空）
- **枠番グループ**: 10–13頭 枠1–5内/6–8外、14–16頭 1–4内/5–8外、17–18頭 1–3内/4–6中/7–8外、それ以外は空（両側共通）

### 5.3 指数の台帳（`shisu/indicators.py`）

| 正式名 | 別名 | 特例 |
|---|---|---|
| Gold | GoldAirplane, Gold指数 | |
| BC | BCindex | |
| ZI | ZI指数 | 0/欠損→同レース非ゼロ平均、新馬除外 |
| 吉馬_SP能力値・吉馬_先行力・吉馬_SP信頼・吉馬_SP調整・吉馬_SP最大・吉馬_末脚力 | | 新馬除外 |
| 展開 | 展開力 | 符号反転 |
| 先行・追走・持久・持続・瞬発 | 先行力・追走力・持久力・持続力・瞬発力 | |
| 想定前半・想定後半 | 想定前半3F・想定後半3F | 符号反転 |
| タイムM | マイニング値, タイム指数, TimeM | 符号反転 |
| 対戦M | 対戦型マイニング値, MatchM | |
| ほか | カツラキ, オメガ指数(Ω指数), かよちん指数, keibaAI指数, SAV, ST指数(ST), 合計値, 仕上り(仕上がり), 圧勝力 | |

照合は DB の `indicator` 列の**完全一致**（`先行` と `吉馬_先行力` は別物）。

### 5.4 偏差値・順位・帯（`compute_indicator`、両側共通）

1. 同一レースで指数欠損2頭以上 → このレースはこの指数で使わない。1頭 → その馬の順位は空
2. ZI: 0/欠損を同レース非ゼロ平均で埋める
3. 符号反転・新馬除外（台帳どおり）
4. 有効2頭未満、または全頭同値（全頭0の未取得など）→ このレースは使わない
5. 偏差値 = (x−平均)/母標準偏差×10+50 を四捨五入。帯: `71以上 / 66~70 / 61~65 / 51~60 / 41~50 / 40以下`
6. 順位は元の順位列を優先（無ければ同値同順位。同梱サンプルの TARGET の順位も ZI・オメガ指数で同値同順位だった）。順位帯: s1 `1~3位/4~6位/7~9位/10位~`、s2 `1~5位/6~10位/11位~`
- 指数値・順位は NFKC・空白除去してから数値化する（全角数字を欠損にしない）

### 5.5 オッズ帯（`shisu/odds.py`）

aggregate が素材を過去レースの**確定単勝オッズ**で絞って集計する: `normal`（なし）/ `odds10u`（10倍未満）/ `odds10_50`（10以上50未満）/ `odds50p`（50倍以上）。成績表のファイル名の接頭辞は `odds10u_` / `odds10_50_` / `odds50p_`。オッズ欠損の馬は normal にだけ入る。
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
  1. 年（素材の `year`。1900〜2100 の整数だけ）ごとに WEDGE を出す
  2. 窓 = 終わりの年から遡る5年。終わりの年は既定（`data`）で**素材ファイル全体の最終年**（＝データとしての「今」）。`--wedge-window key` なら結合データ自身の最終年。窓の外の古い年は使わない
  3. 件数が30未満（`--wedge-min-year-count`）の年は除く
  4. 残った年数 m に応じて古い順にウェイト: m=5 `[0.06, 0.11, 0.18, 0.25, 0.40]`、m=4 `[0.12, 0.19, 0.27, 0.42]`、m≤3 `linspace(0.1, 0.5, m)` を合計1に正規化（m=1→1.0、m=2→約0.17/0.83）
  5. 残った年が無ければ空。使った年数・件数の合計・最終年を `adjusted_WEDGE_年数/件数/最終年` に出す
- オッズ帯別の成績表（odds10u など）でも同じ EDGE 表を使う。窓の終わりは4本とも同じ（素材全体の最終年）
- 正しさの確認: テストで、ベクトル版 `wedge_columns` と仕様どおりの1グループ版（`wedge_from_frame`・`adjusted_wedge`）をランダムデータと手計算の例で照合している

---

## 7. Docker・実行環境

- `Dockerfile`: `python:3.11.18-slim-bookworm`（`--build-arg PYTHON_IMAGE=...` で差し替え可）、ja_JP.UTF-8、sqlite3 CLI、`requirements-dev.txt`（実行用＋pytest）を固定版でインストール
- `requirements.txt`: pandas 2.3.3 / numpy 2.3.5 / scipy 1.16.3 / Flask 3.1.2（依存も固定）。テストは pandas 2.3.3 と 3.0.5 の両方で通ることを確認済み
- パス: 全スクリプトが `shisu/config.py` を使う（既定はリポジトリ直下。Docker では /app）。`SHISU_BASE_DIR`・`DB_PATH` で変更可。Docker 外でもそのまま動く
- 運用は `-v "$(pwd)":/app` でホストを丸ごとマウント。コード変更は再ビルド不要、`requirements*.txt` を変えたら再ビルド
- SQLite 3.30 以上（`NULLS LAST` を使用）。bookworm は 3.40

---

## 8. テスト

`python -m pytest`（Docker: `docker run --rm -v "$(pwd)":/app csv_processor_shisu python -m pytest`）

- `tests/test_core.py`: 正規化・台帳・偏差値の規則・キー（ベクトル版と1件版の一致、**過去側キーが出馬表側の候補に必ず含まれる**こと）・ファイル名・オッズ・着順・分類（境界値、Python 版と SQL 版の一致）・WEDGE（EDGE 表の境界、ベクトル版と1グループ版の照合、手計算例、年度窓・30件除外・ウェイト）
- `tests/test_scripts.py`: 各スクリプトの関数単位（指数順位N の対応付け、旧形式の順位 `13.0`、頭数列なし、mean モード、--clean など）
- `tests/test_pipeline.py`: 合成データ（`tests/synth.py`。空欄の内外・髙・Ｇ１・全頭0・欠損2頭を含む）で replace → aggregate → calc → build_db → app を通し、旧版の不具合が再発しないこと、xjoin とその場照合の分類結果が一致すること、`--mode entries --prune` で foragg_* が消えないことを確認

---

## 9. 既知の制約・要確認事項

1. **重量種別の推定**（出馬表側）: 3歳限定の別定重賞などは過去側（定量）とずれる。TARGET の出馬表出力に `重量種別` 列を追加できれば、その列が優先されて解消する
2. **芝(内・外)**: 出馬表から分かるのは京都・新潟の内外（外回り列）だけ。他場は候補を全部試すので、過去側に「空」と「内/外」の両方のグループがあると同じ馬に2行ヒットすることがある [要実データ]
3. **9頭以下の出馬表**: 過去側は10頭以上だけを集計している。出馬表側は頭数で絞らないので、少頭数レースの順位・偏差値は多頭数の成績と照合される（枠番グループ系は付かない）
4. 着順の1文字表記（取/除/中/失）に対応したが、TARGET の実際の表記は [要実データ]
5. 地域の重複: 主5場ファイルと各場ファイルの両方があれば、同じキーが両方の表でヒットする（地域列で区別して表示）
6. 同梱の `entries_out/entries_merged_hensachi.csv` は旧版の出力で、新版の列（`X_順位帯_s1` など）は無い。再生成して使う（build_db は旧形式として警告する）
7. **xjoin の容量とメモリ**: 1頭×1指数あたり最大 15 family × キー候補 × 50 行を保存するので、1日分（約550頭×15指数）で数百MB規模になる。統合ファイルだけを DB に入れ（`--merge-only`）、古い日の出馬表は entries_out から外す（`--clean`）。構築中のメモリは照合キャッシュに上限（2万件）を設けたので数百MB程度
8. 過去 CSV に `頭数`（`頭`）列が無いと、警告を出して頭数で絞らずに処理する（TARGET 側で10頭以上に抽出済みの前提）
9. **adjusted_WEDGE の付きやすさ**: 「年30件以上」は騎手・枠番を含む細かいキーではほとんど満たせない（合成データでは全グループの約5%）。aggregate の最後のログで本番の割合を見て、必要なら `--wedge-min-year-count` を下げる。値の信頼度は `adjusted_WEDGE_件数`、新しさは `adjusted_WEDGE_最終年` で確認する
10. **WEDGE の小標本**: 全期間の WEDGE には件数の下限が無い（成績表の件数≥5 だけ）。件数5で全勝なら WEDGE=2.0 になるので、横断ランキングでは「件数 下限」と併用する
11. **年度窓の決め方（要確認）**: 今は「暦年で直近5年を切ってから30件未満の年を除く」。このため (a) 素材の最終年が年明け数日分だけの端数年だと、窓が1年ずれ、その年も30件未満で落ちて実質4年になる。(b) 開催の無い年（京都の改修期間など）があると、窓の中に残る年が減る。「30件以上の年のうち新しい方から5年」にする方式も選べるようにできる（その場合は遡る年数の上限も決める）

---

## 10. 今後（Phase 3・4 の設計メモ）

### Phase 3: WEDGE（実装済み。§6.2）
- EDGE の成功判定は素材に焼き込まず、集計時に `着順`＋`単勝オッズ` から計算する（判定表を変えても aggregate の再実行だけで済む）
- 今後の候補: 条件ヒット馬の分類や `/entries/hits` の並びに adjusted_WEDGE を使うか、年別 WEDGE の推移を画面で見せるか（年別の値は今は成績表に出していない。必要なら master_csv から計算できる）

### Phase 4: 指数の組み合わせ・傾向分析
- `master_csv`（1頭1行、全頭・着順・オッズ・順位・偏差値付き）を入力に、レース単位で「指数1位が3着内のとき残り2頭の順位帯/偏差値帯」などを集計する想定。master を DB に取り込むテーブルを追加する

---

## 11. 変更履歴

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
