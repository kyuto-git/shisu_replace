# CLAUDE.md — shisu_replace（競馬 外部指数の集計・照合システム）

最終更新: 2026-11-18（3件目: SP指数・ST指数の3着内基準値 `shisu/baselines.py`（`shisu/data/index_baseline_SP.csv`・`index_baseline_ST.csv`。上位1/3カット・年度減衰 0.65・縮約。replace と `python -m shisu.baselines --update` で更新・`master_csv/__index_baseline_*__.csv` に写し）・基準比の条件4種（`前走SP基準比`・`前走ST基準比`・`過去SP今走基準比`・`過去ST今走基準比`）と `過去SP3走順`・`過去ST3走順`（帯 `SP3走1~5位` など）・履歴 版4（クラス・馬場4）・その指数自身の順位を部品にした family をその指数の master で作らない `is_self_redundant`・build_db の `--baba-turf`／`--baba-dirt`・screen_families の因果トリオ⑤）／2026-11-18（2件目の検証の反映: replace と build_db のレース印の読み方をそろえる `__race_marks__.json`・読めない ZIP を飛ばす・読む順を相対パスの名前順に・条件のみだけのときの古い成績表の削除・履歴の更新）／2026-11-17（2件目: 外部レース印5種（メンバーLv・タイムLv・ペース・クッション値・含水率）の前走条件 `shisu/race_marks.py`（`race_marks/`・`__race_marks__.csv.gz`）・近5走の要約条件 `近5走同条件`・`近5走同場`（履歴に全部の走・場所・着順）・指数を使わない family `tail="NONE"`（条件のみ。地域ごとの基準の master 1本で集計・指数の名前「条件のみ」）・screen_families の `--tails`）／2026-11-17（条件ヒット馬に「条件特化ヒット」の表（指数が下位でも数の基準は絶対軸・軸候補の行。`low_tail_axis_category`）・結果指数（SP指数・ST指数）から作る「過去3走／5走の平均・最高」の8指数 `shisu/past_perf_indices.py`（`result_indices/`・履歴 `__horse_perf_history__.csv.gz`・想定馬場状態 `--baba-turf`／`--baba-dirt` と /ingest のプルダウン））／2026-11-16（指数が下位の条件を絶対軸・軸候補・相手候補にしない上限ゲート `TAIL_*`・負の相関にある指数の「2〜10倍未満だけの消し」を条件ヒット馬から除く・逆引きページ `/entries/drill`（master からの過去走の取り出し・TARGET 貼付用のレースID・コア分布のまとめ）・危険条件メモ `condition_notes.json`）／2026-11-14（軸候補の最低件数 `AX_CNT` 10 → 20・消優勢／要注意の判定を「絶対軸なし・消し3行以上・2指数以上・軸より多い」／「軸あり・消し2行以上・軸以上」に／build_db の照合（xjoin）のメモリを一定に: ヒット行を1行ずつ・5,000行ごとに INSERT・照合のキャッシュを照合1回ごとに上限管理／オッズ帯 odds10u を「2.0倍以上 10.0倍未満」に: 表記は「2〜10倍未満」・`ODDS_BREAKS` に下限 2.0・前の区切りで集計した DB には画面に注意書き）／2026-11-13（条件ヒット馬の人気馬の見分け: 過剰人気カット `cut_low_10u`・消し(2〜10倍未満) の行数と指数数・同じ指数で軸と消しが出ているタグ・消優勢／要注意の判定）／2026-11-12（/ingest の取得元を同時に実行・馬体重発表後の差分更新 `--mining-only`／`--indicators`・区分に高回収率の基準と希少条件の最低件数の緩和・条件ヒット馬の「通常」の重複排除と穴の軸の赤字・表の見出し固定・WEDGE 一覧の絞り込みの折りたたみ・指数クロス・StridePlus のボーダーのファイルを読まない）／2026-11-10（成績表を master 1本につき1ファイル `agg_master_*.csv.gz` に・DB の1表ずつのテーブル `foragg_*` を廃止して unified_all に一本化・aggregate の `--compact`（既定 ON）・`--resume`・並列のまとまり）／2026-11-09（screen_families の `--from-report`・選抜の既定の変更・replace の子プロセスの作り直し・lxml 6.1.3・build_db `--entries-only`）／2026-11-08（raw_csv/ の列パッチCSV（レースID(新) と前走の列だけ）を 全場_*.csv に横結合する（§3.9）・出馬表への ext_indices の自動結合・/ingest の出力を ext_indices へコピー・raw_csv/ の過去基本CSVの統合・単独の部品 場所/距離/芝(内・外)・screen_families の候補の拡大（骨格・因果トリオ・冗長の除外・複勝スプレッド）を反映。それ以前の変更は §11）

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
- **当日の差分更新（2026-11-12〜）**: 馬体重の発表後にマイニングの確定値が出たら、`calc_entry_hensachi.py --mining-only` → `build_db.py --entries-only --mining-only` で タイムM・対戦M の列と照合結果（xjoin のその2指数の行）だけを入れ替える（ext_indices は読み直さない。§3.3・§3.4）
- **オッズ帯（2026-11-14〜）**: `odds10u` は単勝 **2.0倍以上 10.0倍未満**（表記「2〜10倍未満」。1.9倍以下の馬は normal にだけ入る。モード名は変えていない）。成績表に効くのは次に aggregate を回したとき。前の区切りで集計した DB では画面に注意書きが出る（§5.6）
- **逆引きと危険条件メモ（2026-11-16〜）**: 条件ヒット馬・WEDGE 一覧の行から `/entries/drill` を開くと、その条件に当たった過去の馬を master_csv から集計と同じ規則で取り出して、TARGET に貼るレースID・項目ごとの分布・明細を出す。そこで書いたメモは `condition_notes.json`（DB の外）に残り、条件ヒット馬・WEDGE 一覧の行に赤字で出る（§3.5・§3.10・§4.8）
- **条件特化ヒット（2026-11-17〜）**: 条件ヒット馬のページの4区分の表の下に、指数の上限ゲートで区分から外れたが件数・率・回収値の基準は絶対軸・軸候補を満たす行を、条件ごとに1行にまとめて出す（4区分の判定は変えない。§3.5・§6.1）
- **過去SP・ST の8指数（2026-11-17〜）**: レースのあとに確定する結果指数（SP指数・ST指数。`result_indices/`）から、各レース・各馬の「その日より前・600日以内」の直近3走／5走の平均・最高を計算して外部指数にする（そのレースの結果そのものは使わない）。過去側は replace、出馬表側は calc_entry_hensachi（想定の馬場状態 `--baba-turf`／`--baba-dirt`）と /ingest が同じ関数で計算する（§3.11・§4.9）
- **外部レース印の前走条件（2026-11-17〜）**: TARGET の外部レース印CSV 5種（`race_marks/`。メンバーレベル・タイムレベル・ペース判定・クッション値・ゴール前含水率）を前走のレース（`前走RID16`）で引いて、`前走メンバーLv`・`前走タイムLv`・`前走クッション帯`・`前走含水率帯` の4条件にし、`前走R印3` の空欄をペース判定で補う。過去側は replace、出馬表側は build_db が同じ関数で付ける（§3.12・§4.10）
- **近5走の要約条件（2026-11-17〜）**: 馬ごとの過去走の履歴（§3.11 と同じもの）から、600日以内の直近5走のうち「同じ芝・ダ・距離差200以内」「同じ場所・同じ芝・ダ」の走の最高着順を `近5走同条件`・`近5走同場`（連対あり / 3着あり / 4着以下）にする。過去側は replace、出馬表側は build_db（§3.13）
- **指数を使わない family（条件のみ。2026-11-17〜）**: `tail="NONE"` の family は、末尾に指数の部品を付けず部品の値だけでキーを作る。指数に依らないので、集計・採点・照合・逆引きは地域ごとの基準の master 1本（タイムM → 対戦M → 先頭）で1回だけ行い、指数の名前は「条件のみ」（`indicators.CONDITION_ONLY`）で出す。成績表は `agg_csv/agg_master_{地域}_条件のみ.csv.gz`（§3.14）
- **SP・ST の3着内基準値と基準比の条件（2026-11-18〜）**: 条件（芝ダ × クラス13区分 × 馬場区分 × 場所区分 × 距離帯）ごとの「3着馬の SP指数・ST指数の水準」を基準値マスタ `shisu/data/index_baseline_SP.csv`・`index_baseline_ST.csv` に持ち、前走の指数と前走の条件の基準値の差（`前走SP基準比`・`前走ST基準比`）、過去SP・ST の8指数と今走の条件の基準値の比較（`過去SP今走基準比`・`過去ST今走基準比`）、`過去SP_3走平均`・`過去ST_3走平均` のレース内順位（`過去SP3走順`・`過去ST3走順`。キーでは帯 `SP3走1~5位` など）の6条件にする。基準値マスタは replace が結果指数から直近の年を重く（年度減衰 0.65）作り直す。過去側は replace、出馬表側は build_db が同じ関数で付ける（§3.15・§3.16・§4.11）
- **成績表の持ち方（2026-11-10〜）**: aggregate は master 1本（地域×指数）につき1ファイル `agg_csv/agg_master_{地域}_{指数名}.csv.gz` に、family・スキーム・オッズ帯の成績表を縦に並べて書く（旧版は1表1ファイルで、数千 family × 33指数で数十万ファイル・140GB 超になった）。既定でコンパクト化（区分・WEDGE の上位・layer A・件数30以上の行だけ）。build_db は1表ずつのテーブルを作らず unified_all に直接入れる（§3.2・§3.4）
- **キーの型 = family**: 部品（コース・重量種別・クラス名・枠番グループ・騎手・芝・ダ・場所・距離・芝(内・外) と 22条件・外部レース印の前走条件4種・近5走の要約2種・基準比と過去SP/ST 3走順の6種）＋末尾の指数の部品（指数順位 / 指数順位帯 s1・s2 / 偏差値帯 / なし（条件のみ））。初期セットは 49 の型（83 family・スキーム込み 129 通り。§5.3）。`screen_families.py` で全掛け合わせ候補を採点して選んだ `selected_families.json` があれば、初期セットの代わりにそれを使う（集計・DB・照合・画面）
- 順位帯（指数順位・前走着順・前走人気・前走上がり3F順で共通）は **s1 = 1 / 2~3 / 4~5 / 6~9 / 10~（排他）、s2 = 1~3 / 1~5 / 6~（重なりあり。2位は 1~3 と 1~5 の両方）**
- 合成指標 WEDGE（決定力 WIN ＋ 市場の期待を超える力 EDGE）とその年度加重 adjusted_WEDGE（§6.2）
- 外部指数の取得（Ω・吉馬6・かよちん・keibaAI のスクレイピング、ラップ6指数、安井サロン Excel）は `shisu/ingest/`・`shisu/yasui_extractor.py`。画面 `/ingest`（または `ingest.py`）で TARGET 取込用（＝ ext_indices の形式A）の日付別CSVを作る（§3.6・§3.7）

---

## 1. 構成と処理フロー

### 1.1 ディレクトリ

