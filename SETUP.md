# AWS CDK 開発環境 構築手順書（WSL2版）

RSS要約システムを題材に、WSL2上での開発環境構築から初回デプロイ、
動作確認、後片付けまでを順を追って行います。

**前提環境**: Windows 10 (バージョン2004以降) または Windows 11
**所要時間**: 初回セットアップ 2〜3時間

---

## なぜWSL2を使うのか

Lambdaの実行環境は **Amazon Linux** です。Windowsネイティブで開発すると、
次のような差異でつまずきます。

| 問題 | 具体例 |
|---|---|
| 改行コード | Windows は CRLF、Linux は LF |
| パス区切り | `\` と `/` |
| ネイティブ拡張を含むライブラリ | Windowsでビルドすると Lambda で動かない |
| 教材・公式ドキュメント | ほぼ Linux/macOS 前提のコマンド |

WSL2上で開発すれば、本番環境（Linux）とほぼ同じ条件で作業できます。

> **Dev Container は今は不要です。**
> Docker と devcontainer.json の学習コストが増えるうえ、
> AWS認証情報の受け渡しやCDKのバンドリングで別のトラブルが発生します。
> 案件側で指定されたときに対応すれば十分です。

---

## 構成イメージ

```
Windows 11
 ├ VS Code（Windows側にインストール）
 │    └ WSL拡張でWSL内に接続
 └ WSL2 (Ubuntu 24.04)
      ├ Python 3.12 / Node.js / AWS CLI / CDK / Git
      └ ~/dev/rss-summarizer   ← プロジェクトはここに置く
                ↓ cdk deploy
             【AWS】
```

---

## 目次

- [1. AWSアカウントの準備](#1-awsアカウントの準備)
- [2. WSL2のインストール](#2-wsl2のインストール)
- [3. Ubuntu内にツールをインストール](#3-ubuntu内にツールをインストール)
- [4. AWS認証情報の設定](#4-aws認証情報の設定)
- [5. VS CodeとWSLの接続](#5-vs-codeとwslの接続)
- [6. プロジェクトの配置](#6-プロジェクトの配置)
- [7. CDK Bootstrap](#7-cdk-bootstrap)
- [8. 初回デプロイ](#8-初回デプロイ)
- [9. 動作確認](#9-動作確認)
- [10. 開発サイクル](#10-開発サイクル)
- [11. Git管理](#11-git管理)
- [12. 後片付け](#12-後片付け)
- [13. トラブルシューティング](#13-トラブルシューティング)

---

## 1. AWSアカウントの準備

この章だけは **Windows側のブラウザ** で作業します。

### 1-1. アカウント作成

[https://aws.amazon.com/jp/](https://aws.amazon.com/jp/) から作成します。
既にアカウントがある場合はスキップしてください。

### 1-2. ルートユーザーにMFAを設定（必須）

ルートユーザーは全権限を持つため、必ず多要素認証を設定します。

1. コンソール右上のアカウント名 → **セキュリティ認証情報**
2. **多要素認証 (MFA)** → **MFAデバイスの割り当て**
3. スマホの認証アプリ（Google Authenticator, Authy など）で登録

### 1-3. 作業用IAMユーザーを作成

**ルートユーザーで日常作業をしてはいけません。**

1. コンソールで **IAM** を開く
2. **ユーザー** → **ユーザーを作成**
3. ユーザー名: `cdk-dev`
4. **AWSマネジメントコンソールへのアクセスを提供する** にチェック
5. 権限: **ポリシーを直接アタッチ** → `AdministratorAccess`
   - 学習用のため。実務では最小権限に絞ります
6. 作成後、このユーザーでログインし直す
7. このユーザーにも **MFAを設定**

### 1-4. アクセスキーを発行

1. IAM → ユーザー → `cdk-dev` → **セキュリティ認証情報** タブ
2. **アクセスキーを作成**
3. ユースケース: **コマンドラインインターフェイス (CLI)**
4. 確認にチェックを入れて作成
5. **アクセスキーID** と **シークレットアクセスキー** を控える
   - シークレットはこの画面でしか表示されません
   - **絶対にGitにコミットしない・人に共有しない**

### 1-5. 予算アラートを設定（必須）

想定外の課金を防ぎます。**この手順は飛ばさないでください。**

1. コンソールで **AWS Budgets** を開く
2. **予算を作成** → **カスタム予算**
3. 予算タイプ: **コスト予算**
4. 期間: **月別**、予算額: **5 USD**
5. アラートのしきい値: **80%** と **100%** の2つ
6. 通知先メールアドレスを設定

### 1-6. リージョン

本手順書では **東京リージョン (`ap-northeast-1`)** を使います。

> **注意**: Bedrockは一部モデルが東京リージョンで使えない場合があります。
> その場合は `us-east-1`（バージニア北部）を使ってください。
> リージョンをまたぐとリソースが見えなくなるため、統一することが重要です。

---

## 2. WSL2のインストール

ここから **Windows側のPowerShell** で作業します。

### 2-1. WSL2とUbuntuを導入

PowerShellを **管理者として実行** し、以下を入力します。

```powershell
wsl --install -d Ubuntu-24.04
```

インストール後、**PCを再起動**します。

再起動するとUbuntuのウィンドウが開き、初期設定を求められます。

```
Enter new UNIX username: <好きなユーザー名>
New password: <パスワード>
Retype new password: <同じパスワード>
```

> ここで入力するパスワードは、Ubuntu内で `sudo` を使うときに必要です。
> Windowsのパスワードとは別物です。**忘れないようにしてください。**

### 2-2. バージョンを確認

PowerShellで:

```powershell
wsl -l -v
```

こう表示されればOKです。

```
  NAME              STATE           VERSION
