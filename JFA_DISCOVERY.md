# JFA日本代表公式試合日程の自動発見（2026-11-17）

公式日程出典：`https://www.jfa.jp/samuraiblue/schedule_result/2026.html`、
`https://www.jfa.jp/samuraiblue/schedule_result/2027.html`。

## 何が変わるか

`.github/workflows/research-outreach.yml` の日次実行で
`event_discovery.py` が年別JFA公式スケジュールを他の公式予定と一緒に探索します。

- 日本代表戦の開催日・大会名・対戦相手を**審査候補**に記録
- 日付は公式に明記された日本時間の日付だけ。キックオフ・時刻は推測しない
- 対戦相手が`未定`なら「対戦相手未定」と表示
- `中止`、`延期`等は**新規予定候補に載せず**、別の公式変更確認リストへ登録
- JFA公式日程の試合結果が付いた開催済み試合は候補から除外
- 既に公開済みの同日同対戦相手の予定は同一と判断し、重複登録しない
- 年をまたぐ大会開催期間は単一の試合日と誤認しない
- スケジュールに90日以内の予定が無いだけで取得失敗扱いにしない
- 取得不能・HTML変更なら編集者に`no_parseable_items`を示す

現在の自動発見は2026・2027年のSAMURAI BLUE（男子A代表）のみです。
2028年以降の年次追加、女子・年代別代表、放送開始時間の自動抽出は未実装。
将来の公式ページの見た目やrobots設定によっては抽出できない場合があります。

## 公開までの境界

自動取得は**未公開候補の作成まで**です。
データは毎日のワークフローのArtifact
`reports/event-candidates.json`、`reports/event-review.md`
および`reports/event-changes.*`に出力されます。

**重要：自動発見時点では`data/upcoming.json`の既存掲載・日付・`verified_at`を
書き換えません。** 人がJFA一次情報を再確認して採用するまでは公開しません。
「試合の存在を予定として発見」と「3時間先の検索量予測」は別の評価です。

## テスト

`python -m unittest discover -s tests -v`

`tests/test_jfa_schedule.py` で、11月17日のパラグアイ戦、
12月30日の中止、2027年のアジアカップ延期、対戦相手未定、
翌年日程、既存掲載との重複、HTML抽出失敗を合成HTMLで検証します。
本番JFAページ取得可否・実際の運用実行はGitHub Actions側のレポートで確認が必要です。