```
shisu_replace/
├─ shisu/                     共通パッケージ（正規化・22条件・family・キー・偏差値・分類・照合・WEDGE・外部指数の結合・指数の取得）
│   └─ data/                  マスタ: コース属性_帯つき / 輸送距離マスタ / quadrant_link / SP・ST の3着内基準値 index_baseline_SP.csv・index_baseline_ST.csv（リポジトリに入れる。基準値は replace・`python -m shisu.baselines --update` が書き直す。`SHISU_BASELINE_DIR`。§4.11）
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
├─ master_csv/  1頭1行（.csv.gz。集計・選抜の入力）＋ __inner_outer__.csv.gz（前走の内外を引く表）＋ __horse_perf_history__.csv.gz・.json（馬ごとの過去走・着順・結果指数。§4.9）＋ __race_marks__.csv.gz・.json（外部レース印の表と、replace の読み方の記録。§4.10）＋ __index_baseline_SP__.csv・__index_baseline_ST__.csv・__index_baseline__.json（master を作ったときの3着内基準値の写しと記録。出馬表側が読む。§4.11）
├─ agg_csv/     成績表 agg_master_{地域}_{指数名}.csv.gz（master 1本につき1ファイル。1行=1結合データ。条件のみの family は agg_master_{地域}_条件のみ.csv.gz）＋ families.json（集計に使った family）・agg_settings.json（集計の設定。--resume 用）
├─ racing.db    SQLite                            ├─ selected_families.json / screening_report.csv（screen_families の出力）
├─ condition_notes.json  危険条件メモ（/entries/drill で書く。DB の外。`NOTES_PATH` で場所を変えられる。§4.8）
├─ result_indices/  入力: 結果指数（SP指数 SP*.csv・speed*.zip／ST指数 st_se*.csv・st_se*.zip。`RESULT_INDICES_DIR`。§4.9）
├─ race_marks/  入力: TARGET の外部レース印CSV 5種（member*.csv・time*.csv・pace*.csv・cushion*.csv・moistureG*.csv。`RACE_MARKS_DIR`。§4.10）
├─ past_perf_settings.json  過去SP・ST指数の想定馬場状態（/ingest で選んだ値。`PAST_PERF_SETTINGS`。§4.9。calc が実際に使った想定は entries_out/past_perf_baba.json）
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
- **結果指数（過去SP・ST の8指数の元）**: SP指数・ST指数のファイル（見出しなし `レースID(新)18桁, 指数値`）を `result_indices/` に置く（サブフォルダ・ZIP も可。§4.9）。基本CSVには `馬場状態`・`年齢限定`・`血統登録番号` の列も入れて出す（無くても動くが、馬場状態での絞り込み・3歳限定の判定・馬の見分けが粗くなる。§3.11）
- **外部レース印（前走条件の元）**: TARGET の外部レース印CSV（見出しなし3列 `"開催名", RX+旧8桁, "カラーコード2桁+値"`）を `race_marks/` に置く（サブフォルダ・ZIP も可。§4.10）。出馬表側は race_marks/ を先に読むので、毎週出し直して上書きしておくと、先週のレースが前走の馬にも付く（過去側の作り直しは要らない）
- **3着内基準値（基準比の条件の元）**: `shisu/data/index_baseline_SP.csv`・`index_baseline_ST.csv`（リポジトリに入っている）。手で置くものは無い。`result_indices/` に SP・ST の結果指数があれば replace が作り直す（3着馬の標本が 2,000 件に満たなければ今のファイルのまま。§3.15）。基準比の条件は、基本CSVの `クラス名`・`年齢限定`・`馬場状態`・`血統登録番号`・`前走レースID(新)` を使う

### 1.3 データの流れ

```
[A] 過去レース
raw_csv/*.csv（追加分の基本CSV・列パッチ） ─ replace の最初に統合・パッチ ─> csv_files/全場_タイムM.csv・全場_対戦M.csv（§3.9）
csv_files/{地域}_タイムM.csv・{地域}_対戦M.csv（基本CSV） ＋ ext_indices/（外部指数CSV群）
csv_files/{地域}_{指数名}.csv（従来の形）
result_indices/（結果指数 SP・ST）＋ 基本CSV ─ 過去走の履歴 ─> 過去SP・ST の8指数（その日より前・600日以内の直近3走/5走。§3.11）
基本CSV ─ 過去走の履歴（全部の走・場所・着順）─> 近5走同条件・近5走同場（§3.13）   race_marks/ ─> 前走メンバーLv など（§3.12）
result_indices/ ＋ 基本CSV ─ 履歴の3着馬 ─> shisu/data/index_baseline_SP.csv・_ST.csv（3着内基準値。年度減衰つき）─ 写し ─> master_csv/__index_baseline_*__.csv
履歴（前走の指数・クラス・馬場）＋ 基準値 ＋ 過去SP・ST の8指数 ─> 前走SP基準比・前走ST基準比・過去SP今走基準比・過去ST今走基準比・過去SP3走順・過去ST3走順（§3.16）
  └─ replace_csv_ver3.py ─┬─> master_csv/master_{地域}_{指数名}.csv.gz   1頭1行（キーの部品・22条件・偏差値・順位・成績）
                          ├─> master_csv/__inner_outer__.csv.gz         レースID16 → 芝(内・外)（前走の内外。出馬表側も使う）
                          ├─> master_csv/__horse_perf_history__.csv.gz  馬ごとの過去走・着順・結果指数（出馬表側の 過去SP・ST と近5走の計算が読む）
                          ├─> master_csv/__race_marks__.csv.gz          外部レース印の表（出馬表側が race_marks/ の無いときに読む）
                          └─> replace_csv/processed_*.csv               確認用（--processed のときだけ）
  └─ screen_families.py（任意・推奨）  master → 候補の family を一括採点 → screening_report.csv / selected_families.json
  └─ aggregate_stats_ver2.py  master 1本 × family（selected_families.json があればそれ、無ければ初期セット）× スキーム
                              × オッズ帯4区分 → agg_csv/agg_master_{地域}_{指数名}.csv.gz（master 1本につき1ファイル。
                              既定でコンパクト化）＋ agg_csv/families.json・agg_settings.json
                              条件のみの family（tail=NONE）は、地域ごとの基準の master 1本 × 全部の行 → agg_master_{地域}_条件のみ.csv.gz
  └─ build_db.py --mode foragg|both
        unified_all（全成績表の縦結合＋family/odds_mode/rank_scheme/indicator/region/テーブル名 列。1表ずつのテーブルは作らない）,
        __tables_meta__（集計表の一覧。table_name = unified_all のテーブル名）, __facets__, __families__（family の定義。照合・画面はこれを使う）

[B] 出馬表
entries_in/*.csv（マイニング値だけの出馬表1本でよい） ＋ ext_indices/（過去側と同じ外部指数CSV群）
  └─ calc_entry_hensachi.py ─> entries_out/{元名}_hensachi.csv / entries_merged_hensachi.csv（--merge-*）
        （過去SP・ST の8指数は master_csv/__horse_perf_history__.csv.gz から計算。想定の馬場状態は --baba-turf / --baba-dirt）
  └─ build_db.py --mode entries|both（unified_all が必要）
        entries_{元名}（キー部品と条件を正規化。外部レース印は race_marks/ か __race_marks__、近5走は履歴から、
        基準比は master_csv/ の基準値の写し・履歴・calc が付けた 過去SP・ST の列から）, entries_all,
        entries_xjoin_{元名}（照合結果。__families__ の family で照合。条件のみの family は base = 条件のみ で1回）

[C] app.py
  /              横断検索（__tables_meta__ で集計表を選び、unified_all の同じテーブル名の行を表示）
  /entries       出馬表のレース一覧（日付・場所）
  /entries/race  出馬表（各馬の順位・偏差値・帯）と指数クロス（複数の指数で同時に上位の馬）
  /entries/key   結合データの前方一致で unified_all を表示
  /entries/hits  条件ヒット馬（xjoin があれば読む。無ければ同じ関数でその場照合）
  /entries/wedge レース別 WEDGE 一覧（全馬 × 該当する全キー。CSV ダウンロード /entries/wedge.csv）
  /entries/drill 逆引き（条件に当たった過去の馬を master_csv から。CSV は /entries/drill.csv）・危険条件メモ（POST /entries/note）
  /ingest        外部指数の取得（[D]）・raw_csv の統合（§3.9）

[D] 外部指数の取得（ingest.py / /ingest）
ベースCSV（TARGET: レースID(新), 日付S, 枠番, 馬番, ワーク1）
  └─ ①Ω ②吉馬6 ③かよちん ④keibaAI（スクレイピング。⑤一隊は廃止）
ラップCSV ─ ⑥ラップ6指数（手元で計算）／ 安井 Excel ─ 安井8指数／ 出馬表の形のベースCSV ─ 過去SP・ST の8指数（手元で計算。想定馬場状態のプルダウン）
  ─> ingest_out/{実行日時}/{取得元}/{YYYYMMDD}_{名前}.csv（TARGET 取込用2列 = ext_indices の形式A）, *_merged.csv
  ─> ext_indices/{OMEGA,kichiuma,kayochin,keibaAI,yasui}/…（TARGET 取込用CSVのコピー。既定でコピーする。[A]・[B] がそのまま結び付ける）
```

### 1.4 実行コマンド（Docker。イメージ名 `csv_processor_shisu`）

```bash
docker build -t csv_processor_shisu .
docker run --rm -v "$(pwd)":/app csv_processor_shisu python replace_csv_ver3.py --clean
docker run --rm -v "$(pwd)":/app csv_processor_shisu python screen_families.py --indicators タイムM,対戦M --workers 4 --export-selected
docker run --rm -v "$(pwd)":/app csv_processor_shisu python screen_families.py --from-report --export-selected   # 採点し直さず選抜だけ
docker run --rm -v "$(pwd)":/app csv_processor_shisu python screen_families.py --tails RBAND,HBAND,NONE --indicators タイムM,対戦M --export-selected   # 条件のみ（指数なし）も候補に
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
docker run --rm -v "$(pwd)":/app csv_processor_shisu python ingest.py --sources past --base entries_in/shisu_YYYYMMDD_1.csv --baba-turf 良 --baba-dirt 稍・重
# 過去SP・ST の想定馬場状態を指定して出馬表を計算（省くと /ingest で選んだ値。無ければ auto）
docker run --rm -v "$(pwd)":/app csv_processor_shisu python calc_entry_hensachi.py --merge-only --clean --baba-turf 良 --baba-dirt 稍・重
# 馬体重発表後のマイニング確定値（タイムM・対戦M だけの差分更新）
docker run --rm -v "$(pwd)":/app csv_processor_shisu python calc_entry_hensachi.py --mining-only
docker run --rm -v "$(pwd)":/app csv_processor_shisu python build_db.py --entries-only --mining-only
# SP・ST の3着内基準値: 今の中身を見る／結果指数から作り直す（replace も自動で作り直す。作り直したら replace → aggregate → build_db）
docker run --rm -v "$(pwd)":/app csv_processor_shisu python -m shisu.baselines
docker run --rm -v "$(pwd)":/app csv_processor_shisu python -m shisu.baselines --update --decay 0.65
# 出馬表の「今走の基準値」を引く馬場の想定を指定（省くと calc が使った想定 → /ingest で選んだ値。無ければ出馬表の 馬場状態 の列・良）
docker run --rm -v "$(pwd)":/app csv_processor_shisu python build_db.py --entries-only --baba-turf 良 --baba-dirt 重
```

### 1.5 再実行ルール

| 変えたもの | 再実行 |
|---|---|
| csv_files・raw_csv・ext_indices（過去データ）・キーの部品の規則（`shisu/` の normalize・conditions・course・data/） | replace → （screen）→ aggregate → build_db --mode both |
| 使う family（selected_families.json を作り直した・消した） | aggregate --clean → build_db --fresh（master はそのまま使える） |
| 集計の条件（推定方法・MIN_SAMPLE・オッズ帯の定義・WEDGE の年の最低件数・遡る年数・減衰率・窓・コンパクト化の設定など） | aggregate --clean → build_db --fresh（`--resume` で回すと、今の設定で作っていない成績表だけ作り直す。設定のハッシュに入るのは、aggregate の引数・環境変数（AGG_*）・family の定義・区分の閾値・オッズ帯の境目（`shisu/odds.py` の `ODDS_BREAKS`。2026-11-14 に下限 2.0 を足したので、それより前に作った成績表は `--resume` で全部作り直しになる）・EDGE の表（`shisu/wedge.py`）。それ以外のコード（`shisu/*.py`）を変えたときは `--resume` では気づかないので `--clean`） |
| 出馬表・ext_indices（出馬表の日の外部指数）・騎手の乗り替わりや取消のあった出馬表 | calc_entry_hensachi --merge-only --clean → build_db --entries-only（＝ `--mode entries`。古い出馬表を消すなら `--prune`） |
| 馬体重発表後のマイニング確定値（entries_in の出馬表を上書き） | calc_entry_hensachi --mining-only → build_db --entries-only --mining-only（タイムM・対戦M の列と xjoin のその2指数の行だけ。騎手の乗り替わり・馬の増減などマイニング以外の照合に関わる値も変わっていたら、自動で全部の計算・全部の照合に切り替わる） |
| 画面（app.py） | app の再起動だけ |
| 結果指数（result_indices/ にファイルを足した・替えた） | 過去側の成績表に効かせる: replace → aggregate → build_db --mode both。出馬表側だけ: calc_entry_hensachi --merge-only --clean → build_db --entries-only（履歴は calc が自動で作り直す） |
| 過去SP・ST の想定馬場状態（`--baba-turf`／`--baba-dirt`・/ingest のプルダウン） | calc_entry_hensachi --merge-only --clean → build_db --entries-only（過去側は実際の馬場状態で計算するので作り直さない） |
| 外部レース印（race_marks/ にファイルを足した・替えた） | 過去側の成績表に効かせる: replace → aggregate → build_db --mode both。出馬表側だけ（先週のレースの印を足した）: build_db --entries-only（race_marks/ を読み直す。calc は要らない） |
| 基本CSV（近5走の条件・過去SP・ST の元の履歴） | 過去側: replace → aggregate → build_db --mode both。出馬表側だけ: build_db --entries-only（履歴は build_db・calc が自動で作り直す） |
| 条件のみの family（tail=NONE）を足した・やめた | family を入れ替えたときと同じ（aggregate --clean → build_db --fresh）。`--resume` でも設定のハッシュが変わるので全部作り直す。やめたときの `agg_master_*_条件のみ.csv.gz` は aggregate が消す |
| 3着内基準値（`shisu/data/index_baseline_*.csv` を作り直した・手で替えた・`--baseline-decay` を変えた） | replace（基準値を master_csv/ に写し、master の基準比の4列を引き直す）→ aggregate → build_db --mode both。`python -m shisu.baselines --update` だけでは master・出馬表には効かない（出馬表側は master_csv/ の写しを読む） |
| 出馬表の今走の馬場の想定（`--baba-turf`／`--baba-dirt`・/ingest のプルダウン） | calc_entry_hensachi --merge-only --clean（想定を付ける・/ingest で選ぶ）→ build_db --entries-only（引数なしなら calc が使った想定で `過去SP今走基準比`・`過去ST今走基準比` を引く）。基準値の馬場区分だけ変えるなら build_db --entries-only --baba-turf … だけでもよい |
| 区分の閾値（`shisu/classify.py`） | コンパクト化（既定）しているなら aggregate（`--resume` で、閾値が変わった成績表を作り直す。閾値は設定のハッシュに入る）→ build_db --fresh → app の再起動。`--no-compact` なら app の再起動だけ |

- `build_db --mode foragg` だけ回すと xjoin（照合結果）が古いまま残るので、警告が出る。続けて `--mode entries` を回す（回すまで、条件ヒット馬・WEDGE 一覧の画面に「照合結果が今の成績表より前のもの」の注意書きが出る。§3.5）。
- `--prune` は今回作り直した出馬表の古いテーブル（entries_*・entries_xjoin_*）だけを消す。成績表（unified_all・__tables_meta__ の集計表の行）は触らない（前の版の1表ずつのテーブル foragg_* は `--mode foragg` の入れ替えで消える。多すぎて残ったときは `--fresh`）。
- family を入れ替えたら `aggregate --clean`（古い family の成績表を agg_csv に残さない）と `build_db --fresh`。
- 画面は `__tables_meta__` に載っている集計表だけを使う。family の名前・ラベル・照合に使う family は DB の `__families__` から読む。

---

## 2. 共通パッケージ `shisu/`

| モジュール | 役割 |
|---|---|
| `config.py` | パス。3着内基準値は `baseline_dir()`（既定 `shisu/data/`。`SHISU_BASELINE_DIR`）。危険条件メモは `notes_path()`（既定 `condition_notes.json`。`NOTES_PATH`）。結果指数は `result_indices_dir()`（`result_indices/`。`RESULT_INDICES_DIR`）、過去SP・ST の想定馬場状態は `past_perf_settings_path()`（`past_perf_settings.json`。`PAST_PERF_SETTINGS`）。ルートは `SHISU_BASE_DIR`（既定はリポジトリ直下）、DB は `DB_PATH`（既定 `racing.db`）、外部指数は `SHISU_EXT_DIR`（既定 `ext_indices/`）、family の選抜結果は `SHISU_FAMILIES_FILE`（既定 `selected_families.json`）、追加分の過去基本CSVは `raw_dir()`（`raw_csv/`） |
| `csvio.py` | `norm_text`（NFKC・空白除去・欠損表記→""）、CSV 読み書き（読込 cp932→utf-8-sig→utf-8、**書出し cp932**） |
| `normalize.py` | クラス名・重量種別・距離レンジ・枠番グループ・芝(内・外)・レースID/日付（`horse_race_id`）、`split_surface_distance`（距離「ダ1200」→ 芝・ダ と 距離） |
| `indicators.py` | 負の相関にある指数の組（`TRADEOFF_GROUPS`・`tradeoff_opponents`。§3.5）、指数の台帳（正式名・別名・符号反転・ZI 特例・新馬除外・0=欠損）、条件のみの family の指数の名前 `CONDITION_ONLY`（「条件のみ」。台帳には入れない。`parse_source_name`・`split_source_name`・`condition_only_source` が `{地域}_条件のみ` を扱う）、廃止した指数（`DISCONTINUED`・`is_discontinued`）、ファイル名から地域/指数名の取り出し |
| `aggstore.py` | 成績表の保存形式: master ごとの1ファイル（`AggMasterWriter`。.part に書いてから置き換える・gzip の見出しに時刻を入れない）・行のメタ列（`ROW_META`）・コンパクト化の判定（`compact_mask`）・DB に入れる値への作り直し（`prepare_agg_frame`・`estimate_percent`。build_db から移した）・読み込み（`iter_agg_chunks`・旧来の1表1ファイルの形に分ける `read_agg_tables`） |
| `parallel.py` | 子プロセスをまとまりごとに作り直す並列実行（`run_batched`。replace・aggregate で共通。`max_tasks_per_child` は使わない） |
| `ext_indices.py` | ext_indices/ の外部指数CSVを見つけて読み（形式A〜D）、ベースCSVの各馬に値（と順位）を付ける（§4.2）。`attach_filled` は過去側（replace）と出馬表側（calc_entry_hensachi）で共通の付け方（ファイルに無い馬は 0・順位の扱い・`absent`） |
| `raw_merge.py` | raw_csv/ の追加分の基本CSVを `csv_files/全場_タイムM.csv`・`全場_対戦M.csv` に統合し、列パッチ（`レースID(新)` と補う列だけのCSV）を両方に当てる（§3.9） |
| `course.py` | `data/` のマスタ（コース属性・輸送距離）を引く。コースの属性・輸送距離・距離帯・左右・坂 |
| `conditions.py` | 22条件 ＋ 外部レース印の前走条件4種（`MARK_CONDITIONS`。`member_level`・`time_level`・`cushion_band`・`moisture_band`・`pace_r3`）＋ 近5走の要約2種（`RECENT_CONDITIONS`。値は `derive(…, recent=)` で受ける）＋ 基準比・過去SP/ST 3走順の6種（`BASELINE_CONDITIONS`。値は `derive(…, perf=)` で受ける。§3.16）の導出。過去側（★6タブ）と出馬表側の列名の違いを `standardize_history` / `standardize_entries` で吸収し、同じ `derive()` を通す（§5.2） |
| `families.py` | family の定義・台帳（初期セット・selected_families.json・DB の `__families__`）・部品（`PART_TOKENS`。COURSE と単独の 場所・距離・芝(内・外) を含む）・末尾（`TAILS`。`NONE` = 条件のみ。`Family.no_index`・`split_no_index`・`family_indicator`）・キーの組み立て（過去側のベクトル版 `build_keys` と出馬表側の候補 `entry_keys` / `entry_key_candidates`）・その指数自身の順位を部品にした family の判定（`SELF_RANK_PARTS`・`is_self_redundant`。§3.16） |
| `keys.py` | キーの窓口（families.py の再エクスポートと `all_families()`・`is_band_family()` など） |
| `hensachi.py` | レース内の偏差値・順位・偏差値帯、順位帯（新8区分。`band_labels`。前走着順・前走人気・前走上がり3F順と共通） |
| `odds.py` | オッズ4区分（normal / odds10u（2.0倍以上 10.0倍未満）/ odds10_50 / odds50p）。境目 `ODDS_BREAKS = (2.0, 10.0, 50.0)`（集計の設定のハッシュ・成績表の印・DB に残る）・表記 `ODDS_LABELS`・`FAV_LABEL`（§5.6） |
| `naming.py` | 条件のみの family の基準の master（`base_masters`: 地域ごとに タイムM → 対戦M → 名前順の先頭。集計・採点・逆引きで共通）、成績表のファイル名の規約と解析（`agg_master_{地域}_{指数名}.csv.gz`、旧来の `foragg_*.csv`。family 名は台帳から長い順に当てる。「_」の位置で切って台帳の集合と照らすので family が数千でも速い）、テーブル名（unified_all の「テーブル名」。旧来の成績表のテーブル名と同じ） |
| `finish.py` | 着順の正規化（取消・除外・失格・中止、1文字表記対応） |
| `estimates.py` | ベイズ推定（Beta 分位点・事後平均） |
| `classify.py` | 条件ヒット馬の分類（Python の1行版 `classify`・ベクトル版 `classify_frame`（aggregate のコンパクト化）・SQL 版 `sql_condition`。3つの一致はテストで確認）。高回収率の基準・希少条件（`is_rare_key`・`rare_sql`）の最低件数の緩和（§6.1）。`rule_settings()` は閾値の一覧（集計の設定のハッシュに入る）。指数の上限ゲート（`TAIL_LOW_TOKENS`・`is_low_tail`・`low_tail_sql`）と、消し候補の理由（`keshi_rule`: 全モード共通の規則か、2〜10倍未満専用の規則だけか。§6.1）、ゲートで外れた行のもとの区分（`low_tail_axis_category`。条件特化ヒット）。指数の名前が `CONDITION_ONLY` の行（条件のみの family）にはゲートを掛けない（1行版は `rec["indicator"]`、ベクトル版は列 `indicator`、SQL 版は `indicator_col`。§6.1） |
| `matching.py` | 出馬表1頭 × unified_all の完全一致照合（xjoin 構築と画面のその場照合で共通）。`match_bases`（照合する指数の名前。条件のみの family があれば `CONDITION_ONLY` を足す）・`iter_hit_rows`（ヒット行を1行ずつ返す。xjoin 構築はこれを使う。指数を使う family は指数ごと、条件のみの family は1回）・`match_entry_row`（同じ中身の dict のリスト）・`HitCache`（照合のキャッシュ。入れるたびに件数と行数の上限を見る。§3.4） |
| `drill.py` | 逆引き: (地域, 指数) の master を探し（`find_master`。指数が「条件のみ」なら基準の master）、family・スキームのキーとオッズ帯が同じ馬の行を取り出す（`extract`。集計と同じ `build_keys`・`odds_mask`・重複の除き方・分母）。TARGET 用のレースID（`id_lists`）・項目ごとの分布のまとめ（`summarize`）（§3.10） |
| `notes.py` | 危険条件メモの保存（JSON 1本。ロック・一時ファイルからの置き換え・地域ごと／全地域共通の引き方。§4.8） |
| `race_marks.py` | TARGET の外部レース印（5種）の探索（`find_files`。名前で種類を決める `kind_of`）・読み込み（`parse_text`・`read_files`）・レースID(新)16桁 → `RX`+旧8桁（`rx_of`）・値の前処理（`clean_value`）・表（`RaceMarks.get(前走RID16)`）・`master_csv/__race_marks__.csv.gz` の保存と読み込み（`save`・`load_cache`）・replace の読み方の記録（`save_meta`・`load_meta`。`__race_marks__.json`）・出馬表側の読み方（`load`: 記録に合わせて、race_marks/ → 無ければ表）（§3.12・§4.10） |
| `past_perf_indices.py` | 結果指数（SP指数・ST指数）→ 過去3走／5走の平均・最高の8指数。結果ファイルの探索と読み込み（`find_result_files`・`load_results`）・計算に使う列（`target_frame`）・履歴（`history_rows`・`History`）・対象の走の決め方（`select_runs`）・値（`aggregate`）・`compute`・履歴のキャッシュ（`get_history`）・想定馬場状態の設定（`load_settings`・`save_settings`）・近5走の要約条件（`recent_form`。`History.all_runs`・`form_runs`）・前走の走を引く `History.run_at`・履歴の表 `history_frame`（§3.11・§3.13・§3.16・§4.9） |
| `baselines.py` | SP指数・ST指数の3着内基準値。セルの部品（`race_class`・`generation`・`lookup_class`・`track_of`・`place_group`・`dist_band`・`cell_keys`）・表と引き方（`Baseline.lookup`: L1 → L2 → L3。`parse_csv`・`load`・`load_tables`）・master_csv/ への写しと記録（`snapshot`・`save_meta`・`load_meta`・`load_for_master`）・生成（`samples_from_history`・`build_rows`: 上位1/3カット・年度減衰・縮約。`to_csv_text`・`update_from_history`）・条件（`prev_label`・`now_label`・`today_parts`・`conditions`）・CLI（`python -m shisu.baselines [--update]`）（§3.15・§3.16・§4.11） |
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
6. **過去SP・ST の8指数（2026-11-17〜。§3.11）**: `result_indices/`（`--result-dir`）に結果指数のファイルがあれば、main が先に過去走の履歴を作って `master_csv/__horse_perf_history__.csv.gz` に書き（`past_perf_indices.get_history(rebuild=True)`）、地域ごとの基本CSV × 8指数の「perf」ジョブ（`plan_perf_jobs` → `process_perf`）を足す。各ジョブは基本CSVの全馬について、その日より前の走から値を計算して 指数 列にし（対象の走が無い馬は 0。台帳で 0 = 欠損）、`process_frame` に渡す（順位は値から。基本CSVの タイムM の順位は使わない）。ログに「過去走の結果指数が付いた馬 x/y（段階ごとの馬の数）」
   - 優先: 同じ地域×指数のファイルが csv_files にあればそちら（ext と同じ決まり）。計算した値は `ext_indices/past_sp_st/` のファイル（/ingest の出力）より優先（INFO を出して ext のジョブを外す）。結果指数が無く ext のファイルだけがあれば、ほかの外部指数と同じに ext から付ける
   - 1つのプロセスでの計算は基本CSV 1本につき1回（`_perf_values` の lru_cache。8指数ぶんの値を持つ）。`--workers` でも同じ master になる。`--no-past-perf` で計算しない（`ext_indices/past_sp_st/` にファイルがあれば、ほかの外部指数と同じにそこから付く）
   - main の最初に `_read_base`・`_perf_values` のキャッシュを空にする（同じプロセスで続けて呼ばれても前の実行の値を使わない）
7. **外部レース印の前走条件（2026-11-17〜。§3.12）**: main が `race_marks/`（`--marks-dir`）を読んで `master_csv/__race_marks__.csv.gz` に書く（印が1つも無ければ、前の実行の表を消す）。各ジョブ（`process_frame`）は、その表を1つのプロセスで1回だけ読み（`_marks`）、`derive(…, marks)` に渡す。`--no-race-marks` で読まない。どう読んだか（読まない・既定でないフォルダ）は `master_csv/__race_marks__.json` に残し、出馬表側（build_db）が同じ読み方をする（§3.12）。読めないファイル（パスワードつきの ZIP・途中で切れた ZIP など）は、その1本だけ WARN を出して飛ばす。フォルダごと読めなければ `[ERROR] 外部レース印: …` を出して印なしで master を作る（終了コード1）
8. **近5走の要約条件（2026-11-17〜。§3.13）**: main は、結果指数の有無に関係なく（`--no-past-perf` でも）馬ごとの過去走の履歴を作り直して `master_csv/__horse_perf_history__.csv.gz` に書く（`get_history(rebuild=True)`）。過去SP・ST の8指数の「perf」ジョブを足すのは、履歴に結果指数のある走があり（`History.has_results`）`--no-past-perf` でないときだけ。各ジョブは履歴を1つのプロセスで1回だけ読み（`_history`）、入力の各馬の `recent_form` を `derive(…, recent=)` に渡す（基本CSVを何十回も使う ext・perf のジョブは、基本CSV 1本につき1回だけ計算する `recent_of_base`）
   - 履歴を作れなかったとき（読めない基本CSV など）: `[ERROR] 過去SP・ST の履歴: …` を出し、前の実行の履歴のファイルを消して（各ジョブが古い履歴を読まないように）、8指数は作らず・近5走の条件は空のまま、ほかの master は作る（終了コード1）
9. **3着内基準値と基準比の条件（2026-11-18〜。§3.15・§3.16）**: main は履歴を作ったあと、履歴に結果指数のある走があり `--no-baseline-update` でなければ、履歴の3着馬から基準値マスタ（`shisu/data/index_baseline_SP.csv`・`_ST.csv`）を作り直す（`baselines.update_from_history`。年度減衰は `--baseline-decay`（既定 0.65）。標本が `--baseline-min-samples`（既定 2,000）に満たない種類は書き換えず `[INFO] …満たないので更新しません`）。そのあと、今の基準値マスタを `master_csv/__index_baseline_SP__.csv`・`_ST__.csv` に写し、記録 `__index_baseline__.json`（`past_perf` = この実行で 過去SP・ST の8指数を作るか）を書く（`baselines.snapshot`）
   - **試しの実行では基準値マスタを書き換えない**: `--csv-dir`・`--result-dir`・`--master-dir` のどれかが既定の場所（`shisu/config.py`）と違う実行は、`--baseline-dir DIR` を指定しない限り更新しない（`[INFO] 基準値: --csv-dir が既定の場所と違うので、基準値マスタ（…）は更新しません`。今の基準値を写して使う）。`--baseline-dir DIR` を指定すると、そこに作り・そこから写す（既定の場所の実行でも使える）
   - 片方の種類の基準値マスタが無い・読めない（別の形で保存した・途中で切れた）ときは `[WARN] 基準値（SP）: index_baseline_SP.csv が読めないので、前走SP基準比・過去SP今走基準比 は空になります` を出し、その種類の前の実行の写しを消して、もう片方だけ付ける（終了コードは 0）
   - 各ジョブ（`process_frame(…, perf=)`）は、写しと履歴を1つのプロセスで1回だけ読み（`_baselines`・`_history`）、`perf_cond_of` で6列を作って `derive(…, perf=)` に渡す。基本CSVを何十回も使う ext・perf のジョブは、基本CSV 1本につき1回だけ計算する（`perf_cond_of_base`）
   - `過去SP3走順`・`過去ST3走順` は、その基本CSVの 過去SP_3走平均・過去ST_3走平均 の値から `perf_rank`（perf の master と同じ `compute_indicator`）で作る順位。`master_{地域}_過去SP_3走平均` の `順位` と全行で同じ（そのレースでその指数を使わない・欠損の馬は空）
   - `--no-past-perf`（8指数を作らない実行）・履歴に結果指数が無い実行では、`過去SP今走基準比`・`過去ST今走基準比`・`過去SP3走順`・`過去ST3走順` は空（記録の `past_perf` が false）。`前走SP基準比`・`前走ST基準比` は履歴と基準値があれば付く
   - 基準値を用意できなかったとき（`--baseline-decay` が 0 以下・1 超、書けない場所 など）: `[ERROR] 3着内基準値: …（基準比の条件は付きません）` を出し、写しを消して記録に `error` を残し（出馬表側も付けない）、6列を空にして master は作る（終了コード1）
- **family ごとの素材ファイルは作らない**（旧版は 20本×入力。今は aggregate が master からその場でキーを作る）
- オプション: `--clean`（master と processed を空に）, `--workers N`, `--processed`, `--csv-dir`, `--ext-dir`, `--no-ext`, `--master-dir`, `--raw-dir`, `--no-raw-merge`, `--result-dir`（既定 `result_indices/`）, `--no-past-perf`, `--marks-dir`（既定 `race_marks/`）, `--no-race-marks`, `--no-baseline-update`, `--baseline-dir DIR`（既定 `shisu/data/`）, `--baseline-decay D`（既定 0.65。1.0 で等重み）, `--baseline-min-samples N`（既定 2000）
- メモリ（2026-11-09〜）: raw_csv の統合と前走の内外の表を作ったあとに `gc.collect()` で手放す。`--workers` が2以上のとき、子プロセスは1つあたり `MAX_TASKS_PER_CHILD`（4）件ほどで作り直す（`run_parallel` → `shisu.parallel.run_batched`: ジョブを workers × 4 件ずつのまとまりに分け、まとまりごとに新しい `ProcessPoolExecutor` を作って閉じる。基本CSVのキャッシュなどがたまって Docker の OOM Killer に止められ `BrokenProcessPool` になるのを防ぐ）。`ProcessPoolExecutor(max_tasks_per_child=…)` は使わない（Python 3.11・3.12 では、子プロセスが規定の件数で終わったあとに代わりが起動されず親が止まったままになる。Docker の Python 3.11.18 で再現）。それでも子プロセスが止められたら、そのまとまりの終わっていないジョブを `[ERROR] … 子プロセスが止められました` にして次のまとまりへ進む（終了コード1）
- 目安: 実サンプル `202610.csv`（3,447行）で 0.6秒

### 3.2 aggregate_stats_ver2.py（master → 成績表）

- 使う family: `--families FILE` か `selected_families.json`（`SHISU_FAMILIES_FILE`）があればその定義、無ければ初期セット（§5.3）。**`--families`・`SHISU_FAMILIES_FILE` で指定したファイルが無ければ終了コード2**（黙って初期セットにしない）。使った定義を `agg_csv/families.json` に書く（build_db はこれを読む）。並列の子プロセスにも同じ定義を渡す
- `AGG_RANKBAND_SCHEMES`（例 `s1`）で作るスキームを絞ると、families.json もそのスキームだけになる（master ごとのファイルは丸ごと書き直すので、作らないスキームの前の行は残らない）
- master 1本（地域×指数）の `指数有効=1` の馬について、family × スキームごとに `shisu.families.build_keys` でキーを作り、オッズ帯4区分（§5.6）で集計する。**出力は master 1本につき1ファイル `agg_csv/agg_master_{地域}_{指数名}.csv.gz`**（2026-11-10〜。§4.3）: 成績表（family × スキーム × オッズ帯）を縦に並べ、`family, odds_mode, rank_scheme, indicator, region, テーブル名` の列で区別する。テーブル名は旧来の1表1ファイル `foragg_{オッズ接頭辞}{rankband_スキーム_}{family}_{地域}_{指数名}.csv` のテーブル名と同じ（xjoin・画面に出る名前は変わらない）。**0件の成績表は書かない**。1つずつ集計して書き足すので、メモリには1つの成績表しか持たない。書きかけは `.part` で、書き終わったら本来の名前に置き換える（途中で止まっても完成したファイルだけが残る）。gzip の見出しに時刻を入れないので、同じ入力・設定なら同じバイト列になる
  - 旧版は1表1ファイルだったため、数千 family × 33指数 × 4オッズ帯で数十万ファイル・140GB 超になり、Mac の Docker が止まった（ユーザーの実データ）
  - agg_csv に旧来の `foragg_*.csv` が残っていれば WARN（build_db は同じ master の agg_master があれば使わない。消すには `--clean`）
- **その指数自身の順位を部品にした family は、その指数の master では作らない（2026-11-18〜。`families.is_self_redundant`）**: 部品に `過去SP3走順` がある family は `master_{地域}_過去SP_3走平均` では、`過去ST3走順` がある family は `master_{地域}_過去ST_3走平均` では集計しない（末尾の指数の帯と同じ順位を2回掛けるだけの完全な同義。ほかの指数の master では作る。条件のみの family は対象外）。採点（screen_families）も同じ。照合は変えていない（成績表に行が無いので当たらない）
- **指数を使わない family（tail="NONE"。条件のみ。2026-11-17〜。§3.14）**: 指数ごとの master では集計しない（`aggregate_master(…, no_index=False)` は指数を使う family だけ）。地域ごとの基準の master 1本（`shisu.naming.base_masters`: タイムM → 対戦M → 名前順の先頭）について `aggregate_master(…, no_index=True)` を1回呼び、**master の全部の行**（`指数有効` に関係なく）で条件のみの family だけを集計して、`indicator` 列を「条件のみ」・テーブル名を `foragg_{…}{family}_{地域}_条件のみ` にして `agg_csv/agg_master_{地域}_条件のみ.csv.gz` に書く（master が42本あっても成績表は1つ）
  - やること（`plan`）= (master, 条件のみか) の組: 指数を使う family があれば全部の master、条件のみの family があれば地域ごとの基準の master。`--resume` は組ごとに印を見る（条件のみの成績表の印の master は基準の master）。ログは `[OK] master_….csv.gz（条件のみ）: …`
  - 今回作らない `agg_master_*_条件のみ.csv.gz`（条件のみの family をやめた・基準の master が変わった）と印は、集計の前に消す（0行で終わって印だけが残っているものも）。指数を使う family が1つも無い（条件のみだけの）ときは、指数ごとの集計をしないので、前の family で作った指数ごとの成績表 `agg_master_{地域}_{指数名}.csv.gz` と印も消す（残すと build_db が設定の混ざりを警告し、名前が同じで tail だけ NONE に変えた family の古い行を取り込む）
  - コンパクト化の区分の判定は、指数の上限ゲートを掛けない（`compact_mask(…, indicator="条件のみ")`）
- **コンパクト化（`--compact`。既定 ON。全部の行を書くのは `--no-compact`）**: 成績表の行のうち、次のどれかを満たすものだけを書く（`shisu.aggstore.compact_mask`）
  - a) `classify` で 絶対軸・軸候補・相手候補・消し候補 のどれかになる（判定の値は build_db が DB に入れる値と同じ: 5つの率の `*_val` は文字列の括弧内の推定値。`classify_frame` で全行を一度に判定）、または**条件特化ヒットの行**（2026-11-17〜。結合データの末尾が下位で区分なしだが、数の基準は絶対軸・軸候補。`low_tail_axis_frame`。§6.1）
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
- **書き終わりの印と `--resume`**: master を読む前に前の `agg_master_*.csv.gz` と印を消し（読めない・止められた・[SKIP] でも前の成績表を残さない）、master の識別（名前・大きさ・中身の指紋＝gzip の末尾8バイトの CRC32 と長さ。更新時刻はコピー・Docker のマウントで変わるので比べない）を読む前に取り（集計の間に replace が master を作り直したら印を書かない）、書き終えたら fsync して本来の名前に置き換え、印 `agg_master_*.csv.gz.done`（集計の設定のハッシュ・元の master の識別と読んだ場所・行数・成績表のファイルの大きさ）を書く。`--resume` は、印が**今の設定・今の master（名前・大きさ・中身の指紋）と合い、成績表が印のとおりの大きさで残っている** master だけを飛ばし、ほかは作り直す（違う設定で作った成績表・replace で中身を作り直した master・途中で切れたファイルを混ぜない。更新時刻だけ変わった master は作り直さない。設定を変えた実行が途中で止まっても、`--resume` で今の設定にそろう）。設定のハッシュには、family の定義・スキーム・WEDGE・推定（scipy の有無も）・コンパクト化と、コンパクト化しているときは区分の閾値（`shisu/classify.py` の定数）も入る。設定は `agg_csv/agg_settings.json` にも書き、`--resume` のとき前回と違えばどの項目かをログに出す。`--clean` と一緒には使えない。前の実行の書きかけ `.part` は消してからやり直す
- 失敗: 子プロセスが止められた・例外の master は `[ERROR]`（終了コード1）。全部の master が失敗したときもそう出す（「replace を先に」とは出さない）。直してから `--resume` で失敗した分だけ集計できる
- 行ごとに決まる値（着順の解析・オッズ・配当・年）は master 1本につき1回だけ求め、family・オッズ帯ごとには aggregate_frame の入力に要る列だけを渡す（`with_precomputed`。結果は1行ずつ求めたときと同じ）
- 重複排除: レースID × 結合データ × 馬番（s2 の重なりで1頭が2つのキーに入るのは別のキーなので数える）
- 着順: 取消・除外（★6 の「消」「外」も）は分母外、失格・中止（「止」も）は分母に入れて着順=頭数（不明なら18）
- 率: 頻度と推定値。推定値は既定で事後分布 Beta(k+0.5, n−k+0.5) の下側10%点。**複勝率だけは頻度≤20%なら上側90%点**（消し判定を保守的にするため。`AGG_PROP_MODE=mean` でも複勝率はこの切替のまま）。scipy で厳密計算
- 回収値: 単勝・複勝は 100円均等買い。単勝適正回収値 = 100×Σ(1/オッズ×配当/100×的中)/Σ(1/オッズ)。配当の列が無ければ 0
- WIN / EDGE / WEDGE / adjusted_WEDGE（＋年数・件数・最終年）を `shisu.wedge.wedge_columns` で付ける（§6.2）。年度の窓の終わりは、オッズ帯で絞る前の master 全体の最終年
  - `--wedge-min-year-count N`（既定10）、`--wedge-max-years N`（既定10）、`--wedge-decay D`（既定0.65）、`--wedge-window data|key`（既定 data）。環境変数 `AGG_WEDGE_*` でも指定できる
- 件数 < 5 のグループは計算前に除く（出力しない）
- 集計できた master が0本で失敗も無い（master が無い・全部 [SKIP]）なら終了コード2（replace を先に実行）。全部の master が失敗したとき（[ERROR]）は終了コード1（下の「失敗」）
- 環境変数: `AGG_PROP_MODE`(lower|mean), `AGG_LOWER_Q`, `AGG_UPPER_Q`, `AGG_PRIOR_ALPHA/BETA`, `PLC_SWITCH_THRESHOLD_PCT`, `AGG_MIN_SAMPLE`, `AGG_RANKBAND_SCHEMES`（例 `s1` で s2 を作らない）, `AGG_WEDGE_*`。オプション `--in-dir`（既定 master_csv）, `--out-dir`, `--families`, `--clean`, `--resume`, `--workers N`, `--compact`/`--no-compact`, `--compact-min-count N|none`, `--compact-min-wedge X|none`, `--wedge-*`
- 目安（検証環境・1プロセス）: 30万行の master 1本 × 初期セット（122通りの時点で計測）= 3分19秒（合成データ。71万グループ・成績表488本・CSV 168MB。行ごとの値の前計算の前は約2.5倍かかった）。実サンプル（3,188頭）では 18秒・成績表 476本・unified_all 33,210行（うちレイヤーA（旧版の15種に当たる）は 3,847行）

### 3.3 calc_entry_hensachi.py（出馬表 → 偏差値付き出馬表）

- **ext_indices の自動結合（2026-11-07〜）**: `entries_in/` にはマイニング値だけの出馬表（`test.csv` の形）1本を置けばよい。ext_indices/ の外部指数（§4.2。形式A〜D）を、過去側と同じ `shisu.ext_indices.attach_filled` で レースID(新)・レースID(旧)（無ければ レースID(新) から作る）を使って各馬に付けてから、偏差値・順位・帯を計算する。同じ馬なら過去側（master）と順位・偏差値・帯が一致する（`tests/test_entries_ext.py`）
  - **出馬表の値を優先する。判定はファイルごと・レースごと**: そのファイルに同じ指数の列があり、そのレースに 0・空欄以外の値が1頭でもあれば、そのレースは出馬表の値と TARGET の順位を使う（全レースそうなら `[INFO] {指数}: 出馬表CSVの値を使います`）。列が無いファイル、0・空欄だけのレース（TARGET が読み込んでいない指数を 0 で出したもの）は ext の値で埋める（`出馬表に値のあるレース k/n は出馬表の値を使用` とログに出す）。別の日の出馬表が entries_in に残っていても、そのファイルの値は今日のファイルに影響しない
  - 列があるときはその列の該当レースの行を書き換え、対応する順位の列（`指数順位N` など）も ext の順位にする（列を消すと ほかの 指数順位N の対応がずれるため）。列が無ければ末尾に `{正式名}`（順位があるなら `{正式名}順位`）を足す。**末尾に足した列は 指数順位N の対応付け（§3.3 の②〜④）の枠に数えない**（台帳に無い指数（ラップ2F など）の 指数順位N を取り込まないため。無印の 指数順位 の「指数が1種類」の判定も、もとの出馬表の列だけで数える）
  - ファイルに値の無い馬は 0・順位の付け方（形式B・C はファイルの順位、ファイルに無い馬は最下位）・0=欠損の指数の扱いは過去側（§3.1 の3）と同じ。ログに「外部指数 {指数} を ext_indices から付けました（値のある馬 a/b）」
  - レースID(新)・レースID(旧) が無い出馬表には付けられない（`[WARN] 外部指数を結び付けられません`）
  - `--ext-dir`（既定 `ext_indices/`。`SHISU_EXT_DIR`）、`--no-ext`（結合しない＝従来の動き）。出馬表が複数ファイルでも、同じ ext を各ファイルに付けてから従来どおり横結合する
- **過去SP・ST の8指数（2026-11-17〜。§3.11）**: `past_perf_lookups` が、entries_in の全ファイルの各馬について `past_perf_indices.compute` で値を計算し、形式A の `ExtLookup`（レースID(新) → 値）にして ext の指数と同じ `attach_ext` に渡す（末尾に `過去SP_3走平均` などの列が足され、偏差値・順位・帯が付く。順位は値から）。計算した値は `ext_indices/past_sp_st/` のファイルより優先。ログは「外部指数 過去SP_3走平均 を 過去走の結果指数から計算して付けました（値のある馬 a/b）」
  - 履歴は `past_perf_indices.get_history`: `master_csv/__horse_perf_history__.csv.gz`（replace が書く）の印が、今の基本CSV・結果指数のファイル（名前と大きさ）と同じならそれを読む。違えば作り直して書き直す（`[INFO] …作り直します`）。基本CSV・結果指数が無く履歴のファイルだけあれば、それを使う。どちらも無ければ8指数は付けない（ほかの処理はそのまま）。2026-11-17 から履歴は結果指数が無くても作られる（近5走の条件用）ので、8指数を付けるのは履歴に結果指数のある走があるとき（`History.has_results`）だけ
  - **想定の馬場状態**: `--baba-turf`・`--baba-dirt`（`auto`／`良`／`稍・重`／`不`。`稍重`・`不良`・`自動` も受ける。知らない値は終了コード2）。`auto` は出馬表の `馬場状態`（`馬場`）の列（無ければ指定なし＝段階②から）。値を指定すると、その芝・ダのレースは列より指定を優先する。省いたほうは設定ファイル `past_perf_settings.json`（/ingest の画面で選んだ値）、それも無ければ `auto`（`resolve_baba`。ログに「想定の馬場状態は 引数／設定ファイル／既定 から」）
  - `--result-dir`・`--csv-dir`・`--master-dir`（履歴の元と置き場所）、`--no-past-perf`（結果指数から計算しない。`ext_indices/past_sp_st/` にファイルがあれば、ほかの外部指数と同じにそこから付く。それも付けないなら `--no-ext` か、そのフォルダを消す）。`--indicators 過去SP_3走平均,…` の差し替えでも計算する。`--mining-only`（タイムM・対戦M だけ）は履歴も結果指数も読まない
  - 計算に失敗しても（読めない基本CSV など）ほかの指数の処理は続け、終了コード1
  - **使った想定の記録（2026-11-18〜）**: 過去SP・ST を計算したら、その想定（芝・ダ）を `entries_out/past_perf_baba.json`（`PP.save_used_baba`。`{"baba_turf": …, "baba_dirt": …}`）に残す。build_db が今走の基準値を引くときの既定になる（§3.4）。過去SP・ST を計算しなかった全部の計算（`--no-past-perf`・履歴なし）では消す。`--mining-only` など 過去SP・ST を計算し直さない差し替えでは触らない
- 指数列は台帳で検出し正式名にする（`GoldAirplane→Gold`、`マイニング値→タイムM`、`対戦型マイニング値→対戦M`、`先行力→先行` など）
- 各指数列の直後に `X_偏差値, X_偏差値帯, X_順位, X_順位帯_s1, X_順位帯_s2` を挿入。計算は過去側と同じ `compute_indicator`。`X_順位帯_s2` は重なる帯を「・」でつないだ表示用（例 `1~3位・1~5位`。キーは帯ごとに作る）
- 順位は元ファイルの順位列を優先する。対応の付け方: ①明示列（`マイニング順位`・`対戦型マイニング順位`・`{列}順位`）→ ②指数列の**すぐ右**にある `指数順位N`（TARGET の並び）→ ③N 番目の枠（指数を読み込んでいない枠の見出し `指数N` も1枠と数える）→ ④指数が1種類なら無印 `指数順位`。どれも無ければ同値同順位で計算し `[INFO]` を出す
- `--merge-one` / `--merge-only`: レースID(新) で横結合。**空欄は後続ファイルの値で埋め、行は和集合**。DB に入れるのは統合ファイルだけにするのが推奨（`--merge-only`）
- `--clean`: entries_out の CSV を消してから出力
- **一部の指数だけの差し替え（2026-11-12〜。`--indicators タイムM,対戦M`。`--mining-only` はその短縮形）**: 馬体重の発表後にマイニングの確定値が出たときの差分更新（`refresh_indicators`）
  - すでにある `entries_out/entries_merged_hensachi.csv` を読み、**ext_indices は読み直さない**（指定した指数が ext_indices の対象（`ext_indices.SUPPORTED`）でなければフォルダにも触らない。`--indicators オメガ指数` のように ext の指数を指定したときは、その指数のファイルだけ読む）
  - entries_in の出馬表から、指定した指数の列（値・順位）と、指数でも順位でもない基本の列（馬体重・増減・騎手・斤量など）を取り出して、統合ファイルの同じ馬（`レースID(新)`）の行に入れ、指定した指数の `X_偏差値・X_偏差値帯・X_順位・X_順位帯_s1・X_順位帯_s2` だけを全部の計算と同じ `process_df(only=…)` で作り直す。ほかの指数の列（値・順位・派生の5列）はそのまま残す。列の並びは全部の計算と同じ（同じ入力なら全部やり直した結果と同じファイルになることをテストで確認）
  - 入力に無い**レース**の行（別の日の出馬表の分など、統合ファイルにだけあるレース）は残す。書き出しは `.part` に書いてから置き換える
  - 全部の計算に切り替わる場合（`[INFO]` を出して `--merge-only` と同じ動き）: 統合ファイルが無い・読めない・`レースID(新)` が無い／重複している、入力に統合ファイルに無い馬がいる、**入力にあるレースから馬が消えている**（取消の馬を除いて出し直した など。そのレースは全指数の順位・偏差値が変わるので）。指定した指数の列が入力に無いときは、その指数は前の値のまま（`[WARN]`）
  - `--mining-only`／`--indicators` は統合ファイルを書く（`--merge-only` を付けたのと同じ）。`--clean` と一緒でも統合ファイルは消さない（差分の元なので）。台帳に無い指数名・空の `--indicators` は終了コード2
- **列の組み立て（2026-11-12〜）**: `process_df` は指数ごとの派生の列を作ってから、最後に1回の `pd.concat(axis=1)` で並べる（旧版は `df.insert` を指数×5回くり返し、33指数で `PerformanceWarning: DataFrame is highly fragmented` が出ていた）。`attach_ext` が末尾に足す列も同じ。出力（列の並び・値）は旧版と同じ

### 3.4 build_db.py（CSV → racing.db）

- family の定義ファイル（`SHISU_FAMILIES_FILE` など）が無い・読めないときは、DB に触る前に終了コード2
- `--mode both|foragg|entries`, `--entries-only`（＝ `--mode entries`。出馬表だけ取り込み直す。馬体重発表後のマイニング確定値の差し替えなど。`--mode foragg`・`--fresh` とは一緒に使えない）, `--fresh`, `--prune`, `--no-xjoin`, `--agg-dir`, `--allow-empty`, `--entries-dir`, `--db-path`, `--marks-dir DIR`・`--no-race-marks`（出馬表の外部レース印。省くと replace と同じ読み方。§3.12）, `--baba-turf`・`--baba-dirt`（出馬表の今走の基準値を引く馬場の想定。`auto`／`良`／`稍`／`重`／`不`／`稍・重`。知らない値は DB に触らず終了コード2。§3.16）
- **`--indicators タイムM,対戦M`／`--mining-only`（2026-11-12〜。`--entries-only` と一緒に使う。付ければ出馬表だけのモードになる）**: 出馬表のテーブル（`entries_*`・`entries_all`）は今までどおり入れ替え、照合結果は `refresh_xjoin` で**指定した指数の行だけ**作り直す: `DELETE FROM entries_xjoin_* WHERE base IN (…)` → その指数だけを `match_entry_row` で照合して INSERT（1つのトランザクション。ほかの指数の行は触らない。__tables_meta__ の n_rows は全体の行数に直す）。時間は全部の照合のおよそ（指定した指数の数 / 全指数の数）
  - **一部の作り直しで済ませるのは、ほかの指数の照合結果が変わらないと分かるときだけ**（`partial_blocker`）。照合結果（xjoin）を書くたびに、それが何から作られたかの記録（`xjoin_record`）を `__build_info__` の `xjoin_of:{xjoin のテーブル名}` に JSON で残す: `unified`（成績表の印 `unified_stamp`。unified_all を入れ替えるたびに変わる）・`plain`（出馬表の馬の列 `HORSE_COLUMNS`・キーの部品と22条件 `PART_FIELDS` の全馬ぶんのハッシュ）・`bases`（指数ごとの `X_順位`／`X_偏差値帯` のハッシュ。`match_signatures`）。今の成績表・今の出馬表から作った記録と比べて、次のどれかなら `[INFO]`（理由つき）を出して全部の照合（`build_xjoin`）に切り替える
    - 記録が無い（前の版の build_db で作った照合結果・前の実行が途中で止まった）
    - `unified` が違う（`--mode foragg` だけ回したあとなど、照合結果が今の成績表より前のもの）
    - `plain` が違う（騎手の乗り替わり・馬の増減・枠・コースや前走の条件）
    - 指定した指数以外の `bases` が違う（ほかの指数の順位・偏差値帯、指数の増減）
    - xjoin のテーブルが無い・列の形が今の版と違う
    - 馬体重・増減など照合に使わない列と、指定した指数の列の違いは比べない
  - **照合結果と記録は1つのトランザクション**（出馬表のテーブルごと。`BEGIN` → 一部の作り直し or `build_xjoin`（DROP・CREATE・INSERT・索引）→ 記録 → commit）。途中で止まれば前の照合結果と前の記録に戻る（空のテーブル・索引の無いテーブルは残らない）。出馬表のテーブルは先に確定しているので、次の実行では記録が今の出馬表と合わず、全部の照合になる（途中で止まった実行のあとに同じコマンドを打ち直しても、古い照合結果を残さない）
  - つまり `calc --mining-only` → `build_db --entries-only --mining-only` の結果は、いつも通常の2コマンド（`--merge-only --clean` → `--entries-only`）と同じ DB になる（テストで確認）。違うのは時間だけ
  - `--fresh`・`--mode foragg`・`--no-xjoin` とは一緒に使えない（終了コード2）。台帳に無い指数名・空の `--indicators`（空のシェル変数など。黙って全部の作り直しにしない）も終了コード2
- foragg（2026-11-10〜）: `agg_csv/families.json` の family を台帳に入れ（無ければ selected_families.json か初期セット）、`agg_master_*.csv.gz` を20万行ずつ読み、5つの率の `*_val` を文字列の括弧内（推定値）で作り直して **unified_all に直接入れる**（1表ずつのテーブル `foragg_*` は作らない。旧版は同じ行を foragg_* と unified_all の2回持っていた）。__tables_meta__ は「テーブル名」ごとに1行（kind=foragg・n_rows は unified_all の行数・source_file は読んだファイル。その名前の実テーブルは無い）。__facets__ は読みながら集めた値。→ 索引（照合用 (結合データ, family, indicator)、テーブル名、WEDGE、adjusted_WEDGE）→ **`__families__`**（family の定義）
  - 作り直しは `unified_all__build` に入れてから、最後に1つのトランザクションで前の unified_all と入れ替える（途中で止まっても前の unified_all が残る。画面は入れ替えまで前の DB を見る）。行はファイルごとにコミットする。入れ替えのトランザクションで索引を作るので、WAL はその間だけ索引の大きさほどに膨らむ（100万行で約180MB）。最後に `wal_checkpoint(TRUNCATE)` で切り詰める
  - 前の版の DB に残っている1表ずつのテーブル `foragg_*` は入れ替えのときに消す。`DROP_TABLE_LIMIT`（5,000）本を超えるときは消さずに WARN（DROP は1本ごとにスキーマを書き直すので本数の2乗で遅い。1.6万本で約13秒、数十万本だと何時間も）。画面は使わないので表示には影響しない。消してファイルを小さくするには `--fresh`
  - 読めない成績表（途中で切れた gzip・gzip でないファイル・壊れた CSV。見出しから読めないものも）は、そのファイルの分だけ戻して飛ばし `[ERROR]`（終了コード1。ほかのファイルは入れる。`aggregate --resume` で作り直せる）。読めなかった master でも、同じ master の旧来の foragg_*.csv は代わりに使わない
  - 書き終わりの印（`.done`）の設定のハッシュが成績表によって違う、または印の無い agg_master がある（集計の間に master が作り直された・前の版の aggregate で作った）なら WARN（`aggregate --resume` でそろえる）。旧来の foragg_*.csv を使ったときは別に WARN（agg_master と一緒なら。旧来のファイルだけなら INFO。設定が分からないので `aggregate --clean` を勧める）
  - 印の master の大きさ・中身の指紋が、集計が読んだ場所（印の master_path。無ければ master_csv の同じ名前）の今の master と違えば WARN（成績表を作ったあとに replace で作り直した）
  - 旧来の foragg_*.csv を使わない master = agg_master のファイルがある master と、今の版で集計して0行だった master（印だけがある）
  - `--prune` は成績表を触らない（前の版の1表ずつのテーブルは入れ替えのときに扱い、__tables_meta__ の集計表の行は入れ替えで作り直す）
  - 旧来の1表1ファイル（`foragg_*.csv`）も読める（互換）。同じ master の `agg_master_*.csv.gz` があれば、その master の旧来のファイルは使わない（二重に入れない）
  - **オッズ帯の境目の記録（2026-11-14〜）**: 読んだ agg_master の印の `odds_breaks`（その成績表を集計したときの `ODDS_BREAKS`）が全部同じならその値を、違うものが混ざる・印に無い（前の版の aggregate）・旧来の foragg_*.csv を使った なら `null` を、unified_all の入れ替えと同じトランザクションで `__build_info__` の `odds_breaks` に書く（作っている間は `odds_breaks:build`）。今の `ODDS_BREAKS` と違えば `[INFO]` を出す。画面はこれを見て注意書きを出す（§3.5）
  - 読める成績表が0本なら DB に触らず終了コード2（空にしたいときだけ `--allow-empty`）。WEDGE 列の無い旧形式の成績表は取り込まず終了コード1
  - families.json に無い family・スキームの行・ファイル（前の実行の残り）は取り込まず、行数・本数を警告する
- entries: `normalize_entries_df`（キー部品の正規化と条件。前走の内外は `master_csv/__inner_outer__.csv.gz` で引く。外部レース印の前走条件と近5走の要約条件は `entries_side_sources()` が用意する (レース印, 履歴) から、過去側と同じ `derive`・`recent_form` で付ける: レース印は `race_marks.load`（race_marks/ にファイルがあればそれ、無ければ `__race_marks__.csv.gz`。replace が `--no-race-marks`・`--marks-dir` で作った master なら、記録 `__race_marks__.json` に合わせて、読まない・そのフォルダを読む。build_db の `--marks-dir DIR`・`--no-race-marks` で指定もできる）、履歴は `past_perf_indices.get_history`（基本CSV・結果指数が変わっていれば作り直す）。読めなければ WARN を出してその条件を空にする。近5走に使う列（馬・日付・場所・芝ダ・距離）は、距離をレンジ統合後の値に書き換える前の元の列から取る。**障害レース**（今芝ダ=障・レース名が 障害…・トラックコード51以上。TARGET の出馬表は 芝ダ に「ダ」「芝」と入る）は 芝・ダ を「障」にし、照合しない）→ テーブル → entries_all → xjoin（各馬 × 指数 × family × キー候補を unified_all と完全一致照合）。`--mode entries` では DB の `__families__` の family で照合する（DB を作ったときの family）
  - **基準比・過去SP/ST 3走順（2026-11-18〜。§3.16）**: `entries_baselines()` が (基準値の表, 芝の想定, ダの想定) を用意し、`normalize_entries_df(raw, inner, marks, history, tables, baba_turf, baba_dirt)` が過去側と同じ `baselines.conditions` で6列を付ける。基準値は `baselines.load_for_master`（replace の記録 `__index_baseline__.json` があれば master_csv/ の写しだけ、無ければ基準値マスタ）。過去SP・ST の値と順位は、出馬表の列（calc_entry_hensachi が付けた `過去SP_3走平均`・`過去SP_5走平均`・`過去SP_3走最高`・`過去SP_5走最高`・`過去SP_3走平均_順位` と ST の同じ列。`entry_perf_values`。空・0 は値なし）から取る。今走の部品（芝ダ・距離・クラス・年限定・馬場状態・場所・馬）は、列を正規化で書き換える前の元の列から（`baselines.today_parts`）。想定の馬場は 引数 → calc_entry_hensachi が 過去SP・ST の計算に使った想定（`entries_out/past_perf_baba.json`。§3.3）→ `past_perf_settings.json` → `auto` の順（ログに `[INFO] 3着内基準値: SP n セル・ST n セル／今走の馬場の想定 …`。calc と同じ想定で引くので、calc にだけ `--baba-turf` を付けても、過去SP・ST の値の選び方と基準値の馬場区分が食い違わない）。replace を `--no-past-perf` で回した master（記録の `past_perf` が false）のときは、出馬表の 過去SP・ST の列があっても 今走基準比・3走順 を付けない（master と同じ。`[INFO] …出馬表にも 今走基準比・過去SP/ST 3走順 は付けません`）
  - 照合する指数の名前は `match_bases`（出馬表の指数 ＋ 条件のみの family があれば「条件のみ」）。条件のみの family は base = 「条件のみ」で1頭につき1回だけ照合する（指数の数だけ同じ行を作らない）。`--mining-only`／`--indicators` の一部の作り直しは指定した指数の行だけを入れ替えるので、条件のみの行はそのまま残る（キーの部品・条件が変わっていれば `plain` の違いで全部の照合になる）
  - unified_all が無い・旧形式なら xjoin を作らず終了コード1
  - **照合（xjoin）のメモリ（2026-11-14〜）**: 全部の照合（`build_xjoin`）も一部の作り直し（`refresh_xjoin`）も `insert_hits` を通る。メモリに持つのは、INSERT 待ちの行（`XJOIN_BATCH` 5,000行まで）と照合のキャッシュ（`HitCache`: `XJOIN_CACHE_LIMIT` 20,000件・`XJOIN_CACHE_ROWS` 50,000行まで）だけで、family・指数・馬の数に依らない
    - `shisu.matching.iter_hit_rows` がヒット行を `HIT_COLUMNS` の順のタプルで1行ずつ返し、`insert_hits` が 5,000行たまるごとに `executemany` する（旧版は `match_entry_row` で1頭ぶんを dict のリストにしてから INSERT していた。数千 family × 34指数だと1頭で数十万回の照合・数十万行）
    - キャッシュの上限は `HitCache.put` が**照合1回ごと**に見る（件数が上限に達した・行数が上限を超えるなら空にしてから入れる。上限より大きい1件はそれだけを持つ）。旧版は馬1頭を回し終えてから `len(cache) > XJOIN_CACHE_LIMIT` を見ていたので、1頭の途中で数十万件まで膨らんだ。ふつうの dict を渡した `match_entry_row` も件数の上限（`matching.CACHE_MAX_KEYS`）は守る。画面のその場照合（`collect_hits`）も `HitCache`
    - `main` は出馬表のテーブル（`load_entries_tables`・`build_entries_all`）を確定したあと、照合の前に `gc.collect()` を呼ぶ（正規化に使った DataFrame を手放す）
    - 出力は旧版と同じ（行の順まで）。進み具合は `[xjoin] {テーブル}: i/n 頭, hits …` を50頭ごと（`XJOIN_PROGRESS_EVERY`）
    - **トランザクションは分けていない**（出馬表のテーブルごとに BEGIN → DELETE／DROP・CREATE → INSERT → 記録 → commit の1つ。途中で止まれば前の照合結果と記録に戻る、という `--mining-only` の保証のため）。SQLite は大きいトランザクションをメモリではなく WAL ファイル（ディスク）に書くので、メモリのピークには効かない（下の実測）。ディスクは書き換えたページのぶん（一部の作り直しで約220MB）要る
    - 実測（2026-11-14。合成のストレス DB: 4,635 family・34指数・6頭・照合 146万回・unified_all 134万行）: 全部の照合 旧 92.3秒・最大 RSS 1,555MB → 新 71.3秒・126MB（xjoin 155万行は行の順まで同じ）。一部の作り直し（タイムM・対戦M＋安井8指数）は 20.6秒・124MB・WAL の最大 221MB。旧版のメモリは1頭ぶんの行数に比例するので、実データ（4,868通り・34指数。ユーザーの環境で Exit 137）ではこれより大きい
  - レースID(新) が無い出馬表は「日付_場 R」をレースの識別子にする。旧形式の出馬表（`X_順位帯_s1` が無い）は警告

### 3.5 app.py（Flask）

- 接続のたびに DB の `__families__` を family の台帳に反映する（DB が変わったときだけ読み、台帳を作り直す。同じ名前で部品が変わっても置き換わる。無ければ初期セット）。family の選択肢・ラベル・その場照合・`/entries/wedge` の family 絞り込みはこの family
- **オッズ帯の注意書き（2026-11-14〜。`_odds_note`）**: DB の `__build_info__` の `odds_breaks` が今の `ODDS_BREAKS`（`[2.0, 10.0, 50.0]`）と違う（記録が無い・`null`・前の区切り）とき、オッズ帯を出す画面（`/`・`/entries/key`・`/entries/hits`・`/entries/wedge`）の上に「この DB の成績表は、オッズ帯の前の区切り（10倍未満。単勝2倍未満の馬も入る）で集計したものです。…」を出す（`show_odds_note=True` を渡した render だけ。出馬表・レース一覧・/ingest には出さない）。成績表が無い DB には出さない。family の台帳と同じく DB が変わったときだけ読む。表記は集計し直す前から「2〜10倍未満」になるので、その間 中身と表記が食い違うことを知らせるためのもの
- **古い照合結果の注意書き（2026-11-14〜。`xjoin_note`）**: `/entries/hits`・`/entries/wedge` が照合結果（xjoin）を読んだとき、その照合結果の記録（`__build_info__` の `xjoin_of:{xjoin}` の `unified`）が今の成績表の印（`unified_stamp`）と違う・無いなら、「この出馬表の照合結果（下の行）は、今の成績表より前に作ったものです…build_db.py --entries-only を実行すると、今の成績表で照合し直します」を出す（成績表だけを作り直した `build_db --mode foragg` のあと。オッズ帯の区切りを変えて集計し直したあとにこれをすると、オッズ帯の注意書きは消えるのに、この2画面の行は前の区切りの数字のまま残るため）。その場照合のとき・`unified_stamp` の無い DB（2026-11-11 までの版で作った。見分けられない）には出さない。CSV には出さない
- `/`: 条件（場所・芝・ダ・距離・芝(内・外)・地域）、family、指数、オッズ帯、プリセット（絶対軸・軸候補・相手候補・消し候補。判定は `classify` と同じ SQL）、下限（件数・WEDGE・adjusted_WEDGE）で絞り込む
  - 表示は2通り: 「集計表ごと」（最大100表・各200行）と「まとめて」（unified_all の横断ランキング、上位500行。既定の並びは adjusted_WEDGE の大きい順）
  - 「集計表ごと」の中身（2026-11-10〜）: 常に unified_all の「テーブル名」が同じ行（`WHERE "テーブル名" = ?`。並べ替えの列の索引を使わせず `+列` で並べ、同点は rowid＝成績表の行の順）。前の版の DB に1表ずつのテーブル `foragg_*` が残っていても使わない（前の版の unified_all はそのテーブルを同じ順で詰め直したものなので表示は同じ。古い中身のテーブルが残っていても影響しないことをテストで確認）
  - 並べ替えは件数・各率の *_val・平均着順・回収値・WIN・EDGE・WEDGE・adjusted_WEDGE・adjusted_WEDGE_件数。空欄は常に最後。並べ替え欄の「既定」は表示に合わせる（集計表ごと=複勝率_val、まとめて=adjusted_WEDGE）。表示列に無い列で並べたらその列も表示する
  - 横断ランキングの速さ: 絞り込みが無ければ WEDGE/adjusted_WEDGE の索引で即座に返る。他の条件で絞るときは索引を使わず全件を読んで並べる（索引を最後までたどると遅くなるため）。合成550万行で最大約1.4秒
  - 下限の入力は全角・カンマ区切りも受ける。読めない値は入力欄に残して警告
- `/entries`: 日付（最新が既定）・場所でレース一覧。リンクは `レースID(新/馬番無)`（旧リンク `ridn=場 R` も開ける）
- `/entries/race`: 出馬表（各馬の順位・偏差値・帯。順位3位以内は太字、偏差値61以上は緑の背景）。上に**指数クロス**（2026-11-12〜。`cross_rows`）: 複数の指数で同時に上位の馬の一覧。上位 = 順位3位以内 または 偏差値61以上（`CROSS_RANK_MAX`・`CROSS_HEN_MIN`。基準は `xc` = either／rank／hen／both で選ぶ）。`xmin`（何指数以上。既定2）、`xa`・`xb`（この2指数の両方が上位）、`xonly=1`（下の出馬表を該当馬だけに）。表は独立スクロール・見出し固定
- `/entries/hits`: 指数クロス好走馬・馬ごとの該当数の要約・区分ごとの表（WEDGE・adjusted_WEDGE・年数の列あり）。xjoin が古い形式ならその場の照合に切り替えて、その旨を表示
  - **冗長な「通常」の行を省く（2026-11-12〜。`drop_redundant_normal`）**: 同じ馬・指数（base）・family（スキーム・地域も）・結合データで、オッズ帯 normal とオッズ帯別（odds10u・odds10_50・odds50p のどれか）が**同じ区分**に当たり、件数の差が3以内（`REDUNDANT_NORMAL_DIFF`）なら、normal の行を表からも馬ごとの該当数からも除く（省いた行数を画面の上に出す）。件数が離れている・区分が違う・オッズ帯別の行が区分に入らないときは残す。`/entries/wedge`・CSV は省かない（全件）
  - 並びは オッズ帯（**2〜10倍未満 → 10〜50倍 → 50倍以上 → 通常**。`HITS_ODDS_RANK`）→ 馬番 → 複勝率（消しは昇順）→ 指数（台帳の順）・family・スキーム・結合データ・テーブル名（同点の並びも決める。表示の上限で出る行が xjoin の行の順に左右されない）。横断検索 `/` のオッズ帯の並び（`shisu.odds.ODDS_RANK`）は変えていない
  - 区分ごとに約500行まで表示（`HITS_VIEW_LIMIT`・`spread_cap`）。先頭から切らずに (馬番, オッズ帯) ごとの上限をそろえる（行の少ない組は全部出し、余った枠は多い組に回す）。**枠はオッズ帯別の組に先に配り、通常の組は残りの枠**（どの組も最低1行は出す）。見出しと馬ごとの該当数は（冗長な通常を除いた）全件で数え、全件は `/entries/wedge.csv`（CSV には条件ヒット馬と同じ判定の「区分」列がある）
  - **穴の軸の強調**: odds_mode が odds10_50・odds50p で 絶対軸・軸候補 の行は `<tr class="hot">`（行全体が赤の太字・薄い赤の背景。`is_hot`）。馬ごとの該当数に「穴の軸（10倍以上）」の列
  - **表の独立スクロールと見出し固定**: どの表も `<div class="scroll">`（`max-height:520px; overflow:auto`）の中で、`thead th` は `position:sticky; top:0; z-index:2`
  - **過剰人気カット（2026-11-13〜。`cut_low_10u`。既定 ON。チェックの表記は「2〜10倍未満（odds10u）の軸・相手は回収値100以上のみ（過剰人気カット）」。引数の名前は変えていない）**: オッズ帯が odds10u（`FAV_MODE`）で区分が 軸候補・相手候補（`CUT_CATEGORIES`）の行のうち、複勝回収値も単勝適正回収値も 100（`CUT_MIN_RETURN`）未満の行（`is_low_return_favorite`）を除いてから、4区分の表・馬ごとの該当数・指数クロス好走馬を数える（`classify_hits(..., cut_low=True)` → `drop_redundant_normal(rows, cut_low)`）
    - 値の無い（NULL・数でない）回収値は「100以上」ではないものとして除く（classify の「欠損は満たさない」と同じ見方）。絶対軸（回収値100以上が条件）・消し候補・ほかのオッズ帯は対象外
    - 冗長な「通常」の行との組み合わせ（`drop_redundant_normal`）: 冗長かどうかはカットの前の行で決める。カットした odds10u の行とほぼ同じ中身（同じ区分・件数差3以内）の「通常」の行は、カット ON でも出さない（同じ過剰人気の条件が「通常」として出直さない）。**その「通常」の行自身の回収値が100以上でも出さない**（件数差3以内なので、その上振れは10倍以上の馬1〜3頭ぶんでしかない。つまりカット ON の表 = カット OFF の表 − カットの条件に当たる行）。件数が離れた「通常」の行は別の情報なので、回収値に関係なく残る（カットの対象は odds10u だけ）。行の順には依らない。回収値100以上の「通常」を残したくなったら、`drop_redundant_normal` で、カットされる行を冗長の相手から外し、通常の行自身が回収値100未満のときだけ省くようにする
    - 引数: 絞り込みフォームは hidden の `cut_low_10u=0` とチェックボックスの `cut_low_10u=1` を送る。`1` が1つでもあれば ON、引数があって `1` が無ければ OFF、引数が無ければ ON（`cut_low_request`）。画面の上に除いた行数（軸候補・相手候補）と、ほかの絞り込みを保ったまま ON／OFF を入れ替えるリンクを出す。出馬表（/entries/race）のフォームにはこのチェックは出ない
    - 画面だけの絞り込み: DB・xjoin・`/entries/wedge`・`/entries/wedge.csv`（「区分」列）は変えない
  - **消し(2〜10倍未満) の集計と判定（2026-11-13〜。表記は 2026-11-14 から。`horse_stats`・`danger_level`）**: 馬ごとに、odds10u の消し候補の行数 `keshi_fav` と、それが出た指数の数 `keshi_fav_inds`（指数ごとの内訳 `keshi_fav_by`）を数え、「指数クロス好走馬」「馬ごとの該当数」の両方の列に出す（normal・odds10_50・odds50p の消しは数えない。消し候補の合計の列は今までどおり）
    - 指数クロス好走馬のタグ: 同じ馬・同じ指数に odds10u の消しがあれば `{指数} {abs} / {axis} (消{n})`（`<span class="tag clash">`。同じ指数の中で軸と消しが出ている）
    - 判定（2026-11-14 に引き締め。`danger_level(abs_rows, axis_rows, keshi_fav, keshi_fav_inds)`）: `abs_rows` = 絶対軸の行数、`axis_rows` = 軸の合計（絶対軸 + 軸候補）の行数
      - `dominant`（⚠️消優勢・青紫 `.kd`・行 `tr.kd-dominant`）= `abs_rows == 0` かつ `keshi_fav >= 3`（`KESHI_DOMINANT_MIN_ROWS`）かつ `keshi_fav_inds >= 2`（`KESHI_DOMINANT_MIN_INDS`）かつ `keshi_fav > axis_rows`
      - `caution`（要注意・グレー）= 消優勢ではなく、`axis_rows >= 1` かつ `keshi_fav >= 2`（`KESHI_CAUTION_MIN_ROWS`）かつ `keshi_fav >= axis_rows`（絶対軸のある馬は、消しがどれだけ上回ってもここまで）
      - どちらでもなければ何も付けない: 消しが1行だけの馬、軸が無く消しが2行だけの馬、軸が無く消しが1指数だけの馬（3行以上でも）
      - 2026-11-13 の基準（`keshi_fav > 0 かつ keshi_fav >= axis_rows` → 消優勢、`keshi_fav >= axis_rows × 0.5` → 要注意。`KESHI_*_RATIO`）は廃止。軸候補の最低件数を 20 に上げて軸の行が減ったので、1〜2行の偶発的な消しで警告が付きすぎるため
      - 出る場所は同じ: 両方の表の「判定」の列と行の背景、4区分の表の馬名の横のバッジ、馬ごとの該当数の見出しの頭数。表の上の説明は定数から作る（jinja の `kd_rule`）
    - 行数はどれも過剰人気カット・冗長な通常の除外のあとの数（カット ON だと軸が減るので、判定は厳しくなる）。馬ごとの該当数は `axis_cross` の結果（指数クロスと同じ集計）から作る
  - **負の相関にある指数の消し候補を除く（2026-11-16〜。`tradeoff_keshi`）**: 同じ取得元の中で片方が高いともう片方が低く出やすい指数の組（`shisu.indicators.TRADEOFF_GROUPS`。双方向）: StridePlus `先行・想定前半・ST指数` ⇔ `瞬発・想定後半・追走・持久・持続`、吉馬 `吉馬_先行力` ⇔ `吉馬_末脚力`、安井式 `安井_スピード` ⇔ `安井_スタミナ・安井_パワー・安井_根性`。ある馬が片方の指数で 絶対軸・軸候補 に1行でも当たっていれば、その馬の、対になる指数の「odds10u 専用の規則（複勝率 < 20%）だけで消し候補になっている行」（`keshi_rule_of(h) == "fav"`）を除く
    - 除かないもの: 全モード共通の規則（複勝率 < 5% かつ 掲示板率 < 10%）も満たす消し（odds10u でも）、normal・odds10_50・odds50p の消し、組に無い指数の消し、同じ側の指数どうし、別の馬
    - 軸ヒットは、冗長な通常の除外と過剰人気カットのあとに残った 絶対軸・軸候補 の行で見る（画面に出ている軸と同じ。カット ON で消える回収値100未満の軸しか無い指数は軸ヒットに数えない）
    - 除いた行は、消し候補の表・馬ごとの該当数・`keshi_fav`・`keshi_fav_inds`・`(消n)` のタグ・消優勢／要注意の判定のどれにも入らない。画面の上に「負の相関にある指数の軸ヒットにより除外した消し候補 N 行」（`info["tradeoff"]`。0 なら出さない）
    - 除いたあとで、冗長な通常をもう一度決め直す（`classify_hits`）: 除いた odds10u の消しと件数が近いというだけで省かれていた「通常」の消し（全モード共通の規則による別の消し）は残す。軸・相手の行は消しの行に左右されないので、決め直しても同じ
    - 画面だけ: DB・xjoin・`/entries/wedge`・CSV（「区分」列）は変えない
  - **「危険条件メモ / 調査」の列（2026-11-16〜）**: 4区分の表の `年数` の右隣。どの行も 結合データ のセルと「調査」が `/entries/drill`（その行の region・indicator・family・rank_scheme・odds_mode・key）を別タブ（`target="_blank"`）で開く。メモ（§4.8）のある行は、列の中に赤の太字（`style="color:#b91c1c;font-weight:700"`・class `notetext`）で本文（200文字 `NOTE_SHOW` まで。超えたら「…」）、結合データのセル（class `hasnote`）とメモのセルの `title` に全文。`note_of(h)`・`drill_url(h)`・`note_cell(...)`（jinja から呼ぶ。HTML はエスケープする）。メモのファイルが読めないときは、メモなしで表示して画面の上に理由を出す
  - **条件特化ヒット（2026-11-17〜。`special_hits`）**: 4区分の表の下のカード「条件特化ヒット（指数が下位帯でも絶対軸・軸候補の基準を満たす強い条件）」（class `card cat-special`）。ヒット行のうち `low_tail_category(h)`（`classify.low_tail_axis_category`: 結合データの末尾が下位で4区分では区分なしだが、数の基準は絶対軸・軸候補。§6.1）が付く行
    - まとめ方: (馬番, family, rank_scheme, odds_mode, region, 結合データから末尾の部品を除いた部分 `condition_part`) が同じ行は、代表の1行（`複勝率_val` が最大 → `件数` が最大 → 指数の台帳の順）にする。代表の行に `_cat`（代表の行の元区分）・`_members`（まとめた行を指数ごとに1つ: base・cat・tail・そのヒット行。台帳の順）・`_n_inds`（指数の数）・`_n_rows` を足して返す（もとのヒット行は書き換えない）
    - 過剰人気カット（`cut_low_10u`）が ON なら、odds10u で複勝回収値も単勝適正回収値も 100 未満（値なしも）の行を、まとめる前に除く（行数は `info["special_cut"]`。カードに出す）。ほかのオッズ帯は対象外
    - 列（27列）: 枠・馬番・馬名・騎手・元区分・指数数・指数（末尾）・パターン・オッズ帯・地域・結合データ（代表）・件数・率・回収値・WEDGE・年数・危険条件メモ / 調査。件数・結合データ・「調査」は代表の行の `/entries/drill`、指数名のタグはその指数の行の `/entries/drill` を別タブで開く（絶対軸の基準の指数は太字。メモのある指数は枠と `title`）
    - 並び・表示の上限（`spread_cap`・`HITS_VIEW_LIMIT`）・`xonly=1` の絞り込みは4区分の表と同じ。4区分の表・馬ごとの該当数・消優勢の判定には入れない（判定は変えていない）。冗長な「通常」の除外・負の相関の除外はしない（オッズ帯が違えば別の行）。`/entries/wedge`・CSV は変えていない
  - **指数クロス好走馬**（`axis_cross`）: 馬ごとに、絶対軸・軸候補の行がある指数（base）を数える。`xmin`（既定2）指数以上、`xa`・`xb` を指定したときはその両方で軸の馬を一覧にする（軸に当たった指数ごとの 絶対軸/軸候補 の行数。穴の軸に当たった指数は赤字）。`xonly=1` で4区分の表を該当馬だけにする。引数は出馬表の指数クロスと共通（`cross_request`。hits では `xc` は使わない）
- `/entries/key`: WEDGE・adjusted_WEDGE・年数の列あり
- **条件のみの family の行（2026-11-17〜）**: 指数の欄（base・indicator）が「条件のみ」の行として、条件ヒット馬の4区分の表・WEDGE 一覧（指数の絞り込み・「馬 × 指数」の列）・横断検索（指数の選択肢）に出る。区分の判定は指数の上限ゲートを掛けない（`_hit_rec` が indicator を渡す・横断検索のプリセットは `sql_condition(…, indicator_col="indicator")`）。条件特化ヒットには入らない。指数クロス好走馬・消し(2〜10倍未満) の指数数は、ほかの指数と同じに「条件のみ」を1つの指数として数える（§9 の56）。出馬表（/entries/race）の指数の列・指数クロスには出ない（順位・偏差値が無いため）
- `/entries/wedge`（レース別 WEDGE 一覧。`/entries` のレース一覧・`/entries/race`・`/entries/hits` からリンク）: そのレースの全馬 × 照合で該当した全キー（条件ヒット馬と同じ照合結果。xjoin が無ければその場照合）を、**閾値で足切りせず**に並べる（ただし DB に入っている行だけ。aggregate をコンパクト化（既定）したときは、区分にも WEDGE≧0.70 にも入らず件数30未満の行は出ない。全部見るなら `--no-compact`）
  - 絞り込みは選択だけ: オッズ帯（既定「通常」。「すべて」も可）、指数、パターン（family）、馬番。並べ替え: adjusted_WEDGE（既定）・WEDGE・WIN・EDGE・件数・adjusted_WEDGE_件数・馬番。空欄は常に最後
  - 上に「馬 × 指数」の要約（その馬・指数で該当したキーの adjusted_WEDGE 最大／WEDGE 最大・該当数、馬ごとの adj 最大・adj 平均・WEDGE 最大・WEDGE 平均）。色: 1.00以上・0.80以上・0.62以上・0.50未満
  - 画面の並び（2026-11-12〜）: 見出し → オッズ帯・馬番・並べ替えの1行 → 「馬 × 指数」の表。指数・パターンのチェックボックスは `<details class="filters"><summary>指数・パターンの絞り込みを開く（指数 n 個を選択中 ／ パターン …）</summary>` の中（既定で閉じている。選択していても閉じたまま、数を見出しに出す。閉じていてもチェックは送信される）。「馬 × 指数」と全該当キーの表は `<div class="scroll tall">`（72vh）の中で見出し固定。要約の計算（`wedge_matrix`）と列は変えていない
  - 画面の表は先頭3000行まで。`/entries/wedge.csv` は同じ条件の全行（UTF-8 BOM 付き）
  - 全該当キーの表にも「危険条件メモ / 調査」の列（2026-11-16〜。`年数` の右隣・`年の件数計` の左）。結合データ と「調査」は `/entries/drill`、「成績表」は今までの `/entries/key`（結合データの前方一致）を別タブで開く。CSV には列を足していない
- `/entries/drill`・`/entries/drill.csv`・`POST /entries/note`: 逆引きと危険条件メモ（§3.10）
- `/ingest`: 外部指数の取得（§3.6）
- DB が未構築・旧形式なら各画面の上部に作り直しの案内を出す

### 3.6 ingest.py ／ /ingest（外部指数の取得。`shisu/ingest/`）

旧来の別コンテナのシステムを統合したもの。ベースCSVを1回渡して、選んだ取得元を実行する（1つ失敗しても残りは続ける）。
**取得元は同時に実行する（2026-11-12〜）**: 選んだ取得元が2つ以上なら、取得元ごとに1スレッド（`ThreadPoolExecutor`）で同時に動かす（①〜④ はサイトが別なので、待ち時間が重ならない。全体の時間はいちばん遅い取得元ほど）。アクセスの間隔は取得元ごとの `Fetcher` が今までどおり守る（同じサイトへ同時にはアクセスしない）。同時に実行している間の Ctrl-C（KeyboardInterrupt。待っているメインのスレッドに届く）は、中止の合図を立てて動いている取得元を止め（次のアクセスの前に止まる。`[INFO] 中断されました…`）、止まるのを待ってからそのまま上に伝える（2026-11-13〜。CLI は外からの中止の合図が無いので `run_ingest` が内部に持つ）。失敗した取得元は `[ERROR] {名前}: …` とトレースバックを1つのメッセージで出す。ログは1行ずつロックして書き、取得元の名前の無い行には `[INFO] Ω指数: …` のように名前を付ける（`source_logger`）。取得元ごとに `[DONE] {名前}: N ファイル（x.x 秒）`、最後に `[INFO] 全体 x.x 秒`。結果の並びは今までどおり（`ORDER`）。順に実行するには `IngestRequest(parallel=False)`／CLI の `--sequential`（出力は同時に実行したときと同じ）。
**⑤ 一隊（ittai）は 2026-11-05 に廃止**（サイト側の仕様変更でスクレイピングできなくなった）。画面・CLI の選択肢から外し、`--sources ittai` は理由を添えて断る（終了コード2）。番号は旧来のまま（⑥ ラップ）。

| 取得元 | 入力 | 取りに行く先・1回の単位 | 照合キー | 出力（`ingest_out/{実行日時}/`） |
|---|---|---|---|---|
| omega ① Ω指数 | ベースCSV | `keibalab.jp/db/race/{rid12}/syutsuba.html`（1レース。rid12 = 11〜14桁目と17〜18桁目を除いた12桁） | rid12 + 枠番 + 馬番 | `omega/{YYYYMMDD}_omega.csv` |
| kichiuma ② 吉馬6指数 | ベースCSV | `kichiuma.net/php/search.php?race_id=…&p=fp`（1レース。場所コード1桁目 0→7・1→8） | 11〜14桁目を除いた14桁 | `kichiuma/kichiuma_{sp_ability,senkou,sp_trust,sp_control,sp_max,sueashi}/{YYYYMMDD}_kichiuma_{key}.csv` と `{元名}_{key}_merged.csv` |
| kayochin ③ かよちん | ベースCSV | `kayochinkeiba.com/{YYMMDD}{場所英名}/`（開催日×場所。中京=cyuukyo・京都=kyouto） | (YYMMDD+場所, R, 馬番) | `kayochin/{YYYYMMDD}_kayochin.csv` |
| keibaai ④ keibaAI | ベースCSV＋開始ID・終了ID | `muryou-keiba-ai.jp/predict/2021/01/06/{連番}/`（1連番。日付・場所・R はページの meta description / title） | 14桁 | `keibaAI/{YYYYMMDD}_keibaAI.csv` |
| lap ⑥ ラップ6指数 | ラップCSV | なし（手元で計算） | — | `lap/{2F,1F,4F,33rap,rabbit_2F,rabbit_1F}/{日付S}_{名前}.csv`, `lap/pace/{実行日}_pace_of_racecoment.csv` |
| yasui 安井 Excel | Excel（＋ベースCSV） | なし | 日付 + 場所コード + R + 馬番（14桁） | §3.7 |
| past 過去SP・ST指数 | ベースCSV（馬名・芝ダ・距離・年限定 のある出馬表の形）＋ result_indices/・履歴 | なし（手元で計算。§3.11） | レースID(新) | `past_sp_st/past_{sp,st}_{avg,max}{3,5}/{YYYYMMDD}_past_….csv` と `{元名}_{key}_merged.csv` |

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
- **過去SP・ST指数（取得元 `past`。2026-11-17〜。`shisu/ingest/past_perf.py`）**: ベースCSVの各馬について `past_perf_indices.compute`（replace・calc と同じ関数）で8指数を計算し、TARGET 取込用CSVを `past_sp_st/{key}/` に出す（key = `past_sp_avg3`・`past_sp_avg5`・`past_sp_max3`・`past_sp_max5`・`past_st_…`）。ext_indices へのコピー先は `ext_indices/past_sp_st/{key}/`（形式A として、その指数に振り分けられる）
  - ext_indices へのコピーは、この取得元だけ「今回のファイルにある馬は空欄でも今回の値」（`copy_to_ext(keep_old=False)`。空欄は「取れなかった」ではなく「対象の走が無い」なので、想定の馬場状態などを変えて計算し直したときに前の値を残さない。消した頭数をログに出す。今回のファイルに無い馬の行は残す）
  - ベースCSVが複数で列の名前が違っても（`馬名`／`馬名S`・`年齢限定`／`年限定`・`芝・ダ`／`芝ダ`）、行ごとに値のある列から読む（`past_perf_indices._col`。縦につないだ表の NaN を "nan" という馬名・年齢にしない）。列の注意書き（年齢限定・血統登録番号が無い など）はファイルごとに名前つきで出す
  - ベースCSVの `レースID(新)` がレース単位（16桁）なら、馬番を付けた18桁で TARGET 取込用CSV・マージ完全版を書く（16桁のままだと1レース1頭に潰れる）
  - 画面: 「1. 取得元」の欄に「想定馬場状態（過去SP・ST指数用）」のプルダウン2つ（`baba_turf` 芝・`baba_dirt` ダ。選択肢は `自動（出馬表の列／無ければ指定なし）`（値 `auto`）/ `良` / `稍・重` / `不`。前に保存した値が選ばれている）。知らない値は 400。フォームを経ない POST（項目なし）は保存してある値
  - 成功したら、選んだ値を `past_perf_settings.json` に保存する（calc_entry_hensachi の既定になる。ジョブ画面に案内）。失敗した実行・受け付けなかった POST では保存しない。保存できなくても（書けない場所）取得元は成功のまま `[WARN]`
  - ベースCSVに 馬名・血統登録番号 のどちらの列も無ければ、この取得元だけ失敗（理由つき）。芝ダ・距離 の列が無ければ警告して、古馬戦でも600日以内の全走で計算する。結果指数（result_indices/）も履歴のファイルも無ければ失敗
  - CLI: `--sources past --baba-turf 良 --baba-dirt 不`（省いたほうは保存してある値。知らない値は終了コード2）
- CLI: `python ingest.py --sources omega,kichiuma,…|all [--base …] [--lap …] [--yasui …] [--keibaai-start N --keibaai-end M] [--interval 秒] [--no-ext-copy] [--sequential] [--baba-turf …] [--baba-dirt …] [--out DIR] [--run-id ID]`。入力の既定は `ingest_in/{base,lap,yasui}/` の全ファイル。終了コード: 0 正常 / 1 失敗した取得元あり / 2 入力の不足
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
  - 条件の候補（POOL。40種。2026-11-17 に 28 → 34、2026-11-18 に 34 → 40）は 重量種別・クラス名・枠番グループ・騎手 と 条件の36列（22条件の24列・外部レース印の前走条件4列・近5走の要約2列・基準比と過去SP/ST 3走順の6列）。[指数帯] は `--tails`（既定 `RBAND,HBAND` = 指数順位帯（s1・s2）と 偏差値帯。`RANK` = 指数順位・`NONE` = 条件のみ も指定できる。知らない値・空は終了コード2）
  - 骨格: s0 コース不問 / s1 芝・ダ / s2 場所×芝・ダ / s3 芝・ダ×距離 / s4 芝・ダ×距離帯 / s5 場所×芝・ダ×距離帯 / s6 COURSE / s6n COURSE×重量種別×クラス名（normal の舞台）。場所・距離・芝(内・外) は単独の部品（§5.1）
  - 因果トリオ（s0）: ① 騎手 × ローテ（間隔帯・叩き・斤量帯・斤量増減・距離増減・輸送距離）× 前走（前走着順・前走人気・前走上り3F順・前走脚質・前走R印3・前走クワドラント）、② コースの物理属性 × (前走R印3／前走脚質) × (前走着順／前走上り3F順)、③ 前走人気 × 前走着順 × 今走の条件、④ 枠番グループ × (初角帯／F係数帯) × 前走脚質、⑤（2026-11-18〜）過去SP今走基準比 × 過去SP3走順 × (前走SP基準比／前走着順／前走人気／近5走同条件) と、ST の同じ組（8組。計 95 トリオ）。ペア `過去ST今走基準比 × 過去ST3走順`（名前 `X_stb_st3`）は s0 の2条件ペアに入るので、`ST平均基準超×ST3走1~5位×1~5位`（末尾はオメガ指数などその master の指数の帯）の形の成績表ができる
  - `initial`: 初期セット（49の型・83 family・129通り）だけ
  - `default`（既定）: 初期セット ＋ s0 の単一条件・2条件ペア・因果トリオ ＋ s2〜s6・s6n × 単一条件（1,127テンプレート → 2,277 family・3,709通り。2026-11-17 は 858 → 1,739 family・2,791通り、2026-11-16 までは 633 → 1,289 family・2,098通り）
  - `all`: ＋ s1 ×（ペア・トリオ）＋ s2・s3・s4・s6 × ペア ＋ s5 × 主要条件（MAJOR）のペア（4,727テンプレート → 9,299 family・15,195通り。2026-11-17 は 3,433 → 6,735 family・10,815通り、2026-11-16 までは 2,371 → 4,635 family・7,557通り）。MAJOR の組は変えていない（2026-11-17 の6条件・2026-11-18 の6条件は単一条件・ペアに入る。因果トリオは⑤だけ足した）
  - **冗長の除外**（`redundant()`。同じグループにしかならない組み合わせ）: COURSE とコースの物理属性（R値帯・直線帯・初角帯・CFS帯・F係数帯・今走クワドラント・左右・坂・距離帯）、場所 と 左右・坂、距離 と 距離帯、左右遷移 と 左右。部品の集合が同じ候補（並びだけ違う）は1つにまとめる（初期セットを優先）
  - 新しい組み合わせは `X_{部品の略号}`（偏差値帯は末尾 `_hen`。場所 `plc`・距離 `dist`・芝(内・外) `io` など）の名前になる
  - 骨格ごとの数（POOL 40 のとき）: s0 のペアは 779・トリオは 95、s2 × 単一条件は 38（左右・坂だけを除くので 40−2）、s2 × ペアは 703（38C2）、s3・s4 × ペアは 740、s6 × ペアは 465
  - **その指数自身の順位を部品にした候補**（`過去SP3走順` を含む候補 × `過去SP_3走平均` の master、ST も）は、その master では採点しない（`is_self_redundant`。本集計と同じ。レポートの `masters` 列はその分だけ少ない）
  - **条件のみ（`--tails` に `NONE`）**: 同じテンプレートの「条件のみ」の候補（名前は `X_…_none`）を足す（default で +1,127 family・計 3,404 family・5,131通り）。採点は本集計と同じに、選んだ master のうち地域ごとの基準の master 1本だけで・全部の行で行う（`score_master(…, no_index=True)`。指数ごとの master では採点しない）。指数の帯が無いので基準A の指標（wedge_spread・fuku_spread・monotonic）は空 → 基準A では選ばれず、基準B（絶対軸・軸候補のキーの数）で選ばれる。基準B の数は基準の master 1本ぶん（`masters` 列 = 地域の数）なので、`--min-axis` は指数つきの候補（全部の master の合計）より厳しく効く
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
- 目安（検証環境・1プロセス、30万行の master 1本、オッズ帯4区分）: 1通りあたり約2.4秒 → `initial`（129通り）約5分、`default`（3,709通り）約2.5時間、`all`（15,195通り）約10時間（通り数からの換算。2026-11-16 までの 2,098・7,557通りで計測した約1.4時間・約5時間から）。master が多い（2地域 × 約34指数 = 約68本）と `default` は1プロセスで4日近くかかるので、`--indicators タイムM,対戦M` など代表の指数で選ぶか `--workers` を使う。メモリは1プロセス 0.5GB 前後（30万行）
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

### 3.10 逆引き `/entries/drill` と危険条件メモ（2026-11-16〜。`shisu/drill.py`・`shisu/notes.py`・app.py）

条件（成績表の1行）に当たった過去の馬を調べて、隠れた共通項・死角を探し、分かったことをメモとして条件に付ける。**DB も master も作り直さずに使える**（master_csv があればよい）。

- 引数（GET）: `region`, `indicator`（無ければ `base`）, `family`, `rank_scheme`, `odds_mode`（空は normal）, `key`。条件ヒット馬・WEDGE 一覧の行のリンクが付ける
- **取り出し（`drill.extract`）**: `drill.find_master(region, indicator)` で master を探す（master_csv のファイル名を集計と同じ `parse_source_name` で読んで比べる。引数からパスを組み立てない）→ master を 5万行ずつ読み（`CHUNK_ROWS`。cp932 で読めなければまとめて読み直す）、`指数有効=1` の行について `shisu.families.build_keys(family, scheme)` のキーが `key` と同じ行を取り、`shisu.odds.odds_mask` でオッズ帯を絞る。重複は集計と同じ（レースID × 馬番。無ければ馬名）。着順は `parse_finish`（取消・除外は「集計外」、失格・中止は着順=頭数）。新しい日付が上
  - 取り出し方が集計と同じなので、同じ master・同じ設定で作った成績表の `件数`・勝率・複勝率（頻度）と、分母に入る行の数・1着・3着以内の数が一致する（テストで DB の行と照合）。ページの上に DB の成績表の行（件数・率・回収値・WEDGE・区分）を出し、件数が違えば注意書き（集計のあとに master を作り直した・前のオッズ帯の区切りで集計した DB など）
  - family は今の DB の `__families__` の定義（`connect()` が台帳に入れる）。指数が「条件のみ」の行（条件のみの family）は、集計と同じ基準の master（`base_masters`）から、`指数有効` に関係なく全部の行で取り出す（明細の 指数値・順位・偏差値 は、その master の指数（タイムM など）の値）。スキームの要る family にスキームが無い・オッズ帯が無い・family が無い・master を読めない（途中で切れた gzip・空のファイル・列が無い）ときは理由を出す（500 にしない。`DrillError`）。master が無い・読めないときは逆引きを出さず、メモの欄だけ出す
  - 取り出した行は、条件と master（場所・更新時刻・大きさ）ごとに6個まで覚えておく（`DRILL_CACHE_SIZE`。メモの保存のあとの再表示・CSV で master を読み直さない）
- **TARGET 貼付用のレースID**（`drill.id_lists`）: `<textarea readonly>` が2つ。左 = `レースID(新)` 18桁（馬単位）、右 = `レースID(新/馬番無)` 16桁（レース単位・重複なし）。ラジオで 全件／好走（1〜3着）／凡走（4着以下・失格・中止）を切り替え（ページに埋めた JSON を JS で入れ替える。JS が無くても全件は入っている）、「コピー」ボタンで丸ごとコピー。取消・除外の馬は入れない。ブラウザの「戻る」・タブの復元ではラジオの選択だけが戻る（change は起きない）ので、表示のたび（`pageshow`）に、選ばれているラジオに欄と件数を合わせ直す
- **コア分布サマリー**（`drill.summarize`・`summarize_item`。分母に入る行で）: 項目 = 単勝オッズ（帯: 2.0倍未満／2.0〜2.9／3.0〜4.9／5.0〜9.9／10〜19.9／20〜49.9／50倍以上。数でない・0 以下は値なし、inf は 50倍以上）・枠番・馬番・頭数・枠番グループ・場所・芝・ダ・距離（距離_raw）・芝(内・外)・クラス名・重量種別・騎手・年 ＋ 22条件の24列。値が1つも無い項目は出さない
  - コア（`CORE_TARGET` = 80% 以上を覆う範囲）: 並びのある項目（全部の値が数・オッズの帯）は、並びの上でつながった範囲のうち 80% 以上を覆ういちばん狭いもの（同じ長さなら多く覆うほう）。並びの無い項目（騎手・脚質など）は、単独1件の値を除き、件数の多い値から足して 80% に届くまで（届かなければ2件以上の値の全部。全部が単独1件なら全部）
  - 危険候補: `single` = コアの外の単独1件（全体が `SINGLE_MIN_N` 5件以上のとき）、`bad` = 凡走の集まり（`BAD_MIN_COUNT` 2件以上・好走率が全体の `BAD_RATE_RATIO` 0.5 倍以下・`件数 × 全体の好走率 ≥ BAD_MIN_EXPECTED` 1.0。コアの中の値にも付く。全体の好走率が低い条件で少ない件数の「好走0」を危険にしないための最後の条件）
  - 表示: 項目 / コア / コアの割合 / 危険候補 / 内訳（値：件数（好走の数）。種類の多い項目は件数の多い順に `MAX_CHIPS` 18個まで）
- **明細の表**: 区分（好走／凡走／集計外）・日付・場所・レースID(新)・馬番・馬名・騎手・単勝オッズ・着順・指数値・順位・偏差値・枠番・頭数・芝・ダ・距離・クラス名・重量種別・22条件。好走は緑（1着は太字）・凡走は赤・集計外は灰色の字。画面は新しい順に 2,000 行（`DRILL_VIEW_LIMIT`）まで。`/entries/drill.csv` は取り出した全行・master の全列＋`区分`（UTF-8 BOM 付き。master が無ければ 404）
- **危険条件メモ**: ページの上の欄「⚠️ 危険条件・死角メモ（ここに書くと条件ヒット馬の表に赤字で表示されます）」の `<textarea>` と「保存」（`POST /entries/note`）。本文を空にして保存、または「このメモを削除」で消える。終わったら 303 で逆引きページへ戻る（`note_result=saved|deleted|none|error`）
  - 「全地域共通のメモにする」: region を空にして保存する（どの地域の同じ指数・family・スキーム・オッズ帯・結合データの行にも出る）。そのとき、その地域だけのメモは消す（残すとそちらが優先されて共通のメモが出ないため）。引くときは その地域のメモ → 全地域共通のメモ の順（`notes.lookup`）
  - 別のサイトのページからの POST（`same_origin()`）と、Host が `localhost`・IP アドレス・`SHISU_ALLOWED_HOSTS` 以外の POST（`allowed_host()`。DNS リバインディング対策）は 403（/ingest の POST と同じ見方。GET の画面は Host を見ない）。指数・family・結合データの無い POST・条件の1項目が 500 文字（`notes.MAX_FIELD`）を超える POST は 400。戻り先の URL は条件の6項目と結果だけで作る（フォームのほかの値は使わない）。メモのファイルが読めない形のときは保存しない（上書きして人が書いたメモを壊さない。逆引きページに理由を出す）
  - 条件ヒット馬・WEDGE 一覧での表示は §3.5

---

### 3.11 過去SP・ST の8指数（2026-11-17〜。`shisu/past_perf_indices.py`）

SP指数・ST指数は**レースのあとに確定する値**なので、そのレースの値をそのレースの指数にしない（未来の情報）。各レース・各馬について「そのレースの日より前・600日以内の対象の走」の直近3走・5走の平均と最高だけを作る。過去側（replace）・出馬表側（calc_entry_hensachi）・/ingest が同じ `compute` を通る。

- **8指数**（台帳に登録。`reverse=False`・`zero_missing=True`）: `過去SP_3走平均`・`過去SP_5走平均`・`過去SP_3走最高`・`過去SP_5走最高`・`過去ST_3走平均`・`過去ST_5走平均`・`過去ST_3走最高`・`過去ST_5走最高`（`INDICATORS`: 名前 → (種類, 走数, avg/max)。ファイルの名前は `KEYS`: `past_sp_avg3` など）
- **過去走の履歴**（`history_rows`・`History`。2026-11-17 の2件目から、結果指数の無い走も入れ、場所・着順を持つ。§3.13・§4.9）: 基本CSV（`base_csv_paths`: `全場_タイムM.csv` → `全場_対戦M.csv` の順に、あれば1本。どちらも無ければ地域ごとの基本CSV（タイムM 優先）を全部つないで レースID(新) の重複を除く）の各行から、(馬ID, 馬名, 日付, レースID(新) 18桁, 場所, 芝・ダ, 距離, 馬場状態3区分, 着順, SP, ST)
  - 馬の見分け（`History.runs`）: `血統登録番号`（数字10桁）を優先し、無ければ正規化した `馬名`（NFKC・空白除去・先頭の (外)・[地]・○ などを除く）。今走に番号があれば「その番号の走 ＋ 同じ馬名で番号の無い行の走」（番号の列が無い期間の基本CSVの分。同じ馬名でも別の番号の行＝別の馬は入れない）。今走に番号が無ければ（番号の列の無い出馬表など）、その馬名の全部の走
  - 入れない走: 取消・除外（`parse_finish` の分母外。失格・中止は走ったので入れる）、障害（芝・ダ が「障」・トラックコード51以上）、レースID(新)・日付の読めない行、馬を見分けられない行。SP も ST も値が無い走は、履歴には入るが 8指数の母集団には入らない（`History.runs(kind, …)` はその指数の値がある走だけを返す）
  - SP・ST は別々の母集団（その指数の値がある走だけ）
- **対象の走**（`select_runs`。今走の日 D・芝・ダ・距離・馬場状態3区分・限定戦かどうか）:
  1. `D − 600日 ≦ 走った日 ＜ D`（`WINDOW_DAYS`。当日・未来は入らない）。新しい順（同じ日なら レースID の大きい順）
  2. 今走が**2歳戦・3歳限定戦**（`age_limited`: `年齢限定`／`年限定` の列が `2歳`・`3歳`（全角も）。`3上`・`3歳以上`・`4上`・`4歳以上` は古馬戦。列が空・年齢として読めない値なら クラス名 が 新馬・未勝利、またはクラス名・レース名に「2歳」「3歳」があって「以上・上」が続かない）なら、1 の全部。列そのものが無いときは `column_notes` が `[WARN]` を出す（3歳限定の1勝クラス・オープン・重賞を見分けられず、`年限定` のある出馬表側と条件がずれるため）
  3. 古馬戦は次の順に、**最初に1走以上見つかった段階**の走だけ（1〜2走でもそこで止める。0走のときだけ次へ）:
     ① 同じ芝・ダ かつ 距離差400以内（`DIST_TOL`。400 ちょうどは入る）かつ 同じ馬場状態3区分（今走の馬場状態が空なら飛ばす）／ ② 同じ芝・ダ かつ 距離差400以内 ／ ③ 距離差400以内（今走の馬場状態があれば、同じ馬場状態も）／ ④ 同じ芝・ダ ／ ⑤ 1 の全部
  - 馬場状態3区分（`baba3`）: 良 → `良`、稍・稍重・重 → `稍・重`、不・不良 → `不`、空・不明 → 空。今走・過去走の 芝・ダ／距離／馬場状態 が空なら、その条件を使う段階には当たらない
- **値**（`aggregate`）: 対象の走（新しい順）の先頭3走・5走の平均（`Decimal` で計算し、小数第2位を四捨五入 `ROUND_HALF_UP` して小数第1位まで。`54.0` は `54`）と最高。例: 古い順に 65・48・50・55・52 なら 3走平均 52.3・5走平均 54・3走最高 55・5走最高 65。対象の走が無ければ空（指数としては 0 ＝ 欠損）。対象の走があるのに値が 0（丸めて 0）になったときは `0.1`（`ZERO_SUBSTITUTE`）にする（0 は欠損の印なので、そのままだと走のある馬が欠損に数えられ、欠損2頭でレースごと使えなくなる）
- **今走の馬場状態**（`resolve_baba`）: 過去側は基本CSVの `馬場状態` の列（実際の馬場）。出馬表側・/ingest は想定の指定（芝・ダ別。`良`／`稍・重`／`不`）があれば、その芝・ダのレースは指定の値、`auto` なら列の値（無ければ空 ＝ 段階②から）
- **履歴のキャッシュ**（`get_history`。§4.9。戻り値が None なのは、基本CSVも前に作った履歴も無いときだけ。8指数に使えるかは `History.has_results`）: 元（基本CSV・結果指数のファイル。結果指数は無くてもよい）の名前・大きさ・中身の CRC32 から作る印（`source_signature`。大きさが同じまま値だけ直したファイルも見分ける。更新時刻は使わない）が `master_csv/__horse_perf_history__.json` と同じなら履歴のファイルを読み、違えば元から作り直して書く。結果指数のファイルが無いとき、前に作った履歴に結果指数の値があればそれを使い（master_csv と csv_files だけ持ってきた場所で8指数を落とさない）、無ければ基本CSVから着順だけの履歴を作る。前に作った履歴を使うときも、基本CSVが変わっていれば（基本CSVだけの印 `bases` で見分ける。行数の合わない履歴も）、走は基本CSVから作り直して、結果指数の値だけを前の履歴から レースID(新) で引き継ぐ（近5走の条件を古い走のままにしない。`[INFO] …前に作った履歴から引き継ぎます`）。基本CSVに cp932 でも UTF-8 でも読めないバイトがあれば、その文字だけ置き換えて読む（master を作る読み方と同じ。`[WARN] …読めない文字があります`）。**前の版（版3）の履歴しか無く、結果指数のファイルも無いとき**（csv_files と master_csv だけ持ってきた場所で、版を上げた直後）は、履歴を捨てずに、走を基本CSVから作り直して結果指数の値を前の履歴から レースID(新) で引き継ぐ（`_legacy_results`。`[INFO] …前の版の履歴（結果指数つき）があり…引き継ぎます`。版4で増えた クラス・馬場4 は基本CSVから作れる）。基本CSVが無く履歴のファイルだけあれば、それを使う（読めない履歴しか無ければ `[WARN]` を出して8指数・近5走の条件を付けない）。replace は必ず作り直す（作れなければ `[ERROR] 過去SP・ST の履歴: …` を出して8指数だけ作らず、ほかの master は作って終了コード1）
- 段階ごとの馬の数をログに出す（`stage_text`: 対象の走なし／①〜⑤／2歳・3歳限定）
- 0 = 欠損（`zero_missing`）なので、対象の走が無い馬は順位・偏差値なし。同じレースに欠損が2頭以上いれば、そのレースはその指数では使わない（§5.5 の1。安井のランクなどと同じ）

### 3.12 外部レース印の前走条件（2026-11-17〜。`shisu/race_marks.py`・`shisu/conditions.py`）

TARGET の外部レース印（レース単位の印。5種）を、各馬の前走のレースで引いて前走条件にする。過去側（replace）と出馬表側（build_db）が同じ `derive(std, inner_lookup, marks, recent)` を通る。

- **読み込み**（§4.10）: `race_marks/`（`RACE_MARKS_DIR`）の下を再帰的に探し（ZIP の中も）、名前で種類を決める（`kind_of`）。見出しなしの3列 `"開催名", RX+旧8桁, "カラーコード2桁+値"`。値は `clean_value`（先頭2文字が16進なら除く → NFKC・前後の空白を除く → `----`・`－`・`-` は値なし）
- **照合**: `前走RID16`（基本CSVの `前走レースID(新)` の先頭16桁／出馬表の `前走開催` から作る16桁）→ `rx_of` で `RX` + 場所2 + 年の下2桁 + 回1桁 + 日1文字（1〜9・10日 = A・11日 = B …）+ R2 にして引く（例 `2026110803030212` → `RX03263212`。旧8桁は ext_indices の旧ID と同じ作り方。月日は RX に入らない）
- **区分**（`conditions.py`。印が無い・区分に入らない値は空 → その部品を含むキーは作らない）:
  - `前走メンバーLv`（member）: `A`〜`E`。`前走タイムLv`（time）: `S`・`A`〜`E`
  - `前走クッション帯`（cushion。**前走が芝のときだけ**）: v < 9.0 → `軟(8.9以下)`、9.0 ≦ v < 10.0 → `標準(9.0~9.9)`、v ≧ 10.0 → `硬(10.0以上)`
  - `前走含水率帯`（moisture。同じレースの1行目 = 芝・2行目 = ダート）: 前走が芝 → 1行目の値で `芝乾(9.9以下)`（v < 10.0）/ `芝標準(10.0~14.9)` / `芝湿(15.0以上)`、前走がダ → 2行目の値で `ダ乾(4.9以下)`（v < 5.0）/ `ダ標準(5.0~9.9)` / `ダ湿(10.0以上)`
  - クッション値・含水率は、数として読めない・0 以下なら値なし（`_measure`）
  - `前走R印3` の補完（pace）: 基本CSV・出馬表の `前走レース印３` から作った区分が空（空欄・`----`・区分に無い値）の馬だけ、ペース判定（`HHH`/`HH` → `HHH~HH`、`H`、`M`、`S`、`SS`/`SSS` → `SS~SSS`）で埋める。値のある馬は基本CSV・出馬表の値のまま
- **過去側**: replace の main が race_marks/ を読んで `master_csv/__race_marks__.csv.gz` に書き、各ジョブがその表を読む（§3.1 の7）
- **出馬表側**: build_db の `entries_side_sources` → `race_marks.load`: race_marks/ にファイルがあればそれ（先週のレースの印が足されていれば、前走が先週の馬にも付く）、無ければ `__race_marks__.csv.gz`、どちらも無ければ空
  - **replace と同じ読み方をする**（`master_csv/__race_marks__.json`。§4.10）: replace を `--no-race-marks` で回した master なら、race_marks/ にファイルがあっても出馬表側は読まない（`[INFO]`）。`--marks-dir DIR`（既定と違うフォルダ）で回した master なら、出馬表側もそのフォルダを読む（そこにファイルが無ければ表）。記録が無い（前の版の master）なら既定のフォルダ
  - build_db の `--marks-dir DIR`（そのフォルダを読む。記録より優先）・`--no-race-marks`（付けない）で、出馬表側だけ変えることもできる（master と違う印を見ることになるので、試すとき用）
- 同じレースの値が複数のファイルにあれば、相対パスの名前順で後のファイル（値なしでは上書きしない。含水率は芝・ダの組ごと）

### 3.13 近5走の要約条件（2026-11-17〜。`past_perf_indices.recent_form`）

条件を細かく掛け合わせずに「この条件・この場での好走実績があるか」を1つの部品にする。§3.11 と同じ馬ごとの過去走の履歴から作る（履歴は結果指数が無くても、基本CSVがあれば作られる）。

- 今走の日 D に対して `D − 600日 ≦ 走った日 ＜ D` の、**着順のある走**を新しい順に5走まで取る（`RECENT_RUNS`。馬の見分け方は §3.11 と同じ `History.all_runs`）
  - 着順 = 確定着順の数。失格・中止は 頭数（分からなければ18）＝ 4着以下に数える。着順が空・読めない走は数えない（5走の枠も使わない）。取消・除外・障害の走は履歴に無い
- `近5走同条件`: その5走のうち「今走と同じ芝・ダ かつ 距離差200以内（`RECENT_DIST_TOL`。200 ちょうどは入る。統合前の距離）」の走の最高着順 → `同条件連対あり`（1〜2着）/ `同条件3着あり` / `同条件4着以下` /（合う走が無ければ空）
- `近5走同場`: その5走のうち「今走と同じ場所 かつ 同じ芝・ダ」の走の最高着順 → `同場連対あり` / `同場3着あり` / `同場4着以下` /（空）
- 今走の 日付・芝・ダ が分からなければ両方とも空。距離が分からなければ 近5走同条件 が空、場所が分からなければ 近5走同場 が空
- **過去側**: replace の各ジョブが `recent_form(target_frame(入力), 履歴)` を `derive(…, recent=)` に渡す（§3.1 の8）。**出馬表側**: build_db の `normalize_entries_df(raw, inner, marks, history)`（§3.4）。同じ馬・同じレースなら両側で同じ値（`tests/test_condition_only.py`）

### 3.14 指数を使わない family（条件のみ。2026-11-17〜。`tail="NONE"`）

指数順位・偏差値帯を使わず、コース・騎手・枠・条件だけの掛け合わせで絶対軸・軸候補・消し候補になるパターンを出す。

- **定義**（`shisu/families.py`）: `TAILS` に `NONE`（ラベル「条件のみ」）。キーは部品の値だけを `×` でつなぐ（末尾に指数の部品を付けない。部品が1つでも空なら作らない）。`uses_schemes` は帯にする条件（前走着順・前走人気・前走上り3F順）を含むときだけ True（s1・s2）。`Family.no_index`。初期セットには入れていない（`selected_families.json` に書くか、screen_families の `--tails … ,NONE` で選ぶ）
  ```json
  {"name": "N_jk_r5", "parts": ["騎手", "近5走同条件"], "tail": "NONE"}
  ```
- **指数の名前**: 成績表・DB・照合結果の `indicator`（照合結果の `base`）は固定の「条件のみ」（`indicators.CONDITION_ONLY`。指数の台帳には入れない）
- **重複して集計しない**: 指数に依らないので、地域ごとの基準の master 1本（`naming.base_masters`: タイムM を優先、無ければ 対戦M、それも無ければその地域の名前順の先頭）だけで、その master の全部の行（`指数有効` に関係なく）を集計する（§3.2）。採点（§3.8）・逆引き（§3.10）も同じ master
- **照合**: 出馬表1頭につき、指数を使う family は指数ごと、条件のみの family は base = 「条件のみ」で1回（`matching.iter_hit_rows`・`match_bases`。§3.4）
- **指数の上限ゲートに掛けない**: 条件のみのキーの末尾は指数の部品ではない（単独の 距離「1600」・COURSE「東京×芝×1600」のように数字だけで終わることがある）。`is_low_tail(key, indicator)` は indicator が「条件のみ」なら常に False。1行版（`rec["indicator"]`）・ベクトル版（列 `indicator`）・SQL 版（`indicator_col`）・コンパクト化（`compact_mask(…, indicator)`）・採点（screen）のどれも同じ見方（§6.1）
- 希少条件の最低件数の緩和・高回収率の基準・消し候補の規則は、指数つきの行と同じ

### 3.15 SP指数・ST指数の3着内基準値（2026-11-18〜。`shisu/baselines.py`）

「この条件のレースで3着に入る馬の SP指数・ST指数はどのくらいか」を、条件のセルごとの1つの整数（`index_mean`）で持つマスタ。`shisu/data/index_baseline_SP.csv`・`index_baseline_ST.csv`（§4.11）。
受け取った2つの CSV（各 467 セル）がリポジトリに入っている。**生成のスクリプト（build_index_baseline.py）そのものは受け取っていない**ので、依頼の仕様と、受け取った CSV の全行が満たす関係（下の ★）から同じ計算を組んだ。

- **対象の標本**（`samples_from_history`。馬ごとの過去走の履歴 §3.11 から）: 着順が 3、その指数の値があり 0 でない、平地（障害は履歴に無い）、クラスが下の13区分（**新馬・G1 は入れない**。クラス・世代が分からない走も入れない）、芝・ダ、馬場状態が分かる、場所が JRA の10場、距離が分かる
- **セルの部品**:
  - 芝ダ: `芝` / `ダ`
  - クラス（13区分。`race_class(クラス名, 年齢限定, レース名)`）: `2歳未勝利`・`3歳未勝利`／`2歳1勝`・`3歳1勝`・`古馬1勝`／`2勝`／`3勝`／`2歳OP`・`3歳OP`・`古馬OP`／`2歳重賞`・`3歳重賞`・`古馬重賞`
    - クラス名の読み方（NFKC のあと）: 未勝利／1勝・500万／2勝・1000万／3勝・1600万／オープン・OP・OP(L)・L／G2・G3・重賞（GII・GIII も）。新馬 → `新馬`、G1・GI → `{世代}G1`（どちらも集計しない）。障害（J・障）・読めない値は空
    - 世代（`generation`）: 年齢限定（年限定）が `2歳` → 2歳、`3歳` → 3歳、`3上`・`3歳以上`・`4上`・`4歳以上` → 古馬。列が空・読めないときは、クラス名・レース名の「2歳」「3歳」（「以上・上」が続けば古馬）から。2勝・3勝は世代を見ない。未勝利で古馬・1勝やオープンや重賞で世代が分からないときは空
    - 引くとき（`lookup_class`）: G1 → 同じ世代の `…重賞`、新馬 → 基準値なし
  - 馬場区分（`track_of`）: 芝は `良` / `良以外`（稍重・重・不良）、ダは `良稍`（良・稍重）/ `重不良`（重・不良）。馬場が分からないとき、**生成では使わない**（その走を標本にしない）・**引くときは 芝 = 良、ダ = 良稍** として引く。出馬表の想定の「稍・重」はダでは `良稍`
  - 場所区分（`place_group`）: `主場`（東京・中山・京都・阪神・中京）/ `ローカル`（函館・札幌・福島・新潟・小倉）
  - 距離帯（`dist_band`。統合前の距離）: 芝 〜1400 `芝短距離`・1401〜1800 `芝マイル`・1801〜2400 `芝中距離`・2401〜 `芝長距離`／ダ 〜1300 `ダ短距離`・1301〜1600 `ダマイル`・1601〜2000 `ダ中距離`・2001〜 `ダ長距離`（§5.2 の 22条件の `距離帯` とは別の区切り）
- **階層**: L3 = 芝ダ × クラス × 馬場区分（親 = 全体平均）、L2 = L3 × 距離帯（親 = L3）、L1 = 芝ダ × クラス × 馬場区分 × 場所区分 × 距離帯（親 = L2）。キーは ` × `（前後に半角の空白）でつなぐ（例 `芝 × 古馬1勝 × 良 × 主場 × 芝マイル`）
- **セルの値**（`build_rows(samples, decay)`・`_cell_stat`・`_shrink`）:
  1. **年度の重み**: `w = decay ** (最新年 − その走の年)`（最新年 = 標本の中の最新の年。`DEFAULT_DECAY` = 0.65 → 当年 1.0・1年前 0.65・2年前 0.42・3年前 0.27…。`decay = 1.0` なら全部 1 で、重みなしの計算と同じ結果）
  2. **上位1/3カット**: セルの値を小さい順に並べ、上から `n // 3` 件を除く（★ 受け取った CSV は全行 `n_cut == n // 3`）。除く件数は重みに依らない。切れ目に同じ値がまたがるときは、重みの小さい（古い）走から除く（並べ方 = (値, −重み)）
  3. `index_raw` = 残した値の重みつき平均を `round()`（★ Python の round = 偶数丸め。四捨五入では受け取った CSV と合わない行がある）。平均・縮約は分数（`fractions.Fraction`。値と decay は書かれたとおりの10進の値）で計算してから丸める（float で重みを掛けると、ちょうど .5 の平均が 43.49999999999999 のようにずれ、同じ年の標本だけのセルの丸めが、ほかのセルに新しい年の標本があるかどうかで変わるため。`_wmean`・`_shrink`）
  4. **縮約**: `index_mean = round((n × index_raw + 30 × 親の index_mean) / (n + 30))`（`K_SHRINK` = 30。n はカット前の件数）。全体平均 → L3 → L2 → L1 の順（★ 受け取った CSV の 467 × 2 行すべてがこの式どおり）
  5. 全体平均（root）= 全部の標本のカットなしの重みつき平均を round（★ 受け取った CSV では SP 62・ST 48）
  6. 信頼度 `reliability`: n ≥ 100 `高`・30 ≤ n < 100 `中`・n < 30 `低`
  - そのほかの列（参照用。引くときは使わない）: `index_mean_全体`（カットなしの重みつき平均の round）・`n_kept`・`n_cut`・`index_std`（残した値の母標準偏差。重みなし・小数2桁）・`p25`・`median`・`p75`（並べた全部の値の `int(n × 0.25 / 0.5 / 0.75)` 番目。重みなし）・`parent`
- **引き方**（`Baseline.lookup(芝ダ, クラス, 馬場区分, 場所区分, 距離帯)`）: L1 → L2 → L3 の順に、最初にあるセルの `index_mean`。場所区分・距離帯が分からなければ、その段を飛ばす。どれも無ければ None（基準値なし）
- **更新**（`update_from_history(frame, directory, decay, min_samples, kinds, log)`）:
  - replace が自動で（§3.1 の9）、または `python -m shisu.baselines --update [--decay D] [--min-samples N] [--kinds SP,ST] [--csv-dir …] [--result-dir …] [--master-dir …] [--out-dir …]`（履歴を作り直してから更新する。引数なしの `python -m shisu.baselines` は今のマスタのセル数を出すだけ）
  - **標本が `MIN_SAMPLES`（2,000）件に満たない種類は書き換えない**（数か月ぶんの結果指数しか無い場所・試しのデータで、10年ぶんから作ったマスタを置き換えないため。`[INFO] 基準値（SP）: 3着馬のサンプルが n 件で、2000 件に満たないので更新しません`）。下げるには `--min-samples`／replace の `--baseline-min-samples`
  - 中身が同じなら書かない（`（変更なし）`）。書くときは `.part` に書いてから置き換える。ログは `[INFO] 基準値（SP）: 3着馬 n 件（最新年 Y・decay 0.65・全体平均 r）→ k セル を … に書きました`
  - CLI の終了コード: 0 = 更新した・変更なし（片方だけ更新したときも）、1 = どの種類も更新しなかった（標本不足）・基本CSVが無い・履歴を作れない、2 = 引数の誤り（`--kinds`・`--decay` は 0 より大きく 1 以下）
- **master_csv/ への写し**（`snapshot`・`load_for_master`。§4.11）: replace は、master を作るときの基準値マスタを `master_csv/__index_baseline_SP__.csv`・`_ST__.csv` に写し、記録 `__index_baseline__.json` を書く。出馬表側（build_db）は、記録があれば写しだけを読む（基準値マスタだけをあとから更新しても、master と出馬表で違う基準値を見ない。写しの無い・読めない種類は付けない）。記録が無ければ（前の版の master・master_csv の無い場所）基準値マスタを読む
- 置き場所は `config.baseline_dir()`（既定 `shisu/data/`。`SHISU_BASELINE_DIR`）。テストは一時フォルダの写しを使う（`tests/conftest.py`）

### 3.16 基準比の条件と 過去SP/ST 3走順（2026-11-18〜。`baselines.conditions`・`shisu/conditions.py`）

過去側（replace）と出馬表側（build_db）が同じ `baselines.conditions(parts, 前走RID16, 履歴, 基準値, 値, 順位, 芝の想定, ダの想定)` を通り、結果を `derive(…, perf=)` に渡す（`BASELINE_CONDITIONS` の6列。分からなければ空 → その部品を含むキーは作らない）。

- **`前走SP基準比`・`前走ST基準比`**: d = 前走のその馬の結果指数 − 前走の条件の基準値
  - SP: d ≥ +3 `前走SP基準突破`／0 ≤ d < +3 `前走SP基準クリア`／−6 < d < 0 `前走SP基準未達`／d ≤ −6 `前走SP基準大幅未達`（整数どうしなら +3以上／0〜+2／−5〜−1／−6以下）
  - ST: d ≥ +2 `前走ST基準突破`／0 ≤ d < +2 `前走ST基準クリア`／−4 < d < 0 `前走ST基準未達`／d ≤ −4 `前走ST基準大幅未達`（`PREV_RULES`）
  - 前走の走 = 履歴（§3.11）の中の、その馬（血統登録番号 → 馬名。§3.11 の見分け方）の、レースID(新) の先頭16桁が `前走RID16` と同じ走（`History.run_at`）。その走の 芝ダ・クラス・馬場状態（実際の馬場。4区分 `馬場4`）・場所・距離で基準値を引く
  - 空になるとき: 前走が無い・`前走RID16` が作れない、その走が履歴に無い（基本CSVの期間の外・地方・海外・障害・取消）、結果指数が無い・0、前走が新馬戦・クラスの分からないレース（基準値なし）
- **`過去SP今走基準比`・`過去ST今走基準比`**: B = 今走の条件の基準値。過去SP・ST の8指数（§3.11）の値で
  - `過去SP_3走平均 ≥ B` または `過去SP_5走平均 ≥ B` → `SP平均基準超`／そうでなく `過去SP_3走最高 ≥ B` → `SP3走最高基準超`／そうでなく `過去SP_5走最高 ≥ B` → `SP5走最高基準超`／値が1つでもあってどれも B 未満 → `SP今走基準未達`（ST も同じ形。`now_label`）
  - 空になるとき: 4つの値が全部無い（対象の走なし。値 0・空）、今走が新馬戦・クラスや世代の分からないレース・基準値のセルが無い
  - 今走の馬場区分: 過去側は基本CSVの `馬場状態`（実際の馬場）。出馬表側は想定（build_db の `--baba-turf`／`--baba-dirt` → calc が使った想定 `entries_out/past_perf_baba.json` → `past_perf_settings.json` → `auto`）。`auto` は出馬表の `馬場状態`（`馬場`）の列、無ければ 良（芝 = `良`・ダ = `良稍`）
- **`過去SP3走順`・`過去ST3走順`**: 指数 `過去SP_3走平均`・`過去ST_3走平均` のレース内順位（1以上の整数の文字。その指数の master の `順位`・出馬表の `過去SP_3走平均_順位` と同じ値）。値の無い馬・その指数を使わないレース（欠損2頭以上 など。§5.5）は空
  - `BANDED_CONDITIONS` に登録（単位 `sp3`・`st3`）: キーでは帯の名前になる。s1 = `SP3走1位`／`SP3走2~3位`／`SP3走4~5位`／`SP3走6~9位`／`SP3走10位~`、s2 = `SP3走1~3位`／`SP3走1~5位`／`SP3走6位~`（ST は `ST3走…`）。これらを含む family は、末尾が偏差値帯・指数順位・条件のみでも s1・s2 の2つの成績表になる（§5.1）
  - 3走最高・5走平均・5走最高の順位は条件にしていない（部品の数を増やさないため。末尾の指数として使えば帯になる）
- **過去側**（§3.1 の9）: 値は `_perf_values`（基本CSV 1本につき1回の計算）、順位は `perf_rank`。**出馬表側**（§3.4）: 値・順位は calc_entry_hensachi が付けた列から。同じ馬・同じレース・同じ馬場区分なら両側で同じ値（`tests/test_baselines.py`）
- **その指数自身の順位の同義を除く**（`families.is_self_redundant(family, 指数)`）: 部品に `過去SP3走順` を持つ family × 指数 `過去SP_3走平均`（ST も）は、末尾の順位帯と部品の帯が同じ順位から作られる（`…×SP3走1~3位×1~3位` だけが埋まり、ほかの組は空）ので、aggregate・screen_families はその master では作らない・採点しない。ほかの指数（オメガ指数・タイムM・`過去SP_5走平均` など）の master では作る → `ST平均基準超×ST3走1~5位×1~5位`（末尾はその指数の順位帯）。条件のみの family（tail=NONE）は対象外
- 初期セット（§5.3）には入れていない。使うには `selected_families.json` に書くか、screen_families で選ぶ（§3.8 の因果トリオ⑤・ペア・単一条件）:
  ```json
  {"families": [{"name": "X_stb_st3", "parts": ["過去ST今走基準比", "過去ST3走順"], "tail": "RBAND"},
                {"name": "X_pspb_spb_sp3", "parts": ["前走SP基準比", "過去SP今走基準比", "過去SP3走順"], "tail": "RBAND", "schemes": ["s2"]}]}
  ```

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
| A 馬単位・新18桁 | `レースID(新), 指数値`（見出しは有っても無くても良い） | オメガ指数（`OMEGA/`・`*_omega.csv`）、かよちん指数（`kayochin/`）、keibaAI指数（`keibaAI/`）、吉馬6指数（`kichiuma_sp_ability/`・`kichiuma-sp_ability/`・`kichiuma/kichiuma_sp_ability/` など）、安井8指数（`yasui_rank/` など。`/ingest` の出力をそのまま）、過去SP・ST の8指数（`past_sp_st/past_sp_avg3/` など。名前に `past_{sp,st}_{avg,max}{3,5}`。/ingest の取得元 past の出力。結果指数があれば、計算した値のほうを使う。§3.11） | レースID(新)。順位は値から計算 |
| B 馬単位・旧10桁 | `旧ID, 指数値, 順位`（見出しなし。例 `0520380101,54,6`） | `StridePlus/`（大小文字・`Stride_Plus` も可）の下でファイル名の接頭辞: `sav` SAV / `st` ST指数（`st_se` は成績ST指数＝廃止）/ `gokei` 合計値 / `tenkai` 展開 / `siagari` 仕上り / `senko` 先行 / `tsuiso` 追走 / `jikyu` 持久 / `jizoku` 持続 / `shunpatsu` 瞬発 / `mae_stime` 想定前半 / `ato_stime` 想定後半 | 旧10桁。順位はファイルの値 |
| C レース単位・12桁 | `ID12, 指数1, 順位1, 指数2, 順位2, …`（馬番1から。例 `202609260601,64,1,13,13,…`） | Gold（`GoldAirplane/`・`gold*.csv`） | 12桁（YYYYMMDD+場2+R2）＋馬番。順位はファイルの値 |
| D レース単位・旧8桁 | `ID8, 指数1, 指数2, …`（馬番1から値だけ） | BC（`BCindex/`・`BC*.csv`。先頭の `RX` を除く）、ZI（`ZI_DATA/`・`ZI*.CSV`） | 旧8桁＋馬番。順位は値から計算 |

- ID: 旧10桁 = 場所2 + 年下2桁 + 回1 + 日1（10日=A, 11日=B, 12日=C）+ R2 + 馬番2。先頭の0が落ちて9桁（旧8桁なら7桁）になったものは0を補う。ベースCSVに レースID(旧) が無い・空なら レースID(新) から作る
- 文字コード: BOM があれば utf-8-sig、無ければ utf-8（先頭64KB で判定）→ cp932
- 同じ指数のファイルが複数あれば相対パスの順に読み、後のファイルの値を優先（空の値で上書きしない）。上書きの件数をログに出す
- ファイルに値の無い馬は 0 として扱う（§3.1。TARGET と同じ）。ファイルに無い馬の順位はレースの最下位・同順位（形式・順位を値から作るかどうか・指数の向きに関係なく。compute_indicator の last）
- 形式B は3列目（順位）が要る。先頭50行のどの行にも3列目の欄が無い（2列だけの）ファイルは使わない（使わないファイルの WARN に出る）。欄はあって空なら、そのレースは値から順位を作る（レースごと。同じ指数の他のファイルに順位があっても。replace が WARN を出す）
- 0 を欠損とする指数（想定前半・想定後半・安井のランク・5ステータス）では、ファイルに無い馬の 0 は欠損になる（順位なし。1レースに2頭以上ならそのレースは使わない）
- **ボーダーのファイル（2026-11-12〜）**: 名前（ファイル名・フォルダ名）に `border` の付くもの（StridePlus の `st_border*.csv`・`siagari_border*.csv` など。`_is_border`）は、基準値との差で指数の値ではないので読まない（旧版は接頭辞 `st`・`siagari` で ST指数・仕上り と見なし、本物の値を上書きしていた）。`[INFO] ボーダーのファイル n本は使いません` を1行出す（WARN にはしない）
- 使わないファイル: `*_merged.csv`・`yasui_extract.csv`・`*_noid.csv`・CSV でないもの・`entries_in/` の下、廃止した指数（カツラキ・HPRTV5C・成績ST指数（`st_se`）・一隊・33rap・上がり1F/2F。種類ごとに1行ログ）、どの指数か分からないもの・形が合わないもの（先頭5本を WARN）。フォルダ名とファイル名で指数が食い違うものは使わない
- 形式Aの見分け: 1行目が18桁の数字か `レースID(新)` の見出しで、列が2つ以下（`日付,場所,…` の安井の「ベースCSVが無い日」の出力は使わない）

### 4.3 agg_csv（成績表、1行=1結合データ）

- `agg_master_{地域}_{指数名}.csv.gz`（2026-11-10〜。master 1本につき1ファイル・cp932・gzip）: 下の表の列 ＋ `family, odds_mode, rank_scheme, indicator, region, テーブル名`。1つの成績表（family × スキーム × オッズ帯）の行は続けて並び、中の並びは結合データの昇順。テーブル名 = 旧来のファイル名（拡張子なし）を SQLite 用に安全化したもの
- `agg_master_{地域}_条件のみ.csv.gz`（2026-11-17〜）: 指数を使わない family（tail=NONE）の成績表。同じ形で、`indicator` 列は「条件のみ」、テーブル名は `foragg_{オッズ接頭辞}{rankband_スキーム_}{family}_{地域}_条件のみ`。印（`.done`）の master は基準の master（§3.14）
- 旧来の `foragg_{オッズ接頭辞}{rankband_スキーム_}{family}_{地域}_{指数名}.csv`（2026-11-09 まで。1表1ファイル）も build_db が読める。`shisu.aggstore.read_agg_tables` は両方を旧来の1表1ファイルの形（{ファイル名: 成績表}）に分けて返す（確認・テスト用）
- `agg_master_{地域}_{指数名}.csv.gz.done`: 書き終わりの印（JSON: settings（設定のハッシュ）, master, master_size, master_fingerprint（gzip の末尾8バイト。gz でなければ sha256）, master_path, master_mtime_ns（確認用。比べない）, rows, bytes, odds_breaks（2026-11-14〜。集計したときの `ODDS_BREAKS`。build_db が DB に残す））。`--resume` と build_db の確認が見る
- `agg_settings.json`: 集計の設定（family の定義・スキーム・WEDGE・推定・コンパクト化・区分の閾値）。`--resume` のとき、前回と違えば項目をログに出す

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

`レースID(新), レースID(新/馬番無), 日付, year, 地域, 指数名, 場所, 芝・ダ, 距離_raw, 距離, 芝(内・外), 重量種別, クラス名, 頭数, 枠番, 枠番グループ, 馬番, 馬名, 騎手, 指数, 指数順位_元, 順位, 偏差値, 偏差値帯, 順位帯_s1, 順位帯_s2, 指数有効(0/1), 着順, 単勝オッズ, オッズ帯, 単勝配当, 複勝配当` ＋ 条件の36列（22条件の24列・外部レース印の前走条件4列・近5走の要約2列・基準比と過去SP/ST 3走順の6列。§5.2）
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

### 4.8 condition_notes.json（危険条件メモ。`shisu/notes.py`）

プロジェクト直下（`config.notes_path()`。環境変数 `NOTES_PATH` で変えられる）。`.gitignore`・`.dockerignore` に入れてある。racing.db の外なので、`build_db --fresh` でも消えない。

```json
{"version": 1, "notes": [{"indicator": "SAV", "family": "normal", "rank_scheme": "", "odds_mode": "odds10u", "region": "全場",
                          "key": "東京×ダ×1600×定量×1・2_勝×1", "note": "⚠️馬体重478kg以下は危険", "updated": "2026-11-16T10:00:00"}]}
```

- 1つのメモは (indicator, family, rank_scheme, odds_mode, region, key) に付く。region が空のメモは全地域共通。odds_mode の空は normal。前後の空白は除く
- 本文は 2,000 文字まで（`MAX_NOTE`）、条件の1項目は 500 文字まで（`MAX_FIELD`）。改行は `\n` にそろえる
- 書くときは同じフォルダの一時ファイル（`condition_notes.json.{乱数}.tmp`）に書いて fsync してから置き換える（途中で止まっても前のファイルか新しいファイルのどちらかが残る）。権限は、前のファイルがあればそのまま、無ければ umask どおり（ふつう 0644。`mkstemp` の 0600 にしない: Docker の root で書いたメモを、Linux のホスト側で読める・コピーできるように）。プロセスの中ではロックして読み→直し→書く。読むのはファイルの更新時刻・大きさが変わったときだけ
- 読めない形（壊れた JSON・`notes` が配列でない・片方だけのサロゲート `"\ud83d"` のように UTF-8 にできない文字 など）は `NotesError`。画面はメモなしで出して理由を表示し、保存は上書きしない。手で直すなら上の形（`notes` の配列だけの JSON でも読める）

### 4.9 result_indices/・履歴・想定馬場状態の設定（過去SP・ST の8指数。`shisu/past_perf_indices.py`）

- **`result_indices/`**（`config.result_indices_dir()`。`RESULT_INDICES_DIR`。`.gitignore`・`.dockerignore` に入れてある）: 下を再帰的に探す（`find_result_files`。名前の大文字小文字は問わない。`.` で始まるファイル・`__MACOSX` は見ない）
  - SP指数: 名前が `SP` で始まる CSV（`SP01260101.csv`）／ `speed*.zip` の中の、名前が `SP` で始まる CSV
  - ST指数: 名前が `st_se` で始まる CSV（`st_se_20261010.csv`）／ `st_se*.zip` の中の CSV
  - 中身（`parse_result_text`）: 見出しなしの2列 `レースID(新) 18桁, 指数値`。負の値・0・小数も値（`Decimal`）。値が空の行は入れない。1行目の見出しは読み飛ばす。ID が18桁の数字でない行・値が数でない行・整数部が7桁以上（100万以上。ID が2つつながった行・`1e30` など）や無限大の値は飛ばして、件数と例を WARN に出す。3列目以降は見ない。文字コードは BOM → UTF-8 → cp932
  - 同じ馬（レースID(新)）の値が複数あれば、後のファイル（相対パスの名前順。ZIP の中は名前順）の値。ZIP は中身を読むだけで展開しない。読めない ZIP は WARN を出して飛ばす
  - ext_indices では `st_se` で始まる名前は「成績ST指数（廃止）」として読まない（§4.2）。結果指数は別のフォルダ `result_indices/` に置く
- **`master_csv/__horse_perf_history__.csv.gz`**（**UTF-8**・gzip。内部のファイルなので cp932 にしない: cp932 で書けない文字のある馬名を落とすと馬名で引けなくなる）: `馬ID, 馬名, 日付, レースID(新), 場所, 芝・ダ, 距離, 馬場, 着順, SP, ST, クラス, 馬場4`（1行 = 1走。2026-11-17 の2件目から、結果指数の無い走も入る。着順は確定着順の数・失格/中止は頭数・不明は空。SP・ST は結果ファイルの値そのまま・無ければ空。2026-11-18 から `クラス`（3着内基準値のクラス。§3.15 の13区分・`新馬`・`{世代}G1`・空）と `馬場4`（良／稍／重／不／空。`馬場` は 過去SP・ST 用の3区分のまま））。**`__horse_perf_history__.json`**: `{"version": 4, "signature": 元の印, "bases": 基本CSVだけの印, "rows": 行数}`（`bases` の無い記録は、基本CSVが変わったものとして扱う）（版 3 までの履歴は使わず作り直す。版3の履歴の結果指数の値は、結果指数のファイルが無いときに引き継ぐ。§3.11）。一時ファイルから置き換える。版・列が違う・読めないファイルは使わない。行数が印と違う（途中で切れた）ファイルは、元と違うものとして作り直す
- **`past_perf_settings.json`**（`config.past_perf_settings_path()`。`PAST_PERF_SETTINGS`）: `{"baba_turf": "auto|良|稍・重|不", "baba_dirt": …}`。/ingest の取得元 past が成功したときに書く。読めない・知らない値は `auto`
### 4.10 race_marks/・`__race_marks__.csv.gz`（外部レース印。`shisu/race_marks.py`）

- **`race_marks/`**（`config.race_marks_dir()`。`RACE_MARKS_DIR`。`.gitignore`・`.dockerignore` に入れてある）: 下を再帰的に探す（`.` で始まるファイル・`__MACOSX` は見ない。ZIP は名前に関係なく中の CSV を見る。読めない ZIP は WARN）。名前（大文字小文字は問わない）の先頭で種類を決める:

  | 名前 | 種類 | 値 |
  |---|---|---|
  | `member*.csv` | メンバーレベル（レースレベル） | A〜E（`－` は値なし） |
  | `time*.csv` | タイムレベル | S・A〜E |
  | `pace*.csv`（`pace_decision*.csv`） | ペース判定 | HHH・HH・H・M・S・SS・SSS |
  | `cushion*.csv` | クッション値 | 数（ダートなどは `----`） |
  | `moistureG*.csv`（`moisture` の次が英字でない `moisture_2026.csv`・`moisture2026.csv` も） | ゴール前含水率 | 数。同じレースの1行目 = 芝・2行目 = ダート |

  `moistureC*.csv` など（`moisture` の次が G 以外の英字 = 4コーナーの含水率）は使わない
- 中身: 見出しなしの3列 `"開催名", "RX" + 旧8桁, "カラーコード2桁 + 値"`（クォートは有っても無くてもよい。BOM → UTF-8 → cp932）。例:
  `"2026年1回中山1日目01R",RX06261101,"05HHH "`／`"2026年1回中山1日目05R","RX06261105","00 9.4"`／`"2026年1回中山1日目03R",RX06261103,"09 － "`／
  `"2026年1回中山1日目01R","RX06261101","0012.5"`（芝）＋ `"2026年1回中山1日目01R","RX06261101","0D 7.1"`（ダート）
  - 2列目が `RX` + 数字5桁 + 英数字1文字 + 数字2桁 でない行は飛ばして、件数と例を WARN に出す（1行目の見出しは数えない。小文字の `rx` は読む）
  - 値: 先頭2文字が16進（`[0-9A-Fa-f]{2}`）なら必ずカラーコードとして除く → NFKC → 前後の空白を除く。ハイフン・ダッシュだけの値は値なし
  - 含水率: 1つのファイルの中で同じ RX の1行目 = 芝・2行目 = ダート（続いていなくてもよい）。3行目からは使わない（WARN）。1行だけなら芝だけ
  - 同じレースが複数のファイルにあれば、相対パスの名前順で後のファイルの値（値なしでは上書きしない。含水率は芝・ダの組ごと入れ替え、2行とも値なしなら前の値を残す）
- **`master_csv/__race_marks__.csv.gz`**（UTF-8・gzip・時刻なし・一時ファイルから置き換え）: `RX, member, time, pace, cushion, moist_turf, moist_dirt`（1行 = 印が1つでもあるレース。値は前処理のあとの文字。区分にはしていない）。replace が race_marks/ から書く（印が無い・`--no-race-marks` なら消す）。出馬表側は race_marks/ にファイルが無いときに読む
- **`master_csv/__race_marks__.json`**（replace の読み方の記録。一時ファイルから置き換え）: `{"version": 1, "enabled": true|false, "dir": ""|フォルダ}`。`enabled` は `--no-race-marks` で false。`dir` は、読んだフォルダが既定（`config.race_marks_dir()`）と同じなら空（出馬表側も、その場所の既定のフォルダを読む）、違えば絶対パス。replace が毎回書く。無い・読めない・形が違うときは、記録なし（既定のフォルダを読む）として扱う
- 読む順は相対パス（`/` 区切り）の名前順（`a/member.csv` → `member.csv`。大文字は小文字より前）。結果指数（§4.9）も同じ（2026-11-17 の2件目から。それまでは「上のフォルダのファイル → サブフォルダ」の順だった）。読めないファイル・ZIP の中身は、その1本だけ WARN を出して飛ばす

### 4.11 index_baseline_SP.csv・index_baseline_ST.csv（3着内基準値。`shisu/baselines.py`）

- **`shisu/data/index_baseline_SP.csv`・`index_baseline_ST.csv`**（`config.baseline_dir()`。`SHISU_BASELINE_DIR`。リポジトリに入っている。**UTF-8（BOM なし）・改行 CRLF**・見出しあり14列）:

  | 列 | 内容 |
  |---|---|
  | `level` | `L3` / `L2` / `L1`（この順に並ぶ。その中は 芝 → ダ・クラスの順（`CLASSES`）・馬場区分・場所区分・距離帯） |
  | `key` | セル。部品を ` × ` でつなぐ（`芝 × 古馬1勝 × 良`／`芝 × 古馬1勝 × 良 × 芝マイル`／`芝 × 古馬1勝 × 良 × 主場 × 芝マイル`） |
  | `n` | 標本の件数（カット前） |
  | `index_mean` | **基準値**（縮約のあと。引くのはこの列だけ） |
  | `reliability` | `高`（n ≥ 100）/ `中`（30〜99）/ `低`（〜29） |
  | `index_raw` | 上位1/3を除いた（重みつき）平均の round |
  | `index_mean_全体` | カットなしの（重みつき）平均の round |
  | `n_kept`, `n_cut` | 残した件数・除いた件数（`n // 3`） |
  | `index_std` | 残した値の母標準偏差（小数2桁） |
  | `p25`, `median`, `p75` | 全部の値の分位（`57.0` の形） |
  | `parent` | 縮約の親のセルの key（L3 は `（全体平均）`） |

  - 読むとき（`parse_csv`）は `level`・`key`・`index_mean` の3列だけを使う。key の `×` の前後の空白はそろえる。BOM つきも読める。`index_mean` が数でない・inf・nan の行は飛ばす。3列が無い・セルが1つも読めないファイルは使わない（`[WARN]`。その種類の基準比の条件は空）
  - replace をホストのフォルダで回す（`-v "$(pwd)":/app`）と、この2つのファイルが書き換わる（git の差分に出る。元に戻すなら `git checkout shisu/data/index_baseline_SP.csv shisu/data/index_baseline_ST.csv`、書き換えさせないなら replace の `--no-baseline-update`）
- **`master_csv/__index_baseline_SP__.csv`・`__index_baseline_ST__.csv`**: replace が master を作ったときの基準値マスタの写し（同じバイト列）。その種類の基準値マスタが無い・読めなければ写しも無い
- **`master_csv/__index_baseline__.json`**: `{"version": 1, "past_perf": true|false}`（`past_perf` = その実行で 過去SP・ST の8指数を作ったか。false なら master の 今走基準比・3走順 は空）。基準値を用意できなかった実行は `{"version": 1, "past_perf": false, "error": true}` で写しなし（出馬表側も付けない）。replace が毎回書く
### 4.6 DB（racing.db）

| テーブル | 内容 |
|---|---|
| `__tables_meta__` | table_name, source_file, kind(foragg/entries/xjoin), family, odds_mode, rank_scheme, indicator, region, n_rows。kind=foragg の table_name は unified_all の「テーブル名」（実テーブルは無い）。索引 kind |
| `unified_all` | 全成績表の縦結合（場所, 芝・ダ, 距離, 芝(内・外), 結合データ, 成績列, *_val（5つの率は推定値に置換）, WIN〜adjusted_WEDGE_最終年, family, odds_mode, rank_scheme, indicator, region, テーブル名）。索引 (結合データ, family, indicator)・テーブル名・WEDGE・adjusted_WEDGE |
| （`foragg_*`） | 2026-11-09 までの build_db が作った成績表1本=1テーブル。今は作らない（前の DB に残っていれば foragg の作り直しで消す。5,000本（`DROP_TABLE_LIMIT`）を超えるときは消さずに WARN。画面は使わない。消すには `--fresh`） |
| `__facets__` | 画面の選択肢（col, val） |
| `__families__` | family の定義（name, parts（JSON）, tail, label, layer, schemes）。aggregate の `agg_csv/families.json` から。照合・画面はこれを使う |
| `__build_info__` | 作り直しの印（key, value。2026-11-12〜）: `unified_stamp`（unified_all を入れ替えるたびに変わる）・`xjoin_of:{xjoin のテーブル名}`（その照合結果が何から作られたかの記録。JSON: unified・plain・bases。照合結果と同じトランザクションで書く）。`--mining-only` が一部の作り直しで済ませてよいかの判定に使う（§3.4）。`odds_breaks`（2026-11-14〜。unified_all の成績表を集計したときのオッズ帯の境目の JSON。分からない・混ざっているときは `null`。画面の注意書きが見る。§3.5） |
| `entries_*` | 出馬表（元の列 ＋ 正規化したキー部品 `場所/芝・ダ/距離/芝(内・外)/重量種別/クラス名/枠番グループ/騎手`、条件の36列（22条件・外部レース印の前走条件・近5走の要約・基準比と過去SP/ST 3走順）、`日付`、`レースID(新/馬番無)`、`距離_raw`、`クラス名_raw`） |
| `entries_all` | 全出馬表の主要列の縦結合（画面では使わない） |
| `entries_xjoin_*` | 照合結果: source_table, 日付, レースID(新), レースID(新/馬番無), 場 R, 馬番, 枠番, 枠番グループ, 騎手, 馬名, base(指数。条件のみの family は「条件のみ」), key, テーブル名, family, odds_mode, rank_scheme, indicator, region, 成績列（WEDGE 系を含む） |

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
- 末尾（tail）: `RANK`（指数順位そのもの）/ `RBAND`（指数順位帯。スキーム s1・s2 の2通りの集計表になる）/ `HBAND`（偏差値帯6種）/ `NONE`（条件のみ。末尾に何も付けない。2026-11-17〜。§3.14）
- 帯にする条件（前走着順・前走人気・前走上り3F順・過去SP3走順・過去ST3走順。`BANDED_CONDITIONS`）は、キーを作るときに帯の名前にする（`前走2~3着`・`SP3走1~5位` など）。帯のスキームは family のスキームと同じ。これらを含む family は、末尾が偏差値帯・指数順位でも s1・s2 の2つの集計表になる（`Family.uses_schemes`）
- s2 は帯が重なるので、1頭から2つ以上のキーができる（指数2位・前走2着なら `…×前走1~3着×1~3位`・`…×前走1~3着×1~5位`・`…×前走1~5着×1~3位`・`…×前走1~5着×1~5位`）。過去側（集計）も出馬表側（照合）も全部作る
- 出馬表側は 芝(内・外) が分かれば `[値, 空]`、分からなければ `[空, 内, 外, 内外]` を試す。RBAND の family は s1・s2 両方で試す
- 例: `川田将雅×同条件連対あり`（騎手×近5走同条件、tail=NONE）、`東京×ダ×1600×定量×1・2_勝×4`（normal）、`東京×ダ×1600×前走2~3着×4~5位`（D_prevfin, s1）、`髙杉吏麒×前走上り1~3位×1~5位`（B_jk_prevagari, s2）、`芝×芝マイル×R値帯の値×61~65`（C_sd_dist_r_hen）

### 5.2 部品の値（正規化と22条件）

- **文字**: すべて NFKC ＋ 空白除去（`Ｇ１→G1`、`Ｃ．ルメール→C.ルメール`、`ｵｰﾌﾟﾝ→オープン`）。書き出しは cp932（`髙` などを保持）
- **クラス名**: `500万/1000万/1勝/2勝→1・2_勝`、`1600万/3勝/オープン/OP/OP(L)/L→準OP・OP`、`G2/G3/重賞→重賞`、`G1/GI→G1`、未勝利・新馬はそのまま。`馬齢×(1・2_勝|準OP・OP)→1勝・OP`
- **重量種別**: 過去側は TARGET の列（`別定→定量`。値は 馬齢/定量/ハンデ）。出馬表側は `重量種別` 列があればそれ、無ければ推定:
  レース名のハンデ記号（`むらさＨ･3勝`・`目黒記念ＨG2`・`安土城Ｈ(L)`・`北陸ＳＨ･3勝`（ＳＨ・ＣＨ・ＴＨ）。`NHKマイルC`・`HTB賞` は対象外）→ハンデ、年限定に「上」→定量、2・3歳限定のオープン・リステッド（準OP・OP）→定量（TARGET では別定）、その他の2・3歳限定（新馬・未勝利・1勝・重賞・G1。菊花賞・秋華賞も TARGET は 馬齢）→馬齢、年限定が空→不明（重量種別を含むキーは作らない）。★6 サンプルの264レースで TARGET の 重量種別 と全件一致
- **距離**: `1600m`・`1600.0`・`ダ1600`→`1600`。レンジ統合（福島芝1700/1800→17-18、新潟芝 内1200/1400→12-14・外1600/1800→16-18・内2000/2200/2400→20-24・外3000/3200→30-32、中山ダ2400/2500→24-25、東京芝1600/1800→16-18・2300/2400→23-24・ダ1300/1400→13-14、中京ダ1800/1900→18-19、京都芝 内1400/1600→14-16・外1400/1600/1800→14-18・2000→2000×`内外`・外2200/2400→22-24・外3000/3200→30-32・ダ1800/1900→18-19、阪神芝1600/1800→16-18・2400/2600→24-26・3000/3200→30-32、小倉芝1700/1800→17-18）。コース属性・距離帯・距離増減は統合前の距離（`距離_raw`）で引く
- **芝(内・外)**: 過去側は TARGET の列。出馬表側は `外回り` 列が外→`外`、京都・新潟の芝でそれ以外→`内`、他は不明（空）
- **枠番グループ**: 10–13頭 枠1–5内/6–8外、14–16頭 1–4内/5–8外、17–18頭 1–3内/4–6中/7–8外、それ以外は空（両側共通）

**22条件 ＋ 外部レース印の前走条件4種 ＋ 近5走の要約2種 ＋ 基準比・過去SP/ST 3走順の6種**（`shisu/conditions.py` の `CONDITION_COLUMNS`。36列。列名 → 値。分からなければ空）:

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
| 前走R印3（補完） | 上の列が空の馬だけ、外部レース印のペース判定で埋める | race_marks の pace*.csv（前走RID16 で引く。§3.12） |
| 前走メンバーLv | `A` / `B` / `C` / `D` / `E` | race_marks の member*.csv |
| 前走タイムLv | `S` / `A` / `B` / `C` / `D` / `E` | race_marks の time*.csv |
| 前走クッション帯 | `軟(8.9以下)` / `標準(9.0~9.9)` / `硬(10.0以上)`（前走が芝のときだけ） | race_marks の cushion*.csv × 前走の芝・ダ |
| 前走含水率帯 | `芝乾(9.9以下)` / `芝標準(10.0~14.9)` / `芝湿(15.0以上)` / `ダ乾(4.9以下)` / `ダ標準(5.0~9.9)` / `ダ湿(10.0以上)` | race_marks の moistureG*.csv（1行目 = 芝・2行目 = ダート）× 前走の芝・ダ |
| 近5走同条件 | `同条件連対あり` / `同条件3着あり` / `同条件4着以下` | 馬ごとの過去走の履歴（§3.13） |
| 近5走同場 | `同場連対あり` / `同場3着あり` / `同場4着以下` | 馬ごとの過去走の履歴（§3.13） |
| 前走SP基準比 | `前走SP基準突破`（+3以上）/ `前走SP基準クリア`（0〜+2）/ `前走SP基準未達`（−5〜−1）/ `前走SP基準大幅未達`（−6以下） | 履歴の前走の SP指数 − 前走の条件の3着内基準値（§3.16） |
| 前走ST基準比 | `前走ST基準突破`（+2以上）/ `前走ST基準クリア`（0〜+1）/ `前走ST基準未達`（−3〜−1）/ `前走ST基準大幅未達`（−4以下） | 履歴の前走の ST指数 − 前走の条件の3着内基準値 |
| 過去SP今走基準比 | `SP平均基準超` / `SP3走最高基準超` / `SP5走最高基準超` / `SP今走基準未達` | 過去SP の4指数（3走平均・5走平均・3走最高・5走最高）と今走の条件の3着内基準値（§3.16） |
| 過去ST今走基準比 | `ST平均基準超` / `ST3走最高基準超` / `ST5走最高基準超` / `ST今走基準未達` | 過去ST の4指数と今走の条件の3着内基準値 |
| 過去SP3走順・過去ST3走順 | 1以上の整数（キーでは帯 `SP3走1位`・`SP3走2~3位`・`ST3走1~5位`・`ST3走6位~` など） | 指数 過去SP_3走平均・過去ST_3走平均 のレース内順位（master の `順位`／出馬表の `過去SP_3走平均_順位`） |

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
  tail は `RANK`・`RBAND`・`HBAND`・`NONE`（条件のみ。§3.14）。名前は英字で始まる英数字と `_`（61文字まで）。オッズ帯の接頭辞（`odds10u_` など）・`rankband_` で始まる名前、知らない部品・tail・同じ部品の重複、COURSE と 芝・ダ・場所・距離・芝(内・外) の同居、スキームを持たない family の `schemes` は読み込みで弾く。selected_families.json・DB の定義が正（初期セットと同じ名前を別の部品で使えば置き換わる）
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
| 過去SP_3走平均・過去SP_5走平均・過去SP_3走最高・過去SP_5走最高・過去ST_3走平均・過去ST_5走平均・過去ST_3走最高・過去ST_5走最高 | | 結果指数から計算（§3.11）。大きいほど良い・0=欠損（zero_missing）・順位は値から計算 |
| ほか | オメガ指数(Ω指数), かよちん指数, keibaAI指数, SAV, ST指数(ST), 合計値, 仕上り(仕上がり), 圧勝力 | |
| （廃止） | カツラキ, HPRTV5C, 成績ST指数, 結果分析スピード指数・一隊, 33rap, 上がり1F・上がり2F | 読まない |

照合は DB の `indicator` 列の**完全一致**（`先行` と `吉馬_先行力` は別物）。条件のみの family（tail=NONE）の行の `indicator` は「条件のみ」（`CONDITION_ONLY`。指数ではないので台帳には無い。calc の `--indicators`・build_db の `--indicators` には指定できない）。

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

aggregate が master を過去レースの**確定単勝オッズ**で絞って集計する: `normal`（なし）/ `odds10u`（**2.0以上 10.0未満**。表記「2〜10倍未満」。2026-11-13 までは 10倍未満）/ `odds10_50`（10以上50未満）/ `odds50p`（50倍以上）。境目は `ODDS_BREAKS = (2.0, 10.0, 50.0)`（odds10u の下限・odds10u と odds10_50 の境・odds10_50 と odds50p の境）。成績表のファイル名の接頭辞は `odds10u_` / `odds10_50_` / `odds50p_`。オッズ欠損の馬と、単勝 2.0倍未満（1.9倍以下）の馬は normal にだけ入る（1倍台の馬が入ると odds10u の勝率・複勝率が上振れするため）。
- モード名 `odds10u`・ファイル名の接頭辞・DB の odds_mode の値・引数 `cut_low_10u` は変えていない（前の DB・entries_xjoin_* と同じ名前のまま読める）
- **成績表に効くのは次に aggregate を回したとき**: `ODDS_BREAKS` は集計の設定のハッシュに入るので、`aggregate --resume` でも全部の master を作り直す（`agg_settings.json` の `odds.breaks` が `[10.0, 50.0]` → `[2.0, 10.0, 50.0]`）。aggregate は master の `単勝オッズ` を `odds_mask_numeric` で絞るので、replace（master）は作り直さなくてよい（master の `オッズ帯` 列は表示用で、集計は読まない。2.0倍未満の馬のこの列が空になるのは、次に replace を回したとき）
- それまでの DB（前の区切りで集計した成績表）は、表記だけ「2〜10倍未満」で中身に1倍台の馬が入っている。成績表の印（`odds_breaks`）→ DB（`__build_info__`）→ 画面の注意書きで知らせる（§3.4・§3.5）
- 区分の規則（§6.1 の odds10u の消し・高回収率の基準）と条件ヒット馬の画面（過剰人気カット・消し(2〜10倍未満)・消優勢）は、モード名で見ているので変わらない
画面の「オッズ帯」は「過去にそのオッズ帯だった馬の成績」で、当日の馬のオッズとは無関係に全馬へ表示される。

---

## 6. 評価指標

### 6.1 条件ヒット馬の分類（`shisu/classify.py`）

値: `fv`=複勝率_val（推定）, `rv`=連対率_val（推定）, `top5`=掲示板率_val（頻度）, `frc`=複勝回収値, `tra`=単勝適正回収値, `cnt`=件数。欠損は「満たさない」。
判定順 **消し → 絶対軸 → 軸候補 → 相手** で、最初に当てはまった区分を1つだけ付ける。

| 区分 | 条件 |
|---|---|
| 消し候補 | (cnt≥20 かつ fv<5 かつ top5<10) または (odds10u かつ cnt≥20 かつ fv<20) |
| 絶対軸 | cnt≥10 かつ rv≥80 かつ (frc≥100 または tra≥100)。※ 絶対軸・軸候補・相手候補は、指数が下位の条件（下の「指数の上限ゲート」）を除く |
| 軸候補 | cnt≥20（`AX_CNT`。2026-11-14〜。それまで 10）かつ [fv≥80&top5≥90 / rv≥60&top5≥90 / fv≥70&top5≥80&frc≥100 / rv≥50&top5≥80&frc≥100] のいずれか |
| 軸候補（高回収率。2026-11-12〜） | オッズ帯が odds10u・odds10_50 のときだけ（`HR_MODES`）: cnt≥30 かつ [fv≥35&top5≥50&(frc≥200 または tra≥200) / fv≥50&(frc≥150 または tra≥150)] のいずれか |
| 相手候補 | cnt≥30 かつ [fv≥60&top5≥70 / rv≥50&top5≥70 / fv≥33&frc≥150&top5≥50] のいずれか |

**希少条件の最低件数の緩和（2026-11-12〜）**: 結合データを `×` で分けた部品に `G1`・`重賞`・`準OP・OP`・`芝長`・`ダ長`（`RARE_TOKENS`）のどれかが**完全に一致**して入っている行（`is_rare_key`。`1勝・OP` や騎手名の一部は対象外）は、件数の下限を 絶対軸 10→6、軸候補 20→6、相手候補 30→15、消し候補 20→12、高回収率 30→15 にする（`RARE_*_CNT`）。率・回収値の条件は同じ。
**指数の上限ゲート（2026-11-16〜。`TAIL_*`）**: 結合データを `×` で分けた**末尾の部品**（指数の順位・順位帯・偏差値帯）が下位なら、絶対軸・軸候補・相手候補（`TAIL_GATED`）にしない（騎手や前走の条件が強くて率が高いだけで、指数そのものは低評価の行を除く）。**消し候補には制限をかけない**（判定順のとおり、先に消しを見てから）
- 下位（`is_low_tail`）: `6~9位`・`10位~`・`6位~`・`41~50`・`40以下`（`TAIL_LOW_TOKENS`）、または末尾が ASCII の数字だけ（指数順位そのもの）で 6 以上（`TAIL_LOW_RANK_MIN`。`06` も 6。桁数 → 文字列の順で比べるので長い数字でもあふれない）
- 上位（今までどおり判定する。`TAIL_OK_TOKENS`）: `1位`・`2~3位`・`4~5位`・`1~3位`・`1~5位`・`71以上`・`66~70`・`61~65`・`51~60`・指数順位 1〜5
- 結合データが無い（None・空）・末尾がどの形でもない（`0`・全角の数字・前後に空白・`前走6着~` など）ときは制限しない。見るのは末尾だけ（途中の部品が `6位~` の形でも関係ない）
- 1行版は `rec["key"]`（無ければ `rec["結合データ"]`）、ベクトル版は列 `key`（無ければ `結合データ`）、SQL 版は `low_tail_sql(key_col)`（結合データの前に `×` を付け、末尾が `×`+下位の部品か、`rtrim` で数字を落とすと `×` で終わって数字が 6 以上か。数の条件のあとに評価する）。3つの一致と、上位・下位の部品が `shisu/hensachi.py` の帯の名前と同じであることをテストで確認。説明の文（`description_lines`）は 絶対軸・軸候補・相手候補 に「指数が下位の条件は対象外」、消し候補に「絞らない」
- 値は `rule_settings()` に入る（集計の設定のハッシュが変わる。コンパクト化では、指数が下位で区分に入らなくなった行は WEDGE の下限・件数の下限・layer A のどれかでなければ残らない）
- **条件のみの family の行には掛けない（2026-11-17〜）**: 指数の名前が「条件のみ」（`CONDITION_ONLY`）の行は、末尾が指数の部品ではないので、末尾の文字に関係なくゲートを掛けない（単独の 距離・COURSE で終わるキーは `芝×1600`・`東京×芝×1600` のように数字だけの末尾になる）。`is_low_tail(key, indicator)`。1行版は `rec["indicator"]`、ベクトル版は列 `indicator`（あれば）、SQL 版は `sql_condition(…, indicator_col="indicator")` / `low_tail_sql(key_col, indicator_col)`。条件のみの行がある表（unified_all・照合結果）では indicator を必ず渡す（渡さないと、数字で終わる条件のみのキーが軸・相手から外れる）。`low_tail_axis_category`（条件特化ヒット）も 条件のみの行には None。3つの一致をテストで確認
**条件特化の行（2026-11-17〜。`low_tail_axis_category(rec, mode)`）**: 上のゲートで区分なしになった行のうち、数の基準は 絶対軸・軸候補 を満たす行のもとの区分（`"abs"`／`"axis"`／`None`）。`is_low_tail(key)` で、ゲートを外した判定（`_classify(rec, mode, gate=False)`。件数の下限・希少条件の緩和・高回収率の基準・消しを先に見る順は classify と同じ）が abs か axis のときだけ返す。末尾が下位でない行・消し候補になる行・相手候補の基準までの行は `None`。classify・classify_frame（既定の `gate=True`）・sql_condition の結果は変えていない。ベクトル版は `low_tail_axis_frame(df, mode)`（`classify_frame(df, mode, gate=False)` と末尾の判定から。1行版との一致をテストで確認）。条件ヒット馬の「条件特化ヒット」の表（§3.5）と、集計のコンパクト化（§3.2 の a。この行も残す）が使う。コンパクト化の残し方が変わるので、`rule_settings()` に `LOW_TAIL_AXIS_CATEGORIES` が入り、集計の設定のハッシュが変わる（次の `aggregate --resume` は全部の master を作り直す。§9 の41）
**軸候補の最低件数 10 → 20（2026-11-14〜。`AX_CNT`）**: 上の表の「軸候補」の4つの条件だけ。消し候補（`KS_CNT` 20）と同じ基準にそろえた（10〜19件の少ない標本で率だけ高い行を軸にしない）。絶対軸（10）・高回収率（30）・相手（30）・消し（20）・希少条件の下限はそのまま。件数 10〜19 で絶対軸の条件（rv≥80 かつ 回収値≥100）を満たす行は今までどおり絶対軸、満たさない行はどの区分にもならない（相手は30件から・高回収率も30件からなので、ほかの区分に落ちることは無い）。1行版・ベクトル版・SQL 版・説明の文（`description_lines`）は同じ定数を読む。
判定には結合データが要る: `classify(rec, mode)` は `rec["key"]`（無ければ `rec["結合データ"]`）、`classify_frame` は列 `key`（無ければ `結合データ`。1行版と同じ順。部品の一致は SQL 版と同じ「×で挟んで部分一致」で見る）、`sql_condition(category, mode, key_col="結合データ")` は列名を受ける（結合データが無ければ希少条件にならない＝通常の下限）。高回収率の基準は従来の軸候補の条件のあと・相手候補の前に見るので、相手候補の条件 `fv≥33&frc≥150&top5≥50` も満たす行は odds10u・odds10_50 では軸候補になる。

推定値は「軸は下限、消しは上限」で判定する設計（複勝率の推定は頻度20%以下なら上側90%点）。閾値は `classify.py` の定数で変える（定数の一覧 `rule_settings()` は aggregate の設定のハッシュに入るので、コンパクト化しているなら閾値を変えたあとの `aggregate --resume` は全部の master を作り直す。§1.5）。区分は画面を開くたびに DB の値から判定するので、DB にある行には新しい閾値がすぐ効く（コンパクト化で落ちた行は集計し直すまで無い。§9 の27）。`/` のプリセットも同じ定義の SQL を使う。WEDGE はこの分類には使っていない（表示と並べ替え・絞り込み用）。

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
- `tests/test_aggstore.py`（2026-11-10）: master ごとの1ファイルとメタ列・テーブル名、`classify_frame` と1行版の一致（境界値・欠損・4オッズ帯）、旧来のファイル名の速い解析が前の当て方と同じこと、**コンパクト化で残る行が仕様の条件から独立に計算した行と完全に一致**すること・DB の4区分と WEDGE≧0.70 の行が全部の行の DB と同じこと・layer A は全部残ること・下限のオプション、`--resume`（済んだ master を触らない・書きかけを消す・設定を変えた実行が途中で止まっても今の設定にそろう・途中で切れたファイルと作り直した master は作り直す・--clean と一緒なら止める）、全部の master が失敗したときの終了コードとメッセージ、下限の `none` と 0 以下を弾くこと、並列のまとまり（子プロセスを作り直しても1プロセスと同じバイト列）、build_db が旧来の1表1ファイルからでも同じ unified_all・__tables_meta__ を作ること・agg_master と重なる旧来のファイルを使わないこと・前の版の1表ずつのテーブルを消すこと（多すぎれば残して WARN）・読める成績表が無ければ DB を変えないこと・途中で切れたファイルを飛ばして [ERROR]・違う設定の混ざりの WARN、__facets__、**「集計表ごと」の画面が、前の版の DB（1表ずつのテーブルあり・古い中身のテーブルあり）でも新しい DB でも同じ HTML**になること。2回目の検証で再現した不具合の再発防止（master が読めないときに前の成績表を残さない・集計の間に master が作り直されたら印を書かない・区分の閾値が設定のハッシュに入る・先頭から壊れたファイルと gzip でないファイルを [ERROR] にし旧来のファイルで代用しない・旧来のファイルとの混ざりと master の作り直しの WARN・`--prune` が集計表の一覧を消さない）、3回目の検証の分（印の無い成績表だけでも WARN・0行で終わった master の旧来のファイルを使わない・更新時刻だけ変わった master は作り直さず、中身が変わったものは作り直す・オッズ帯の境目と EDGE の表が設定のハッシュに入る・印を書かなかった master も年度の窓のまとめに入る）
- `tests/test_screen_families.py` に選抜の新しい規則（基準B の絶対軸・基準A の adj 上位）と `--from-report`（採点し直さず同じ選抜になる・基準を変えると減る・基準が JSON に残る・読んだファイルに書き戻す・ファイル名なしの --from-report に --report を付けると --report を読む・レポートが無い/形が違うと終了コード2）・同じ値の並びに依らない選抜、`tests/test_pipeline.py` に `build_db --entries-only`（`--fresh`・`--mode foragg` と一緒なら弾く）と replace の `--workers 2`（まとまりごとに子プロセスを作り直しても1プロセスと同じ master・子プロセスが止められてもそのまとまりを [ERROR] にして残りを続ける）
- `tests/test_mining_only.py`（2026-11-12）: `--mining-only` の結果が全部の計算と同じファイルになること・ext_indices を読まないこと、別名・`--clean`・不正な指数名、`--indicators オメガ指数`（ext の1指数だけ）、全部の計算への切り替え（統合ファイルが無い・新しい馬・レースから馬が消えた）と入力に無いレースの行を残すこと、build_db が タイムM・対戦M の行だけを照合し直し、ほかの指数の行に触らず、全部作り直した xjoin と同じになること、**マイニング以外の照合に関わる値が変わったとき（騎手の乗り替わり・馬が消えた）・成績表を作り直したあと・途中で止まった実行のあと・記録の無い照合結果では全部の照合に切り替わり、通常の2コマンドと同じ DB になる**こと（止まった実行は前の照合結果と記録に戻る）、`partial_blocker` が指定した指数と馬体重の違いを無視し、ほかの指数・騎手・前走の条件・馬の増減を拾うこと、オプションの組み合わせ（空の `--indicators` を弾く）と xjoin が無いときの切り替え、画面（出馬表・条件ヒット馬・WEDGE 一覧の CSV）が全部作り直した DB と同じ中身になること、`process_df` が34指数でも PerformanceWarning を出さないこと
- `tests/test_views.py`（2026-11-12。DB を使わない関数単位）: 冗長な「通常」の行（件数の差3以内・同じ馬/指数/family/スキーム/地域/結合データ/区分のときだけ除く）、並び（2〜10倍未満 → 10〜50倍 → 50倍以上 → 通常）、表示上限でオッズ帯別を先に配ること、穴の軸の判定、指数クロス好走馬（軸の指数数・指数A・B）、出馬表の指数クロス（順位・偏差値の4つの基準）
- `tests/test_pipeline.py` の末尾（2026-11-12）: 条件ヒット馬のページ（冗長な通常を表と該当数から除く・`<tr class="hot">`・全部の表が `.scroll` の中・並び・指数クロスの絞り込み）、出馬表のページ（指数クロスの頭数を DB の値から独立に数えて比べる）、WEDGE 一覧のページ（`<details>` の折りたたみと選択中の数・「馬 × 指数」が絞り込みのすぐ下・列がそのまま）
- `tests/test_views.py`・`tests/test_pipeline.py` の末尾（2026-11-13）: 過剰人気カット（odds10u の軸候補・相手候補だけ・境界 100・値なし・絶対軸と消しとほかのオッズ帯は対象外・カットした行と重なる「通常」の行は回収値に関係なく出さない・行の順に依らない）、`danger_level`（依頼の式との一致）、`horse_stats`・`axis_cross`（odds10u の消しだけを数える・指数ごとの内訳・カットのあとの行数で判定）、ページ（既定 ON と除いた行数・ON/OFF のリンクとフォームの 0/1・リンクがほかの絞り込みを保つ・両方の表の列・`(消n)` のタグ・消優勢/要注意の行とバッジ・出馬表にはチェックが出ない・選択肢に無い「何指数以上」も選ばれた状態で出る）。`tests/test_ingest.py` に、動いている取得元への中止（最初のアクセスの中で中止 → 次のアクセスをしない）・同時実行中の Ctrl-C（止めてから伝わる）・失敗のログ（名前つきの1メッセージ）・ジョブ画面の「実行中」の表示
- `tests/test_core.py`・`tests/test_aggstore.py` に、高回収率の基準と希少条件（1行版・ベクトル版・SQL 版の一致。部品の完全一致・境界値・4オッズ帯）、`tests/test_ext_indices.py` にボーダーのファイルを読まないこと、`tests/test_ingest.py` に取得元の同時実行（同時に動いていること・順に実行したときと同じ出力・取得元ごとの間隔・中止と失敗がほかの取得元に及ばないこと・ジョブ画面の進み具合・`--sequential`）
- 2026-11-14 の分: `tests/test_mining_only.py` に照合のメモリ（`HitCache` が件数・行数の上限を入れるたびに守る・ふつうの dict でも件数の上限・`iter_hit_rows` が `match_entry_row` と同じ中身と順で、キャッシュを極端に小さくしても結果が変わらず1頭の途中でも上限を超えない・build_db が XJOIN_BATCH 行ごとに INSERT し、上限とまとまりを極端に小さくしても xjoin が行の順まで同じ・出馬表を読んだあと照合の前に `gc.collect()`）。`tests/test_core.py` にオッズ帯の境目（1.9・1.99 は入らず 2.0・2 は入る・全角・0・負・空）、`tests/test_aggstore.py` に `ODDS_BREAKS` の下限が設定のハッシュに入ること（前の境目 `LEGACY_BREAKS` と違うハッシュ）、`tests/test_verified_fixes.py` に集計まで通した確認（単勝 1.5・1.9 の馬は normal にだけ入り、odds10u は 2.0・9.9 だけ。master の オッズ帯 列・成績表の印・agg_settings.json）、`tests/test_pipeline.py` に DB の記録と注意書き（今の境目で集計した DB には出さない・印に境目の無い成績表／記録の無い DB／一部だけ前の区切りの成績表では、オッズ帯を出す4画面に出て、出馬表・レース一覧には出ない・成績表だけ作り直したあとは条件ヒット馬と WEDGE 一覧に「古い照合結果」の注意書きが出て、`--entries-only` で消える・画面の表記「2〜10倍未満」）
- 2026-11-14 の2件目（軸候補の最低件数・消優勢の基準）: `tests/test_core.py` に軸候補の境目（20件で軸・19.9件までは4つの条件のどれでも軸にしない・4オッズ帯・10〜19件でも絶対軸の条件なら絶対軸・希少条件は6件のまま・`rule_settings()` と説明の文の値）。SQL 版・ベクトル版との照合（`test_sql_condition_matches_python`・`tests/test_aggstore.py`）は件数 19・20 を含む値でそのまま回る。`tests/test_views.py` に `danger_level` の個別の例と、依頼の式を別に書いたものとの総当たり（絶対軸 0〜3・軸候補 0〜5・消し 0〜8・指数数 0〜4）、`horse_stats`・`axis_cross`（絶対軸のある馬は要注意まで・1行の消しには付けない・1指数だけの消し・同数は要注意・カットのあとの行数で判定）。`tests/test_pipeline.py` の条件ヒット馬のページ（消優勢 1頭・要注意 2頭の見出し、両方の表と4区分の表のバッジ、判定の説明の文、カット OFF でも消優勢のまま）
- 2026-11-16 の分:
  - `tests/test_core.py`: 指数の上限ゲート（上位・下位の部品が `hensachi` の帯の名前と同じ・下位／上位／どの形でもない末尾（0・全角・空白つき・前走の帯 など）での 絶対軸・軸候補・高回収率の軸・相手候補・消し候補の判定・結合データなし・希少条件・途中の部品は見ない・説明の文と `rule_settings()`）、1行版・ベクトル版・SQL 版（列名 結合データ／key）の一致（2万行 × 4オッズ帯）、`keshi_rule`（classify が消しと言う行だけ理由が付く）、負の相関の組が台帳の正式名で双方向に引けること
  - `tests/test_views.py`: 負の相関の除外（3つの組・双方向・絶対軸でも軸候補でも・除かないもの（共通の規則の消し・ほかのオッズ帯・組に無い指数・同じ側・別の馬・相手候補しか無い指数）・カットのあとの軸で見ること・`keshi_fav`／判定に入らないこと・除いた消しと重なるだけの「通常」の消しを隠さないこと・行の順に依らないこと）、画面の区分が末尾を見ること、`note_cell`（エスケープ・赤の太字・title・別タブ・長いメモの切り方）
  - `tests/test_drill.py`（新規）: メモの保存（保存・上書き・削除・地域ごと／全地域共通と優先・長さの上限・不正な条件・壊れたファイルを上書きしない・外から直したファイルの読み直し・同時の保存で失われない・`NOTES_PATH`・書き込みの途中で失敗しても前のファイルが残る・権限が umask／前のファイルのまま・片方だけのサロゲートを断る）、空の master と単勝オッズ inf、逆引き（master の探し方・キー／オッズ帯／指数有効／重複／取消の扱い・s2 の重なり・小さく分けて読んでも同じ・gz でない／UTF-8 の master・不正な引数と読めない master・TARGET 用のレースIDの並び）、分布のまとめ（オッズの帯・並びのある項目のいちばん狭い範囲・並びの無い項目・単独1件・凡走の集まりと期待の好走数・全部が単独1件・空の値・種類の多い項目）
  - `tests/test_pipeline.py`: DB の成績表で、指数が下位の行が軸・相手にならず SQL のプリセットと1行版が同じ行を選ぶこと、**逆引きの件数・複勝率・勝率が DB の成績表と一致**すること（family × オッズ帯 × スキームごとに抜き出した260行）、逆引きのページ（レースIDの2つの欄と切り替え用の JSON・まとめ・明細の色分け・`base=` でも同じ・CSV・順位帯の family・引数が合わないときの理由・master が無くてもメモの欄・見るだけではメモのファイルを作らない・DB と master の件数が違うときの注意書き）、メモのページ（4区分の表と WEDGE 一覧の列の位置・リンクの6つの引数と別タブ・保存 → 赤の太字と title・その条件の行だけ・エスケープ・全地域共通と優先・削除・別サイトからの POST 403・Host が別の名前の POST 403・条件の足りない／長すぎる POST 400・戻り先の URL にフォームのほかの値を使わない・「該当なし」の行の幅・DB を替えても出る・壊れたファイルでも画面は出て保存はせず理由を出す）、逆引きの覚えている結果（同じ結合データを 通常 → 2〜10倍未満 と続けて開く・master を作り直す）と明細の表の上限・ラジオの切り替えのスクリプト、条件ヒット馬の「負の相関…除外した消し候補 N 行」と該当数、区分の説明の文
- 2026-11-17 の分:
  - `tests/test_core.py`: `low_tail_axis_category`（下位の末尾で 絶対軸・軸候補・高回収率の軸 の基準を満たす行だけ・相手候補の基準まで／消し候補／上位・不明の末尾／結合データなしは None・件数の下限と希少条件・乱数の行で「末尾を上位に替えた classify が abs / axis」と一致・classify は今までどおり）
  - `tests/test_views.py`: `special_hits`（代表の行 = 複勝率_val 最大 → 件数 最大 → 台帳の順・元区分・指数ごとの内訳と台帳の順・まとめない組（馬・パターン・スキーム・オッズ帯・地域・条件の部分）・入らない行・4区分に出ないこと・行の順に依らないこと・同じ指数の2行は指数1つ・馬番の数と文字／地域とオッズ帯の None と空）、その行のオッズ帯で元区分を決めること（高回収率は 2〜10倍未満・10〜50倍だけ・2〜10倍未満専用の消しは入らない・希少条件の下限）、過剰人気カット（odds10u の回収値100未満・値なしだけ。行数）と並び
  - `tests/test_aggstore.py`: コンパクト化が条件特化ヒットの行を残すこと（件数・WEDGE の下限では残らない行で。相手候補の基準まで・件数不足は落とす）、`low_tail_axis_frame` と1行版の一致（4,000行 × 4オッズ帯）、「残る行 = 独立に計算した行」の照合に条件特化の行を追加
  - `tests/test_pipeline.py`: 条件ヒット馬のページの「条件特化ヒット」（4区分の表の下・行数と過剰人気カットの行数・27列・代表の行・指数数と指数名（台帳の順・絶対軸は太字・同じ指数の2行でも指数数は変わらない）・件数／結合データ／調査／指数名のリンクの6つの引数と別タブ・カット OFF・メモ（代表の行は赤の太字、まとめた指数は枠と title）・`xonly`）
  - `tests/test_past_perf.py`（新規）: 台帳と ext_indices の振り分け、馬場状態3区分・想定の指定・2歳／3歳限定の判定・馬名と血統登録番号の正規化、結果ファイルの読み方（負の値・0・小数・見出し・18桁でない ID・数でない値・桁あふれと無限大）と丸め（依頼の例・ROUND_HALF_UP・負の値・0 → 0.1）、結果ファイルの探索（サブフォルダ・大文字小文字・speed*.zip の SP*.csv だけ・st_se*.zip・読めない ZIP・後のファイル優先）、600日の窓（当日・未来・601日前は入らない）と並び、段階①〜⑤（1走でも止める・馬場状態なしは②から・③の2通り・芝ダ／距離が分からないとき）、履歴に入れる走（取消・除外・障害・馬の分からない行を除く・中止は入れる）、馬の見分け（番号 ＋ 番号の無い同じ馬名の行・同じ馬名の別の番号は別の馬・番号の無い今走は馬名）、列が無いときの注意書き、そのレースの結果を使わないことと想定の馬場状態（芝・ダ別）、**別に書いた計算（oracle）との全件一致**（8指数 × 想定3通り・血統登録番号なしでも同じ）、履歴のキャッシュ（再利用・結果ファイルの追加／同じ大きさの値の直し／基本CSVの直し／行数の違いで作り直す・`save=False`・`rebuild=True`・元が無いときは履歴だけで・読めない履歴の WARN・UTF-8 の馬名）、基本CSVの選び方と設定ファイル
    - 組み込み（基本CSV・結果指数・出馬表を置いた場所で）: replace が8本の master と履歴を書き、全行が oracle と一致・0 = 欠損と「欠損2頭以上のレースは使わない」・順位は値から・その馬の最初の走は値なし・22条件は タイムM の master と同じ／`--no-past-perf`・結果指数なし・ext_indices より計算を優先・csv_files の同名を優先・結果指数が無ければ ext から・同じプロセスで結果指数を足して回し直すと新しい値・履歴を作れなくてもほかの master は作る・`--workers 2` でも同じバイト列／calc が8指数と派生の列を付け oracle と一致・`--baba-turf`／`--baba-dirt`・設定ファイルと引数の優先・知らない値は終了コード2・ext のファイルより計算を優先（全部の計算でも `--indicators` でも）・`--no-past-perf`・`--mining-only` は履歴を読まない・`--indicators 過去SP_3走平均` の差し替え／**過去の開催日を出馬表の形にすると master と値・順位・偏差値・帯が同じ**／古い履歴の作り直しと履歴だけでの計算／ingest の取得元 past（8指数のファイル・ext_indices へのコピーと振り分け・設定の保存・馬名の無いベースCSVは失敗・CLI・列の名前が違う2本のベースCSV・16桁の ID・コピーで前の値を残さない・芝ダ／距離の無いベースCSVの警告と全走での値・設定を保存できないときは警告だけ・結果指数が無ければ失敗と終了コード1・失敗した実行は設定を保存しない）／/ingest の画面（プルダウンの4つの選択肢・知らない値は 400・実行と保存・保存した値が選ばれる・フォームを経ない POST）
- 2026-11-17 の2件目の分:
  - `tests/test_race_marks.py`（新規）: レースID16桁 → RX（依頼の例・日 10〜12 の A〜C・18桁・読めない ID・ext_indices の旧ID と同じ作り方）、値の前処理（依頼の実データの行・カラーコード・全角・`----`／`－`）と名前からの種類（`moistureC` は使わない）、依頼の実データの行をフォルダ・サブフォルダ・ZIP・cp932／UTF-8／BOM で置いて読む、読めない行と見出し・後のファイルの優先（値なしで上書きしない）・含水率の組（1行目 = 芝・2行目 = ダート・3行目以降・1行だけ・離れた2行・複数ファイル）、区分の境目（クッション 8.9／9.0／9.9／10.0、含水率 芝 9.9／10.0／14.9／15.0・ダ 4.9／5.0／9.9／10.0、前走の芝・ダ、数でない値・0 以下）、`derive` が 前走RID16 で印を引くこと・前走R印3 の補完の優先、`__race_marks__.csv.gz` の往復・出馬表側の読み方（race_marks → 表 → 空）・`RACE_MARKS_DIR`。検証のあとに足した分: 読む順（相対パスの名前順。`a/member.csv` → `member.csv`）・隠しフォルダ（`.old/`・`__MACOSX/`・ZIP の中の `.x/`）を読まないこと・ZIP の中の名前順・パスワードつきの ZIP の中身を1本だけ飛ばすこと（結果指数も同じ）・後の含水率ファイルに芝の1行しか無いときは組ごと入れ替えること・値が1つも無いレースを表に入れないこと・カラーコードを除く順、`__race_marks__.json`（記録の往復・`--no-race-marks` の master なら出馬表側も読まない・`--marks-dir` の master なら同じフォルダ・読めない記録は無いものとして扱う）、**replace の `--no-race-marks`・`--marks-dir` が3種類の master（基本CSV・ext の指数・過去SP）と build_db の出馬表の表に同じに効く**こと（前の実行の表を残さない・build_db の `--marks-dir`／`--no-race-marks`・出馬表側は race_marks/ を表より先に読む・読めない ZIP があっても master を作る）
  - `tests/test_recent_form.py`（新規）: 近5走の要約（最高着順の区分・距離差200の境目・場所・芝ダ）、直近5走に絞ってから条件を見ること・600日の窓（当日・未来・600日ちょうど・601日）・着順の分からない走を数えないこと・今走の値が分からないとき、馬の見分け（番号 ＋ 番号の無い同じ馬名の行）、履歴に入れる走（結果指数の無い走も・中止は頭数・失格で頭数なしは18・取消／除外／障害は入れない・着順の列が無い基本CSV・前の版の表）、**別に書いた計算（oracle）との全件一致**（基本CSVの全行と、出馬表の形）、`get_history`（結果指数なしで作る・2回目は読むだけ・結果指数を足すと作り直す・結果指数のファイルが無くなっても前の履歴の値を使う・`rebuild=True`・版2の履歴は使わない）、replace が 近5走 の列を書くこと・履歴を作れないときは空にして前の履歴を残さないこと。検証のあとに足した分: 結果指数が無いまま基本CSVが変わったら作り直すこと・結果指数のファイルが無く前の履歴を使っているときに基本CSVが変わったら、走を作り直して結果指数の値を引き継ぐこと（行数の合わない履歴も）・今走の芝・ダが分からなければ芝・ダの空の過去走があっても空・cp932 でも UTF-8 でも読めないバイトのある基本CSVでも履歴を作ること・replace は `--clean` なしでも履歴を作り直すこと（結果指数のファイルが無くなったら引き継がない）
  - `tests/test_condition_only.py`（新規）: 単体 = `NONE` の family（定義・スキーム・キーの過去側と出馬表側の一致・コースで終わるキー・台帳と DB の往復）、`{地域}_条件のみ` の名前と基準の master の選び方、**指数の上限ゲートを条件のみの行に掛けないこと**（数字・下位の帯で終わるキー。1行版・ベクトル版・SQL 版（`indicator_col`）の一致・条件特化ヒットに入らないこと）、コンパクト化、照合（`match_bases`・条件のみは1回・指数だけを渡したときは作らない）。全段（`tests/synth2.py` の合成データ: 同じ馬が何度も走り、前走の列が実際の前走を指す。レース印5種・出馬表・タイムM と 対戦M の2本の master・条件のみを含む family 9種）= master の 前走メンバーLv・前走タイムLv・前走クッション帯・前走含水率帯・前走R印3（補完）・近5走同条件・近5走同場 が別に書いた計算と全行一致、**出馬表の表（DB）でも同じ値**（前走開催 → RX・race_marks が無ければ `__race_marks__.csv.gz`）、条件のみの成績表が `agg_master_全場_条件のみ.csv.gz` だけにあり・基準の master が タイムM で・件数が master の全部の行（指数有効=0 も）から数えた数と一致、`--resume`（条件のみのジョブも済みに数える・消えた成績表だけ作り直す）と、条件のみをやめたとき／条件のみだけのときのファイル、DB と照合結果（条件のみの行は base = 条件のみ で1頭・1キー・1オッズ帯につき1行・その場照合と同じ）、画面と横断検索のプリセット（SQL と1行版の区分が条件のみの行でも同じ）・逆引き（基準の master・件数が成績表と同じ）、`--mining-only` の一部の作り直しが条件のみの行を残し、全部の照合と同じになること、screen_families の `--tails`（知らない値は終了コード2・条件のみの候補は基準の master 1本だけで採点・基準B の数が本番の DB と同じ・`--export-selected`／`--from-report` に条件のみが出る）。検証のあとに足した分: 条件のみだけにしたとき・条件のみをやめたときに、前の成績表と印（0行で印だけのものも）を残さないこと（`--resume` でも）、**数字で終わる条件のみのキー**（`強井×1200`・`相手井×1600`・`ダ×1600`・`東京×ダ×1600`）で、コンパクト化（区分の行だけを残す設定）が相手候補・絶対軸の行を残し、件数が 指数有効=0 の馬も数え、採点（基準B・`covered`）が同じ見方で、`--indicators` で絞ったときの基準の master が絞ったあとの中から選ばれること、画面のどの経路でも上限ゲートに掛からないこと（条件ヒット馬の絶対軸・相手候補のカード・条件特化ヒットに出ない・指数クロスの選択肢・WEDGE 一覧の絞り込みと「馬 × 指数」の列・CSV の「区分」・横断検索のプリセット（まとめて／集計表ごと）・逆引きの区分。照合結果を読む経路とその場照合の両方）、一部の作り直しのあとの `__tables_meta__` の照合結果の行が全部の照合と同じこと
  - 既存のテストの更新: `tests/test_conditions.py`（両側の一致の確認にレース印を足す。合成データ `tests/synth.py` の前走のレースID を実際の場所コードに）、`tests/test_screen_families.py`（POOL 34・テンプレートと候補の数・`--tails` に NONE）、`tests/test_past_perf.py`（履歴の列・結果指数の無い走も履歴に入る・`--no-past-perf` でも履歴は書く）、`tests/test_drill.py`（まとめの項目の数）
- 2026-11-18 の3件目の分:
  - `tests/test_baselines.py`（新規）: 受け取った基準値マスタ（各467行）の全行が縮約の式・偶数丸め・`n // 3`・信頼度と合うこと（全体平均 SP 62・ST 48）と引き方（L1 → L2 → L3・G1 → 重賞・新馬は None）、セルの部品（世代・クラス13区分と表記ゆれ・障害・馬場区分と不明のとき・想定の指定・場所区分・距離帯の境目）、生成（手計算の例・端数の残し方・年度の重み・切れ目の同値は古いほうから除く・並びに依らない・decay の範囲）、**別に書いた計算（oracle）との全列一致**（乱数の標本 × decay 1.0／0.65／0.9・整数と小数）と CSV の形（CRLF・往復）、標本の絞り込み（3着・0・新馬・G1・クラス／馬場／場所／距離の分からない走）、更新（最低件数・同じ中身なら書かない・片方だけ・壊れたファイルの WARN）、ラベルの境目（前走 SP +3／0／−6・ST +2／0／−4、今走の優先順）と乱数での oracle 照合、`conditions()`（前走を 前走RID16 で引く・番号なしは馬名・G1 の前走・新馬・履歴に無い馬・想定の馬場は今走だけ・値 0 = 値なし・順位の読み方・履歴／基準値／値が無いとき）、帯のキー（`SP3走1位`・`ST3走1~5位` などが過去側と出馬表側で同じ・どの末尾でも s1・s2）と `is_self_redundant`、候補の数と因果トリオ⑤
    - 全段（`tests/synth2.py` の合成データ80日ぶん。レースごとにクラス名・年齢限定を11通りに振り、結果指数に値なし・0 を混ぜる。`--baseline-min-samples 30`）: replace が基準値マスタを 3着馬から作り直し（基本CSVから別に計算した全列と一致・既定の decay 0.65）、master_csv/ に同じバイト列の写しと記録を書くこと、履歴 版4 の `クラス`・`馬場4`、**master（タイムM・対戦M・過去ST_5走最高）の6列が別に書いた計算と全行一致**（前走は 前走レースID(新) の走・`過去SP3走順` は perf の master の `順位`。どの区分も出ていること・新馬戦は今走基準比が空）、**出馬表の表（DB）の6列も同じ計算と一致**、成績表（`ST平均基準超×ST3走1~5位×1~5位` の形・s1 と s2）と照合結果（キーが出馬表の馬の部品から作れる）、その指数自身の順位の family が `過去SP_3走平均`・`過去ST_3走平均` の成績表にだけ無いこと（aggregate と screen の `masters` の数）、想定の馬場（引数 → 設定ファイル → auto。前走は実際の馬場のまま・不正な値は終了コード2）、基準値マスタだけをあとから変えても出馬表側は写しで引くこと（記録があって写しが無ければ付けない・記録も無ければ基準値マスタ）、replace のオプション（既定の最低件数では作り直さない・`--no-baseline-update`・`--baseline-decay 1.0`・不正な decay は [ERROR] で6列が空・`--no-past-perf` は今走基準比と3走順だけ空・`--workers 2` でも同じ master）、`python -m shisu.baselines`（表示・`--update`・`--kinds`・`--decay`・`--min-samples`・`--out-dir`・終了コード）
  - 既存のテストの更新: `tests/conftest.py`（基準値マスタの写しを一時フォルダに置き `SHISU_BASELINE_DIR` を向ける。テストが `shisu/data/` を書き換えない）、`tests/test_conditions.py`・`tests/test_race_marks.py`・`tests/test_drill.py`（条件の列 30 → 36）、`tests/test_screen_families.py`（POOL 40・テンプレートと候補の数）、`tests/test_recent_form.py`・`tests/test_past_perf.py`（履歴 版4・13列）
- 件数: 650件（pandas 2.3.3・pandas 3.0.5・Docker（Python 3.11.18）で全件パス。2026-11-18）

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
27. **新しい区分の基準（高回収率・希少条件）とコンパクト化済みの DB**: 区分は画面で DB の値から判定するので、DB にある行には 2026-11-12 の基準がすぐ効く。ただしコンパクト化は**集計した時点の基準**で行を残しているので、新しい基準で初めて区分に入る行（odds10u・odds10_50 の高回収率の軸、希少条件で件数 6〜9 の軸・15〜29 の相手・12〜19 の消し）は、layer A・WEDGE の下限以上・件数の下限以上のどれかでなければ DB に無い。全部出すには aggregate（`--resume` でも設定のハッシュの違いで全 master を作り直す）→ build_db --fresh
28. **`--mining-only` の時間**: 一部の作り直しで済むとき（マイニングと馬体重だけが変わった）は全部の照合のおよそ 2/33（DB の大きさによる。数秒とは限らない）。騎手の乗り替わり・馬の増減・成績表の作り直しのあと・途中で止まった実行のあとなどで全部の照合に切り替わったときは `--entries-only` と同じ時間（`[INFO]` に理由が出る）。前の版の build_db で作った照合結果には記録が無いので、最初の1回は全部の照合になる（その週の `--entries-only`（STEP 3）を今の版で回していれば、記録はもうある）
29. **取得元の同時実行とアクセスの間隔**: 同時に動くのは別のサイトの取得元どうしで、1つのサイトへのアクセスは今までどおり1本・間隔つき（既定 Ω・かよちん 3秒、ほか2秒。1秒未満にはできない）。間隔を縮めたいときは画面の取得元ごとの間隔・CLI の `--interval`
30. **条件ヒット馬の「通常」の重複排除は画面だけ**: `/entries/wedge`・`/entries/wedge.csv`（「区分」列）・横断検索 `/` は全件のまま。件数の差の基準（3）は `app.REDUNDANT_NORMAL_DIFF`
31. **指数クロスの基準は固定**: 上位 = 順位3位以内・偏差値61以上（`app.CROSS_RANK_MAX`・`CROSS_HEN_MIN`）。順位・偏差値の無い指数（そのレースで使わない指数）は数えない。指数クロス好走馬（軸）は、冗長な通常を除いたあとの 絶対軸・軸候補 の行で数える
32. **過剰人気カットは既定で ON**（2026-11-13〜）: `/entries/hits` を引数なしで開くと、odds10u の軸候補・相手候補のうち回収値100未満の行は表にも該当数にも出ない（画面の上に除いた行数とリンクが出る）。全部見るときは `cut_low_10u=0`。`/entries/wedge`・CSV・横断検索 `/` は全件のまま。配当の列の無い過去データ（回収値がすべて 0）では、odds10u の軸候補・相手候補が全部カットされる
33. **消優勢・要注意は行数の比べ方**（消し(2〜10倍未満) = odds10u の消し候補）: 行の重み（件数・回収値・family の重なり）は見ていない。同じ指数の family 違いで似た条件が何行も出れば、その分だけ行数が増える。目安として使い、(消n) の付いた指数と行の中身を見て判断する。消し(2〜10倍未満) は odds10u の消し候補だけ（normal の消しは「消し候補」の列で見る）。2026-11-14 の基準では、軸が無く消しが1つの指数にしか出ていない馬は、消しが何行あっても何も付かない（family 違いの重なりを1指数ぶんと見るため）。絶対軸が1行でもある馬は消優勢にならない
34. **オッズ帯 odds10u の変更は次の集計から**（2026-11-14〜）: 表記はすぐ「2〜10倍未満」になるが、DB の成績表は集計し直すまで前の区切り（1倍台の馬も入る）のまま。その間は画面に注意書きが出る（前の版で作った DB には記録が無いので、作り直すまで出続ける）。集計し直すと odds10u の件数が減るので、件数の下限（軸10・相手30・消し20。コンパクト化の件数30）を割って区分・DB から外れる行が出る。1倍台の馬は normal の成績表にだけ入る（オッズ帯別の3つを足しても normal にならない）
35. **照合（xjoin）のトランザクションは1つのまま**: メモリは一定だが、WAL ファイル（ディスク）は書き換える行のぶん大きくなる（全部の照合なら xjoin の大きさほど）。ディスクの空きが要る。照合のキャッシュは上限つきなので、数千 family では1頭の中でも何度も空になり、再利用はほとんど効かない（時間は照合の回数に比例する）。build_db は旧版より速いが、画面のその場照合（xjoin が無い・古い形式のときだけ）は、数千 family の DB では旧版（上限なしの dict）より遅くなる（合成のストレス DB・6頭で照合 146万回 → 173万回・88秒 → 99秒。メモリは 5.5GB → 3.9GB）。初期セットの規模では変わらない。その規模でその場照合は実用にならないので、xjoin を作って使う
36. **軸候補の最低件数 20 と、作ってあるデータ**（2026-11-14〜）: 区分は画面で DB の値から判定するので、app を再起動すれば今の DB にすぐ効く（基準を厳しくしただけなので、要る行は全部 DB にある。10〜19件の行は DB に残ったまま軸候補に出なくなる）。`rule_settings()` が変わるので集計の設定のハッシュも変わり、コンパクト化していれば次の `aggregate --resume` は全部の master を作り直す（同じ日のオッズ帯の変更でもともと全部作り直しになる）。作り直すと、区分に入らなくなった 10〜19件の行は、WEDGE の下限以上・layer A でなければ DB に入らない。screen_families の基準B（絶対軸＋軸候補のキーの数・`--min-axis`）も軸候補が減るぶん小さくなる: 前に作った screening_report.csv の数は前の基準のままなので、`--from-report` は前の基準の数で選ぶ（今の基準で選び直すには採点からやり直す）。希少条件の軸候補は 6件のままなので、ふつうの条件（20件）との差が前より大きい
37. **指数の上限ゲートは「結合データの末尾」だけを見る**（2026-11-16〜）: family の末尾は必ず指数の部品（指数順位・指数順位帯・偏差値帯）なので、末尾の文字で決まる。末尾が数字だけなら指数順位とみなす（family の定義で末尾が指数の部品でないものは作れない）。s2 は帯が重なるので、指数4位の馬は `1~5位`（上位）のキーに、指数8位の馬は `6位~`（下位）のキーにだけ当たる。偏差値帯 `51~60` は上位の側（平均より上）。区分は画面で判定するので今の DB にすぐ効くが、`rule_settings()` が変わるので次の `aggregate --resume` は全部の master を作り直す。screen_families の基準B（絶対軸・軸候補・相手のキーの数）も、指数が下位のキーのぶん減る（前に作った screening_report.csv は前の基準の数）
38. **負の相関の除外は行の組み合わせだけを見る**（2026-11-16〜）: 「片方の指数に軸が1行でもある」かどうかで決まり、行の件数・回収値・family は見ない。軸ヒットは過剰人気カットのあとの行なので、カットの ON／OFF で除かれる消しが変わる。組（`TRADEOFF_GROUPS`）は固定。`/entries/wedge`・CSV・横断検索は全件のまま
39. **逆引きは master が要る**（2026-11-16〜）: `master_csv/master_{地域}_{指数名}.csv.gz` を読むので、master の無い環境（DB だけ持ってきた など）では出ない（メモは使える）。10年分の master（約30万行）で1回 数秒（5万行ずつ読むのでメモリは増えない。同じ条件の2回目は覚えている結果を使う）。成績表を集計したあとに master を作り直した・前のオッズ帯の区切りで集計した DB では、DB の件数と合わない（注意書きが出る）。コア分布のまとめは master にある列だけ（馬体重・4角位置などは TARGET にレースIDを貼って調べる）。危険候補の印は目安（件数の少ない値の「凡走集中」は偶然でも付く）
40. **危険条件メモは1ファイル・ログインなし**（2026-11-16〜）: `condition_notes.json` を1つのプロセスの中でロックして書く。app を2つ同時に動かして同じ瞬間に保存すると、あとから書いたほうが残る（ふつうの運用＝app 1つでは起きない）。メモは条件（指数・family・スキーム・オッズ帯・地域・結合データ）の文字が同じ行にだけ出る: family を選び直して名前が変わった・地域の名前が変わったときは、前のメモは出なくなる（ファイルには残る）。バックアップはこのファイルをコピーする
41. **条件特化ヒットと、作ってある DB**（2026-11-17〜）: 表に出るのは DB にある行だけ。コンパクト化（既定）で集計した成績表では、区分に入らない行は 件数の下限以上・WEDGE の下限以上・layer A のどれかでなければ落ちている。2026-11-16 の上限ゲートより前に集計した DB には、指数が下位の軸の行が（その時点では区分に入っていたので）残っている → そのまま出る。上限ゲートのあと・この変更の前に集計し直した DB では、その行の多くが落ちている（とくに `--compact-min-count none --compact-min-wedge 1.0` で集計した DB）。この変更からコンパクト化は条件特化の行も残すので、次に aggregate を回せば出る（`rule_settings()` が変わるので `--resume` でも全部の master を作り直す）
42. **条件特化ヒットは別枠の表だけ**: 4区分の表・馬ごとの該当数・指数クロス・消優勢の判定には入れない。冗長な「通常」の除外・負の相関の除外はしない（同じ条件の「通常」とオッズ帯別の行が両方出ることがある）。元区分は代表の行のもの（まとめたほかの指数は、指数名が太字なら絶対軸の基準）。`/entries/wedge`・CSV の「区分」列は変えていない
43. **過去SP・ST は「基本CSVにある走」だけが過去走**（2026-11-17〜）: 履歴は csv_files の基本CSVから作るので、基本CSV（raw_csv への追加）が半年前までなら、直近半年の走は「無い」ものとして直近3走・5走を数える。結果指数のファイルだけ足しても、その走の行（馬・芝ダ・距離・馬場状態）が基本CSVに無ければ使えない。直近の走まで使うには、先週までの基本CSVを raw_csv に足して replace を回す（そのあと aggregate まで回すかは別。出馬表側だけなら calc が履歴を作り直す）
44. **過去SP・ST の 0 = 欠損**: 対象の走が無い馬（新馬・600日以上の休み明け・結果指数や基本CSVに走が無い馬）は欠損で、同じレースに2頭以上いると、そのレースはその指数では使わない（§5.5 の1。2歳戦・3歳の春は多い。合成データでは 5〜6% のレース）。対象の走がある馬の値がちょうど 0 になったときは 0.1 にしている（§3.11）。結果ファイルの値 0 は値として数える
45. **過去側は実際の馬場状態・出馬表側は想定の馬場状態**: 成績表（過去側）は、そのレースの実際の馬場状態で①の走を選んだ値から作る。出馬表側は想定（`auto` で 馬場状態 の列が無ければ②から）なので、想定が外れた・指定しなかったときは、同じ馬でも過去側の数え方と対象の走がずれる（値が少し変わる）。基本CSVに `馬場状態` の列が無いと、過去走の馬場状態が分からず①・③の「同じ馬場状態」には当たらない（WARN）
46. **2歳・3歳限定の判定は列しだい**: 基本CSVに `年齢限定` の列が無いと、3歳限定の1勝クラス・オープン・重賞（レース名に「3歳」の無いもの）は古馬戦として絞り込む（WARN）。出馬表は `年限定` で判定するので、その条件のレースでは両側の対象の走がずれる。基本CSVに `年齢限定` を入れて出す
47. **馬の見分け**: `血統登録番号` の列が無い出馬表・基本CSVでは馬名で見分ける（同じ馬名の別の馬が600日以内に走っていれば混ざる。JRA の現役馬では起きにくい）。番号のある今走は「その番号の走 ＋ 番号の無い同じ馬名の行」
48. **結果指数のファイルの優先は名前の順**: 同じ馬の値が複数のファイルにあれば、相対パスの名前順で後のファイル（大文字は小文字より前）。障害の走は履歴に入れない
49. **履歴の印は中身の CRC32**: calc・/ingest は実行のたびに基本CSV・結果指数のファイルを読んで CRC32 を取る（10年分の基本CSVで1秒前後）。違えば履歴を作り直す（基本CSVの必要な列だけを読む。数秒〜十数秒）
50. **/ingest の取得元 past のベースCSV**: 馬名（または血統登録番号）・芝ダ・距離・年限定 のある出馬表の形が要る（スクレイピング用の ★5タブのベースCSV（レースID(新)・日付S・枠番・馬番・ワーク1）だけでは計算できない → この取得元だけ失敗）。ext_indices/past_sp_st へのコピーは、今回のファイルにある馬を今回の値に置き換える（空欄も。24 の「前の値を残す」とは違う。今回のファイルに無い馬の行は残る）。calc は計算した値を優先するので、ext のファイルは TARGET への取り込み用
51. **外部レース印の照合は「年の下2桁・場所・回・日・R」**（2026-11-17〜）: RX… には月日が入っていないので、前走RID16 の月日は見ない（同じ年・場所・回・日・R は1レースなので、ふつうは取り違えない）。前走が地方・海外の馬、印のファイルの期間より前のレースが前走の馬は空。値の先頭2文字が16進なら必ずカラーコードとして除くので、カラーコードの付いていない2文字だけの値（`12` など）は空になる（TARGET の出力には必ず付いている前提）
52. **過去側の印は replace を回した時点・出馬表側は build_db を回した時点**: 出馬表側は race_marks/ を先に読む（先週のレースの印を使うため）。race_marks/ のファイルの値をあとから直すと、同じ前走でも過去側（master）と出馬表側で区分が変わりうる（過去側も合わせるには replace → aggregate）。replace は、印が1つも無ければ `__race_marks__.csv.gz` を消す。replace を `--no-race-marks`・`--marks-dir` で回したときは、出馬表側も同じ読み方をする（`__race_marks__.json`）。build_db の `--marks-dir`・`--no-race-marks` を使うと、その実行だけ master と違う印を見る
53. **前走R印3 の補完で、既存の family の成績表も変わる**: ペース判定のファイルを置いて replace → aggregate を回すと、基本CSVの `前走レース印３` が空だった馬にも 前走R印3 が付くので、前走R印3 を使う family（初期セットの D_r3・C_sd_r3_style など）の件数が増える。基本CSV・出馬表に値があれば、印と違っていてもそちらを使う
54. **近5走の要約は「基本CSVにある平地の走」だけ**（43 と同じ）: 基本CSVが半年前までなら、直近半年の走は無いものとして数える（出馬表側も同じ履歴なので両側はそろう）。着順が空の走は数えない。失格・中止は4着以下。障害の走・取消・除外は入らない。馬の見分けは 47 と同じ。履歴は結果指数が無くても基本CSVから作るので、calc_entry_hensachi・build_db は結果指数を使っていない場所でも基本CSVを読む（読めない基本CSV＝0バイトなど があると、calc は `[ERROR] 過去SP・ST の計算: …` を出して終了コード1・build_db は WARN を出して近5走を空にする。ほかの処理は続ける）
55. **履歴は全部の走を持つ**（版3 から。版4 で クラス・馬場4 の2列が増えた）: 10年分（約48万走）で、履歴のファイルは数MB、読み込んだ History は1プロセスあたり約210MB・作るのに約4秒（合成データでの実測。2026-11-17）。replace の `--workers` の子プロセスは、それぞれ1回読む。近5走の計算は30万行で約2秒
56. **「条件のみ」は指数の1つとして数える**: 条件ヒット馬の指数クロス好走馬（何指数で軸か）・消し(2〜10倍未満) の指数数・WEDGE 一覧の「馬 × 指数」では、条件のみを1つの指数として扱う（条件のみの family がいくつあっても1つ）。出馬表（/entries/race）の指数クロスには出ない
57. **条件のみの成績表は、基準の master の全部の行**: 指数が欠けて `指数有効=0` になった馬（除外レース・新馬除外・欠損）も数える（指数つきの family の件数とは母集団が違う）。基準の master は地域ごとに タイムM → 対戦M → 名前順の先頭。その master を作り直したら、条件のみの成績表も `--resume` で作り直される
58. **条件のみの候補の選抜（screen_families）**: 基準B の数は基準の master 1本ぶんなので、指数つきの候補（選んだ master の合計）と同じ `--min-axis` では選ばれにくい（絶対軸のキーが1つでもあれば `--always-select-abs` で選ばれる）。基準A の指標は付かない。`--indicators` で絞ったときの基準の master は、絞ったあとの master の中から選ぶ
59. **候補の数が増えた**（POOL 28 → 34 → 40）: `--space default` は 2,098 → 2,791 → 3,709通り、`all` は 7,557 → 10,815 → 15,195通り（`--tails` に NONE を足すとさらに増える）。採点の時間もその分かかる。前に作った screening_report.csv・selected_families.json はそのまま使える（新しい6条件・条件のみの family は入っていない）
60. **新しい条件の列は、作り直すまで空**: 前の版で作った master には 前走メンバーLv など6列が無い（その条件を含む family のキーができないだけで、ほかは動く）。出馬表の表は `build_db --entries-only` で6列が入る。条件の列が増えたので、`--mining-only` の最初の1回は「キーの部品・条件が違う」として全部の照合になる
61. **3着内基準値は「受け取った CSV と依頼の仕様」から組んだ**（2026-11-18〜）: 生成のスクリプト（build_index_baseline.py）は受け取っていない。受け取った CSV の全行で確かめられたのは、縮約の式・偶数丸め・`n_cut = n // 3`・信頼度・全体平均の作り方。元の標本が無いので、**標本の絞り込み**（クラス名の読み方・世代の決め方・馬場が不明の走の扱い）と **切れ目の同値・分位・標準偏差の細部** は、実データで作り直した値が受け取った CSV と一致するかを確かめていない。最初に実データで replace を回したとき、`--baseline-decay 1.0` で作った CSV と受け取った CSV（`git diff shisu/data/`）を見比べると、絞り込みの違いが分かる（データの期間が同じなら n がほぼ同じになるはず）
62. **基準値マスタは replace が書き換える追跡ファイル**: `shisu/data/index_baseline_*.csv` はリポジトリに入っている。実データで replace を回すと git の差分に出る（コミットするかは運用しだい。別の PC に持っていくなら、このファイルか master_csv/ の写しが要る）。標本が 2,000 件に満たない場所・`--csv-dir` などを既定と違う場所にした実行（`--baseline-dir` なし）では書き換えない
63. **基準値は全期間のデータから作る（過去側は少し先読みになる）**: 過去のレースの基準比も、今の基準値マスタ（そのレースより後の年の3着馬も入り、直近の年ほど重い）で引く。セルの水準は年で大きくは動かない前提の割り切り。年ごとの基準値は持っていない
64. **前走基準比は「基本CSVにある前走」だけ**（43・54 と同じ）: 前走が基本CSVの期間の外・地方・海外・障害なら空。前走の結果指数が result_indices/ に無い馬も空。出馬表側は `前走開催` から作る 前走RID16 で引くので、先週のレースが前走の馬は、基本CSV（raw_csv への追加）と結果指数が先週ぶんまで入っていなければ空になる
65. **今走基準比の馬場は、過去側は実際・出馬表側は想定**（45 と同じ）: 出馬表側は build_db の `--baba-turf`／`--baba-dirt`（無ければ calc が使った想定・/ingest で選んだ値・出馬表の 馬場状態 の列・良）。芝の 良／良以外、ダの 良稍／重不良 の2区分なので、想定が区分をまたいで外れたときだけ基準値が変わる。build_db の引数を省けば calc_entry_hensachi の想定（過去SP・ST の値の選び方）と同じになる。build_db にだけ引数を付けると、値の選び方と基準値の馬場区分が別の想定になる（calc の3区分 良／稍・重／不 に対し、build_db は 稍 と 重 を分けて指定できる: ダの 稍 = 良稍・重 = 重不良）
66. **世代の分からないレースは基準値なし**: 基本CSVに `年齢限定` の列が無い・空だと、1勝クラス・オープン・重賞の世代をクラス名・レース名からしか決められない（「3歳以上」「2歳」などの文字が無ければ空 → そのレースの今走基準比・そのレースが前走の馬の前走基準比が空。2勝・3勝は世代を見ないので付く）。46 と同じく `年齢限定` を入れて出す
67. **`過去SP3走順`・`過去ST3走順` は 3走平均の順位だけ**: 出馬表側は calc_entry_hensachi の出力の `過去SP_3走平均_順位` の列が要る（calc を `--no-past-perf`・`--no-ext` で回して列が無ければ空）。replace を `--no-past-perf` で回した master では、今走基準比と3走順が空（その条件を含む family の成績表は0行）。その指数自身の順位の family は、その指数の成績表に無い（§3.16）ので、画面で指数を `過去SP_3走平均` に絞ると `過去SP3走順` を含む family は出ない
68. **最初の実行で作り直されるもの**（2026-11-18 の版に上げたあと）: 履歴（版3 → 版4。calc・build_db・replace の最初の1回で自動）。master は replace を回すまで6列が無い（その条件を含む family のキーができないだけ）。出馬表の表は `build_db --entries-only` で6列が入り、条件の列が増えたので `--mining-only` の最初の1回は全部の照合になる。family の定義は変えていないので、aggregate の設定のハッシュは変わらない（ただし replace を回すと master の中身が変わるので、`aggregate --resume` はその master の成績表を作り直す）