* Ubuntu-24.04      Running         2
```

**`VERSION` が `2`** であることを確認してください。`1` の場合:

```powershell
wsl --set-version Ubuntu-24.04 2
```

### 2-3. メモリ使用量の制限（任意）

WSL2はWindowsのメモリを大きく使うことがあります。
気になる場合は `C:\Users\<ユーザー名>\.wslconfig` を作成します。

```ini
[wsl2]
memory=8GB
processors=4
```

反映するには一度シャットダウンします。

```powershell
wsl --shutdown
```

### 2-4. WSLターミナルの起動方法

以降の作業は **Ubuntuのターミナル** で行います。開き方はいくつかあります。

- スタートメニューから **Ubuntu 24.04** を起動
- **Windows Terminal** を開き、タブの `∨` から Ubuntu を選択（推奨）
- PowerShellで `wsl` と入力

> Windows Terminal はタブ機能があり使いやすいので、
> 未導入なら `winget install Microsoft.WindowsTerminal` で入れておくと便利です。

---

## 3. Ubuntu内にツールをインストール

ここから先は **すべてUbuntuのターミナル** で作業します。

### 3-1. パッケージを更新

```bash
sudo apt update && sudo apt upgrade -y
```

パスワードを聞かれたら、2-1で設定したものを入力します。

### 3-2. 基本ツール

```bash
sudo apt install -y git curl unzip build-essential
```

### 3-3. Python

Ubuntu 24.04 は Python 3.12 が標準で入っています。

```bash
sudo apt install -y python3 python3-pip python3-venv
python3 --version
```

`Python 3.12.x` と表示されればOKです。

### 3-4. Node.js（nvm経由）

CDK CLI は Node.js 上で動きます。バージョン管理のしやすい nvm を使います。

```bash
curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc

nvm install --lts
nvm use --lts

node --version    # v22.x.x など
npm --version
```

> `nvm: command not found` と出た場合はターミナルを開き直してください。

### 3-5. AWS CDK CLI

```bash
npm install -g aws-cdk
cdk --version    # 2.x.x (build xxxxx)
```

### 3-6. AWS CLI v2

```bash
curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "awscliv2.zip"
unzip awscliv2.zip
sudo ./aws/install
aws --version    # aws-cli/2.x.x

