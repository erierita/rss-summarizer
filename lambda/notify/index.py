"""
要約結果をSlackへ通知する。

学習ポイント:
- Secrets Manager から実行時に機密情報を取得する
- モジュールスコープでキャッシュし、呼び出しのたびに取得しない
- 取得した値をログに出力しない
"""
import json
import logging
import os
import urllib.error
import urllib.request

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sm = boto3.client("secretsmanager")

SECRET_ARN = os.environ.get("SECRET_ARN", "")

# ★ハンドラ外で保持する。同じLambda実行環境が再利用される間はキャッシュが効く
_webhook_url = None


def get_webhook_url() -> str:
    """Secrets ManagerからWebhook URLを取得する（初回のみ）"""
    global _webhook_url

    if _webhook_url is not None:
        return _webhook_url

    if not SECRET_ARN:
        logger.warning("SECRET_ARN が設定されていません")
        return ""

    try:
        res = sm.get_secret_value(SecretId=SECRET_ARN)
        secret = json.loads(res["SecretString"])
        _webhook_url = secret.get("webhook_url", "")
        # ★取得した値そのものは絶対にログへ出さない
        logger.info("Secrets Manager から設定を読み込みました")
    except Exception:
        logger.exception("シークレットの取得に失敗しました")
        _webhook_url = ""

    return _webhook_url


def post_to_slack(url: str, payload: dict) -> int:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
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

    webhook_url = get_webhook_url()

    if not webhook_url:
        # 未設定でも学習は進められるようにする
        logger.info("Webhook URL未設定のため通知をスキップ:\n%s", text)
        return {**event, "notified": False}

    try:
        status = post_to_slack(webhook_url, {"text": text})
        logger.info("Slack通知 status=%s", status)
    except urllib.error.HTTPError as e:
        # 認証系エラーならキャッシュを破棄して1回だけ再試行する
        # （ローテーションでURLが更新された場合に対応）
        if e.code in (401, 403, 404):
            logger.warning("認証エラー。キャッシュを破棄して再試行します")
            global _webhook_url
            _webhook_url = None
            webhook_url = get_webhook_url()
            if not webhook_url:
                raise
            status = post_to_slack(webhook_url, {"text": text})
            logger.info("Slack通知（再試行） status=%s", status)
        else:
            raise

    return {**event, "notified": True}