---

## 10. 今後

- DB の大きさ: unified_all の率の列は文字列（`45.00%（47.10%）`）と数値（*_val）の両方を持っている。数値だけにして画面で文字列を作れば、さらに小さくできる（2026-11-10 に1表ずつのテーブル foragg_* の二重持ちは解消した）
- 集計の時間: family の数に比例する。同じキーの部品を共有する family（例: 同じ parts で tail だけ違う）でグループ化を共有すれば短くできる
- 条件ヒット馬の分類や `/entries/hits` の並びに adjusted_WEDGE を使うか、年別 WEDGE の推移を画面で見せるか
- 指数の組み合わせ・傾向分析（master_csv を入力に、レース単位で「指数1位が3着内のとき残り2頭の順位帯」などを集計）
- 安井 Excel の定性情報（§4.7 の未使用列）を予想コメント・特徴タグとして使う

---

## 11. 変更履歴

### 2026-11-18 SP指数・ST指数の3着内基準値・基準比の条件・過去SP/ST 3走順
- 背景: SP指数・ST指数は「その条件で3着に入る水準」と比べて初めて意味が分かる（クラス・馬場・距離で水準が違う）。受け取った基準値マスタ（`index_baseline_SP.csv`・`index_baseline_ST.csv`）を組み込み、直近の年を重くして自動で更新し、「基準比 × 過去SP/ST の順位帯 × 他の指数」の掛け合わせを集計・照合できるようにする（ユーザーの依頼）
- shisu/baselines.py（新規）・shisu/data/index_baseline_SP.csv・index_baseline_ST.csv（受け取ったもの）・shisu/config.py（`baseline_dir()`）: セルの部品・引き方・生成（上位1/3カット・年度減衰 0.65・K=30 の縮約）・更新（最低 2,000 件）・master_csv/ への写しと記録・条件の計算・CLI
- shisu/past_perf_indices.py: 履歴を版4に（`クラス`・`馬場4`）。`History.run_at`（前走の走）・`history_frame`
- shisu/conditions.py: `BASELINE_CONDITIONS`（6列。`CONDITION_COLUMNS` は36列）・`BANDED_CONDITIONS` に 過去SP3走順・過去ST3走順・`derive(…, perf=)`。shisu/hensachi.py: 帯の単位 `sp3`・`st3`（`SP3走1~5位`）
- shisu/families.py: `SELF_RANK_PARTS`・`is_self_redundant`。aggregate_stats_ver2.py・screen_families.py: その指数自身の順位を部品にした family を、その指数の master では作らない・採点しない
- replace_csv_ver3.py: 基準値の更新と写し（`--no-baseline-update`・`--baseline-decay`・`--baseline-min-samples`）・各ジョブが6列を付ける（`perf_cond_of`・`perf_rank`）。build_db.py: 出馬表に同じ関数で付ける（`entries_baselines`・`entry_perf_values`・`--baba-turf`／`--baba-dirt`）
- screen_families.py: POOL・PART_CODES に6条件、因果トリオ⑤（候補 2,791 → 3,709通り・10,815 → 15,195通り）
- 初期セットの family は変えていない（成績表・DB は、新しい部品の family を選ぶまで今までと同じ）
- テストを書く途中で見つけて直したもの: build_db が `--baba-turf` の不正な値で終了コード2を返さず例外で落ちた（`parse_cli` の戻り値を main が見ていなかった）。基準値を用意できなかった replace（[ERROR]）のあと、各ジョブと出馬表側が基準値マスタを直接読んで「基準比の条件は付きません」と言いながら付けていた → 記録があれば写しだけを使う（`load_for_master`）・エラーのときは記録に `error` を残す
- テスト: test_baselines.py を追加、conftest・test_conditions・test_race_marks・test_drill・test_screen_families・test_recent_form・test_past_perf を更新
### 2026-11-17（2件目） 外部レース印の前走条件・近5走の要約条件・指数を使わない family（条件のみ）
- 背景: 前走のレースの質（メンバーレベル・タイムレベル・ペース）と馬場（クッション値・含水率）を条件に使いたい。条件を細かく掛け合わせると組み合わせが爆発するので、「近5走での同条件・同場の好走実績」は1つの要約条件にする。指数の順位・偏差値帯を使わない、条件だけの掛け合わせのパターンも抽出したい（ユーザーの依頼）
- shisu/race_marks.py（新規）・shisu/config.py（`race_marks_dir()`）: 外部レース印5種の探索・読み込み・RX への変換・`__race_marks__.csv.gz`
- shisu/conditions.py: `CONDITION_COLUMNS` に6列（`MARK_CONDITIONS` 4・`RECENT_CONDITIONS` 2）。`derive(std, inner_lookup, marks, recent)`。前走R印3 の補完
- shisu/past_perf_indices.py: 履歴を版3に（結果指数の無い走も・場所・着順）。`History` は1走1タプル（`all_runs`・`form_runs`・`runs(kind)`・`has_results`）。`recent_form`。`get_history` は結果指数が無くても基本CSVから作る
- replace_csv_ver3.py: レース印の表と履歴を書いて各ジョブが読む（`--marks-dir`・`--no-race-marks`）。履歴は `--no-past-perf` でも作る。build_db.py: `entries_side_sources`・`normalize_entries_df(raw, inner, marks, history)`。calc_entry_hensachi.py・shisu/ingest/past_perf.py: 8指数は `has_results` のときだけ
- shisu/families.py: `NONE`（条件のみ）。shisu/indicators.py: `CONDITION_ONLY`・`split_source_name`・`condition_only_source`。shisu/naming.py: `base_masters`。shisu/classify.py: 指数の名前が「条件のみ」の行に上限ゲートを掛けない（1行版・ベクトル版・SQL 版）。shisu/aggstore.py: `compact_mask(…, indicator)`
- aggregate_stats_ver2.py: 条件のみの family は地域ごとの基準の master 1本・全部の行 → `agg_master_{地域}_条件のみ.csv.gz`。shisu/matching.py・build_db.py・app.py・shisu/drill.py: 条件のみは base = 「条件のみ」で1回だけ照合・逆引きは基準の master
- screen_families.py: POOL・PART_CODES に6条件（候補 2,098 → 2,791通り・7,557 → 10,815通り）。`--tails`（NONE を含められる。条件のみの候補は基準の master だけで採点）
- `.gitignore`・`.dockerignore`・Dockerfile: `race_marks/`
- テストを書く途中で見つけて直したもの: 出馬表側の近5走が全部空になった（`normalize_entries_df` が 距離 をレンジ統合後の値（16-18 など）に書き換えたあとの列から今走の距離を読んでいた → 書き換える前の元の列から取る）。合成データ `tests/synth.py` の前走のレースID の場所コードが形だけ（東京・京都以外も 05）で、過去側と出馬表側が別のレースを指していた → 実際のコードに
- 検証（外部レース印・近5走と履歴・条件のみの集計と採点・条件のみの照合と画面の4領域を、指摘役が別に書いた計算との突き合わせ・変異試験・再現つきで確認）の指摘を反映（2026-11-18）:
  - 外部レース印: replace の `--no-race-marks`・`--marks-dir` が出馬表側に伝わらず、同じ馬で master と DB の出馬表の4列・前走R印3 が食い違った → replace の読み方を `master_csv/__race_marks__.json` に残し、build_db が同じ読み方をする（build_db にも `--marks-dir`・`--no-race-marks`）。パスワードつき・壊れた圧縮データの ZIP の中身で replace が例外で止まり、`--clean` のあとだと master が1本も無いまま残った → その1本だけ WARN を出して飛ばす（結果指数も同じ）。ファイルの優先が文書の「相対パスの名前順」ではなく「上のフォルダのファイル → サブフォルダ」だった → 相対パスの名前順に（結果指数も。上のフォルダとサブフォルダに同じレース・同じ馬の値があるときだけ、結果が変わる）。テストの穴（オプション・古い表の削除・出馬表側が race_marks/ を先に読むこと・含水率の組の入れ替え・隠しフォルダ・ZIP の中の順・カラーコードを除く順・DB の値と比べていなかった assert）
  - 近5走と履歴: cp932 でも UTF-8 でも読めないバイトが1つあるだけの基本CSVで、master は作れるのに履歴ごと落ちて近5走が全部空になり、結果指数を使っていない場所でも calc が終了コード1になった → その文字だけ置き換えて読む。結果指数のファイルが無く前の履歴（結果指数つき）を使っているとき、基本CSVが変わっても・行数の合わない履歴でも、そのまま使い続けた → 走は基本CSVから作り直し、結果指数の値だけ引き継ぐ（印に基本CSVだけの印 `bases`）。テストの穴（結果指数なしで基本CSVが変わったときの作り直し・芝ダの分からない今走・replace の作り直し）。過去SP・ST の8指数は前の版（356a316）と全列一致（9本の master × 馬の見分け方3通り・77,760行）、近5走は別に書いた計算と不一致0
  - 条件のみの集計と採点: family を条件のみだけにすると、前の family で作った指数ごとの成績表と印が残り、build_db が設定の混ざりを警告し続け、名前が同じで tail だけ NONE に変えた family の古い行を取り込んだ → 集計の前に消す。0行で終わった「条件のみ」の成績表の印が、条件のみをやめても残った → 印も探して消す。テストの穴（コンパクト化・採点が「条件のみ」を渡すこと・全部の行で数えること・`--indicators` で絞ったときの基準の master）。条件のみの成績表は別に書いた集計と 2,671 グループで不一致0、初期セット129通りの成績表は前の版とバイト単位で同じ
  - 条件のみの照合と画面: 一部の作り直し（`--mining-only`）のあとの `__tables_meta__` の照合結果の行に「条件のみ」が入らず、全部の照合と違った → 同じ式に。テストの穴（画面が「条件のみ」を渡さない・その場照合や画面から条件のみの行を落とす変異12個が通っていた）→ 数字で終わるキーに絶対軸・相手候補の数を入れた DB で、ページを通して確かめる。区分の判定は、指数を渡さないときは前の版と全部同じ（30万件 × 4オッズ帯）、渡したときに変わるのは「条件のみ」の行だけ
  - 反証役（4領域とも完了。途中で利用上限に2回当たり、そのつど再開）: 指摘した欠陥・テストの穴は、直したあとの作業ツリーでどれも再現しないことを、指摘役の再現スクリプトと変異の再実行（画面の変異12個・集計と採点の変異5個・レース印の変異11個・履歴の変異3個）で確認。残した点: 結果指数を使っていない場所でも calc は基本CSVから履歴を作るので、読めない基本CSV（0バイトなど）があると `[ERROR] 過去SP・ST の計算: …` を出して終了コード1になる（出力は書く。前の版は結果指数が無ければ基本CSVを開かなかった）