# 後片付け
rm -rf awscliv2.zip aws
```

### 3-7. インストール確認

```bash
echo "--- versions ---"
python3 --version
node --version
npm --version
cdk --version
aws --version
git --version
```

すべてバージョンが表示されれば完了です。

---

## 4. AWS認証情報の設定

### 4-1. プロファイルを作成

**WSL内の認証情報はWindows側とは別管理**です。改めて設定します。

```bash
aws configure --profile cdk-study
```

対話形式で入力します。

```
AWS Access Key ID [None]:     <1-4で控えたアクセスキーID>
AWS Secret Access Key [None]: <1-4で控えたシークレット>
Default region name [None]:   ap-northeast-1
Default output format [None]: json
```

> **名前付きプロファイルを推奨する理由**
> 案件が始まると業務用の認証情報も設定することになります。
> 最初から分けておくと、誤って業務環境にデプロイする事故を防げます。

### 4-2. 保存場所

```bash
ls -la ~/.aws/
cat ~/.aws/config
```

- `~/.aws/credentials` … アクセスキー（**機密**）
- `~/.aws/config` … リージョンや出力形式

### 4-3. 疎通確認

```bash
aws sts get-caller-identity --profile cdk-study
```

こう返れば成功です。

```json
{
    "UserId": "AIDA...",
    "Account": "123456789012",
    "Arn": "arn:aws:iam::123456789012:user/cdk-dev"
}
```

### 4-4. プロファイルを既定にする

毎回 `--profile` を付けるのは面倒なので、環境変数に設定します。

```bash
echo 'export AWS_PROFILE=cdk-study' >> ~/.bashrc
source ~/.bashrc

# 確認（--profile なしで通ればOK）
aws sts get-caller-identity
```

---

## 5. VS CodeとWSLの接続

### 5-1. VS Codeは「Windows側」にインストール

WSL内には入れません。Windows側のVS Codeから、WSL内に接続する形になります。

**Windows側のPowerShell** で:

```powershell
winget install Microsoft.VisualStudioCode
```

既に入っている場合はスキップします。

### 5-2. WSL拡張機能をインストール

1. Windows側でVS Codeを起動
2. 左サイドバーの拡張機能アイコン（四角が4つ）
3. **WSL**（発行元: Microsoft）を検索してインストール

### 5-3. WSLから起動する

**Ubuntuのターミナル** で:

```bash
cd ~
code .
```

初回は VS Code Server がWSL内に自動インストールされます（数十秒）。

VS Codeが開いたら、**左下に「WSL: Ubuntu-24.04」** と表示されていることを確認してください。これが接続成功の目印です。

### 5-4. 拡張機能をWSL側にインストール

**重要**: WSL接続中に入れる拡張機能は、WSL側にインストールされます。

以下を検索してインストールしてください。ボタンに
「**Install in WSL: Ubuntu-24.04**」と表示されます。

| 拡張機能 | 発行元 | 用途 |
|---|---|---|
| **Python** | Microsoft | 補完・デバッグ |
| **Pylance** | Microsoft | 型チェック |
| **AWS Toolkit** | Amazon Web Services | ログ閲覧・Step Functions可視化 |
| **YAML** | Red Hat | CloudFormationテンプレート閲覧 |

### 5-5. AWS Toolkit に認証情報を接続

1. VS Code左サイドバーの **AWS** アイコン
2. **Select AWS Credentials Profile** → `cdk-study`
3. リージョンを `ap-northeast-1` に設定

> これでVS Code内から CloudWatch Logs を直接見られます。
> デバッグのたびにブラウザを開かずに済むので効率が上がります。

---

## 6. プロジェクトの配置

### 6-1. 【最重要】ファイルの置き場所

**プロジェクトは必ずWSL内のファイルシステムに置いてください。**

```bash
# ⭕️ 正しい
~/dev/rss-summarizer

# ❌ 避ける（Windows側のマウント）
/mnt/c/Users/sport/dev/rss-summarizer
```

**理由**: `/mnt/c/` はWindowsとWSLの境界をまたぐため、
ファイルI/Oが **10倍以上遅くなります**。
`npm install` や `pip install` が異常に遅い場合、ほぼこれが原因です。

### 6-2. zipを展開

配布された `rss-summarizer.zip` がWindowsのダウンロードフォルダにある想定です。

```bash
mkdir -p ~/dev
cd ~/dev

# Windows側からコピー（パスは環境に合わせて変更）
cp /mnt/c/Users/sport/Downloads/rss-summarizer.zip .

