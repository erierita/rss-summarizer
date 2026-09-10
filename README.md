# RSS要約システム — AWS学習用プロジェクト

技術記事のRSSを定期収集し、Bedrockで要約してSlackに通知するシステム。
案件で使う要素（Lambda / Step Functions / EventBridge / SQS / DynamoDB / S3 / CDK / Bedrock / IAM）を
1つの題材でひと通り触ることを目的にしています。

## アーキテクチャ

```
EventBridge (毎朝9時)
    ↓
Lambda: fetch_feed          RSSを取得し、記事URLを1件ずつSQSへ
    ↓
SQS (+ DLQ)
    ↓
Lambda: enqueue_worker      キューから取り出しStep Functionsを起動
    ↓
Step Functions (記事1件ごとのワークフロー)
    ├ Task   : fetch_article   本文を取得しS3へ保存
    ├ Choice : 文字数が閾値以上か
    ├ Task   : summarize       Bedrockで要約
    ├ Task   : DynamoDB PutItem (SDK直接統合 — Lambdaを介さない)
    └ Task   : notify          Slackへ通知
```

## 段階的に作る（重要）

**最初から全部作らないこと。** 1フェーズずつデプロイして動作確認しながら進めます。
各フェーズで `cdk deploy` して、マネジメントコンソールで結果を確認してください。

### Phase 1 — 土台（Week 1）
- [ ] `cdk init` からデプロイまで通す
- [ ] DynamoDBテーブル + Lambda 1個
- [ ] LambdaからDynamoDBに書き込む
- [ ] **学ぶこと**: CDKの基本、Construct、`grant_*`によるIAM権限付与、CloudWatch Logs

### Phase 2 — 非同期（Week 2前半）
- [ ] EventBridgeで定期実行
- [ ] SQSを挟む + DLQ
- [ ] わざと例外を投げてDLQに落ちるのを確認 ← **ここが一番の学び**
- [ ] **学ぶこと**: cron式、可視性タイムアウト、`ReportBatchItemFailures`、冪等性

### Phase 3 — ワークフロー（Week 2後半）
- [ ] Step Functionsでフローを組む
- [ ] Choice / Retry / Catch を入れる
- [ ] DynamoDBはSDK直接統合で書く（Lambdaを介さない）
- [ ] **学ぶこと**: ASL、入出力処理（`ResultPath`が最大の難所）、エラーハンドリング

### Phase 4 — 統合（Week 3）
- [ ] Bedrockで要約
- [ ] S3に本文保存
- [ ] Slack通知
- [ ] Mapステートで複数フィードを並列処理
- [ ] **学ぶこと**: Bedrock API、S3、外部API連携、並列処理

## セットアップ

```bash
# 前提: Node.js, Python 3.12+, AWS CLI設定済み
npm install -g aws-cdk

python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 初回のみ（アカウント/リージョンごと）
cdk bootstrap

# 差分確認 → デプロイ
cdk diff
cdk deploy
```

## 環境変数（cdk.jsonのcontextでも可）

| 変数 | 説明 | 例 |
|---|---|---|
| `SLACK_WEBHOOK_URL` | Slack Incoming Webhook。未設定ならログ出力のみ | `https://hooks.slack.com/...` |
| `BEDROCK_MODEL_ID` | 使用するモデル | `anthropic.claude-3-5-haiku-20241022-v1:0` |

Bedrockはリージョンとモデルアクセス許可が必要です。
マネジメントコンソール → Bedrock → モデルアクセス から有効化してください。

## コスト管理（必ず読む）

- **Budgetsで予算アラートを設定**してから始めること（月$5程度）
- EventBridgeの定期実行は放置すると動き続ける。使わないときは無効化
- 練習が終わったら `cdk destroy`
- Bedrockは従量課金。テストは記事数を絞る（`MAX_ARTICLES`）

```bash
cdk destroy
```

## つまずいたら

| 症状 | 見るところ |
|---|---|
| `AccessDenied` | IAMロールの権限。CDKの`grant_*`を書き忘れていないか |
| Step Functionsでデータが渡らない | `ResultPath` / `Parameters` の設定。実行履歴で各ステートの入出力を確認 |
| SQSのメッセージが繰り返し処理される | 可視性タイムアウト < Lambdaタイムアウト になっていないか |
| Lambdaがタイムアウト | デフォルト3秒。HTTP取得するなら30秒以上に |
| Bedrockが `ValidationException` | モデルIDとリージョンの組み合わせ、モデルアクセス許可 |

CloudWatch Logs と Step Functions の実行履歴が最大の情報源です。
エラーが出たら必ずログを読む習慣をつけてください。