- テスト: test_race_marks.py・test_recent_form.py・test_condition_only.py・synth2.py を追加、test_conditions・test_screen_families・test_past_perf・test_drill を更新。650件

### 2026-11-17 条件特化ヒット・過去SP・ST の8指数
- 背景: 2026-11-16 の上限ゲートで、指数が下位の条件は 絶対軸・軸候補・相手候補 から外した。その中で「指数は下位でも、条件そのものは絶対軸・軸候補の数の基準を満たす」行を別枠で見たい。SP指数・ST指数はレースのあとに確定する値なので、そのレースの指数には使えない → その日より前の走の値から作る（ユーザーの依頼）
- shisu/classify.py: `low_tail_axis_category`（1行版）・`low_tail_axis_frame`（ベクトル版）。`classify`・`classify_frame`（既定）・`sql_condition` の結果は変えていない。`rule_settings()` に `LOW_TAIL_AXIS_CATEGORIES`（集計の設定のハッシュが変わる）
- shisu/aggstore.py: コンパクト化が条件特化の行も残す（残さないと、上限ゲートのあとに集計した DB には表に出す行がほとんど無い）
- app.py: `special_hits`・条件ヒット馬のページのカード「条件特化ヒット」（4区分の表の下。過剰人気カット・条件ごとに代表の1行・元区分・指数数と指数名・`/entries/drill` へのリンク）
- shisu/past_perf_indices.py（新規）: 結果指数の探索と読み込み・過去走の履歴・対象の走の決め方（600日の窓・2歳／3歳限定・古馬戦の段階①〜⑤）・値・履歴のキャッシュ・想定馬場状態の設定。shisu/indicators.py に8指数（0 = 欠損）、shisu/ext_indices.py に形式A の振り分け（`past_sp_st/`）、shisu/config.py に `result_indices_dir()`・`past_perf_settings_path()`
- replace_csv_ver3.py: 履歴を作って `master_csv/__horse_perf_history__.csv.gz` に書き、基本CSV × 8指数の master を作る（`--result-dir`・`--no-past-perf`）
- calc_entry_hensachi.py: 出馬表の各馬に同じ関数で8指数を付ける（`--baba-turf`／`--baba-dirt`・設定ファイル・`--result-dir`・`--csv-dir`・`--master-dir`・`--no-past-perf`）
- shisu/ingest（`past_perf.py`・`__init__.py`・`web.py`）・ingest.py: 取得元 `past`・画面の「想定馬場状態（過去SP・ST指数用）」のプルダウン・`--baba-turf`／`--baba-dirt`・`past_perf_settings.json`
- `.gitignore`・`.dockerignore`・Dockerfile: `result_indices/`・`past_perf_settings.json`
- 検証（条件特化ヒット・過去SP・ST の計算・replace／calc への組み込み・/ingest の4領域を指摘役が再現つきで確認）の指摘を反映:
  - 計算: 今走に血統登録番号があると、番号の列が無い期間の同じ馬名の走を落としていた・番号で見つからないと同じ馬名の別の馬の走を使っていた → 「その番号の走 ＋ 番号の無い同じ馬名の行」。対象の走がある馬の値がちょうど 0 になると欠損に数えられた → 0.1。結果ファイルの桁あふれ・無限大の値で計算全体が止まった → 読み込みで飛ばす。`年齢限定` の列が読めない値・列なしのとき黙って古馬戦になっていた → クラス名・レース名から判定し、列が無ければ WARN
  - 条件特化ヒット: コンパクト化が条件特化の行を落としていた（上の aggstore）。カットの行数の説明に「全馬の合計」。テストの穴（元区分がその行のオッズ帯で決まること・指数数は行数でなく指数の数）
  - 組み込み: 大きさが同じまま値だけ直した結果ファイル・基本CSVで古い履歴を使い続けた → 印に中身の CRC32・行数の確認。読めない基本CSVで replace がトレースバックで止まった → `[ERROR]` を出して8指数だけ作らず、ほかの master は作る（終了コード1）。cp932 で書けない馬名を履歴から落としていた → 履歴は UTF-8。読めない履歴しか無いとき黙って付けなかった → WARN。テストの穴（calc が ext のファイルより計算を優先すること・同じプロセスで回し直したときのキャッシュ）
  - /ingest: 列の名前が違う複数のベースCSVで、片方の行の馬名・年齢が "nan" になった → 行ごとに値のある列から読む・NaN は空。ext_indices へのコピーが、対象の走が無くなった馬に前の値を残した → この取得元は今回の値で置き換える。16桁の レースID(新) のベースCSVで TARGET 取込用CSVが1レース1頭に潰れた → 18桁で書く。`--no-past-perf` でも ext_indices/past_sp_st のファイルからは付く → ヘルプと文書に明記。テストの穴（失敗した実行で設定を保存しない・結果指数なしの失敗と終了コード・設定を保存できないときの警告・芝ダ／距離なしの警告・ダの検証の文・設定の置き換え保存）
  - 反証役（4領域とも完了。途中で利用上限に当たり、3領域は修正のあとに再開）: 指摘した製品コードの欠陥は、直したあとの作業ツリーでどれも再現しないことを、指摘役の再現スクリプトの再実行と別の入力で確認。条件特化ヒットは前の版との突き合わせ（classify 60万件で不一致0・4区分の表は前の版と同じ）。残っていたテストの穴1件（想定馬場状態は正しいが受け付けなかった POST・失敗したジョブで設定を保存しないこと）を追加。反証役が残した点: 履歴のファイルだけで動くとき（結果指数なし）は、行数が印と違う履歴でもそのまま使う（作り直す元が無いため。INFO に行数）
