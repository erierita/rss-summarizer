"""
SQSからメッセージを取り出し、Step Functionsを起動する。

学習ポイント:
- SQSトリガー時のevent構造（event["Records"] に配列で来る）
- ReportBatchItemFailures: 一部だけ失敗した場合の扱い
- 冪等性: 標準キューは重複配信の可能性があるので、処理済みかを必ず確認する
"""
import json
import logging
import os
import time

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sfn = boto3.client("stepfunctions")
table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

STATE_MACHINE_ARN = os.environ["STATE_MACHINE_ARN"]


def already_processed(url_hash: str) -> bool:
    """DynamoDBに既にあるなら処理済みとみなす（冪等性の確保）"""
    try:
        res = table.get_item(Key={"url_hash": url_hash})
        return "Item" in res
    except ClientError:
        logger.exception("DynamoDB参照に失敗。処理は続行する")
        return False


def handler(event, context):
    logger.info("received %d records", len(event.get("Records", [])))

    # 失敗したメッセージだけを報告する。成功分はキューから消える
    batch_item_failures = []

    for record in event.get("Records", []):
        message_id = record["messageId"]
        try:
            body = json.loads(record["body"])
            url_hash = body["url_hash"]

            if already_processed(url_hash):
                logger.info("処理済みのためスキップ: %s", body["url"])
                continue

            # Step Functionsを起動
            # nameを指定すると同名の実行は重複起動できない = 二重起動の防止になる
            sfn.start_execution(
                stateMachineArn=STATE_MACHINE_ARN,
                name=f"exec-{url_hash}-{int(time.time())}",
                input=json.dumps(body, ensure_ascii=False),
            )
            logger.info("ワークフロー起動: %s", body["url"])

        except sfn.exceptions.ExecutionAlreadyExists:
            # 同名の実行が既にある = 既に起動済み。成功扱いでよい
            logger.info("実行が既に存在: %s", message_id)

        except Exception:
            logger.exception("メッセージ処理に失敗: %s", message_id)
            batch_item_failures.append({"itemIdentifier": message_id})

    # この形式で返すと、失敗したメッセージだけがキューに戻る
    return {"batchItemFailures": batch_item_failures}
