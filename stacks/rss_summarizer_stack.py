import os

from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_lambda as _lambda,
    aws_dynamodb as dynamodb,
    aws_s3 as s3,
    aws_sqs as sqs,
    aws_events as events,
    aws_events_targets as targets,
    aws_stepfunctions as sfn,
    aws_stepfunctions_tasks as tasks,
    aws_lambda_event_sources as lambda_events,
    aws_iam as iam,
    aws_logs as logs,
    aws_secretsmanager as sm,
    aws_cognito as cognito,
    aws_apigatewayv2 as apigwv2,
    aws_apigatewayv2_authorizers as apigwv2_authorizers,
    aws_apigatewayv2_integrations as apigwv2_integrations,
)
from constructs import Construct

# --- 設定値 -------------------------------------------------------------
# 学習用なので少なめに。Bedrockは従量課金なので記事数は絞る
MAX_ARTICLES = "3"
MIN_BODY_LENGTH = 300  # これ未満の記事は要約せずスキップ（Choiceの分岐条件）
BEDROCK_MODEL_ID = os.getenv(
    "BEDROCK_MODEL_ID", "jp.anthropic.claude-haiku-4-5-20251001-v1:0"
)

FEED_URLS = ",".join([
    "https://aws.amazon.com/jp/blogs/news/feed/",
    # 増やすとMapステートの並列処理が体感できる
])


class RssSummarizerStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # ==================================================================
        # Phase 1: データストア
        # ==================================================================

        # DynamoDB: 記事のメタデータと要約を保存
        # パーティションキーだけのシンプルな設計。url_hash で重複判定もできる
        table = dynamodb.Table(
            self,
            "ArticleTable",
            partition_key=dynamodb.Attribute(
                name="url_hash", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,  # 学習用は従量課金
            removal_policy=RemovalPolicy.DESTROY,  # cdk destroy で消える（本番ではRETAIN）
            time_to_live_attribute="ttl",  # 一定期間後に自動削除。コスト対策
        )

        # S3: 記事本文の全文を保存
        # DynamoDBは1アイテム400KB上限があるので、本文はS3に逃がすのが定石
        bucket = s3.Bucket(
            self,
            "ArticleBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,  # destroy時に中身も消す（学習用）
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            lifecycle_rules=[
                s3.LifecycleRule(expiration=Duration.days(30))  # 30日で自動削除
            ],
        )

        # ==================================================================
        # Phase 2: キュー
        # ==================================================================

        # DLQ: 規定回数失敗したメッセージの退避先
        # ここに溜まったメッセージを見ることで、何が失敗したか調査できる
        dlq = sqs.Queue(
            self,
            "ArticleDLQ",
            retention_period=Duration.days(14),
        )

        # メインキュー
        # visibility_timeout は「取り出したメッセージが他から見えなくなる時間」
        # 必ず Lambda のタイムアウトより長くすること（でないと二重処理される）
        queue = sqs.Queue(
            self,
            "ArticleQueue",
            visibility_timeout=Duration.seconds(180),  # Lambda(60s)より長く
            retention_period=Duration.days(4),
            dead_letter_queue=sqs.DeadLetterQueue(
                max_receive_count=3,  # 3回失敗したらDLQ行き
                queue=dlq,
            ),
        )

        # ==================================================================
        # Lambda 共通設定
        # ==================================================================

        def make_lambda(
            name: str,
            asset_dir: str,
            timeout_sec: int = 60,
            memory: int = 256,
            env: dict | None = None,
        ) -> _lambda.Function:
            """Lambda関数を作るヘルパー。共通設定をまとめる"""
            return _lambda.Function(
                self,
                name,
                runtime=_lambda.Runtime.PYTHON_3_12,
                handler="index.handler",
                code=_lambda.Code.from_asset(f"lambda/{asset_dir}"),
                timeout=Duration.seconds(timeout_sec),
                memory_size=memory,
                environment=env or {},
                log_retention=logs.RetentionDays.ONE_WEEK,  # ログ保持期間。コスト対策
            )

        # --- fetch_feed: RSSを取得してSQSへ投入 ---------------------------
        fetch_feed_fn = make_lambda(
            "FetchFeedFunction",
            "fetch_feed",
            timeout_sec=60,
            env={
                "QUEUE_URL": queue.queue_url,
                "FEED_URLS": FEED_URLS,
                "MAX_ARTICLES": MAX_ARTICLES,
            },
        )
        # ★重要: grant_* で必要なIAM権限が自動生成される
        # 手書きでポリシーを書かなくてよいのがCDKの利点
        queue.grant_send_messages(fetch_feed_fn)

        # --- fetch_article: 本文取得してS3へ ------------------------------
        fetch_article_fn = make_lambda(
            "FetchArticleFunction",
            "fetch_article",
            timeout_sec=60,
            env={"BUCKET_NAME": bucket.bucket_name},
        )
        bucket.grant_put(fetch_article_fn)

        # --- summarize: Bedrockで要約 -------------------------------------
        summarize_fn = make_lambda(
            "SummarizeFunction",
            "summarize",
            timeout_sec=120,  # LLM呼び出しは時間がかかる
            memory=512,
            env={
                "BUCKET_NAME": bucket.bucket_name,
                "BEDROCK_MODEL_ID": BEDROCK_MODEL_ID,
            },
        )
        bucket.grant_read(summarize_fn)
        # Bedrockはgrant系のヘルパーがないので、IAMポリシーを手書きする
        # ここでIAMポリシーの構造（Effect/Action/Resource）を学べる
        summarize_fn.add_to_role_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
                resources=["*"],  # 本番ではモデルARNを絞る
            )
        )

        # --- notify: Slack通知 --------------------------------------------
        # Secrets Manager に「箱」だけ作る。値はデプロイ後にCLIで登録する
        # こうすることで、コードにもCloudFormationテンプレートにも値が残らない
        slack_secret = sm.Secret(
            self,
            "SlackWebhookSecret",
            secret_name="rss-summarizer/slack-webhook",
            description="Slack Incoming Webhook URL",
            removal_policy=RemovalPolicy.DESTROY,  # 学習用。本番はRETAIN
        )

        notify_fn = make_lambda(
            "NotifyFunction",
            "notify",
            timeout_sec=30,
            # ARN自体は機密ではないので環境変数で渡してよい
            env={"SECRET_ARN": slack_secret.secret_arn},
        )
        # Lambdaにシークレット読み取り権限を付与（このシークレットのみ）
        slack_secret.grant_read(notify_fn)

        
        # ==================================================================
        # 段階1: 認可サーバー（Cognito）
        # ==================================================================
        # OAuth2 Client Credentials でサービス間認証を行うための認可サーバー。
        # ユーザーは作らず、アプリ（クライアント）自身の資格情報でトークンを発行する。

        # User Pool: Resource Server と App Client を収める入れ物
        user_pool = cognito.UserPool(
            self,
            "ApiUserPool",
            user_pool_name="rss-summarizer-api",
            self_sign_up_enabled=False,  # ユーザー登録機能は使わない
            removal_policy=RemovalPolicy.DESTROY,
        )

        # Resource Server: 保護対象のAPIとスコープを定義する
        # identifier がスコープ名の接頭辞になる → "rss-summarizer/articles.write"
        read_scope = cognito.ResourceServerScope(
            scope_name="articles.read", scope_description="記事の参照"
        )
        write_scope = cognito.ResourceServerScope(
            scope_name="articles.write", scope_description="記事の登録"
        )
        resource_server = user_pool.add_resource_server(
            "ArticleApiResourceServer",
            identifier="rss-summarizer",
            scopes=[read_scope, write_scope],
        )

        # Domain: トークンエンドポイントのURLを決める
        # 全AWSアカウントで一意である必要があるためアカウントIDを含める
        user_pool_domain = user_pool.add_domain(
            "ApiUserPoolDomain",
            cognito_domain=cognito.CognitoDomainOptions(
                domain_prefix=f"rss-summarizer-{self.account}"
            ),
        )

        # App Client: client_id / client_secret の発行元
        # write スコープのみ割り当てる（read は将来の閲覧用クライアント向けに定義だけ）
        api_client = user_pool.add_client(
            "BatchApiClient",
            user_pool_client_name="rss-summarizer-batch",
            generate_secret=True,  # client_secret を発行する
            o_auth=cognito.OAuthSettings(
                flows=cognito.OAuthFlows(client_credentials=True),
                scopes=[
                    cognito.OAuthScope.resource_server(resource_server, write_scope),
                ],
            ),
            access_token_validity=Duration.hours(1),
        )
        # Resource Server が先に作られる必要がある
        api_client.node.add_dependency(resource_server)

        # Secrets Manager: 資格情報の保管場所（箱だけ作る）
        # 値はデプロイ後にCLIで登録する。Slack Webhook と同じ方針
        api_credentials = sm.Secret(
            self,
            "ApiClientCredentials",
            secret_name="rss-summarizer/api-client",
            description="OAuth2 client credentials for the article registry API",
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ==================================================================
        # 段階2: 保護されたAPI（呼ばれる側）
        # ==================================================================

        # 登録された記事の保存先。既存の ArticleTable とは別テーブル
        registry_table = dynamodb.Table(
            self,
            "ArticleRegistryTable",
            partition_key=dynamodb.Attribute(
                name="url_hash", type=dynamodb.AttributeType.STRING
            ),
            billing_mode=dynamodb.BillingMode.PAY_PER_REQUEST,
            removal_policy=RemovalPolicy.DESTROY,
            time_to_live_attribute="ttl",
        )

        # APIの実処理を行うLambda
        receiver_fn = make_lambda(
            "ArticleReceiverFunction",
            "article_receiver",
            timeout_sec=30,
            env={"TABLE_NAME": registry_table.table_name},
        )
        registry_table.grant_read_write_data(receiver_fn)

        # JWT Authorizer: トークンの署名・exp・iss・aud を自動で検証する
        # Cognitoの公開鍵(JWKS)はAPI Gatewayが自動取得する
        jwt_authorizer = apigwv2_authorizers.HttpJwtAuthorizer(
            "ArticleApiJwtAuthorizer",
            jwt_issuer=(
                f"https://cognito-idp.{self.region}.amazonaws.com/"
                f"{user_pool.user_pool_id}"
            ),
            jwt_audience=[api_client.user_pool_client_id],
            identity_source=["$request.header.Authorization"],
        )

        # HTTP API: REST APIより軽量・低コストなAPI Gateway
        article_api = apigwv2.HttpApi(
            self,
            "ArticleHttpApi",
            api_name="rss-summarizer-article-api",
            description="OAuth2で保護された記事登録API",
        )

        # ルート定義: POST /articles
        # authorization_scopes でスコープを要求する
        article_api.add_routes(
            path="/articles",
            methods=[apigwv2.HttpMethod.POST],
            authorizer=jwt_authorizer,
            authorization_scopes=["rss-summarizer/articles.write"],
            integration=apigwv2_integrations.HttpLambdaIntegration(
                "ArticleReceiverIntegration", receiver_fn
            ),
        )

        # ==================================================================
        # Phase 3: Step Functions（記事1件ごとの処理フロー）
        # ==================================================================

        # --- Task 1: 本文取得 ---
        fetch_article_task = tasks.LambdaInvoke(
            self,
            "本文取得",
            lambda_function=fetch_article_fn,
            # payload_response_only=True にすると、Lambdaの戻り値だけが次に渡る
            # （$.Payload でネストされない）。入出力がシンプルになる
            payload_response_only=True,
            retry_on_service_exceptions=True,
        )
        # 明示的にRetryを追加。指数バックオフの挙動を確認できる
        fetch_article_task.add_retry(
            errors=["States.TaskFailed"],
            interval=Duration.seconds(2),
            max_attempts=2,
            backoff_rate=2.0,
        )

        # --- Choice: 本文が短すぎないか判定 ---
        length_choice = sfn.Choice(self, "本文長を判定")

        # --- Task 2: 要約 ---
        summarize_task = tasks.LambdaInvoke(
            self,
            "要約",
            lambda_function=summarize_fn,
            payload_response_only=True,
            retry_on_service_exceptions=True,
        )
        summarize_task.add_retry(
            errors=["States.TaskFailed"],
            interval=Duration.seconds(5),
            max_attempts=2,
            backoff_rate=2.0,
        )

        # --- Task 3: DynamoDBへ保存（SDK直接統合。Lambdaを介さない）---
        # Step FunctionsはAWSサービスを直接呼べる。Lambdaを書かずに済む
        put_item_task = tasks.DynamoPutItem(
            self,
            "DynamoDBへ保存",
            table=table,
            item={
                "url_hash": tasks.DynamoAttributeValue.from_string(
                    sfn.JsonPath.string_at("$.url_hash")
                ),
                "url": tasks.DynamoAttributeValue.from_string(
                    sfn.JsonPath.string_at("$.url")
                ),
                "title": tasks.DynamoAttributeValue.from_string(
                    sfn.JsonPath.string_at("$.title")
                ),
                "summary": tasks.DynamoAttributeValue.from_string(
                    sfn.JsonPath.string_at("$.summary")
                ),
                "s3_key": tasks.DynamoAttributeValue.from_string(
                    sfn.JsonPath.string_at("$.s3_key")
                ),
                "ttl": tasks.DynamoAttributeValue.number_from_string(
                    sfn.JsonPath.string_at("States.JsonToString($.ttl)")
                ),
            },
            # ★重要: ResultPathをDISCARDにすると、このタスクの結果を捨てて
            # 入力をそのまま次に渡す。指定しないと入力が上書きされて壊れる
            result_path=sfn.JsonPath.DISCARD,
        )

        # --- Task 4: 通知 ---
        notify_task = tasks.LambdaInvoke(
            self,
            "Slack通知",
            lambda_function=notify_fn,
            payload_response_only=True,
        )

        # --- エラー時の分岐 ---
        failed_state = sfn.Fail(self, "処理失敗", cause="記事処理に失敗しました")
        skipped_state = sfn.Succeed(self, "スキップ（本文が短い）")

        # --- フローを組み立てる ---
        definition = fetch_article_task.next(
            length_choice.when(
                sfn.Condition.number_greater_than_equals(
                    "$.body_length", MIN_BODY_LENGTH
                ),
                summarize_task.next(put_item_task).next(notify_task),
            ).otherwise(skipped_state)
        )

        # Catchでエラーを拾う
        fetch_article_task.add_catch(failed_state, errors=["States.ALL"])
        summarize_task.add_catch(failed_state, errors=["States.ALL"])

        state_machine = sfn.StateMachine(
            self,
            "ArticleWorkflow",
            definition_body=sfn.DefinitionBody.from_chainable(definition),
            timeout=Duration.minutes(10),
            tracing_enabled=True,  # X-Rayトレース。処理の流れを可視化できる
        )

        # --- enqueue_worker: SQSから取り出してStep Functionsを起動 --------
        enqueue_worker_fn = make_lambda(
            "EnqueueWorkerFunction",
            "enqueue_worker",
            timeout_sec=60,
            env={
                "STATE_MACHINE_ARN": state_machine.state_machine_arn,
                "TABLE_NAME": table.table_name,
            },
        )
        state_machine.grant_start_execution(enqueue_worker_fn)
        table.grant_read_data(enqueue_worker_fn)  # 重複チェック用

        # SQSをLambdaのトリガーに設定
        enqueue_worker_fn.add_event_source(
            lambda_events.SqsEventSource(
                queue,
                batch_size=5,
                # 部分的失敗を報告できる。1件失敗しても他は成功扱いにできる
                report_batch_item_failures=True,
            )
        )

        # ==================================================================
        # Phase 2: EventBridge（定期実行）
        # ==================================================================

        # cron式はUTC。日本時間9時 = UTC 0時
        rule = events.Rule(
            self,
            "DailyRule",
            schedule=events.Schedule.cron(minute="0", hour="0"),
            description="毎日JST 9:00にRSS収集を起動",
            enabled=True,  # 使わないときは False にして無効化（コスト対策）
        )
        rule.add_target(targets.LambdaFunction(fetch_feed_fn))

        # ==================================================================
        # 出力（デプロイ後にコンソールで確認しやすくする）
        # ==================================================================
        CfnOutput(self, "TableName", value=table.table_name)
        CfnOutput(self, "BucketName", value=bucket.bucket_name)
        CfnOutput(self, "QueueUrl", value=queue.queue_url)
        CfnOutput(self, "DlqUrl", value=dlq.queue_url)
        CfnOutput(self, "StateMachineArn", value=state_machine.state_machine_arn)
        CfnOutput(self, "FetchFeedFunctionName", value=fetch_feed_fn.function_name)
        CfnOutput(self, "SlackSecretName", value=slack_secret.secret_name)

                # --- 段階1で追加 ---
        CfnOutput(self, "CognitoUserPoolId", value=user_pool.user_pool_id)
        CfnOutput(self, "CognitoClientId", value=api_client.user_pool_client_id)
        CfnOutput(
            self,
            "CognitoTokenUrl",
            value=f"{user_pool_domain.base_url()}/oauth2/token",
        )
        CfnOutput(self, "ApiCredentialsSecretName", value=api_credentials.secret_name)
        CfnOutput(self, "ArticleApiEndpoint", value=article_api.api_endpoint)
        CfnOutput(self, "ArticleRegistryTableName", value=registry_table.table_name)