- テスト: test_past_perf.py を追加、test_core・test_views・test_aggstore・test_pipeline・test_ext_indices に追加・更新。615件

### 2026-11-16 指数の上限ゲート・負の相関の消し・逆引き・危険条件メモ
- 背景: 騎手や前走の条件が強いだけで、指数そのものは下位（6位~・40以下 など）の条件が 絶対軸・軸候補・相手候補 に出ていた。先行と瞬発のように負の相関にある指数では、片方が軸のとき、もう片方の人気帯の消しが性質上そろって出て「消優勢」になっていた。軸に当たった条件の中身（過去のどのレースか）を TARGET で調べ直す手段と、調べた結果を条件に残す場所が無かった（ユーザーの実運用）
- shisu/classify.py: 結合データの末尾（指数の部品）が下位なら 絶対軸・軸候補・相手候補 にしない（`TAIL_LOW_TOKENS`・`TAIL_LOW_RANK_MIN`・`is_low_tail`・`low_tail_sql`）。消し候補は制限しない。1行版・ベクトル版・SQL 版・説明の文に同じ規則。`keshi_rule`（消しの理由）
- shisu/indicators.py・app.py: 負の相関の組（`TRADEOFF_GROUPS`）と、条件ヒット馬での「2〜10倍未満だけの消し候補」の除外（`tradeoff_keshi`。行数を画面の上に）
- shisu/drill.py・app.py: `/entries/drill`（master から集計と同じ規則で取り出す・TARGET 貼付用のレースID・コア分布サマリー・明細・CSV）
- shisu/notes.py・app.py: 危険条件メモ（`condition_notes.json`・`POST /entries/note`・条件ヒット馬と WEDGE 一覧の「危険条件メモ / 調査」の列）。`.gitignore`・`.dockerignore` にメモのファイル
- どれも DB・master を作り直さずに効く（区分は画面で判定・逆引きは今ある master を読む・メモは DB の外）
- 検証（上限ゲート・負の相関・逆引き・メモの4領域を指摘役が再現つきで確認し、反証役が再確認。1回目は利用上限で途中まで、2回目で4領域とも完了）の指摘を反映:
  - 上限ゲート・負の相関: 製品コードの欠陥の指摘は無し（前の版との突き合わせ 40万件で、変わったのは「末尾が下位で 絶対軸・軸候補・相手候補 だった行」が区分なしになった分だけ・消し候補は不変。1行版・ベクトル版・SQL 版の不一致0）。途中で見つかった pandas 3 の読み取り専用配列への代入（`classify_frame`）は修正済み。テストの穴4件を反映（SQL 版が列名を無視する変異・除外のあとの決め直しでカットを外す変異・カットで隠れた軸を軸ヒットに数える変異・同じ指数の消しを除く変異が通っていた）
  - 逆引き（集計との突き合わせは 5,094 条件で件数・1着・3着以内・平均着順の不一致0）: 空の master（0バイト・中身が空の gzip）で 500 → 理由を出す（`EmptyDataError` も `DrillError` に）、単勝オッズ inf の行があるとまとめが 500 → 50倍以上の帯に、「戻る」でラジオの選択だけが戻り、欄は全件のまま（凡走のつもりで全件を TARGET に貼ってしまう）→ 表示のたびに選択へ合わせ直す（Chromium で確認）。テストの穴（覚えている結果のキーからオッズ帯・master の更新時刻を外す変異・明細の上限を外す変異が通っていた）
  - メモ: `POST /entries/note` が Host を見ていなかった（DNS リバインディングでメモを消せた。/ingest の POST は 403）→ `allowed_host()` と条件の長さの上限、片方だけのサロゲートを含むファイルで3画面と保存が 500 → 読めない形として断る、メモのファイルが必ず 0600 になった（`mkstemp`。Docker の root で書くと Linux のホスト側で読めない）→ umask／前のファイルの権限、テストの穴（戻り先の URL にフォーム全体を使う変異・「該当なし」の行の幅・置き換えでない直接の書き込み・成績表のリンクの別タブ・保存できない理由の表示）
