# README_RECAP

開発再開時の「思い出し用」要約。詳細な仕様は **CLAUDE.md** を参照（コードが正、CLAUDE.md はコードに合わせて更新）。

更新日: 2026-11-05

## 流れ

```
csv_files/ ─ replace_csv_ver3.py ─> foragg_csv/（素材）, master_csv/, replace_csv/
           ─ aggregate_stats_ver2.py ─> agg_csv/（成績表）
           ─ build_db.py ─> racing.db（foragg_*, unified_all, __tables_meta__）
entries_in/ ─ calc_entry_hensachi.py ─> entries_out/
           ─ build_db.py --mode entries ─> racing.db（entries_*, entries_xjoin_*）
app.py（http://localhost:8000）: 横断検索 / 出馬表 / 条件ヒット馬 / レース別 WEDGE 一覧 / 指数の取得（/ingest）
ingest_in/ ─ ingest.py（または /ingest）─> ingest_out/実行日時/（Ω・吉馬6・かよちん・keibaAI・ラップ6・安井 → TARGET 取込用CSV。一隊は廃止）
```

正規化・キー・偏差値・分類・照合は共通パッケージ `shisu/` にある。**キーに関わる変更は必ず `shisu/` で行う**（過去側と出馬表側で同じ関数を使うことで一致を保証している）。

## よく使うコマンド（プロジェクト直下）

```bash
docker build -t csv_processor_shisu .
docker run --rm -v "$(pwd)":/app csv_processor_shisu python replace_csv_ver3.py --clean
docker run --rm -v "$(pwd)":/app csv_processor_shisu python aggregate_stats_ver2.py --clean
docker run --rm -v "$(pwd)":/app csv_processor_shisu python calc_entry_hensachi.py --merge-only --clean
docker run --rm -v "$(pwd)":/app csv_processor_shisu python build_db.py --fresh
docker run --rm -p 8000:8000 -v "$(pwd)":/app csv_processor_shisu python app.py
docker run --rm -v "$(pwd)":/app csv_processor_shisu python -m pytest
docker run --rm -v "$(pwd)":/app csv_processor_shisu python ingest.py --sources omega,kichiuma,kayochin
```

## 要点

- オッズ帯: 通常 / 10倍未満（odds10u）/ 10〜50倍（odds10_50）/ 50倍以上（odds50p）
- 推定値: Jeffreys 事前の事後分布の下側10%点（複勝率は頻度20%以下なら上側90%点）。DB の 勝率_val〜3着率_val は推定値、掲示板率_val は頻度。率はすべて 0–100
- 条件ヒット馬: 消し → 絶対軸 → 軸候補 → 相手 の順で最初に当てはまった区分（閾値は `shisu/classify.py`）
- WEDGE = WIN（決定力）+ EDGE（オッズから見た期待着順を上回った割合）。adjusted_WEDGE は年10件以上の年すべて（最大10年）を新しい年ほど重い指数減衰（0.65）で加重。横断検索の「まとめて」表示で全集計表を adjusted_WEDGE 順に並べられ、`/entries/wedge` で1レースの全馬・全キーを閾値なしで見比べられる
- 外部指数の取得は `shisu/ingest/`（Ω・吉馬6・かよちん・keibaAI・ラップ6指数。一隊はサイトの仕様変更で廃止）と `shisu/yasui_extractor.py`（安井サロン Excel → 安井_* 8指数。吉馬と同じく指数ごとのフォルダ。ベースCSV未指定でも ingest_in/base/・entries_in/ から該当日を探す）。出力は日付別の `レースID(新), ワーク1`（cp932）
- レース単位（16桁）の レースID(新) は replace が馬番を付けて18桁にする（旧版の band 系の件数膨張の再発防止。tests/test_band_regression.py）
- 集計条件だけ変えたときは aggregate → build_db だけでよい（素材は上書きしない）