unzip rss-summarizer.zip
rm rss-summarizer.zip
cd rss-summarizer
ls -la
```

### 6-3. VS Codeで開く

```bash
code .
```

### 6-4. Python仮想環境を作成

プロジェクトごとにライブラリを隔離します。

```bash
python3 -m venv .venv
source .venv/bin/activate
```

プロンプトの先頭に `(.venv)` が付けば有効化されています。

> 新しいターミナルを開くたびに `source .venv/bin/activate` が必要です。

### 6-5. 依存パッケージをインストール

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

インストールされるもの:
- `aws-cdk-lib` … CDKの本体ライブラリ
- `constructs` … CDKの基底クラス

### 6-6. VS Codeにインタプリタを認識させる

1. `Ctrl+Shift+P` でコマンドパレット
2. `Python: Select Interpreter`
3. `./.venv/bin/python` を選択

これで補完が効くようになります。

---

## 7. CDK Bootstrap

### 7-1. Bootstrapとは

CDKがデプロイに使う土台リソース（S3バケット、IAMロール、ECRリポジトリ）を
AWSアカウントに1回だけ作成する作業です。

**アカウント × リージョンごとに1回だけ**実行します。

### 7-2. 実行

```bash
cdk bootstrap
```

`AWS_PROFILE` を設定済みなのでオプション不要です。
明示したい場合:

```bash
cdk bootstrap aws://<アカウントID>/ap-northeast-1 --profile cdk-study
```

成功すると `CDKToolkit` というCloudFormationスタックが作られます。

---

## 8. 初回デプロイ

### 8-1. Bedrockのモデルアクセスを有効化（Phase 4で必要）

要約機能を使う段階になったら設定します。今すぐでなくても構いません。

1. コンソールで **Amazon Bedrock** を開く
2. 左メニュー **モデルアクセス**
3. 使いたいモデル（Claude 3.5 Haiku など）で **アクセスをリクエスト**
4. 承認まで数分かかることがあります

### 8-2. 環境変数（任意）

Slack通知を使う場合のみ設定します。未設定でもログ出力で動作確認できます。

```bash
export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."
```

永続化するなら:

```bash
echo 'export SLACK_WEBHOOK_URL="https://hooks.slack.com/services/..."' >> ~/.bashrc
```

> **注意**: `.bashrc` に機密情報を書くとバックアップ時に漏れる可能性があります。
> 学習用途なら許容範囲ですが、本番では Secrets Manager を使ってください。

### 8-3. 差分を確認する（重要な習慣）

デプロイ前に必ず何が変わるか確認します。

```bash
cdk diff
```

初回は全リソースが `[+]`（新規作成）で表示されます。

> **この習慣は実務で非常に重要です。**
> チーム開発では、PRに `cdk diff` の結果を貼ることで
> 「このPRでインフラがどう変わるか」をレビュアーに示せます。

### 8-4. 合成結果を見る（学習用）

```bash
cdk synth
```

`cdk.out/RssSummarizerStack.template.json` が生成されます。
**PythonのCDKコードが最終的にCloudFormationのJSONになる**ことを
実感できるので、一度は中身を覗いてみてください。

```bash
less cdk.out/RssSummarizerStack.template.json
```

### 8-5. デプロイ

```bash
cdk deploy
```

IAMの変更内容が表示され、確認を求められます。

```
Do you wish to deploy these changes (y/n)?
```

`y` を入力します。5〜10分ほどで完了し、出力が表示されます。

```
Outputs:
RssSummarizerStack.TableName = RssSummarizerStack-ArticleTable...
RssSummarizerStack.BucketName = rssummarizerstack-articlebucket...
RssSummarizerStack.QueueUrl = https://sqs.ap-northeast-1.amazonaws.com/...
RssSummarizerStack.DlqUrl = https://sqs.ap-northeast-1.amazonaws.com/...
RssSummarizerStack.StateMachineArn = arn:aws:states:...
RssSummarizerStack.FetchFeedFunctionName = RssSummarizerStack-FetchFeedFunction...
```

**この出力値は動作確認で使うので控えておいてください。**

> **段階的に進める場合**
> README.md の Phase 1 から順に進めたい場合は、
> `stacks/rss_summarizer_stack.py` の後半（Step Functions以降）を
> 一旦コメントアウトしてからデプロイしてください。

---

## 9. 動作確認

### 9-1. Lambdaを手動で起動

定期実行を待たずにパイプラインを動かします。

```bash
aws lambda invoke \
  --function-name <FetchFeedFunctionNameの値> \
  --cli-binary-format raw-in-base64-out \
  --payload '{}' \
  response.json