- テスト: test_drill.py を追加、test_core・test_views・test_pipeline に追加。586件

### 2026-11-14（2件目） 軸候補の最低件数 10 → 20・消優勢／要注意の基準
- shisu/classify.py: `AX_CNT` 10 → 20（消し候補と同じ基準）。絶対軸（10）・希少条件（軸候補 6 など）・高回収率（30）・相手（30）はそのまま。1行版・ベクトル版・SQL 版・説明の文は同じ定数を読むので、変えたのは定数と説明だけ
- app.py: `danger_level` を4つの値（絶対軸の行数・軸の合計の行数・消し(2〜10倍未満) の行数・その指数数）で判定する形に。消優勢 = 絶対軸なし・消し3行以上・2指数以上・軸の合計より多い、要注意 = 軸あり・消し2行以上・軸の合計以上（絶対軸のある馬はここまで）。前の比率の定数（`KESHI_*_RATIO`）は廃止。表の上の説明の文も新しい基準に
- 影響: 画面を開くたびに判定するので、DB を作り直さなくても app の再起動で効く。集計の設定のハッシュは変わる（§9 の36）
- 検証（区分の下限・消優勢の判定の2領域を指摘役が再現つきで確認し、反証役が再確認）: 製品コードの欠陥の指摘は無し（前の版との突き合わせ 16万件 × 4オッズ帯で、変わったのは「希少条件でない・件数10〜19・軸候補のもとの条件」の行が区分なしになった分だけ。1行版・ベクトル版・SQL 版の不一致0。`danger_level` は依頼の式を別に書いたものと 4,914通りで一致。ページの2つの表・4区分の表のバッジ・見出しの頭数は、絞り込みや表示の上限に依らず一致）。テストの指摘2件を反映: 件数12の軸候補の偽の行を使っていた別のテスト（`_fake_hit` が作った行の区分を確かめていなかった → 確かめるように）、判定に「全部のオッズ帯の消しの行数」を渡す変異が通った（前の版のテストは落としていた）→ 2〜10倍未満の消しが1行・ほかのオッズ帯の消しが2行の馬に何も付かないことを確かめる
- テスト: test_core・test_views・test_pipeline を更新・追加。554件

