# Trend Ahead — 事前予測履歴・収集安定性の監査（2026-11-17）

## 今回の実測で判明した取りこぼし

2026-11-17 22:10 JSTの日本代表対パラグアイ戦（JFA一次情報）は、
`news_sports` RSSで関連記事が2026-11-17 06:00 JSTに観測された一方、
`data/upcoming.json`の公式予定には登録されていなかった。
出典：https://www.jfa.jp/samuraiblue/20261117/tv.html

この事実から、**ニュースに登場したこと**と**予定として事前に知ることができる**ことは別指標とする。
手動でJFA公式確認した試合を予定に追加（ニュース検知を的中扱いしない）。
今後の同種漏れは公式日程の取得元拡充・優先審査の対象。

## 事前予測のスナップショット

`forecast_ledger.py issue` は、実際に成功したGoogle TrendsのRSS収集の後で起動する。
同一トピックの推定検索量が**30分以上・6時間以内**を隔てて2回以上観測され、
最新観測の実時刻が直前の収集時刻と一致した場合にだけ、3時間先の増加仮説を発行する。
過去のデータから過去時刻の予測を捏造しない。

- 凍結項目：発行時刻・対象`event_id`・観測時刻・観測下限値・成長比・モデル版・上昇予測真偽値
- 上昇ルール：現在値が前回比+100以上、かつ25%以上なら真。これは**既存の単純な比較基準**でありAIモデルではない
- 同じ`event_id`には直近3時間に予測済みなら重複発行しない
- 出力：`data/predictions/YYYY-MM-DD.jsonl`（GitHubの収集コミットとともに追記保存）
- 収集が古い、失敗した、履歴が足りない場合は予測0件。絶対に代替の的中を発生させない

`forecast_ledger.py evaluate` は各スナップショット発行時点を起点に3時間後±45分のRSS推定値を照合する。
未来の該当観測がなければ`missing_future`、評価期間前は`pending`として扱い、
**不正解としては数えない**。
評価可能な件数50・7日・正例5未満では的中指標は`null`のまま。
この評価はあくまで同じRSS内の変化の検証で、独立した社会的な流行の実績とは異なる。

## 429への配慮

`collector.py` は情報源単位で`data/source_health.json`に429の連続回数と再試行予定時刻を残し、
連続429後の再取得を1時間、2時間、4時間、最大6時間の範囲で延期。
取得元が明示的にレート制限している場合に即時リトライを繰り返さず、
健全な情報源の収集は継続する。429以外のエラーは既存どおりエラーとして記録。

## 収集稼働率

`collection_health.py` は直近24時間のGitHubコミット済み収集ログから
実行間隔、最後の取得時刻、429発生、スキップした情報源を報告する。
設定は30分間隔で目標48回/24時間だが、GitHub Actionsのcron起動自体にはSLAがない。

**重要**：コミットされた`data/runs`だけでは、そもそもジョブが起動しなかったのか、
起動後にpushに失敗したのかを区別できない。原因特定にはActions実行履歴を必ず確認する。
目標の取得成功率を達成したと書くには連続48時間の実測が必要。

## コマンド

```bash
python -m unittest discover -s tests -v
python collector.py
python forecast_ledger.py issue --data-dir data
python forecast_ledger.py evaluate --data-dir data --output reports/forecast-evaluation.json
python collection_health.py --data-dir data --output reports/collection-health.json
```

`Collect trend candidates` が収集・予測履歴をコミットする。
`Prepare research and outreach review pack` のArtifactに
`forecast-evaluation.json`、`collection-health.json`を含める。
公開サイトのスコアリング・SEO設定・通知はこの変更で切り替えない。

## 続く改善

1. JFA公式日程など重要な公的情報源を安全に候補発見対象へ追加し、重複・中止・時刻変更を人が審査
2. Google News検索を固定キーワードから重要イベントと固有名詞を拾える探索へ拡張
3. 予測正解ラベルを独立した外部情報に広げ、単純基準との差を時系列分割で検証
4. 収集失敗・公開遅延の原因をActionsログで監査し、必要なら別スケジューラの安全な冗長化を検討
