# Trend Ahead — Public MVP v0.3 (2026-11-15)

**明日の話題を、今日知る。**

「夜になって話題を知り、楽しむ機会を逃した」という課題を減らすサービスの公開前MVPです。日本の広いジャンルから候補を取得し、検知済み話題と公式に予定が確認できたイベントを区別して表示します。

> **重要：現段階は「未来の盛り上がりを予測するAI」ではありません。** 自動収集・データ保存・検索/保存・個人向けジャンル並べ替え・公式イベントの表示・カレンダー追加・共有用ページを備えた初期版です。

## できること

- Google Trends日本版＋Google Newsトップ・12ジャンルのRSS（14ソース）を取得する設計
- 日次`events`、日次`observations`、月次`runs`と`state.json`を生成。重複抑止・失敗記録
- 最近の観測結果を、情報種別と初回検知日時つきで掲載
- **興味ジャンル**を利用者のブラウザ内に保存。おすすめ順で関心ジャンル3枠に対し未知ジャンル1枠を挟み、視野を狭めすぎない
- キーワード検索、ジャンル絞り込み、お気に入り保存、個別話題の共有（個別ページはnoindex）
- 公式情報で時刻と出典を確認した予定5件（2026-11-15時点）、今週の予定ページ、個別/一覧ICS、RSS購読リンク
- データ収集が停止・失敗したときは、画面のビルド時刻とは独立して収集遅延を警告
- プライバシー・検知方法・使い方ページ、SEO安全設定

### まだできないこと

- 将来の話題化の確率予測
- スマホへのプッシュ通知/Telegram個人配信
- リアルタイムX・TikTok API連携、イベントの自動一次確認
- 複数端末の興味設定同期、外部解析サービスによる流入・継続率計測
- 実データRSSの継続稼働確認、一般公開後の利用者獲得検証

## 動作環境・最短実行

Python 3.12以上。コレクタ・Webジェネレータともに追加pip依存はありません。

```bash
cd trend-ahead
python -m unittest discover -s tests -v
python collector.py
python build_site.py
python -m http.server 8000 --directory dist
```

`collector.py`は外部接続が必要です。現在の開発環境では本番のRSS通信は検証できていません。`build_site.py`単体なら、収集未開始を明示し、確認済み予定のみでページを作れます。

## データと表示ポリシー

| パス | 役割 |
|---|---|
| `data/events/YYYY-MM-DD.jsonl` | 新規候補を初検知した日時・ソース・題名・原典リンク |
| `data/observations/YYYY-MM-DD.jsonl` | Google Trends観測履歴 |
| `data/runs/YYYY-MM.jsonl` | 取得成否・失敗理由 |
| `data/state.json` | 直近7日間の重複判定・再観測記録 |
| `data/upcoming.json` | 手動で公式確認した将来予定。出典URLと確認日が必要 |
| `dist/calendar/upcoming.ics` | 確認済みイベントをまとめてインポートできるICS |
| `dist/feeds/` | canonical URLを指定した場合のみ出力するRSS配信用ファイル |

RSSに掲載されても、内容の正確性や未来の話題化を保証しません。自動生成の個別話題ページをSEO対象にすることはありません。予定の古い情報は非表示になりますが、実際の開催内容は必ず公式案内で再確認してください。

## GitHub Pagesでの公開手順

**1.** 専用のGitHubリポジトリ `trend-ahead` を用意し、このフォルダの全ファイルをpushします。プライベートなデータやAPIキーは含めないでください。GitHub Pagesの条件はご利用プラン・リポジトリ公開設定で確認してください。

**2.** GitHubの Settings → Pages → Source で GitHub Actions を選択します。

**3.** Actions の `Test collector` を実行してテスト成功を確認します。その後 `Collect trend candidates` を手動実行し、実際のRSS接続、JSONL生成、ソース別エラーを確認します。

**4.** Actions の `Publish Trend Ahead` を実行します。カスタムドメインを使わない場合は `https://<GitHubユーザー>.github.io/trend-ahead/` が基準URLになります。カスタムドメインなら Actions variable `SITE_URL` にその完全なHTTPS基準URLを指定します。

**5.** 正式公開して検索インデックスを許可する前に、以下をすべて満たしてください。すべて満たしてから Actions variables で `PUBLISH_READY=1` および `OPERATOR_CONTACT` に公開用メールアドレスを設定します。`SITE_URL`のみではnoindexのままです。

- 実際のRSSの成功を異なる時刻で2回以上確認
- 運営者連絡先、プライバシー方針、コンテンツ利用条件を確認
- モバイル/PCおよび各種リンク・カレンダー動作を実機確認
- 独自ドメインやサービス名の重複・権利面を確認
- Google Search Consoleで所有権・sitemap・indexingを確認

**6.** 公開後は `GROWTH.md` と `METRICS.md` に従って検索流入・翌日再訪・取りこぼしを評価します。

GitHub Actionsのスケジュール実行時刻は保証されず、遅延・欠落する場合があります。データコミットは別ワークフローを自動起動しないため、公開ワークフローは毎時スケジュールも設定しています。

## よくあるトラブル

- **トレンド候補が0件**：初回収集の成否を `data/runs` で確認。取得できていなければ空表示が正常です。
- **ページは表示されるのに更新日時が新しい**：画面生成と収集成功は異なります。収集ステータス欄を確認してください。
- **サイト内リンクが404**：`SITE_URL` とGitHub Pagesのプロジェクトパスの一致、`dist` 配信設定を確認。
- **検索結果に出ない**：正式公開ゲートがデフォルトで閉じています。`PUBLISH_READY` と公開用連絡先を確認してください。
- **保存が消えた**：ブラウザ内の `localStorage` に保存しており、ブラウザデータ削除や別端末では同期されません。

## 公開準備・分析の別資料

- [`GROWTH.md`](GROWTH.md)：流入・活性化・再訪・紹介の設計
- [`METRICS.md`](METRICS.md)：イベント定義、データ品質、評価の手順
- [`tests`](tests)：ユニットテスト（追加パッケージ不要）

**公開状態（2026-11-15）：** `main` へのv0.3反映、GitHub Pagesデプロイ、公開URLのHTTP疎通テストは成功済みです（[サイト](https://m-osugi1230.github.io/trend-ahead/) / [公開実行ログ](https://github.com/M-Osugi1230/trend-ahead/actions/runs/43722098314)）。Google Trends・ニュースRSSの14/14ソース取得を異なる時刻で2回実施し、履歴データのコミットまで確認しました（[初回収集](https://github.com/M-Osugi1230/trend-ahead/actions/runs/43721780546) / [2回目収集](https://github.com/M-Osugi1230/trend-ahead/actions/runs/43722080142)）。収集成功後に公開を再実行する設定も確認済みです。**検索インデックス許可は未実施（noindexのまま）**。独自のAI予測・通知は未実装、長期安定性と実機UX・運営情報・法的表記の確認はこれからです。