cat response.json
```

### 9-2. ログを見る

**リアルタイムで追う（最もよく使う）:**

```bash
aws logs tail /aws/lambda/<関数名> --follow
```

**直近10分のログ:**

```bash
aws logs tail /aws/lambda/<関数名> --since 10m
```

**VS CodeのAWS Toolkitから:**
1. 左サイドバーのAWSアイコン
2. CloudWatch Logs → ロググループを展開
3. ログストリームをクリック

### 9-3. SQSの状態を確認

```bash
aws sqs get-queue-attributes \
  --queue-url <QueueUrlの値> \
  --attribute-names ApproximateNumberOfMessages
```

### 9-4. Step Functionsの実行を確認

**ここが最も重要な確認ポイントです。**

Windows側のブラウザでコンソールを開きます。

1. **Step Functions** を開く
2. ステートマシン → **実行** タブ
3. 実行をクリックすると **グラフビュー** が表示される
4. 各ステートをクリックすると **入力 / 出力** が見られる

> **`ResultPath` の挙動を理解するには、この画面を見るのが一番早いです。**
> ステート間でデータがどう変化するかを目で追ってください。

CLIで確認する場合:

```bash
aws stepfunctions list-executions --state-machine-arn <ARN>
aws stepfunctions describe-execution --execution-arn <実行ARN>
```

### 9-5. DynamoDBを確認

```bash
aws dynamodb scan --table-name <TableNameの値> --max-items 5
```

### 9-6. S3を確認

```bash
aws s3 ls s3://<BucketNameの値>/articles/
aws s3 cp s3://<BucketNameの値>/articles/<キー> - | head -50
```

---

## 10. 開発サイクル

### 10-1. 基本の流れ

```
1. VS Codeでコードを修正
2. cdk diff          何が変わるか確認
3. cdk deploy        AWSに反映
4. 動作確認・ログ確認
5. 修正 → 2へ戻る
6. 動いたら git commit
```

### 10-2. デプロイを高速化

`cdk deploy` は毎回5分前後かかります。
**Lambdaのコードだけ直した場合**は数秒に短縮できます。

```bash
cdk deploy --hotswap
```

> CloudFormationを経由せず直接Lambdaを更新します。
> **開発環境専用**です。本番では使わないでください。

**ファイル変更を検知して自動デプロイ:**

```bash
cdk watch
```

### 10-3. よく使うコマンド集

```bash
# --- 仮想環境 ---
source .venv/bin/activate       # 毎回必要

# --- CDK ---
cdk ls                          # スタック一覧
cdk diff                        # 差分確認
cdk synth                       # テンプレート生成
cdk deploy                      # デプロイ
cdk deploy --hotswap            # 高速デプロイ（開発用）
cdk destroy                     # 削除

# --- ログ ---
aws logs tail /aws/lambda/<関数名> --follow
aws logs tail /aws/lambda/<関数名> --since 10m

# --- Lambda ---
aws lambda invoke --function-name <関数名> \
  --cli-binary-format raw-in-base64-out --payload '{}' out.json

# --- SQS ---
aws sqs send-message --queue-url <URL> --message-body '{"test":1}'
aws sqs receive-message --queue-url <DLQ_URL>
aws sqs purge-queue --queue-url <URL>        # キューを空にする

# --- Step Functions ---
aws stepfunctions list-executions --state-machine-arn <ARN>

# --- DynamoDB ---
aws dynamodb scan --table-name <テーブル名> --max-items 5
```

### 10-4. WSL特有の便利技

```bash
# Windowsのエクスプローラーで現在のフォルダを開く
explorer.exe .

# Windows側のファイルにアクセス
ls /mnt/c/Users/sport/Downloads/

# WSLを再起動（PowerShellから）
# wsl --shutdown
```

---

## 11. Git管理

### 11-1. Gitの初期設定

WSL内で初めてGitを使う場合、名前とメールを設定します。

```bash
git config --global user.name "Kojima Hiroshi"
git config --global user.email "your@email.com"