### 2026-11-14 照合（xjoin）のメモリ・オッズ帯 odds10u を 2〜10倍未満に
- 背景: 3,531 family（4,868通り）・34指数の DB で `build_db.py --entries-only --mining-only --indicators 安井_…` が、`[entries] loaded` の直後（照合中）に Docker の OOM（Exit 137）で止まった（ユーザーの実運用）。odds10u（10倍未満）は単勝1倍台の馬が入って勝率・複勝率が上振れしていた
- build_db・shisu/matching.py: 照合のヒット行を1行ずつ受けて 5,000行ごとに INSERT（`iter_hit_rows`・`insert_hits`。全部の照合と一部の作り直しで共通）、照合のキャッシュ `HitCache` は入れるたびに件数（2万）・行数（5万）の上限を見る（旧版は1頭を回し終えてから件数だけを見ていた）、出馬表を読んだあと照合の前に `gc.collect()`。出力は行の順まで同じ。合成のストレス DB（4,635 family・34指数）で最大 RSS 1,555MB → 126MB・92秒 → 71秒
  - 原因の切り分け: メモリを使っていたのは Python 側（1頭ぶんの dict のリストと、1頭の途中で膨らむキャッシュ）。「DELETE のあと同じトランザクションで大量に INSERT」は SQLite が WAL ファイル（ディスク）に書くのでメモリのピークには効いていなかった → 途中で止まっても前の照合結果に戻る1つのトランザクションはそのまま
