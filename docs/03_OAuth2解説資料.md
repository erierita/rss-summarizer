# OAuth2 Client Credentials 解説資料

**目的**: Cognito + API Gateway で認証付きAPI連携を実装する前に、OAuth2の仕組みを理解する

案件要件「REST APIおよび認証機能（OAuth2等）を利用したシステム連携」に対応する知識をまとめます。

---

## 目次

1. [なぜ認証が必要か](#1-なぜ認証が必要か)
2. [OAuth2の全体像](#2-oauth2の全体像)
3. [4つのフローと使い分け](#3-4つのフローと使い分け)
4. [Client Credentials フローの詳細](#4-client-credentials-フローの詳細)
5. [JWTの構造](#5-jwtの構造)
6. [トークン検証で何を見るか](#6-トークン検証で何を見るか)
7. [スコープによる権限分離](#7-スコープによる権限分離)
8. [実装上の勘所](#8-実装上の勘所)
9. [今回作るものの設計](#9-今回作るものの設計)
10. [用語集](#10-用語集)

---

## 1. なぜ認証が必要か

### 素朴な疑問

「APIを呼ぶだけなら、URLを叩けばいいのでは？」

実際、今のSlack通知はそうしています。

```python
urllib.request.urlopen("https://hooks.slack.com/services/T01/B02/xyz")
```

これで動きます。**URLを知っていること自体が認証**になっているためです。

### この方式の限界

| 問題 | 内容 |
|---|---|
| **権限の分離ができない** | URLを持つ人は全員同じことができる |
| **失効させにくい** | 漏れたらURL自体を作り直すしかない |
| **誰が呼んだか分からない** | 監査ログに残せない |
| **有効期限がない** | 一度漏れたら永続的に使える |

### 認証を分ける発想

OAuth2は、この問題を**「資格情報」と「アクセストークン」を分離**することで解決します。

```
資格情報（client_id / client_secret）
  → 長期間有効。厳重に保管する。これ自体ではAPIを呼べない
       ↓ トークン発行要求
アクセストークン
  → 短期間（1時間程度）のみ有効。これでAPIを呼ぶ
```

**トークンが漏れても1時間で無効になる**のが最大の利点です。

---

## 2. OAuth2の全体像

### 登場人物

OAuth2の仕様では4つの役割が定義されています。

| 役割 | 意味 | 今回の実装では |
|---|---|---|
| **クライアント** | APIを呼ぶ側 | Lambda（api_client） |
| **リソースサーバー** | APIを提供する側 | API Gateway + Lambda |
| **認可サーバー** | トークンを発行する | Cognito User Pool |
| **リソースオーナー** | データの持ち主（人） | **Client Credentialsでは登場しない** |

**リソースオーナーがいないのがClient Credentialsの特徴**です。人の代理ではなく、アプリ自身として動きます。

### 基本の流れ

```
[クライアント]                    [認可サーバー]
     │                                 │
     │ ① client_id + client_secret     │
     ├────────────────────────────────▶│
     │                                 │ 資格情報を検証
     │ ② access_token                  │ トークンを発行
     │◀────────────────────────────────┤
     │
     │              [リソースサーバー]
     │ ③ Authorization: Bearer {token}
     ├────────────────────────────────▶│
     │                                 │ トークンを検証
     │ ④ レスポンス                     │
     │◀────────────────────────────────┤
```

**3ステップ**です。資格情報でトークンを取り、トークンでAPIを呼ぶ。

---

## 3. 4つのフローと使い分け

OAuth2には複数の「フロー（グラントタイプ）」があります。

| フロー | 誰として動くか | ユーザー操作 | 用途 |
|---|---|---|---|
| **Client Credentials** | **アプリ自身** | **不要** | **サーバー間連携** |
| Authorization Code | ユーザーの代理 | 必要（初回） | Webアプリ、Gmail連携など |
| Authorization Code + PKCE | ユーザーの代理 | 必要（初回） | モバイルアプリ、SPA |
| Device Code | ユーザーの代理 | 必要 | TV、CLI など入力が困難な端末 |

### Client Credentials が適する場面

```
バッチ処理がAPIを呼ぶ
サーバーAがサーバーBを呼ぶ
定期実行のLambdaが外部APIを呼ぶ
```

**共通点**: 人が介在しない。特定ユーザーのデータではなく、システムとしてアクセスする。

### Authorization Code が必要な場面

```
「あなたのGmailを読みたい」
「あなたのGoogleドライブに保存したい」
```

**ユーザーの同意が必要**なので、ブラウザでの認可画面が挟まります。その代わり `refresh_token` という長期トークンを受け取り、以降は自動更新できます。

### 今回選ぶもの

**Client Credentials** です。理由は3つ。

- サーバー間連携なのでユーザーの同意が不要
- 実装がシンプル（ブラウザ操作が不要）
- 案件で最も遭遇する方式

---

## 4. Client Credentials フローの詳細

### トークン取得リクエスト

```http
POST /oauth2/token HTTP/1.1
Host: your-domain.auth.ap-northeast-1.amazoncognito.com
Content-Type: application/x-www-form-urlencoded
Authorization: Basic {base64(client_id:client_secret)}

grant_type=client_credentials&scope=rss-summarizer/articles.write
```

**ポイント**

| 項目 | 内容 |
|---|---|
| Content-Type | **`application/x-www-form-urlencoded`**。JSONではない |
| 資格情報の渡し方 | Basic認証ヘッダ、またはボディに含める（サービスによる） |
| grant_type | `client_credentials` 固定 |
| scope | 要求する権限。省略可だがCognitoでは指定が必要 |

### レスポンス

```json
{
  "access_token": "eyJraWQiOiJ...",
  "expires_in": 3600,
  "token_type": "Bearer"
}
```

| 項目 | 意味 |
|---|---|
| `access_token` | これをAPIに付けて送る |
| `expires_in` | 有効期限（秒）。3600 = 1時間 |
| `token_type` | 通常 `Bearer` |

**`refresh_token` は返りません。** Client Credentialsでは、期限切れたら再度取得すればよいためです。

### API呼び出し

```http
POST /articles HTTP/1.1
Host: api.example.com
Authorization: Bearer eyJraWQiOiJ...
Content-Type: application/json

{"url_hash": "abc123", "title": "記事タイトル"}
```

`Authorization: Bearer {token}` の形式が標準です。

---

## 5. JWTの構造

Cognitoが発行するアクセストークンは **JWT（JSON Web Token）** です。

### 3つの部分

```
eyJraWQiOiJhYmMiLCJhbGciOiJSUzI1NiJ9 . eyJzdWIiOiJ4eXoiLCJleHAiOjE3... . SflKxwRJSMeKKF2QT4f...
└──────────── ヘッダ ────────────┘   └────── ペイロード ──────┘   └───── 署名 ─────┘
```

**ドット2つで区切られた3ブロック**です。各ブロックはBase64URLエンコードされています。

### ヘッダ

```json
{
  "kid": "abc123...",
  "alg": "RS256"
}
```

| 項目 | 意味 |
|---|---|
| `kid` | Key ID。署名に使った鍵の識別子 |
| `alg` | 署名アルゴリズム。RS256 = RSA + SHA-256 |

### ペイロード（クレーム）

```json
{
  "sub": "5v1a2b3c4d5e6f7g8h9i0j",
  "token_use": "access",
  "scope": "rss-summarizer/articles.write",
  "auth_time": 1789000000,
  "iss": "https://cognito-idp.ap-northeast-1.amazonaws.com/ap-northeast-1_XXXXX",
  "exp": 1789003600,
  "iat": 1789000000,
  "client_id": "abcdefghijklmnop"
}
```

**標準クレーム**

| クレーム | 意味 |
|---|---|
| `iss` | Issuer。**誰が発行したか** |
| `sub` | Subject。主体の識別子 |
| `aud` | Audience。**誰向けか**（Cognitoのアクセストークンでは `client_id` が該当） |
| `exp` | Expiration。**有効期限**（UNIX秒） |
| `iat` | Issued At。発行時刻 |
| `scope` | 許可された権限 |

### 重要：ペイロードは暗号化されていない

**Base64は暗号化ではなくエンコードです。** 誰でもデコードして中身を読めます。

```bash
echo "eyJzdWIiOiJ4eXoi..." | base64 -d
```

**だからJWTに機密情報を入れてはいけません。**

では何のための署名かというと、**改ざん検知**です。ペイロードを書き換えると署名が合わなくなるので、検証側で弾けます。

### 署名の仕組み

```
署名 = RSA暗号化( SHA256(ヘッダ + "." + ペイロード), 秘密鍵 )
```

- **発行側**が秘密鍵で署名する
- **検証側**が公開鍵で検証する

Cognitoの公開鍵は以下のURLで公開されています。

```
https://cognito-idp.{region}.amazonaws.com/{userPoolId}/.well-known/jwks.json
```

**JWKS（JSON Web Key Set）** と呼ばれる形式です。API Gatewayはここから鍵を取得して検証します。

---

## 6. トークン検証で何を見るか

受け取ったトークンが正当かを確認する手順です。

| # | 検証項目 | 何を防ぐか |
|---|---|---|
| 1 | **署名** | 改ざん、偽造 |
| 2 | **`exp`（有効期限）** | 期限切れトークンの再利用 |
| 3 | **`iss`（発行者）** | 別の認可サーバーが発行したトークン |
| 4 | **`aud` / `client_id`** | 別のアプリ向けトークンの流用 |
| 5 | **`token_use`** | IDトークンをアクセストークンとして使う誤用 |
| 6 | **`scope`** | 権限外の操作 |

### API Gateway の JWT Authorizer

今回は**API Gatewayが1〜5を自動で検証**してくれます。自分で実装する必要はありません。

```python
authorizer = apigwv2_authorizers.HttpJwtAuthorizer(
    "JwtAuthorizer",
    jwt_issuer=f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}",
    jwt_audience=[client_id],
)
```

**6のスコープ検証**は、ルートごとに指定します。

```python
http_api.add_routes(
    path="/articles",
    methods=[apigwv2.HttpMethod.POST],
    authorizer=authorizer,
    authorization_scopes=["rss-summarizer/articles.write"],   # ← ここ
    integration=...,
)
```

**自前で検証する場合**は、Pythonなら `python-jose` や `PyJWT` を使います。今回はAPI Gatewayに任せるので不要です。

---

## 7. スコープによる権限分離

### スコープとは

**「このトークンで何ができるか」を表す文字列**です。

```
rss-summarizer/articles.read    記事の参照のみ
rss-summarizer/articles.write   記事の登録も可能
```

### なぜ必要か

同じAPIを複数のクライアントが使う場合、**権限を分けたい**ことがあります。

```
[記事登録バッチ]  → articles.write が必要
[閲覧ダッシュボード] → articles.read だけでよい
```

閲覧用クライアントに書き込み権限を与えないことで、事故や侵害の影響を限定できます。**最小権限の原則**の一形態です。

### Cognitoでの定義

```python
# リソースサーバー = 保護対象のAPIのまとまり
resource_server = user_pool.add_resource_server(
    "ArticleApi",
    identifier="rss-summarizer",          # ← スコープの接頭辞になる
    scopes=[
        cognito.ResourceServerScope(
            scope_name="articles.read",
            scope_description="記事の参照",
        ),
        cognito.ResourceServerScope(
            scope_name="articles.write",
            scope_description="記事の登録",
        ),
    ],
)
```

**完全なスコープ名** = `identifier` + `/` + `scope_name`

```
rss-summarizer/articles.read
rss-summarizer/articles.write
```

### App Client への割り当て

```python
client = user_pool.add_client(
    "BatchClient",
    generate_secret=True,                 # ← client_secret を発行
    o_auth=cognito.OAuthSettings(
        flows=cognito.OAuthFlows(client_credentials=True),
        scopes=[write_scope],             # このクライアントに許す範囲
    ),
)
```

**クライアントに割り当てていないスコープは要求できません。** トークン発行の時点で弾かれます。

---

## 8. 実装上の勘所

実務でハマりやすい点を先に押さえておきます。

### 8-1. トークンをキャッシュする

**最重要です。**

```python
# ハンドラの外に置く（Lambda実行環境が再利用される間、保持される）
_token = None
_expires_at = 0

def get_token() -> str:
    global _token, _expires_at
    if _token and time.time() < _expires_at - 60:   # 60秒のマージン
        return _token
    # ... 取得処理 ...
    _token = data["access_token"]
    _expires_at = time.time() + data["expires_in"]
    return _token
```

キャッシュしないとどうなるか。

| 問題 | 影響 |
|---|---|
| レイテンシ | 1リクエストごとに認可サーバーへの往復が増える |
| コスト | Secrets Manager、認可サーバーの呼び出し回数 |
| **レート制限** | 認可サーバー側でスロットリングされる |

### 8-2. 有効期限にマージンを持たせる

```python
if _token and time.time() < _expires_at - 60:
```

ちょうど期限で切り替えると、**リクエスト処理中に期限切れ**になる可能性があります。60秒程度の余裕を持たせます。

### 8-3. 401 を受けたらキャッシュを破棄

トークンが何らかの理由で無効化されることがあります。

```python
try:
    return call_api(...)
except HTTPError as e:
    if e.code == 401:
        global _token
        _token = None          # キャッシュ破棄
        return call_api(...)   # 1回だけ再試行
    raise
```

**無限ループにしない**よう、再試行は1回に限定します。

### 8-4. 資格情報をログに出さない

```python
# ❌ 絶対にやらない
logger.info("creds: %s", creds)
logger.debug("token: %s", token)

# ⭕️ 存在確認だけ
logger.info("token acquired: %s", bool(token))
```

CloudWatch Logsに出ると、ログ閲覧権限がある全員に漏れます。

### 8-5. Content-Type を間違えない

トークンエンドポイントは **`application/x-www-form-urlencoded`** です。

```python
# ⭕️ 正しい
body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
headers = {"Content-Type": "application/x-www-form-urlencoded"}

# ❌ よくある間違い
body = json.dumps({"grant_type": "client_credentials"}).encode()
headers = {"Content-Type": "application/json"}
```

JSONを送ると `invalid_request` で弾かれます。

### 8-6. Basic認証ヘッダの組み立て

Cognitoは資格情報をBasic認証ヘッダで受け取ります。

```python
import base64

credentials = f"{client_id}:{client_secret}"
encoded = base64.b64encode(credentials.encode()).decode()
headers = {"Authorization": f"Basic {encoded}"}
```

`client_id:client_secret` を**コロンで連結してBase64**、という形式です。

---

## 9. 今回作るものの設計

### 全体構成

```
【呼ぶ側】                              【認可サーバー】
Lambda: api_client                      Cognito User Pool
  │ ① Secrets Manager から取得            ├ Resource Server
  │   client_id / client_secret          │   identifier: rss-summarizer
  │                                      │   scopes: articles.read/write
  │ ② POST /oauth2/token ───────────────▶├ App Client
  │◀──────────── access_token ───────────┘   (client_credentials)
  │
  │ ③ Authorization: Bearer {token}
  ▼                                     【呼ばれる側】
API Gateway (HTTP API)
  ├ JWT Authorizer（署名・exp・iss・audを検証）
  ├ authorization_scopes でスコープ検証
  └─▶ Lambda: article_receiver
         └─▶ DynamoDB (ArticleRegistryTable)
```

### 作成するリソース

| # | リソース | 役割 |
|---|---|---|
| 1 | Cognito User Pool | 認可サーバーの土台 |
| 2 | Resource Server | 保護対象APIとスコープの定義 |
| 3 | User Pool Domain | トークンエンドポイントのURL |
| 4 | App Client | client_id / client_secret の発行元 |
| 5 | Secrets Manager | 資格情報の保管 |
| 6 | DynamoDB (ArticleRegistry) | 登録された記事の保存先 |
| 7 | Lambda: article_receiver | APIの実処理 |
| 8 | API Gateway (HTTP API) | エンドポイント公開 |
| 9 | JWT Authorizer | トークン検証 |
| 10 | Lambda: api_client | トークン取得してAPIを呼ぶ |

### 既存ワークフローへの組み込み

```
Step Functions
  ├ 本文取得
  ├ 本文長を判定
  ├ 要約
  ├ DynamoDBへ保存
  ├ Slack通知
  └ ★ 記事登録API呼び出し（新規追加）
```

### スコープ設計

| スコープ | 用途 | 割り当て先 |
|---|---|---|
| `rss-summarizer/articles.write` | 記事の登録 | api_client（バッチ用クライアント） |
| `rss-summarizer/articles.read` | 記事の参照 | 将来の閲覧用クライアント（今回は定義のみ） |

**readを定義だけして使わない**のは意図的です。「スコープで権限を分ける」という設計を形にしておくためで、後から閲覧用クライアントを追加する際に活きます。

### エンドポイント設計

| メソッド | パス | スコープ | 処理 |
|---|---|---|---|
| POST | /articles | articles.write | 記事を登録 |
| GET | /articles | articles.read | 記事一覧を取得（拡張候補） |

### リクエスト・レスポンス

**POST /articles**

```json
// リクエスト
{
  "url_hash": "f340f06190c76be66b5984281afe4b3e",
  "url": "https://aws.amazon.com/jp/blogs/news/iso-27001/",
  "title": "Kiro が ISO/IEC 27001:2022 のカバレッジに対応",
  "summary": "・要約1行目\n・要約2行目\n・要約3行目"
}

// レスポンス（201 Created）
{
  "registered": true,
  "url_hash": "f340f06190c76be66b5984281afe4b3e"
}
```

**エラーレスポンス**

| ステータス | 条件 |
|---|---|
| 400 | 必須項目の欠落 |
| 401 | トークンが無効・期限切れ |
| 403 | スコープ不足 |
| 500 | サーバー内部エラー |

---

## 10. 用語集

| 用語 | 意味 |
|---|---|
| **OAuth 2.0** | 認可のためのフレームワーク。「誰が何をしてよいか」を扱う |
| **認証（Authentication）** | 「誰であるか」の確認 |
| **認可（Authorization）** | 「何をしてよいか」の決定 |
| **グラントタイプ** | トークン取得の方式。フローとも呼ぶ |
| **Client Credentials** | アプリ自身の資格情報でトークンを得る方式 |
| **アクセストークン** | APIを呼ぶための短期の鍵 |
| **リフレッシュトークン** | アクセストークンを更新するための長期の鍵（Client Credentialsでは発行されない） |
| **スコープ** | トークンに付与された権限の範囲 |
| **Bearer トークン** | 「持参人式」。持っている人が使える形式 |
| **JWT** | JSON Web Token。署名付きのトークン形式 |
| **クレーム** | JWTのペイロードに含まれる各項目 |
| **JWKS** | JSON Web Key Set。公開鍵の配布形式 |
| **kid** | Key ID。どの鍵で署名したかの識別子 |
| **iss / aud / exp** | 発行者 / 対象者 / 有効期限。検証に使う標準クレーム |
| **リソースサーバー** | 保護されたAPIを提供する側 |
| **認可サーバー** | トークンを発行する側 |
| **User Pool（Cognito）** | ユーザーとクライアントを管理する単位 |
| **Resource Server（Cognito）** | 保護対象APIとスコープを定義する設定 |
| **App Client（Cognito）** | アプリケーションの登録。client_id/secretの発行元 |

---

## 理解度チェック

実装前に、以下に答えられるか確認してください。

1. Client Credentials フローで `refresh_token` が発行されないのはなぜか
2. JWTのペイロードに機密情報を入れてはいけないのはなぜか
3. トークンをキャッシュすべき理由を3つ挙げよ
4. 有効期限のチェックに60秒のマージンを持たせる理由
5. スコープを `read` と `write` に分ける目的
6. トークン検証で `iss` を確認する理由

**解答**

1. トークンの期限が切れたら、保持している client_id/secret で再取得すればよいため。ユーザーの同意を伴わないので、長期の代理権限を持つ必要がない
2. Base64エンコードは暗号化ではなく、誰でもデコードして読めるため。署名は改ざん検知のためであり、内容の秘匿はしない
3. レイテンシの削減、認可サーバーへの呼び出しコスト削減、レート制限の回避
4. ちょうど期限で切り替えると、リクエスト処理の途中で期限切れになる可能性があるため
5. 最小権限の原則。閲覧しかしないクライアントに書き込み権限を与えないことで、事故や侵害の影響範囲を限定する
6. 別の認可サーバーが発行したトークンを受け入れてしまわないため。署名が正しくても、発行元が想定と違えば拒否する必要がある

---

## 次のステップ

この資料の内容が概ね理解できたら、実装に進みます。

実装は3段階で進めます。

| 段階 | 内容 |
|---|---|
| 1 | Cognito（認可サーバー）とSecrets Managerを作る |
| 2 | API Gateway + Lambda（呼ばれる側）を作る |
| 3 | Lambda（呼ぶ側）を実装し、Step Functionsに組み込む |

各段階でデプロイして動作確認しながら進めます。
