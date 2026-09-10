"""
要約結果をSlackへ通知する。

学習ポイント:
- 外部API（Webhook）への連携
- 環境変数が未設定でも落ちないようにする（学習中はログ出力だけで済ませる）
"""
import json
import logging
import os
import urllib.request

logger = logging.getLogger()
logger.setLevel(logging.INFO)

SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")


def post_to_slack(payload: dict) -> int:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        SLACK_WEBHOOK_URL,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as res:
        return res.status


def handler(event, context):
    title = event.get("title", "(タイトルなし)")
    url = event.get("url", "")
    summary = event.get("summary", "")

    text = f"*{title}*\n{url}\n\n{summary}"

    if not SLACK_WEBHOOK_URL:
        # Webhook未設定でも学習は進められるようにする
        logger.info("SLACK_WEBHOOK_URL未設定のため通知をスキップ:\n%s", text)
        return {**event, "notified": False}

    status = post_to_slack({"text": text})
    logger.info("Slack通知 status=%s", status)

    return {**event, "notified": True}
