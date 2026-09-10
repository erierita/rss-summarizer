"""
RSSフィードを取得し、記事URLを1件ずつSQSへ投入する。

学習ポイント:
- EventBridgeから起動されるLambdaのevent構造
- boto3でSQSにメッセージを送る
- 標準ライブラリだけでRSSをパースする（外部ライブラリ不要 = パッケージング不要）
"""
import hashlib
import json
import logging
import os
import urllib.request
import xml.etree.ElementTree as ET

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

sqs = boto3.client("sqs")

QUEUE_URL = os.environ["QUEUE_URL"]
FEED_URLS = [u.strip() for u in os.environ["FEED_URLS"].split(",") if u.strip()]
MAX_ARTICLES = int(os.environ.get("MAX_ARTICLES", "3"))

USER_AGENT = "Mozilla/5.0 (compatible; RssSummarizer/1.0; learning purpose)"


def fetch_url(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()


def parse_rss(xml_bytes: bytes) -> list[dict]:
    """RSS 2.0 / Atom の両方をゆるくパースする"""
    root = ET.fromstring(xml_bytes)
    items = []

    # RSS 2.0: <rss><channel><item>
    for item in root.iter("item"):
        title = item.findtext("title", default="").strip()
        link = item.findtext("link", default="").strip()
        if title and link:
            items.append({"title": title, "url": link})

    # Atom: <feed><entry>
    if not items:
        ns = {"atom": "http://www.w3.org/2005/Atom"}
        for entry in root.findall("atom:entry", ns):
            title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
            link_el = entry.find("atom:link", ns)
            link = link_el.get("href", "").strip() if link_el is not None else ""
            if title and link:
                items.append({"title": title, "url": link})

    return items


def handler(event, context):
    logger.info("event: %s", json.dumps(event, ensure_ascii=False))

    sent = 0
    for feed_url in FEED_URLS:
        try:
            xml_bytes = fetch_url(feed_url)
            articles = parse_rss(xml_bytes)[:MAX_ARTICLES]
            logger.info("feed=%s articles=%d", feed_url, len(articles))

            for a in articles:
                # url_hash を採番。DynamoDBのキー兼、冪等性の判定に使う
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
            # 1つのフィードが失敗しても他は処理を続ける
            logger.exception("フィード取得に失敗: %s", feed_url)

    logger.info("SQSへ投入: %d件", sent)
    return {"sent": sent}
