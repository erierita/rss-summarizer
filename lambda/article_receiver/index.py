"""
OAuth2で保護された記事登録API の実処理。

学習ポイント:
- API Gateway (HTTP API) から呼ばれる Lambda の event 構造
- 認可済みのトークン情報（scope, client_id）を event から参照する
- HTTPステータスコードでの応答
"""
import json
import logging
import os
import time

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

table = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])

TTL_DAYS = 30
REQUIRED_FIELDS = ("url_hash", "url", "title")


def response(status: int, body: dict) -> dict:
    """API Gateway (payload format 2.0) への応答を組み立てる"""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def handler(event, context):
    # ★ここが重要: API Gatewayが検証済みのトークン情報を渡してくれる
    # Lambda側で改めて署名検証する必要はない
    claims = (
        event.get("requestContext", {})
        .get("authorizer", {})
        .get("jwt", {})
        .get("claims", {})
    )
    logger.info(
        "認可済みリクエスト client_id=%s scope=%s",
        claims.get("client_id"),
        claims.get("scope"),
    )

    # リクエストボディの取得
    raw_body = event.get("body") or "{}"
    try:
        payload = json.loads(raw_body)
    except json.JSONDecodeError:
        logger.warning("JSONのパースに失敗しました")
        return response(400, {"message": "リクエストボディが不正なJSONです"})

    # 必須項目の検証
    missing = [f for f in REQUIRED_FIELDS if not payload.get(f)]
    if missing:
        logger.warning("必須項目が不足: %s", missing)
        return response(400, {"message": "必須項目が不足しています", "missing": missing})

    item = {
        "url_hash": payload["url_hash"],
        "url": payload["url"],
        "title": payload["title"],
        "summary": payload.get("summary", ""),
        "registered_by": claims.get("client_id", "unknown"),  # 誰が登録したか
        "registered_at": int(time.time()),
        "ttl": int(time.time()) + TTL_DAYS * 24 * 3600,
    }

    try:
        table.put_item(Item=item)
    except Exception:
        logger.exception("DynamoDBへの登録に失敗しました")
        return response(500, {"message": "登録処理に失敗しました"})

    logger.info("記事を登録しました url_hash=%s", item["url_hash"])
    return response(201, {"registered": True, "url_hash": item["url_hash"]})