- shisu/odds.py: odds10u = 単勝 2.0倍以上 10.0倍未満（`ODDS_BREAKS = (2.0, 10.0, 50.0)`・表記「2〜10倍未満」・モード名はそのまま）。1.9倍以下の馬は normal だけ。設定のハッシュが変わるので、次の aggregate は `--resume` でも全部の master を作り直す
- 表記と中身の食い違いを知らせる仕組み: 成績表の印に `odds_breaks` → build_db が `__build_info__` の `odds_breaks` に残す → 画面は今の境目と違う DB（前の版で作った DB はすべて）で、オッズ帯を出す4画面に注意書きを出す
- app.py・shisu/classify.py・Readme.txt: 表記を「2〜10倍未満」に（引数 `cut_low_10u`・モード名・並びは変えていない）。Readme.txt の毎週のルーティンと当日更新コマンド（main で追記された分）はそのまま
- 検証（照合のメモリ・オッズ帯のデータ側・画面側の3領域を指摘役が再現つきで確認し、反証役が再確認）の指摘を反映: 成績表だけを作り直した（`build_db --mode foragg`）あと、オッズ帯の注意書きは消えるのに、条件ヒット馬・WEDGE 一覧は前の区切りの照合結果（xjoin）を出し続けた → 照合結果の記録が今の成績表の印と違うとき、この2画面に「古い照合結果」の注意書き（`xjoin_note`）。新しいテストが逐次の INSERT を確かめていなかった（1頭ぶん・全部をためてから 5,000行ずつ入れる変異、`gc.collect()` を読む前に動かす変異でも通った。ためる変異はストレス DB で最大 RSS 778MB・3,291MB に戻る）→ 受け取って未 INSERT の行数を1行ごとに確かめ、読む → gc → 照合 の順を確かめる。製品コードの欠陥の指摘は無し（xjoin は全部・一部・ストレスとも旧版と行の順まで一致、途中で止めても前の照合結果と記録に戻る、集計は normal・odds10_50・odds50p が旧版と同じで odds10u は 2倍未満の馬を除いた master の旧版と同じ）。画面のその場照合が数千 family では旧版より約12%遅い点は、メモリを優先してそのまま（§9 の35）
- テスト: test_mining_only・test_core・test_aggstore・test_verified_fixes・test_pipeline に追加。539件

### 2026-11-13 条件ヒット馬の人気馬（10倍未満）の見分け
- 背景: odds10u では、複勝率・掲示板率が高いだけで回収値が70〜80の過剰人気の条件が軸候補・相手候補に大量に当たり、買える人気馬と切るべき人気馬が見分けにくかった（ユーザーの実運用）
- app.py（`/entries/hits` の表示・集計・絞り込みだけ。DB・xjoin・classify は変えていない）: 過剰人気カット（`cut_low_10u`。既定 ON）、消し(10倍未満) の行数と指数数の列（指数クロス好走馬・馬ごとの該当数）、同じ指数で軸と消しが出ているタグ `(消n)`、消優勢／要注意の判定とバッジ・行の色
- shisu/ingest（2026-11-12 の同時取得の検証で見つかった分）: 同時に実行している間の Ctrl-C（KeyboardInterrupt）が握りつぶされ、裏のスレッドが最後までサイトにアクセスし続けて「失敗した取得元」として終わっていた → 中止を伝えて止まるのを待ってから、そのまま上に伝える（スレッドを起こしている最中の割り込みも同じ。CLI には中止の合図が無いので内部に持つ）。失敗した取得元のトレースバックを、取得元の名前の付いた1つのメッセージに
- 検証（集計・画面・同時取得の3領域を指摘役が再現つきで確認し、反証役が今の作業ツリーで再確認）の指摘を反映: 上の Ctrl-C とログ、カットした odds10u の行と重なる「通常」の行（回収値100以上のものも）を出さないことは、指摘役と反証役で見方が割れたので、趣旨（過剰人気の条件を出直させない）に合うほうを仕様として明記、選択肢に無い `xmin`（URL を手で書いた）がフォームの送り直しで 1 に戻る → 効いている値を選択肢に足す（`min_choices`）、テストの穴（動いている取得元への中止・ジョブ画面と run_ingest のつなぎ）
- テスト: test_views.py・test_pipeline.py・test_ingest.py に追加。534件

### 2026-11-12 週末の実戦運用に向けた8点（同時取得・差分更新・区分の基準・画面）
- /ingest・ingest.py: 選んだ取得元を取得元ごとのスレッドで同時に実行（サイトごとの間隔はそのまま。ログは取得元の名前つき・`--sequential` で順に）。ジョブ画面は「済み k/n ／ 実行中（同時に取得）: …」
- calc_entry_hensachi・build_db: `--indicators`／`--mining-only`（馬体重発表後のマイニング確定値の差分更新。統合ファイルの タイムM・対戦M の列と xjoin のその2指数の行だけ）。`process_df`・`attach_ext` の列の組み立てを1回の `pd.concat` に（PerformanceWarning の解消。出力は同じ）
- shisu/classify.py: 高回収率の基準（odds10u・odds10_50 の軸候補）と、希少条件（G1・重賞・準OP・OP・芝長・ダ長）の最低件数の緩和。1行版・ベクトル版・SQL 版とも結合データを見る（呼び出し側: aggstore のコンパクト化・screen_families・app）。閾値の一覧 `rule_settings()` を集計の設定のハッシュに
- app.py: 条件ヒット馬の冗長な「通常」の行の除外・並びと表示上限で通常を最後に・10〜50倍/50倍以上の軸の行を赤字・表の独立スクロールと見出し固定・指数クロス好走馬。出馬表に指数クロス（順位3位以内・偏差値61以上が複数の指数で同時）。WEDGE 一覧の指数・パターンの絞り込みを折りたたみにして「馬 × 指数」を上に（列と計算はそのまま）
- shisu/ext_indices.py: 名前に border の付くファイル（StridePlus の st_border・siagari_border）を読まない
- 検証（区分・差分更新（calc）・差分更新（DB）の3領域は指摘役が再現つきで確認。同時取得・画面の2領域と反証役は利用上限で未完了のため、テストとブラウザでの確認だけ）の指摘を反映:
  - `--mining-only`: 出馬表のレースから馬が消えていても古い行と古い順位が残った → 全部の計算に切り替える。build_db は、騎手の乗り替わり・馬の増減・ほかの指数の順位が変わっていても タイムM・対戦M の行しか作り直さなかった → 照合結果ごとに「何から作ったか」の記録（`__build_info__`）を持ち、合わなければ全部の照合。記録は照合結果と同じトランザクションで書く（途中で止まった実行のあとの打ち直し・`--mode foragg` だけ回したあとでも、古い照合結果を残さない）。空の `--indicators` が黙って全部の作り直しになった → 終了コード2
  - 条件ヒット馬の同点の並びが xjoin の行の順に左右され、差分更新のあとは表示の上限で出る行が変わった → 指数・family・結合データ・テーブル名まで並びを決める
  - ベクトル版の希少条件の判定が、改行で終わる結合データで1行版・SQL 版とずれた（正規表現の `$`）→ SQL 版と同じ「×で挟んで部分一致」に。1行版とベクトル版で key と 結合データ の優先も同じに
  - テストの穴: コンパクト化が希少条件を見なくても通っていた（合成データの希少条件の行が全部 layer A）・画面の区分が結合データを渡さなくても通っていた・差分更新のあとの画面の中身を見ていなかった → それぞれ落ちるテストを足した
- テスト: test_mining_only.py・test_views.py を追加、test_core・test_aggstore・test_ext_indices・test_ingest・test_pipeline・test_verified_fixes を更新。510件

### 2026-11-10 成績表の持ち方（master ごとの1ファイル・unified_all への一本化・コンパクト化）
- 背景: 16GB RAM・500GB SSD の MacBook Air で 1,655〜4,868通りの family × 33指数 × 4オッズ帯を集計したら、agg_csv に数十万個の CSV（140GB 超・数億行）ができて SSD とメモリが尽き、Docker エンジンが止まった（ユーザーの実データ）
- aggregate: 出力を master 1本につき1ファイル `agg_master_{地域}_{指数名}.csv.gz` に（family, odds_mode, rank_scheme, indicator, region, テーブル名 の列。テーブル名は旧来と同じ）。成績表を1つずつ書き足す（メモリに持つのは1つだけ）。`--compact`（既定 ON。`--no-compact`・`--compact-min-count`・`--compact-min-wedge`）で区分・WEDGE の上位・layer A・件数30以上の行だけを書く。並列は master を1本ずつ渡し、workers × 2 本ごとに子プロセスを作り直す（`shisu/parallel.py`。replace と共通）。`--resume`（agg_settings.json で設定を確かめて、済んだ master を飛ばす）。進み具合を1本ずつ出す
- build_db: 1表ずつのテーブル `foragg_*` を作らず、agg_master（と旧来の foragg_*.csv）を少しずつ読んで unified_all に直接入れる。`unified_all__build` に作ってから入れ替える。__tables_meta__ はテーブル名ごと、__facets__ は読みながら集める。前の版の1表ずつのテーブルは消す
- app: 「集計表ごと」を常に unified_all の同じテーブル名の行から（1表ずつのテーブルは使わない。前の版の DB でも表示は同じ）
- shisu: `aggstore.py`（保存形式・コンパクト化・読み込み。`prepare_agg_frame`・`estimate_percent` を build_db から移した）、`parallel.py`、`classify.classify_frame`（ベクトル版）、`naming` に agg_master の名前と速い旧来名の解析
- 独立レビュー（再現つき）の指摘を反映: `--resume` が設定を変えた実行の途中で止まったあとに前の設定の成績表を飛ばして混ぜた（agg_settings.json を毎回書き直していたため）→ 成績表ごとの書き終わりの印（設定のハッシュ・master の大きさと更新時刻・ファイルの大きさ）で判定し、合わないものは作り直す。書き終わりに fsync。途中で切れた成績表で build_db が例外で止まった → そのファイルを飛ばして [ERROR]。全部の master が失敗したときに「replace を先に」と出た。`--compact-min-count 0` で下げたのに残る行が減った → `none` で外し、0 以下は弾く。前の版の DB の1表ずつのテーブルが数十万本あると消すのに何時間もかかる → 画面は常に unified_all から読み、5,000本を超えたら消さずに WARN。WAL の記述の訂正と切り詰め
- 2回目の検証（修正分。3つの観点で探して反証役が再現。確定12件）の指摘を反映: master の読み込みで失敗すると前の成績表と印が残り build_db が黙って使った → 読む前に消す。印の master の大きさ・更新時刻を集計のあとに取っていた（集計中に replace が作り直すと古い結果が「済み」になる）→ 読む前に取り、変わっていたら印を書かない。設定のハッシュに区分の閾値（classify.py）と scipy の有無が無かった。build_db が先頭から壊れたファイル・gzip でないファイルを黙って飛ばしていた → [ERROR]。壊れた agg_master の代わりに同じ master の旧来のファイルを使っていた → 使わない。`--prune` で前の版のテーブルを残したとき集計表の一覧（__tables_meta__）を全部消していた → `--prune` は成績表を触らない。旧来のファイルとの混ざり・master の作り直しを WARN。`--resume` の説明（ヘルプ・ドキュメント）と画面の説明・終了コードの記述の食い違い
- 3回目の検証（2回目の修正分。確定7件）の指摘を反映: 印の無い agg_master しか無いと build_db が警告しなかった（旧来のファイルを印の無いものに数えないよう条件を狭めたため）→ 旧来のファイルは別に数えて別の WARN。0行で終わった master（印だけ）の旧来のファイルを使っていた → 使わない。「master を作り直した」の WARN が `--in-dir` で集計したとき・master をコピーしたとき（更新時刻が変わる）に誤って出た → 印に読んだ場所と中身の指紋を残して比べ、更新時刻は比べない（`--resume` も同じ）。オッズ帯の境目・EDGE の表が設定のハッシュに無かった。印を書かなかった master の [OK] 行の追記が年度の窓のまとめを壊した。`--prune` と区分の閾値の手順の記述
- テスト: `tests/test_aggstore.py` を追加。個別の成績表ファイルを読んでいたテストは `read_agg_tables` で同じ形に分けて読む（件数を厳密に比べるテストは `--no-compact`）。pipeline の fixture は既定のコンパクト化で全段を通す。テスト 472件

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
