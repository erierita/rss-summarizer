# RSS要約システム 解説資料

構築したシステムを理解するための資料です。
「何をしているコードか」だけでなく「なぜそう書くか」まで踏み込んで説明します。

案件（AWSサーバレス + セキュリティ運用ツール）で必要になる知識と対応させながら読んでください。

---

## 目次

1. [システムの全体像](#1-システムの全体像)
2. [CDKの基礎](#2-cdkの基礎)
3. [各サービスの役割と設定の意味](#3-各サービスの役割と設定の意味)
4. [Lambdaコードの解説](#4-lambdaコードの解説)
5. [Step Functionsのデータの流れ](#5-step-functionsのデータの流れ)
6. [押さえるべき5つの概念](#6-押さえるべき5つの概念)
7. [今回のエラーから学ぶ](#7-今回のエラーから学ぶ)
8. [用語集](#8-用語集)
9. [手を動かして確認する課題](#9-手を動かして確認する課題)

---

## 1. システムの全体像

### 処理の流れ

```
EventBridge（毎朝9時）
    │
    ▼
Lambda: fetch_feed
    │  RSSを取得し、記事URLを1件ずつメッセージ化
    ▼
SQS（ArticleQueue）────失敗3回────▶ SQS（ArticleDLQ）
    │
    ▼
Lambda: enqueue_worker
    │  DynamoDBで重複チェック → Step Functionsを起動
    ▼
Step Functions（ArticleWorkflow）
    ├─ Task   : fetch_article  本文取得 → S3保存
    ├─ Choice : body_length >= 300 か
    ├─ Task   : summarize      Bedrockで要約
    ├─ Task   : DynamoDB PutItem（SDK直接統合）
    └─ Task   : notify         Slack通知
```

### なぜこの構成なのか

各段階に意味があります。

| 要素 | なぜ必要か |
|---|---|
| **EventBridge** | 定期実行の起点。時刻を変えたければルールだけ直せばよく、Lambdaのコードは触らない |
| **SQS を挟む** | 記事が100件来てもLambdaを100個同時起動しない。失敗しても消えず再処理される |
| **DLQ** | 3回失敗したメッセージの退避先。原因調査に使う。無いと失敗が闇に消える |
| **Step Functions** | 「取得 → 判定 → 要約 → 保存 → 通知」の順序と分岐を宣言的に書ける。1つのLambdaに全部書くと、どこで失敗したか分からなくなる |
| **S3 と DynamoDB の分離** | DynamoDBは1アイテム400KBが上限。本文全体は入らないのでS3へ逃がす |

### 案件との対応

セキュリティ運用ツール（SOAR的なもの）を作る場合、構造はほぼ同じです。

| このシステム | セキュリティ運用ツール |
|---|---|
| EventBridge（時刻トリガー） | EventBridge（GuardDuty検知をトリガー） |
| SQS でバッファ | SQS で大量アラートをバッファ |
| Step Functions で記事処理 | Step Functions でプレイブック実行 |
| Bedrock で要約 | Bedrock でアラート内容の要約・分類 |
| DynamoDB に結果保存 | DynamoDB にインシデント記録 |

---

## 2. CDKの基礎

### CDKは何をするツールか

CDKは**AWSリソースを直接作りません**。CloudFormationのテンプレートを生成するツールです。

```
Pythonコード
   │ cdk synth（合成）
   ▼
CloudFormationテンプレート（JSON）
   │ cdk deploy
   ▼
CloudFormation がリソースを作る
```

`cdk synth` を実行すると `cdk.out/RssSummarizerStack.template.json` が生成されます。一度中身を見ておくと、CDKが何をしているか腑に落ちます。

```bash
cdk synth
less cdk.out/RssSummarizerStack.template.json
```

### Construct（コンストラクト）

CDKの構成要素です。3階層あります。

| レベル | 名前 | 例 |
|---|---|---|
| L1 | CloudFormationそのまま | `CfnFunction` |
| **L2** | **推奨。デフォルト値と便利メソッド付き** | `lambda_.Function` |
| L3 | パターン。複数リソースをまとめたもの | `LambdaRestApi` |

このプロジェクトは全部L2です。L2の利点は**`grant_*` メソッド**にあります。

```python
bucket.grant_put(fetch_article_fn)
```

この1行で、以下のIAMポリシーが自動生成されます。

```json
{
  "Effect": "Allow",
  "Action": [
    "s3:PutObject", "s3:PutObjectLegalHold",
    "s3:PutObjectRetention", "s3:PutObjectTagging",
    "s3:PutObjectVersionTagging", "s3:Abort*"
  ],
  "Resource": "arn:aws:s3:::rsssummarizer.../*"
}
```

手書きしていたら抜け漏れが出る量です。しかも**リソースARNまで自動で絞られる**ので、最小権限が自然に守られます。

### Stackのコード構造

```python
class RssSummarizerStack(Stack):
    def __init__(self, scope, construct_id, **kwargs):
        super().__init__(scope, construct_id, **kwargs)
        # ここにリソース定義を書いていく
```

- `scope` … 親（通常は `App`）
- `construct_id` … スタック内で一意な論理ID

リソース作成時の第2引数（`"ArticleTable"` など）も論理IDです。**これを変えると、CloudFormationは別リソースと判断して作り直します**。DynamoDBの論理IDを変えるとデータが消えるので注意が必要です。

### コマンドの使い分け

| コマンド | 何をするか | 使う場面 |
|---|---|---|
| `cdk synth` | テンプレート生成のみ | 生成物を確認したいとき |
| `cdk diff` | 変更セットを作って差分表示（実行しない） | **deployの前に必ず** |
| `cdk deploy` | 変更セットを作って実行 | 反映するとき |
| `cdk deploy --hotswap` | Lambdaのコードだけ直接更新 | 開発中の反復。**本番禁止** |
| `cdk destroy` | 全リソース削除 | 学習終了時 |

---

## 3. 各サービスの役割と設定の意味

### 3-1. DynamoDB

```python
table = dynamodb.Table(
    self, "ArticleTable",
    partition_key=dynamodb.Attribute(
        name="url_hash", type=dynamodb.AttributeType.STRING
    ),
    billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
    removal_policy=RemovalPolicy.DESTROY,
    time_to_live_attribute="ttl",
)
```

| 設定 | 意味 |
|---|---|
| `partition_key` | 主キー。この値でデータが分散配置される。**後から変更できない** |
| `billing_mode=PAY_PER_REQUEST` | リクエスト単位の課金。アクセスがなければほぼ0円。学習用に適する |
| `removal_policy=DESTROY` | `cdk destroy` でテーブルも消える。**本番では `RETAIN`** にして誤削除を防ぐ |
| `time_to_live_attribute="ttl"` | この属性にUNIX時刻を入れておくと、その時刻を過ぎたら自動削除される |

**パーティションキーの設計が最重要**です。DynamoDBはRDBと違い、後からキー構成を変えられません。「どういうクエリをするか」から逆算して決めます。

このシステムでは `url_hash`（記事URLのSHA256先頭32文字）にしました。理由は2つあります。

- 記事URLで一意に決まる
- **重複チェックに使える**（`get_item` で存在確認すれば処理済みか分かる）

もし「日付で範囲検索したい」なら、パーティションキーを日付にしてソートキーにURLを置く設計になります。

### 3-2. S3

```python
bucket = s3.Bucket(
    self, "ArticleBucket",
    removal_policy=RemovalPolicy.DESTROY,
    auto_delete_objects=True,
    block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
    encryption=s3.BucketEncryption.S3_MANAGED,
    lifecycle_rules=[s3.LifecycleRule(expiration=Duration.days(30))],
)
```

| 設定 | 意味 |
|---|---|
| `auto_delete_objects=True` | S3は中身が残っていると削除できない。CDKがカスタムリソース（Lambda）を作って中身を空にしてくれる |
| `block_public_access=BLOCK_ALL` | **公開設定を全面禁止**。S3の情報漏洩事故はここの設定漏れが原因 |
| `encryption=S3_MANAGED` | 保存時の暗号化。AWSが鍵を管理する（SSE-S3） |
| `lifecycle_rules` | 30日経過したオブジェクトを自動削除。コスト対策 |

セキュリティ案件では `block_public_access` と `encryption` は必須項目です。**設定していないこと自体が指摘対象**になります。

### 3-3. SQS と DLQ

```python
dlq = sqs.Queue(self, "ArticleDLQ", retention_period=Duration.days(14))

queue = sqs.Queue(
    self, "ArticleQueue",
    visibility_timeout=Duration.seconds(180),
    retention_period=Duration.days(4),
    dead_letter_queue=sqs.DeadLetterQueue(
        max_receive_count=3,
        queue=dlq,
    ),
)
```

| 設定 | 意味 |
|---|---|
| `visibility_timeout` | 取り出したメッセージが他から見えなくなる時間。**Lambdaのタイムアウトより長くする** |
| `retention_period` | メッセージの保持期間。これを過ぎると消える |
| `max_receive_count=3` | 3回受信されても削除されなければDLQへ移す |

**可視性タイムアウトの理解が重要**です。

```
Lambda がメッセージを取り出す
  │  この瞬間からタイマー開始（180秒）
  │  他のLambdaからは見えない
  ├─ 処理成功 → メッセージを削除 → 完了
  └─ 処理失敗（またはタイムアウト超過）
        → 180秒後にまた見えるようになる
        → 別のLambdaが取り出して再処理
        → これが3回起きたらDLQへ
```

もし可視性タイムアウト（180秒）がLambdaのタイムアウト（60秒）より短いと、**まだ処理中なのに別のLambdaが同じメッセージを取り出す**という事故が起きます。だから必ず長くします。

### 3-4. Lambda

```python
_lambda.Function(
    self, name,
    runtime=_lambda.Runtime.PYTHON_3_12,
    handler="index.handler",
    code=_lambda.Code.from_asset(f"lambda/{asset_dir}"),
    timeout=Duration.seconds(timeout_sec),
    memory_size=memory,
    environment=env or {},
    log_retention=logs.RetentionDays.ONE_WEEK,
)
```

| 設定 | 意味 |
|---|---|
| `handler="index.handler"` | `index.py` の `handler` 関数を実行する、という指定 |
| `code=Code.from_asset(...)` | このディレクトリをzip化してS3へアップロードする |
| `timeout` | **デフォルトは3秒**。HTTP取得やLLM呼び出しには全く足りない |
| `memory_size` | メモリだけでなく**CPU性能も比例して上がる**。256MBと512MBで実行時間が半分になることもある |
| `log_retention` | CloudWatch Logsの保持期間。指定しないと**無期限保存されて課金が積み上がる** |

**関数ごとにタイムアウトを変えている理由**を見てください。

```python
fetch_feed_fn    → 60秒   RSSを1〜2回取得するだけ
fetch_article_fn → 60秒   記事HTMLの取得
summarize_fn     → 120秒  LLM呼び出しは時間がかかる
notify_fn        → 30秒   Webhookを叩くだけ
```

タイムアウトは**その処理に必要な時間 + 余裕**で設定します。長すぎると異常時に無駄に待ち、短すぎると正常な処理が切られます。

### 3-5. Step Functions

```python
state_machine = sfn.StateMachine(
    self, "ArticleWorkflow",
    definition_body=sfn.DefinitionBody.from_chainable(definition),
    timeout=Duration.minutes(10),
    tracing_enabled=True,
)
```

Step Functionsは**ワークフローの定義**です。Pythonで書いた `definition` が、最終的にASL（Amazon States Language）というJSONに変換されます。

主なステートの種類です。

| ステート | 用途 | このシステムでの使用 |
|---|---|---|
| **Task** | Lambdaや他サービスを実行 | 本文取得、要約、通知、DynamoDB保存 |
| **Choice** | 条件分岐 | 本文長の判定 |
| **Parallel** | 並列実行 | 未使用 |
| **Map** | 配列の各要素に処理を繰り返す | 未使用（拡張候補） |
| **Wait** | 一定時間待つ | 未使用 |
| **Succeed / Fail** | 終了 | スキップ時、失敗時 |

`tracing_enabled=True` はX-Rayトレースを有効にします。どのステートで何秒かかったかが可視化されます。

### 3-6. EventBridge

```python
rule = events.Rule(
    self, "DailyRule",
    schedule=events.Schedule.cron(minute="0", hour="0"),
    enabled=True,
)
rule.add_target(targets.LambdaFunction(fetch_feed_fn))
```

**cron式はUTC**です。`hour="0"` はUTC 0時、つまり日本時間の朝9時です。ここを間違えると9時間ずれます。

EventBridgeは定期実行だけでなく、**イベントパターン**でも起動できます。セキュリティ案件ではこちらが主役です。

```python
# GuardDutyの重大度7超の検知でLambdaを起動する例
events.Rule(
    self, "GuardDutyRule",
    event_pattern=events.EventPattern(
        source=["aws.guardduty"],
        detail_type=["GuardDuty Finding"],
        detail={"severity": [{"numeric": [">", 7]}]},
    ),
)
```

### 3-7. IAM

AWSで最もつまずくのがIAMです。ここが分かると `AccessDenied` の8割は自力で解決できます。

**登場人物**

| 用語 | 意味 |
|---|---|
| **プリンシパル** | 操作する主体。Lambda、Step Functions、ユーザーなど |
| **ロール** | 権限のかたまり。サービスに割り当てる |
| **信頼ポリシー** | 「誰がこのロールを引き受けられるか」の定義 |
| **アクセス許可ポリシー** | 「何に対して何ができるか」の定義 |
| **AssumeRole** | ロールを引き受けること |

**ポリシーの構造**

```json
{
  "Effect": "Allow",           // 許可 or 拒否
  "Action": ["s3:PutObject"],  // 何ができるか
  "Resource": "arn:aws:s3:::bucket/*"  // 何に対して
}
```

このシステムでは、CDKが以下を自動生成しています。

```
FetchFeedFunction のロール
  ├─ 信頼ポリシー: lambda.amazonaws.com が AssumeRole できる
  ├─ AWSLambdaBasicExecutionRole（CloudWatch Logsへの書き込み）
  └─ sqs:SendMessage on ArticleQueue     ← grant_send_messages() が生成
```

**Bedrockだけ手書きしている理由**は、`grant` ヘルパーが用意されていないためです。

```python
summarize_fn.add_to_role_policy(
    iam.PolicyStatement(
        effect=iam.Effect.ALLOW,
        actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
        resources=["*"],   # 本番ではモデルARNに絞る
    )
)
```

`resources=["*"]` は学習用の妥協です。本番では以下のように絞ります。

```python
resources=[
    f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/jp.anthropic.claude-haiku-4-5-20251001-v1:0",
    f"arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-20251001-v1:0",
]
```

推論プロファイル経由の場合、**プロファイル自体と背後のモデル両方**への権限が必要になる点に注意してください。

---

## 4. Lambdaコードの解説

### 4-1. fetch_feed（RSSを取得してSQSへ）

**役割**: EventBridgeから起動され、RSSをパースして記事URLをSQSへ投入する。

```python
def handler(event, context):
    logger.info("event: %s", json.dumps(event, ensure_ascii=False))

    sent = 0
    for feed_url in FEED_URLS:
        try:
            xml_bytes = fetch_url(feed_url)
            articles = parse_rss(xml_bytes)[:MAX_ARTICLES]

            for a in articles:
                url_hash = hashlib.sha256(a["url"].encode()).hexdigest()[:32]
                message = {
                    "url": a["url"],
                    "title": a["title"],
                    "url_hash": url_hash,
                    "feed_url": feed_url,
                }
                sqs.send_message(
                    QueueUrl=QUEUE_URL,
                    MessageBody=json.dumps(message, ensure_ascii=False),
                )
                sent += 1

        except Exception:
            logger.exception("フィード取得に失敗: %s", feed_url)

    return {"sent": sent}
```

**注目点**

`try` を**ループの中**に置いています。1つのフィードが失敗しても、他のフィードは処理を続けます。もし `try` をループの外に置くと、最初のフィードでエラーが出た時点で全部止まります。

```python
logger.exception(...)
```

`logger.error` ではなく `logger.exception` を使うと、**スタックトレースも一緒に記録**されます。except節の中では常にこちらを使ってください。

`ensure_ascii=False` は日本語をエスケープせずログに出すための指定です。付けないと `\u65e5\u672c\u8a9e` のように読めなくなります。

**外部ライブラリを使っていない理由**

RSSパースには `feedparser`、HTTP取得には `requests` が定番ですが、あえて標準ライブラリ（`xml.etree`、`urllib`）だけで書いています。Lambdaに外部ライブラリを含めるには**バンドリング**（依存を含めたzip化、またはLambdaレイヤー）が必要で、学習の初期段階では余計な障害になるためです。

実務では以下のいずれかを使います。

```python
# 方法1: バンドリング（Dockerが必要）
code=_lambda.Code.from_asset(
    "lambda/fetch_feed",
    bundling=BundlingOptions(
        image=_lambda.Runtime.PYTHON_3_12.bundling_image,
        command=["bash", "-c",
                 "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output"],
    ),
)

# 方法2: Lambdaレイヤー（複数関数で共通のライブラリを使う場合）
layer = _lambda.LayerVersion(self, "CommonLayer", code=...)
```

### 4-2. enqueue_worker（SQS → Step Functions）

**役割**: SQSからメッセージを受け取り、重複を除いてStep Functionsを起動する。

```python
def handler(event, context):
    batch_item_failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            body = json.loads(record["body"])
            url_hash = body["url_hash"]

            if already_processed(url_hash):
                logger.info("処理済みのためスキップ: %s", body["url"])
                continue

            sfn.start_execution(
                stateMachineArn=STATE_MACHINE_ARN,
                name=f"exec-{url_hash}-{int(time.time())}",
                input=json.dumps(body, ensure_ascii=False),
            )

        except sfn.exceptions.ExecutionAlreadyExists:
            logger.info("実行が既に存在: %s", message_id)

        except Exception:
            logger.exception("メッセージ処理に失敗: %s", message_id)
            batch_item_failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": batch_item_failures}
```

**SQSトリガー時のevent構造**

Lambdaの `event` は、**何から呼ばれたかで形が全く違います**。SQS経由の場合はこうです。

```json
{
  "Records": [
    {
      "messageId": "abc-123",
      "receiptHandle": "...",
      "body": "{\"url\": \"https://...\", \"title\": \"...\"}",
      "attributes": {"ApproximateReceiveCount": "1", ...},
      "eventSource": "aws:sqs"
    }
  ]
}
```

`body` は**文字列**なので `json.loads` が必要です。また `Records` は**配列**なので、`batch_size=5` の設定により最大5件がまとめて渡ってきます。

**部分的失敗の報告**

```python
return {"batchItemFailures": batch_item_failures}
```

この形式で返すと、**失敗したメッセージだけがキューに戻ります**。CDK側で有効化しています。

```python
lambda_events.SqsEventSource(queue, batch_size=5, report_batch_item_failures=True)
```

これを設定していない場合、5件中1件だけ失敗しても**5件すべてが再処理**されます。成功した4件は二重処理になるので、冪等性がないと事故になります。

**冪等性の確保**

```python
def already_processed(url_hash: str) -> bool:
    res = table.get_item(Key={"url_hash": url_hash})
    return "Item" in res
```

DynamoDBに既にあれば処理済みとみなしてスキップします。SQSの標準キューは**重複配信の可能性がある**ため、この対策が必要です。

### 4-3. fetch_article（本文取得 → S3）

**役割**: 記事HTMLを取得し、タグを除去してS3に保存する。

```python
def handler(event, context):
    url = event["url"]
    url_hash = event["url_hash"]

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as res:
        charset = res.headers.get_content_charset() or "utf-8"
        html = res.read().decode(charset, errors="replace")

    parser = TextExtractor()
    parser.feed(html)
    body = parser.get_text()

    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=s3_key,
        Body=body.encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )

    return {
        **event,                          # ★ここが重要
        "s3_key": s3_key,
        "body_length": len(body),
        "ttl": int(time.time()) + TTL_DAYS * 24 * 3600,
    }
```

**`**event` で入力をマージして返す**

これがStep Functions内のLambdaを書くときの定石です。

```python
return {**event, "s3_key": s3_key, ...}
```

入力をそのまま含めて返すことで、**後続のステートが元の情報（title, url など）を参照できます**。もし `return {"s3_key": s3_key}` だけにすると、次の要約ステートに `title` が渡らず、通知ステートで記事タイトルが出せなくなります。

**文字コードの扱い**

```python
charset = res.headers.get_content_charset() or "utf-8"
html = res.read().decode(charset, errors="replace")
```

日本語サイトはUTF-8とは限りません（Shift_JIS、EUC-JPもある）。HTTPヘッダから文字コードを読み取り、それでデコードします。`errors="replace"` は、デコードできない文字を `` に置き換えて例外を出さないための指定です。

### 4-4. summarize（Bedrockで要約）

```python
def handler(event, context):
    obj = s3.get_object(Bucket=BUCKET_NAME, Key=event["s3_key"])
    body = obj["Body"].read().decode("utf-8")[:MAX_INPUT_CHARS]

    prompt = PROMPT_TEMPLATE.format(title=event.get("title", ""), body=body)

    res = bedrock.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 500, "temperature": 0.3},
    )

    summary = res["output"]["message"]["content"][0]["text"].strip()
    return {**event, "summary": summary}
```

**Converse API**

Bedrockには2つの呼び出し方があります。

| API | 特徴 |
|---|---|
| `invoke_model` | モデルごとにリクエスト形式が違う。移植性が低い |
| **`converse`** | **モデル差異を吸収する統一インターフェース。推奨** |

`converse` を使えば、ClaudeからLlamaにモデルを変えても**コードはほぼそのまま**です。

**推論パラメータ**

```python
inferenceConfig={"maxTokens": 500, "temperature": 0.3}
```

| パラメータ | 意味 |
|---|---|
| `maxTokens` | 出力の最大長。長すぎるとコスト増 |
| `temperature` | 0に近いほど決定的、1に近いほど多様。**要約や分類は0.0〜0.3が適切** |

要約タスクで `temperature` を高くすると、実行のたびに結果が変わって扱いづらくなります。

**入力の切り詰め**

```python
body = obj["Body"].read().decode("utf-8")[:MAX_INPUT_CHARS]
```

LLMは入力トークン数に比例して課金されます。長文をそのまま投げるとコストが跳ね上がるため、先頭8000文字に制限しています。

実務では、単純な切り詰めではなく以下のような工夫をします。

- 本文の重要部分を抽出してから投げる
- 長文はチャンクに分けて要約し、それをさらに要約する（Map-Reduce方式）

**トークン数のログ出力**

```python
usage = res.get("usage", {})
logger.info("input_tokens=%s output_tokens=%s",
            usage.get("inputTokens"), usage.get("outputTokens"))
```

コスト監視のために必ず記録してください。生成AIを使うシステムでは、**トークン数がそのままコスト**になります。

### 4-5. notify（Slack通知）

```python
def handler(event, context):
    text = f"*{event.get('title')}*\n{event.get('url')}\n\n{event.get('summary')}"

    if not SLACK_WEBHOOK_URL:
        logger.info("SLACK_WEBHOOK_URL未設定のため通知をスキップ:\n%s", text)
        return {**event, "notified": False}

    status = post_to_slack({"text": text})
    return {**event, "notified": True}
```

**環境変数が未設定でも落ちない設計**にしています。学習中はWebhookを用意せずログで確認でき、本番では設定するだけで通知が飛びます。

実務では、Webhook URLは**環境変数ではなくSecrets Managerに置きます**。

```python
# CDK側
secret = sm.Secret.from_secret_name_v2(self, "SlackSecret", "slack/webhook")
secret.grant_read(notify_fn)
notify_fn.add_environment("SECRET_ARN", secret.secret_arn)

# Lambda側
import boto3
sm = boto3.client("secretsmanager")
url = sm.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"]
```

環境変数はCloudFormationテンプレートに平文で残り、コンソールからも見えてしまいます。**機密情報を環境変数に入れてはいけません**。

---

## 5. Step Functionsのデータの流れ

Step Functionsで最もつまずくのが**入出力の受け渡し**です。ここを理解すれば大半の問題は解決します。

### データがどう変化するか

```
【起動時の入力】
{
  "url": "https://aws.amazon.com/jp/blogs/news/iso-27001/",
  "title": "Kiro が ISO/IEC 27001:2022 のカバレッジに対応",
  "url_hash": "f340f061...",
  "feed_url": "https://aws.amazon.com/jp/blogs/news/feed/"
}
        │
        ▼ 本文取得（Lambda）
{
  ...上記すべて...,
  "s3_key": "articles/f340f061....txt",   ← 追加
  "body_length": 5432,                     ← 追加
  "ttl": 1791536911                        ← 追加
}
        │
        ▼ 本文長を判定（Choice）※データは変化しない
        │
        ▼ 要約（Lambda）
{
  ...上記すべて...,
  "summary": "・Kiro が AWS の ISO/IEC..."  ← 追加
}
        │
        ▼ DynamoDBへ保存（SDK直接統合）※ResultPath: DISCARD なのでデータは変化しない
        │
        ▼ 通知（Lambda）
{
  ...上記すべて...,
  "notified": false                        ← 追加
}
```

### 4つの制御フィールド

Taskステートには、入出力を制御する4つのフィールドがあります。

| フィールド | タイミング | 役割 |
|---|---|---|
| `InputPath` | 実行前 | 入力のどの部分を使うか絞る |
| `Parameters` | 実行前 | 渡す内容を組み立て直す |
| **`ResultPath`** | 実行後 | **結果を入力のどこに入れるか** |
| `OutputPath` | 実行後 | 次に渡す部分を絞る |

このうち **`ResultPath` が最重要**です。

### ResultPathの3つの挙動

```python
# ① 指定なし（デフォルト）→ 結果で入力を完全に上書き
tasks.LambdaInvoke(self, "T", lambda_function=fn)
# 入力 {"a": 1} + 結果 {"b": 2} → {"b": 2}   ← "a" が消える

# ② パス指定 → 結果を指定の場所に格納し、入力は保持
tasks.LambdaInvoke(self, "T", lambda_function=fn, result_path="$.result")
# 入力 {"a": 1} + 結果 {"b": 2} → {"a": 1, "result": {"b": 2}}

# ③ DISCARD → 結果を捨てて入力をそのまま次へ
tasks.LambdaInvoke(self, "T", lambda_function=fn,
                   result_path=sfn.JsonPath.DISCARD)
# 入力 {"a": 1} + 結果 {"b": 2} → {"a": 1}
```

### このシステムでの使い分け

```python
# Lambdaタスク: 結果で上書き（Lambda側が **event でマージしているので問題ない）
fetch_article_task = tasks.LambdaInvoke(
    self, "本文取得",
    lambda_function=fetch_article_fn,
    payload_response_only=True,
)

# DynamoDB保存: 結果を捨てる
put_item_task = tasks.DynamoPutItem(
    self, "DynamoDBへ保存",
    table=table,
    item={...},
    result_path=sfn.JsonPath.DISCARD,   # ★これがないと壊れる
)
```

**なぜDynamoDB保存で `DISCARD` が必要か**

`DynamoPutItem` の実行結果は `{"SdkHttpMetadata": {...}, "SdkResponseMetadata": {...}}` のようなメタデータです。`ResultPath` を指定しないと、これで入力が上書きされます。

```
入力: {"title": "...", "summary": "...", "url": "..."}
   ↓ PutItem実行（ResultPath指定なし）
出力: {"SdkHttpMetadata": {...}}     ← title も summary も消える
   ↓ 通知ステート
event.get("title") → None            ← 通知が壊れる
```

`DISCARD` を指定することで入力がそのまま通過し、通知ステートで `title` と `summary` を使えます。

### payload_response_only

```python
tasks.LambdaInvoke(self, "本文取得", lambda_function=fn, payload_response_only=True)
```

これを付けないと、Lambdaの戻り値が `$.Payload` の下にネストされます。

```json
// payload_response_only=False（デフォルト）
{
  "ExecutedVersion": "$LATEST",
  "Payload": {"s3_key": "...", "body_length": 5432},
  "StatusCode": 200
}

// payload_response_only=True
{"s3_key": "...", "body_length": 5432}
```

`True` の方が扱いやすいので、特に理由がなければ付けます。

### Choiceステート

```python
length_choice = sfn.Choice(self, "本文長を判定")

definition = fetch_article_task.next(
    length_choice.when(
        sfn.Condition.number_greater_than_equals("$.body_length", 300),
        summarize_task.next(put_item_task).next(notify_task),
    ).otherwise(skipped_state)
)
```

`$.body_length` は**JSONPath記法**です。`$` が入力全体を指し、`.body_length` でその属性を参照します。

条件の種類は豊富にあります。

```python
sfn.Condition.string_equals("$.status", "OPEN")
sfn.Condition.number_greater_than("$.severity", 7)
sfn.Condition.is_present("$.optional_field")
sfn.Condition.and_(cond1, cond2)
sfn.Condition.or_(cond1, cond2)
```

### RetryとCatch

```python
fetch_article_task.add_retry(
    errors=["States.TaskFailed"],
    interval=Duration.seconds(2),
    max_attempts=2,
    backoff_rate=2.0,
)
fetch_article_task.add_catch(failed_state, errors=["States.ALL"])
```

**Retryは指数バックオフ**で動きます。

```
1回目失敗 → 2秒待機 → 再試行
2回目失敗 → 4秒待機（2 × backoff_rate）→ 再試行
2回失敗したら Catch へ
```

一時的なネットワークエラーやスロットリングは、待って再試行すれば成功することが多いため、この仕組みが有効です。

**Catchの改善点**

今回のコードには問題があります。

```python
failed_state = sfn.Fail(self, "処理失敗", cause="記事処理に失敗しました")
fetch_article_task.add_catch(failed_state, errors=["States.ALL"])
```

これだと**元のエラー情報が固定文言に置き換わって消えます**。実際、デバッグ時に `describe-execution` を見ても「記事処理に失敗しました」としか出ませんでした。

改善版はこうです。

```python
fetch_article_task.add_catch(
    failed_state,
    errors=["States.ALL"],
    result_path="$.error",     # エラー情報を $.error に格納
)
```

これで、失敗時の入力に元のエラーが残ります。

```json
{
  "url": "...",
  "error": {
    "Error": "ValidationException",
    "Cause": "{\"errorMessage\": \"Invocation of model ID ...\"}"
  }
}
```

**エラーハンドリングは「握りつぶさず情報を残す」**のが原則です。これは実務でも頻出のミスなので、覚えておいてください。

---

## 6. 押さえるべき5つの概念

### 6-1. 冪等性（べきとうせい / idempotency）

**同じ処理を何回実行しても結果が同じになる性質**です。

分散システムでは「1回だけ実行する」保証は困難です。ネットワーク断、リトライ、重複配信により、同じ処理が複数回走ることを前提に設計します。

```python
# 冪等な処理
table.put_item(Item={"url_hash": "abc", "summary": "..."})
# → 何回実行しても同じレコードが1件あるだけ

# 冪等でない処理
counter += 1
# → 実行回数だけ増えてしまう
```

**冪等にする定番の方法**

```python
# 方法1: 処理済みIDを記録してスキップ
if already_processed(url_hash):
    return

# 方法2: 条件付き書き込み（既存なら失敗させる）
table.put_item(
    Item={...},
    ConditionExpression="attribute_not_exists(url_hash)"
)

# 方法3: 上書き前提の設計にする（PutItemは上書きなので冪等）
```

### 6-2. 可視性タイムアウト（Visibility Timeout）

SQSでメッセージを取り出してから、他のコンシューマーに見えなくなる時間です。

**設定の鉄則**

```
可視性タイムアウト > Lambdaのタイムアウト
```

このシステムでは 180秒 > 60秒 になっています。

処理が成功したらメッセージを削除、失敗（または時間超過）したら再び見えるようになって再処理されます。Lambdaトリガーの場合、削除はLambdaサービスが自動で行います。

### 6-3. DLQ（デッドレターキュー）

規定回数失敗したメッセージの退避先です。

**なぜ必要か**

DLQがないと、失敗し続けるメッセージが延々とリトライされます。これは以下の問題を起こします。

- 同じエラーでログが埋まる
- リトライがリソースを消費し続ける
- 保持期間を過ぎたら**気づかないうちに消える**

DLQに送ることで、失敗を「保留」して後から調査できます。

**実務での運用**

DLQにメッセージが入ったらCloudWatchアラームで通知し、原因を調べて修正後に**リドライブ**（DLQから元のキューに戻す）します。

```bash
# DLQの中身を確認
aws sqs receive-message --queue-url $DLQ_URL --max-number-of-messages 10
```

### 6-4. 最小権限の原則

**必要最小限の権限だけを与える**という考え方です。

セキュリティ案件では必須の観点になります。

```python
# 悪い例
iam.PolicyStatement(actions=["s3:*"], resources=["*"])

# 良い例
bucket.grant_put(fetch_article_fn)    # このバケットへのPutだけ
bucket.grant_read(summarize_fn)       # このバケットからのReadだけ
```

CDKの `grant_*` メソッドは、**アクションもリソースARNも自動で絞ってくれる**ので、自然に最小権限が守られます。

権限を絞る観点は3つあります。

| 観点 | 例 |
|---|---|
| **アクション** | `s3:*` ではなく `s3:GetObject` |
| **リソース** | `*` ではなく特定のバケットARN |
| **条件** | 特定のIPからのみ、特定のタグが付いたリソースのみ |

### 6-5. Infrastructure as Code（IaC）

インフラをコードで定義し、バージョン管理する考え方です。

**得られるもの**

| 利点 | 具体例 |
|---|---|
| **再現性** | 開発・検証・本番で同じ構成を作れる |
| **レビュー可能** | `cdk diff` の結果をPRに貼れる |
| **変更履歴** | Gitで「いつ誰が何を変えたか」が追える |
| **削除の確実性** | `cdk destroy` で消し忘れがない |
| **ドキュメント性** | コードが構成の説明そのものになる |

**ドリフト（drift）に注意**

コードと実環境がズレることをドリフトと呼びます。

- コンソールから手動で設定を変えた
- `cdk deploy --hotswap` を使った

ドリフトがあると、次のデプロイで意図せず上書きされたり、逆に失敗したりします。

```bash
cdk deploy --revert-drift   # ドリフトを解消して整合を取る
```

**IaCで管理しているリソースは、コンソールから触らない**のが原則です。

---

## 7. 今回のエラーから学ぶ

構築中に3つのエラーに遭遇しました。それぞれ実務で頻出のパターンです。

### 7-1. ValidationException — モデルIDが呼べない

```
Invocation of model ID anthropic.claude-haiku-4-5-20251001-v1:0
with on-demand throughput isn't supported.
Retry your request with the ID or ARN of an inference profile.
```

**原因**: 新しめのBedrockモデルは、単体のモデルIDでは呼び出せません。クロスリージョン推論プロファイル経由が必須です。

**調査方法**

```bash
aws bedrock list-inference-profiles --region ap-northeast-1 \
  --query "inferenceProfileSummaries[].inferenceProfileId" --output table
```

**プロファイルの種類**

| プレフィックス | 意味 |
|---|---|
| `jp.` | 日本国内で完結。データが国外に出ない |
| `apac.` | アジア太平洋圏に分散 |
| `global.` | 世界中に分散。可用性が最も高い |

セキュリティ案件では**データの所在地**が要件になることがあるため、`jp.` を選ぶ判断は実務的にも意味があります。

**教訓**: AWSのエラーメッセージは解決策まで書いてあることが多い。まず読む。

### 7-2. Failステートの握りつぶし

```bash
$ aws stepfunctions describe-execution --execution-arn $EXEC_ARN
{
    "status": "FAILED",
    "error": null,
    "cause": "記事処理に失敗しました"    # ← 固定文言。原因が分からない
}
```

**原因**: `sfn.Fail(cause="記事処理に失敗しました")` が元のエラーを置き換えていました。

**本当の原因を掘り出す方法**

```bash
aws stepfunctions get-execution-history --execution-arn $EXEC_ARN \
  --query "events[?type=='LambdaFunctionFailed'].lambdaFunctionFailedEventDetails"
```

**教訓**: エラーハンドリングを書くときは、**元の情報を残す**。`result_path="$.error"` を指定する。

### 7-3. ExecutionAlreadyExists — 再実行できない

**原因**: Step Functionsの実行名に `url_hash` を使っていたため、同じ記事で2回目の起動が弾かれていました。

```python
name=f"exec-{url_hash}",   # 同じ記事なら同じ名前
```

**これは意図した設計でもあった**点が重要です。同名の実行が作れない性質を使って、二重起動を防いでいました。しかし学習中に何度も再実行したい場面では邪魔になります。

**修正**

```python
name=f"exec-{url_hash}-{int(time.time())}",
```

二重起動防止の役割は、DynamoDBの重複チェック（`already_processed`）が担っています。

**教訓**: 「安全のための制約」が「開発の妨げ」になることがある。**どこで冪等性を担保するか**を設計として決めておく。

### 調査の順路

エラー時はこの順で確認してください。

```
1. CloudWatch Logs           ← Lambdaのエラーは全部ここ
2. Step Functions 実行履歴    ← どのステートで失敗したか、入出力は何か
3. CloudFormation イベント    ← デプロイ失敗の原因
4. DLQ の中身                ← 処理に失敗したメッセージ
```

---

## 8. 用語集

### AWS共通

| 用語 | 意味 |
|---|---|
| **ARN** | Amazon Resource Name。リソースの一意識別子。`arn:aws:サービス:リージョン:アカウントID:リソース` |
| **リージョン** | データセンターの地理的な場所。`ap-northeast-1` は東京 |
| **マネージドサービス** | AWSがサーバー管理を代行するサービス。Lambda、DynamoDBなど |
| **サーバーレス** | サーバーの存在を意識せず使えるモデル。使った分だけ課金 |

### CDK / CloudFormation

| 用語 | 意味 |
|---|---|
| **スタック** | まとめてデプロイ・削除する単位 |
| **Construct** | CDKの構成要素。L1/L2/L3の3階層 |
| **論理ID** | スタック内でリソースを識別する名前。変えると作り直しになる |
| **Bootstrap** | CDKがデプロイに使う土台リソースの作成。アカウント×リージョンごとに1回 |
| **変更セット** | 適用したら何が起きるかの事前計算結果 |
| **ドリフト** | コードと実環境のズレ |

### Lambda

| 用語 | 意味 |
|---|---|
| **ハンドラ** | Lambdaが呼び出す関数。`index.handler` など |
| **コールドスタート** | 初回起動時の遅延。実行環境の準備に時間がかかる |
| **同時実行数** | 同時に走るLambdaの数。アカウント単位の上限がある |
| **レイヤー** | 複数のLambdaで共通利用するライブラリ |

### SQS

| 用語 | 意味 |
|---|---|
| **可視性タイムアウト** | 取り出したメッセージが見えなくなる時間 |
| **DLQ** | 規定回数失敗したメッセージの退避先 |
| **標準キュー** | 高スループット。順序保証なし、重複配信あり |
| **FIFOキュー** | 順序保証＋重複排除。スループットは低い |
| **ロングポーリング** | メッセージが来るまで待つ。空振りのAPI呼び出しを減らす |

### Step Functions

| 用語 | 意味 |
|---|---|
| **ステートマシン** | ワークフロー全体の定義 |
| **ASL** | Amazon States Language。ワークフローを記述するJSON |
| **ステート** | ワークフローの1ステップ |
| **ResultPath** | 実行結果を入力のどこに入れるかの指定 |
| **標準ワークフロー** | 最大1年実行可。実行履歴が残る |
| **Expressワークフロー** | 最大5分。高頻度・低コスト |

### Bedrock / 生成AI

| 用語 | 意味 |
|---|---|
| **基盤モデル** | 事前学習済みの汎用モデル。Claude、Llamaなど |
| **推論プロファイル** | 複数リージョンに負荷分散する呼び出し口 |
| **Converse API** | モデル差異を吸収する統一インターフェース |
| **トークン** | テキストの処理単位。課金の基準 |
| **temperature** | 出力のランダム性。0に近いほど決定的 |

### セキュリティ運用（案件で使う）

| 用語 | 意味 |
|---|---|
| **SOC** | Security Operation Center。監視組織 |
| **SIEM** | ログを集約・相関分析する基盤。**検知**が役割 |
| **SOAR** | 検知後の**対応を自動化**する仕組み |
| **EDR** | 端末を監視・対応する製品 |
| **プレイブック** | 定型対応の手順を定義したワークフロー |
| **トリアージ** | アラートの緊急度を判定して振り分けること |
| **IoC** | Indicator of Compromise。侵害の痕跡 |
| **誤検知** | 問題ないのにアラートが出ること |

---

## 9. 手を動かして確認する課題

読むだけでは身につきません。以下を実際に試してください。難易度順に並べています。

### 課題1: Step Functionsの入出力を目で追う（15分）

**目的**: `ResultPath` の挙動を理解する

1. コンソールで Step Functions → 成功した実行を開く
2. グラフビューで各ステートをクリック
3. 「入力」「出力」タブを比較する

**確認ポイント**

- 「本文取得」の出力に `s3_key`, `body_length`, `ttl` が増えている
- 「要約」の出力に `summary` が増えている
- 「DynamoDBへ保存」の入力と出力が**同一**（`DISCARD` の効果）

### 課題2: 意図的に失敗させてDLQを観察する（30分）

**目的**: リトライとDLQの動きを体感する

`lambda/fetch_article/index.py` の `handler` の先頭に1行追加します。

```python
def handler(event, context):
    raise Exception("テスト用の意図的な失敗")
```

デプロイして実行します。

```bash
cdk deploy --hotswap
aws lambda invoke --function-name $FN_FETCH_FEED \
  --cli-binary-format raw-in-base64-out --payload '{}' response.json
```

**観察すること**

```bash
# Step Functionsがリトライしている様子（実行履歴を見る）
aws stepfunctions get-execution-history --execution-arn <ARN> \
  --query "events[].type" --output text

# DLQにメッセージが溜まる（数分待つ）
aws sqs get-queue-attributes --queue-url $DLQ_URL \
  --attribute-names ApproximateNumberOfMessages

# DLQの中身を見る
aws sqs receive-message --queue-url $DLQ_URL
```

確認したら、追加した行を消して元に戻してください。

### 課題3: エラーハンドリングを改善する（20分）

**目的**: 「握りつぶし」を解消する

`stacks/rss_summarizer_stack.py` を修正します。

```python
fetch_article_task.add_catch(
    failed_state, errors=["States.ALL"], result_path="$.error"
)
summarize_task.add_catch(
    failed_state, errors=["States.ALL"], result_path="$.error"
)
```

課題2と組み合わせて、**失敗時に元のエラーが `$.error` に残ること**を確認してください。

### 課題4: 非推奨警告を解消する（30分）

**目的**: 実務でよくある「非推奨APIの置き換え」を経験する

デプロイのたびに出ているこの警告を消します。

```
[WARNING] aws-cdk-lib.aws_lambda.FunctionOptions#logRetention is deprecated.
  use `logGroup` instead
```

**修正の方針**

```python
# 現在
log_retention=logs.RetentionDays.ONE_WEEK,

# 新しい書き方
log_group=logs.LogGroup(
    self, f"{name}LogGroup",
    log_group_name=f"/aws/lambda/{name}",
    retention=logs.RetentionDays.ONE_WEEK,
    removal_policy=RemovalPolicy.DESTROY,
),
```

`make_lambda` ヘルパー内で、関数ごとに一意なLogGroupを作る必要があります。

### 課題5: Mapステートで並列処理する（60分）

**目的**: Step Functionsの並列処理を理解する

複数のRSSフィードを並列で処理するよう拡張します。

```python
FEED_URLS = ",".join([
    "https://aws.amazon.com/jp/blogs/news/feed/",
    "https://aws.amazon.com/jp/blogs/security/feed/",
    "https://aws.amazon.com/jp/blogs/machine-learning/feed/",
])
```

Mapステートの基本形はこうです。

```python
map_state = sfn.Map(
    self, "各記事を処理",
    items_path="$.articles",       # 配列がある場所
    max_concurrency=3,             # 同時実行数の上限
)
map_state.item_processor(article_workflow)
```

### 課題6: 権限を最小化する（40分）

**目的**: 最小権限の原則を実践する

現在 `resources=["*"]` になっているBedrockの権限を絞ります。

```python
summarize_fn.add_to_role_policy(
    iam.PolicyStatement(
        effect=iam.Effect.ALLOW,
        actions=["bedrock:InvokeModel"],
        resources=[
            f"arn:aws:bedrock:{self.region}:{self.account}:inference-profile/{BEDROCK_MODEL_ID}",
            "arn:aws:bedrock:*::foundation-model/anthropic.claude-haiku-4-5-*",
        ],
    )
)
```

デプロイして**正常に動くこと**を確認してください。動かなければ、CloudWatch Logsの `AccessDenied` メッセージに必要なARNが書かれています。

---

## おわりに

このシステムで触れた要素は、案件の必須要件とほぼ一致しています。

| 案件の要件 | このシステムでの経験 |
|---|---|
| Python開発（業務ロジック、API連携） | 5つのLambda |
| AWSサーバレス（Lambda / Step Functions / EventBridge / SQS） | 全部使用 |
| AWSデータストア（DynamoDB / S3） | 使い分けも含めて |
| Infrastructure as Code（CDK） | 全リソースを定義 |
| API連携（REST、認証） | Bedrock、外部HTTP |
| 生成AI活用 | Bedrock Converse API |
| ワークフローエンジン | Step Functions |

残りは**セキュリティ運用（SOC/SOAR）の知識**と**OpenSearch**ですが、これらは参画後に業務を通じて学べる領域です。

参画までの間は、上記の課題を通じて**エラーを起こして直す経験**を積むことをおすすめします。正常系より異常系の経験のほうが、現場では役に立ちます。