# 改行コードの扱い（WSLはLFのまま）
git config --global core.autocrlf input
```

### 11-2. リポジトリを初期化

```bash
cd ~/dev/rss-summarizer
git init
git add .
git commit -m "初期コミット: RSS要約システム"
```

### 11-3. コミットしてはいけないもの

`.gitignore` に記載済みですが、改めて確認してください。

```
❌ .venv/              仮想環境
❌ cdk.out/            生成物
❌ __pycache__/
❌ .env                機密情報
❌ AWSのアクセスキー・シークレット
❌ Slack Webhook URL などのトークン
```

**コミット前の確認:**

```bash
git status
git diff --cached      # ステージされた内容を確認
```

> **この案件はセキュリティ部門のツール開発です。**
> 機密情報の混入は特に厳しく見られる領域なので、
> コミット前の確認を習慣にしてください。

### 11-4. 機密情報の正しい扱い方

| 方法 | 用途 |
|---|---|
| 環境変数 | ローカル開発時 |
| **AWS Secrets Manager** | 本番の認証情報。ローテーション可 |
| **SSM Parameter Store** | 設定値。SecureStringで暗号化 |

CDKで Secrets Manager を使う例:

```python
from aws_cdk import aws_secretsmanager as sm

secret = sm.Secret.from_secret_name_v2(self, "SlackSecret", "slack/webhook")
secret.grant_read(notify_fn)
notify_fn.add_environment("SECRET_ARN", secret.secret_arn)
```

Lambda側で実行時に取得すれば、**コードにもテンプレートにも値が残りません。**

### 11-5. GitHubへpush

```bash
git remote add origin https://github.com/<ユーザー名>/rss-summarizer.git
git branch -M main
git push -u origin main
```

初回はGitHubの認証を求められます。
**パスワードではなくPersonal Access Token**が必要です。
GitHub → Settings → Developer settings → Personal access tokens から発行してください。

認証情報をキャッシュする設定:

```bash
git config --global credential.helper store
```

### 11-6. ブランチ運用の練習

```bash
git switch -c feature/add-step-functions
# 作業
git add .
git commit -m "Step Functionsのワークフローを追加"
git push -u origin feature/add-step-functions
# GitHubでプルリクエストを作成
```

---

## 12. 後片付け

### 12-1. 定期実行を止める

学習を中断するときは、EventBridgeルールを無効化します。

`stacks/rss_summarizer_stack.py`:

```python
rule = events.Rule(
    self, "DailyRule",
    schedule=events.Schedule.cron(minute="0", hour="0"),
    enabled=False,        # ← True から False に変更
)
```

```bash
cdk deploy
```

### 12-2. 全リソースを削除

学習が終わったら必ず削除してください。

```bash
cdk destroy
```

確認プロンプトで `y` を入力します。

### 12-3. 削除されないもの

| リソース | 対応 |
|---|---|
| CloudWatch Logs のロググループ | 本教材では1週間で自動削除設定済み |
| CDKToolkit スタック | 他のCDKプロジェクトでも使うので残してよい |

### 12-4. 課金の確認

コンソールの **Billing and Cost Management** → **コストエクスプローラー** で確認するのが確実です。

---

## 13. トラブルシューティング

### WSL関連

| 症状 | 対処 |
|---|---|
| `wsl --install` が失敗する | Windowsの機能で「仮想マシンプラットフォーム」「Linux用Windowsサブシステム」を有効化。BIOSで仮想化支援機能(VT-x/AMD-V)を有効化 |
| `VERSION` が `1` になっている | `wsl --set-version Ubuntu-24.04 2` |
| `npm install` / `pip install` が異常に遅い | プロジェクトが `/mnt/c/` にある。`~/dev/` 配下に移動する |
| ネットワークに繋がらない | PowerShellで `wsl --shutdown` して再起動 |
| ディスク容量が肥大化 | `wsl --shutdown` 後、`diskpart` の `compact vdisk` で圧縮 |
| `code .` が動かない | Windows側にVS Codeと **WSL拡張** が入っているか確認 |
| メモリを使いすぎる | `.wslconfig` で `memory=8GB` などを設定 |

### インストール・設定関連

| 症状 | 対処 |
|---|---|
| `nvm: command not found` | ターミナルを開き直す。または `source ~/.bashrc` |
| `cdk: command not found` | `npm install -g aws-cdk` が未実行。`nvm use --lts` でNode.jsが有効か確認 |
| `Unable to locate credentials` | `aws configure --profile cdk-study` を実行。`echo $AWS_PROFILE` で確認 |
| `python3 -m venv` が失敗 | `sudo apt install python3-venv` |
| 仮想環境が有効にならない | `source .venv/bin/activate`（`./` ではなく `source`） |

### CDK関連

| 症状 | 対処 |
|---|---|
| `This stack uses assets, so the toolkit stack must be deployed` | `cdk bootstrap` が未実行 |
| `Need to perform AWS calls but no credentials configured` | 認証情報が読めていない。`aws sts get-caller-identity` で確認 |
| デプロイが `ROLLBACK_COMPLETE` で止まる | コンソールのCloudFormation → イベントタブでエラー原因を確認 |
| `cdk destroy` でS3バケットが消せない | `auto_delete_objects=True` を確認。手動で空にしてから再実行 |

### 実行時エラー

| 症状 | 見るところ |
|---|---|
| `AccessDenied` | IAMロールの権限。CDKの `grant_*` を書き忘れていないか |
| Lambdaがタイムアウト | `timeout` の設定。HTTP取得なら30秒以上必要 |
| SQSのメッセージが何度も処理される | 可視性タイムアウト < Lambdaタイムアウト になっていないか |
| Step Functionsでデータが渡らない | `ResultPath` / `Parameters` の設定。実行履歴で各ステートの入出力を確認 |
| Bedrockで `AccessDeniedException` | モデルアクセスが未承認。コンソールで有効化 |
| Bedrockで `ValidationException` | モデルIDとリージョンの組み合わせが不正 |

### 情報源の優先順位

エラーが出たときは、この順で確認してください。

1. **CloudWatch Logs** … Lambdaのエラーはここに全部出る
2. **Step Functions の実行履歴** … どのステートで失敗したか、入出力は何か
3. **CloudFormation のイベント** … デプロイ失敗の原因
4. **DLQ の中身** … 処理に失敗したメッセージ

> **エラーメッセージを読む習慣が最も重要です。**
> AWSのエラーは比較的丁寧に書かれており、
> 「何の権限が足りないか」「どのリソースが見つからないか」が
> ほぼそのまま書かれています。

---

## チェックリスト

### AWS準備
- [ ] AWSアカウント作成済み
- [ ] ルートユーザーにMFA設定済み
- [ ] 作業用IAMユーザー（`cdk-dev`）作成済み・MFA設定済み
- [ ] アクセスキーを発行して控えた
- [ ] **予算アラート設定済み**

### WSL環境
- [ ] WSL2 + Ubuntu 24.04 インストール済み
- [ ] `wsl -l -v` で VERSION 2 を確認
- [ ] Python / Node.js / CDK / AWS CLI / Git インストール済み
- [ ] `aws sts get-caller-identity` が成功する
- [ ] `AWS_PROFILE` を `.bashrc` に設定した

### VS Code
- [ ] Windows側にVS Codeをインストール
- [ ] WSL拡張をインストール
- [ ] `code .` でWSLに接続できる（左下に「WSL: Ubuntu-24.04」表示）
- [ ] Python / Pylance / AWS Toolkit をWSL側にインストール

### プロジェクト
- [ ] **プロジェクトを `~/dev/` 配下に配置**（`/mnt/c/` ではない）
- [ ] 仮想環境を作成し有効化した
- [ ] `pip install -r requirements.txt` 完了
- [ ] `cdk bootstrap` 完了
- [ ] `cdk synth` が成功する
- [ ] `cdk deploy` が成功する
- [ ] Lambdaを手動実行してログが見られた
- [ ] Step Functionsの実行履歴を見られた

### Git
- [ ] `git config` で名前とメールを設定
- [ ] `git init` 済み
- [ ] `.gitignore` の内容を確認した
- [ ] 機密情報がコミットされていないことを確認した

---

## 次のステップ

環境構築が終わったら、プロジェクトの `README.md` に戻り、
**Phase 1 から順に** 機能を追加していってください。

一度に全部デプロイせず、1フェーズずつ動作を確認しながら進めることが、
理解を深める最短ルートです。
