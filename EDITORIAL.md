# Trend Ahead｜公式イベントの継続発見・公開審査

更新：2026-11-16

## 現在の状態

- 公式の2情報源：東京ビッグサイト、幕張メッセ（公式スケジュール）
- 毎朝07:15 JSTに`Prepare research and outreach review pack` が一次情報のHTMLを取得して、確認候補を抽出
- 初回実行で、`2/2` 情報源成功・`25` 件の**未審査候補**を検出（[実行結果](https://github.com/M-Osugi1230/trend-ahead/actions/runs/43777206964)）
- 別途、元の公式サイトを人が確認した9件を`data/upcoming.json`に追加し、全体42件
- 「元の会場にある」だけでは時刻・チケット・実施変更を確認したことにはなりません。候補と公開情報は厳格に区別します

## 運用フロー

1. [Actions > Prepare research and outreach review pack](https://github.com/M-Osugi1230/trend-ahead/actions/workflows/research-outreach.yml) を開き、最新の成功Runを確認
2. **Artifacts** の `trend-ahead-research-outreach` を保存して解凍
3. `event-review.md`：見やすい編集チェックリスト。 `event-candidates.json`：機械可読候補（**未検証**）
4. 候補の主催者公式ページを直接開き、正しい開催日・時刻・参加条件・中止情報・重複を確認
5. 承認する場合は`approved-events.json`を**自分で**作成。自動生成キューに`verified_at`を付け足しただけで承認したことにしない
6. `review_apply.py`で候補ID・日付・公式URL・審査情報の妥当性を検証
7. **明示的に** `--apply` を付けた場合に限り`data/upcoming.json`を更新
8. GitHubのテストが成功した後、変更した公開データをコミットして自動デプロイ

## 承認マニフェストの例

架空データの例です。下記は実イベントではありません。実際に確認できた場合のみ置き換えて使用してください。

```json
[
  {
    "candidate_id": "実際のevent-candidates.jsonのID",
    "status": "approved",
    "source_confirmed": true,
    "title": "候補のタイトル（同じ内容）",
    "starts_at": "2026-12-15",
    "category": "季節・イベント",
    "description": "実際に公式ページで確認した内容（短文）",
    "official_url": "https://example.org/organizer/official-event",
    "verified_at": "2026-11-16T09:00:00+09:00",
    "reviewer": "editor_1",
    "review_note": "開始日・参加方法・中止情報を公式ページで目視確認"
  }
]
```

**日時は主催者が公開した正確なもの**を記載。開始時刻が不明なら`YYYY-MM-DD`の終日イベントとする。`verified_at`は審査した時刻であり、候補抽出時刻ではありません。

## 編集者コマンド

```bash
# まず審査（dry-run）。ファイルは変更されない
python review_apply.py --queue reports/event-candidates.json --approvals reports/approved-events.json

# すべての承認レコードが検証済みのときだけ明示的に公開データへ反映
python review_apply.py --queue reports/event-candidates.json --approvals reports/approved-events.json --apply

# 公開前の回帰テスト、情報の鮮度監査
python -m unittest discover -s tests -v
python event_audit.py
```

`--apply`しない限り、公開データに変更はありません。エラーのある承認マニフェストは**全件一括で適用不可**です。

## 情報源と制約

- [東京ビッグサイト公式イベント情報](https://www.bigsight.jp/visitor/event/)：会場の掲載であり、開催日時の変更・中止・未掲載の可能性がある
- [幕張メッセ公式イベントカレンダー](https://www.m-messe.co.jp/event/)：主催者の事情で内容変更の可能性がある
- 東京アニメセンターの`tokyoanimecenter.jp`は、GitHub Actionsのrobots取得がHTTP 403となったため自動巡回を取りやめ。手動閲覧可能であってもスクレイピング制限を迂回しない
- robots確認後、情報源ごとに**1日1回**、HTTPSの同一ホストのみ。応答1.2MB上限、タイムアウトあり。無差別クロール、認証回避、別ドメインへのリダイレクトはしない
- 観測元の掲載開始日を抽出するだけで、**開始時刻は推測しない**
- 利用者に有益な音楽・ゲーム・配信・一般参加可能な予定を優先。業界限定の展示会を機械的に大量掲載しない
- `noindex` は既定のまま。利用者からの実フィードバック・運営者情報・権利確認を経て正式公開を判断

## 次の拡張

- ほかのジャンルの公式一次情報源を権利・robots等を確認しながら追加
- 審査待ち候補の差分・取り消し・同名イベントの重複検出精度を改善
- ユーザーが求めるジャンルを実測したうえで収集優先順位を決める
- SNS原稿は`outreach.py`で下書きのみ自動作成。投稿先ルールや情報源を確認して人が発信


## 公式イベントの日時変更・掲載消失監視（v1.3）

毎朝のGitHub Actionsの[編集審査パック](https://github.com/M-Osugi1230/trend-ahead/actions/workflows/research-outreach.yml)では、**前回の公式掲載のスナップショット**と今回の掲載情報を比較します。

- `event-snapshot.json`：当日の掲載確認記録。既に公開済みで審査キューから除かれる予定も含む
- `event-changes.json`：差分の機械可読情報
- `event-changes.md`：日時変更の可能性・見えなくなった掲載・複数回開催の曖昧な差分・新着を整理したチェックリスト
- GitHub Actions実行ページのSummaryに、差分の件数と確認が必要かどうかを表示
- 過去のスナップショットはActionsキャッシュに保存。初回・古すぎる記録・取得失敗時は無理に比較しない

**「以前の一覧にあって今回なかった」ことは、中止・延期・販売停止が確定した証拠ではありません。** 掲載期限、一覧の件数上限、取得障害、主催者側のページ更新でも発生します。

### 編集者の確認順序

1. `event-changes.md`で差分がある公式ソースを確認
2. イベント主催者・メーカーの個別公式ページを開いて開催・放送・発売日時の最新情報を確認
3. 確認できたものだけ、適切に`data/upcoming.json`の訂正・補足を行う
4. 公式ページで確認できないものは`未確認`とし、**自動で中止扱い・削除しない**
5. 日付変更時も、確認しなかったのに`verified_at`を更新しない
6. テスト・監査・公開後のHTTP確認が成功したことを確認

遊戯王OCGの商品一覧は、実行環境からのアクセスで同一ホスト外のHTTP転送が発生したため自動巡回を停止しています。**アクセス制限を回避しない**方針です。公開商品情報の手動確認は別途可能です。
