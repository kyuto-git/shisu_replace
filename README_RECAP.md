# README_RECAP

開発再開時の「思い出し用」要約。詳細な仕様は **CLAUDE.md** を参照（コードが正、CLAUDE.md はコードに合わせて更新）。

更新日: 2026-11-10

## 流れ

```
raw_csv/（追加分の基本CSV）─ replace の最初に統合 ─> csv_files/全場_タイムM.csv・全場_対戦M.csv（重複は後優先・最大10年）
raw_csv/（列パッチ: レースID(新) と前走の列だけ・指数 列なし）─ 統合のあとに 全場_タイムM・全場_対戦M の両方へ横結合（空でない値で上書き）
csv_files/（★6タブの基本CSV {地域}_タイムM / {地域}_対戦M。従来の {地域}_{指数名} も可）＋ ext_indices/（外部指数CSV群）
           ─ replace_csv_ver3.py ─> master_csv/（1頭1行。キーの部品・22条件・偏差値・順位）
           ─ screen_families.py（推奨）─> screening_report.csv / selected_families.json（使う family）
           ─ aggregate_stats_ver2.py ─> agg_csv/agg_master_{地域}_{指数名}.csv.gz（master 1本につき1ファイル。
                                      family × s1/s2 × オッズ帯の成績表を縦に並べる。既定でコンパクト化）＋ families.json
           ─ build_db.py ─> racing.db（unified_all, __tables_meta__, __families__。1表ずつのテーブル foragg_* は作らない）
entries_in/（マイニング値だけの出馬表1本でよい）＋ ext_indices/ ─ calc_entry_hensachi.py ─> entries_out/
           ─ build_db.py --mode entries ─> racing.db（entries_*, entries_xjoin_*）
app.py（http://localhost:8000）: 横断検索 / 出馬表 / 条件ヒット馬 / レース別 WEDGE 一覧 / 指数の取得（/ingest）
ingest_in/ ─ ingest.py（または /ingest）─> ingest_out/実行日時/（Ω・吉馬6・かよちん・keibaAI・ラップ6・安井 → TARGET 取込用CSV ＝ ext_indices の形式A。一隊は廃止）
           ─> ext_indices/ にもコピー（既定。同じ名前は馬ごとに足し合わせ。--no-ext-copy で止める。ラップ6は台帳に未登録なのでコピーしない）
```

正規化・22条件・family・キー・偏差値・分類・照合は共通パッケージ `shisu/` にある。**キーに関わる変更は必ず `shisu/` で行う**（過去側と出馬表側で同じ関数を使うことで一致を保証している）。

## よく使うコマンド（プロジェクト直下）

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
docker run --rm -v "$(pwd)":/app csv_processor_shisu python ingest.py --sources omega,kichiuma,kayochin
```

## 要点

- 外部指数の結び付け（`shisu/ext_indices.py`）: 形式A（18桁ID,値: Ω・かよちん・keibaAI・吉馬6・安井8）/ B（旧10桁ID,値,順位: StridePlus 12。ファイル名の接頭辞）/ C（12桁レースID と (値,順位) の組: Gold）/ D（旧8桁レースID と値: BC・ZI）。廃止: カツラキ・HPRTV5C・成績ST指数・一隊・33rap・上がり1F/2F
- 順位帯（指数順位・前走着順・前走人気・前走上がり3F順で共通）: s1 = 1 / 2~3 / 4~5 / 6~9 / 10~、s2 = 1~3 / 1~5 / 6~（重なりあり。両方のキーを作る）
- 22条件（`shisu/conditions.py`、マスタは `shisu/data/`）: 時期・輸送距離・間隔帯・叩き・R値帯・直線帯・初角帯・CFS帯・F係数帯・今走/前走クワドラント・左右/左右遷移・距離帯・坂・斤量帯・斤量増減・牝馬限定・距離増減・前走着順/人気/上り3F順/脚質/R印3
- family（`shisu/families.py`）: 部品は COURSE・重量種別・クラス名・枠番グループ・騎手・芝・ダ・場所・距離・芝(内・外)・22条件。初期セット49の型（A 15・B 9・C 10・D 15 → 83 family・129通り）。`selected_families.json` があれば置き換え（集計・DB・照合・画面）。初期セットのままだと成績表・DB は旧版の約8〜9倍
- オッズ帯: 通常 / 10倍未満（odds10u）/ 10〜50倍（odds10_50）/ 50倍以上（odds50p）
- 推定値: Jeffreys 事前の事後分布の下側10%点（複勝率は頻度20%以下なら上側90%点）。DB の 勝率_val〜3着率_val は推定値、掲示板率_val は頻度。率はすべて 0–100
- 条件ヒット馬: 消し → 絶対軸 → 軸候補 → 相手 の順で最初に当てはまった区分（閾値は `shisu/classify.py`）。screen_families の基準B はこの判定のキー数（既定: 絶対軸＋軸候補 25 以上、または絶対軸が1つでも）。基準A は groups_ge10・coverage・wedge_spread・fuku_spread（既定20%ポイント）を満たす wedge_spread 上位100 か adj_wedge_spread 上位100。`--from-report` で採点し直さずに選抜だけやり直せる。候補は 骨格（コース不問〜COURSE）× 条件28種 × 因果トリオ（default 2,098通り・all 7,557通り。master 1本30万行で 1.4時間・5時間）
- WEDGE = WIN（決定力）+ EDGE（オッズから見た期待着順を上回った割合）。adjusted_WEDGE は年10件以上の年すべて（最大10年）を新しい年ほど重い指数減衰（0.65）で加重
- 外部指数の取得は `shisu/ingest/` と `shisu/yasui_extractor.py`（安井 8指数。指数ごとのフォルダ。ベースCSV未指定でも ingest_in/base/・entries_in/ から該当日を探す。出馬表用の entries_in/yasui_*.csv は廃止）
- 出馬表の外部指数: 出馬表に値のある指数は出馬表の値（ファイル・レースごとに判定）、無い・0 だけのレースは ext_indices から過去側と同じ関数（`ext_indices.attach_filled`）で付ける → 同じ馬なら master と順位・偏差値が一致
- レース単位（16桁）の レースID(新) は replace が馬番を付けて18桁にする（旧版の band 系の件数膨張の再発防止。tests/test_band_regression.py）
- family・集計条件だけ変えたときは aggregate → build_db だけでよい（master はそのまま）
- 馬体重発表後のマイニング確定値: entries_in の出馬表を上書きして calc_entry_hensachi.py --merge-only --clean → build_db.py --entries-only
- 成績表のコンパクト化（aggregate の既定）: 4区分（絶対軸・軸候補・相手候補・消し候補）・WEDGE か adjusted_WEDGE ≧0.70・layer A・件数≧30 のどれかの行だけを書く（区分と高 WEDGE は1行も落とさない）。全部の行は `--no-compact`
- aggregate の --workers は master を1本ずつ渡し、workers × 2 本ごとに子プロセスを作り直す。--resume は agg_settings.json と設定が同じときだけ、済んだ master を飛ばす
- 画面の「集計表ごと」は unified_all のテーブル名が同じ行（前の版の DB の foragg_* テーブルがあればそこから。同じ結果）
- replace の --workers は子プロセスを4件ほどで作り直す（workers × 4 件のまとまりごとに新しいプール。OOM・BrokenProcessPool 対策。止められたファイルは [ERROR] にして残りを続ける）
