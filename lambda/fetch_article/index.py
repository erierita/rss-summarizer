"""
記事本文を取得し、HTMLタグを除去してS3へ保存する。

学習ポイント:
- Step FunctionsのTaskとして呼ばれるLambdaの入出力
  → payload_response_only=True にしているので、returnした辞書がそのまま次のステートへ渡る
- S3へのオブジェクト書き込み
- 外部ライブラリを使わずHTMLからテキストを抽出する
"""
import logging
import os
import re
import time
import urllib.request
from html.parser import HTMLParser

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
BUCKET_NAME = os.environ["BUCKET_NAME"]

USER_AGENT = "Mozilla/5.0 (compatible; RssSummarizer/1.0; learning purpose)"
TTL_DAYS = 30


class TextExtractor(HTMLParser):
    """script/style を除いたテキストだけを集める簡易パーサ"""

    def __init__(self):
        super().__init__()
        self.parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False

    def handle_data(self, data):
        if not self._skip:
            text = data.strip()
            if text:
                self.parts.append(text)

    def get_text(self) -> str:
        return re.sub(r"\s+", " ", " ".join(self.parts)).strip()


def handler(event, context):
    logger.info("input: %s", event)

    url = event["url"]
    url_hash = event["url_hash"]

    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as res:
        charset = res.headers.get_content_charset() or "utf-8"
        html = res.read().decode(charset, errors="replace")

    parser = TextExtractor()
    parser.feed(html)
    body = parser.get_text()

    s3_key = f"articles/{url_hash}.txt"
    s3.put_object(
        Bucket=BUCKET_NAME,
        Key=s3_key,
        Body=body.encode("utf-8"),
        ContentType="text/plain; charset=utf-8",
    )
    logger.info("S3へ保存: s3://%s/%s (%d文字)", BUCKET_NAME, s3_key, len(body))

    # 次のステートへ渡す情報。入力をマージして返すのがポイント
    return {
        **event,
        "s3_key": s3_key,
        "body_length": len(body),
        "ttl": int(time.time()) + TTL_DAYS * 24 * 3600,
    }
