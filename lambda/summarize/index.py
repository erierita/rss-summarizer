"""
S3から本文を読み、Bedrockで要約する。

学習ポイント:
- Bedrock Runtime API の呼び出し方（converse API を使用）
- LLMへのプロンプト設計
- トークン数・コストを意識した入力の切り詰め
"""
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime")

BUCKET_NAME = os.environ["BUCKET_NAME"]
MODEL_ID = os.environ["BEDROCK_MODEL_ID"]

# コスト対策。長い記事は先頭だけ使う
MAX_INPUT_CHARS = 8000

PROMPT_TEMPLATE = """以下の技術記事を、日本語で3行に要約してください。

制約:
- 各行は箇条書き（・で開始）
- 専門用語はそのまま残す
- 要約以外の前置きや後書きは書かない

タイトル: {title}

本文:
{body}
"""


def handler(event, context):
    logger.info("input keys: %s", list(event.keys()))

    title = event.get("title", "")
    s3_key = event["s3_key"]

    obj = s3.get_object(Bucket=BUCKET_NAME, Key=s3_key)
    body = obj["Body"].read().decode("utf-8")[:MAX_INPUT_CHARS]

    prompt = PROMPT_TEMPLATE.format(title=title, body=body)

    # converse API はモデル差異を吸収してくれる新しいインターフェース
    res = bedrock.converse(
        modelId=MODEL_ID,
        messages=[{"role": "user", "content": [{"text": prompt}]}],
        inferenceConfig={"maxTokens": 500, "temperature": 0.3},
    )

    summary = res["output"]["message"]["content"][0]["text"].strip()
    usage = res.get("usage", {})
    logger.info(
        "要約完了 input_tokens=%s output_tokens=%s",
        usage.get("inputTokens"),
        usage.get("outputTokens"),
    )

    return {**event, "summary": summary}
