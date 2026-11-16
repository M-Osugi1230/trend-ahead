# 公式YouTube配信予定の取得（v1.1）

## 現在の状態

**実装済み、APIキー未設定・チャンネル未登録のため実データ取得は未稼働です。**

- [`youtube_upcoming.py`](youtube_upcoming.py) はYouTube Data APIの`search.list`で`eventType=upcoming`、`videos.list`で`liveStreamingDetails.scheduledStartTime`を確認します
- 公式と確認したチャンネルIDだけを対象とし、実際の配信URL・予定開始時刻を審査キューに保存します
- `YOUTUBE_API_KEY`がなければ**外部へのAPI通信を一切行わず**`status=not_configured`で正常終了
- `data/youtube_channels.json`は当初`[]`。未承認チャンネルからの取得は禁止
- 候補は`needs_review`であり、公開予定には自動反映しません。誤配信・変更・中止・遅延は編集審査が必要

## 公式APIの使用準備（有効化する場合のみ）

1. Google Cloudで自分が管理するプロジェクトを作成し、**YouTube Data API v3**を有効化する
2. サーバー用APIキーを作成し、APIの制限をYouTube Data API v3に設定する
3. GitHubの `M-Osugi1230/trend-ahead` → Settings → Secrets and variables → Actions → **New repository secret** で `YOUTUBE_API_KEY` を登録。**キーをチャットやGitHubのコード・Issue・ログに書かない**
4. 対象チャンネルがブランド・配信者の**実際の公式チャンネル**だと確かめ、チャンネルID（`UC`から始まる24文字）を`data/youtube_channels.json`に追加
5. 各エントリに`official_confirmed: true`、`name`、`category`（配信・YouTuber／ゲーム／音楽・ライブ／映画・アニメ）を指定する
6. [GitHub Actionsの審査パック](https://github.com/M-Osugi1230/trend-ahead/actions/workflows/research-outreach.yml) を手動実行して結果を確認

設定の形（以下はサンプルのためチャンネルIDを実在のIDに置き換える必要があります）：

```json
[
  {
    "channel_id": "UCXXXXXXXXXXXXXXXXXXXXXX",
    "name": "確認済みの公式チャンネル名",
    "category": "ゲーム",
    "official_confirmed": true
  }
]
```

## 品質・費用・安全性

- 最大10チャンネル、1チャンネルにつき`search.list`最大1回＋候補があれば`videos.list`1回を1日1回の審査パックで実行
- APIの利用上限はGoogle Cloud管理画面で確認。2026年11月時点、検索リクエストは独立した日次枠で管理されています
- 動画IDと公式チャンネルIDが一致し、`liveBroadcastContent=upcoming`、予定開始時刻が今後30日以内である動画のみ対象
- 実際の配信開始済み・予定時刻未設定・別チャンネル・無許可チャンネル・日時が過去の動画は除外
- 原文の大量転載や無差別YouTube検索はしません
- キーを含むURLや例外メッセージはログに出さず、HTTPリダイレクトを禁止
- `reports/youtube-candidates.json`と`reports/youtube-review.md`をActions Artifactへ保存

## 公開への承認

`reports/youtube-candidates.json`を公式動画ページと突き合わせ、人が`source_confirmed=true`、`status=approved`、`reviewer`、`review_note`、`verified_at`等を記録した後だけ[編集承認手順](EDITORIAL.md)に従って公開します。

YouTube候補は予定開始時刻を持つため、`review_apply.py`は**UTC換算で同一時刻を要求**します。日付だけの終日予定に落としたり、予定時刻を確認なしに変更することは拒否されます。

公式資料：
- https://developers.google.com/youtube/data/reference/rest/v3/search/list
- https://developers.google.com/youtube/data/reference/rest/v3/videos
- https://developers.google.com/youtube/data/getting-started

**本機能はAPI接続が完了するまで利用者向け配信機能ではありません。** 既存のイベントサイト・RSS収集・ページ公開は独立して稼働します。
