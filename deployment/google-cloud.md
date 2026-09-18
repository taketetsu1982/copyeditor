# Google Cloud setup

This page walks through the Google Cloud side of running copyeditor: a project with billing, the
Vertex AI API, credentials for a local run, and an optional Cloud Run deployment with Google
authentication. Each step is given twice, first for the Cloud Console and then for the `gcloud`
CLI. Pick one style; the results are the same. Replace `PROJECT_ID`, `PROJECT_NUMBER`, `REGION`
(for example `asia-northeast1`) and `example.com` with your own values.

What you need before starting: a Google account that can create projects (or an existing project
where an administrator grants permissions for billing, API enablement, resource creation, and
IAM changes), and Docker on your machine for a local run.

## Console

### 1. Project and billing

1. Open the project picker at the top of the Console and choose **New project**. Note the
   **Project ID** and, on the Dashboard's *Project info* card, the **Project number**.
2. Open **Billing** with the new project selected and link an active billing account if needed
   ([instructions](https://docs.cloud.google.com/billing/docs/how-to/modify-project)). Vertex AI calls
   are billed, so a project without billing cannot call the model. If the link button is disabled, ask your billing administrator to link the project or to
   grant you the *Billing Account User* role.

### 2. Enable the Vertex AI API

Open **APIs & Services** → **Library**, type the API name in the search box at the top of the page
(not in the category filter on the left), and click **Enable**:

- Vertex AI API (`aiplatform.googleapis.com`)

For a Cloud Run deployment, enable these as well:

- Cloud Run Admin API
- Artifact Registry API
- Secret Manager API

Since April 2026 the Console groups Vertex AI features under *Gemini Enterprise Agent Platform*.
The API name and endpoint used by copyeditor are unchanged.

### 3. Grant model access

Open **IAM & Admin** → **IAM**, find your account (or click **Grant access**), and add the role
**Vertex AI User** (`roles/aiplatform.user`). The project creator already has the Owner role and
can skip this step.

### 4. Credentials for a local run

A local container reads Application Default Credentials (ADC). The Console cannot create ADC; run
this once in a terminal after installing the Google Cloud CLI:

```sh
gcloud auth application-default login
```

The credential file is written to `~/.config/gcloud/application_default_credentials.json`. Pass it
to the container as `ADC_FILE` as shown in the README's *Local Docker: none* section, and keep it
outside any image or repository.

### 5. Cloud Run with Google authentication

Cloud Run supports public `ghcr.io` images directly. This guide uses an Artifact Registry remote
repository, recommended for availability ([supported registries](https://docs.cloud.google.com/run/docs/deploying)).
The examples assume `v0.1.0` is published and public; substitute an available release tag.

1. **Artifact Registry** → **Repositories** → **Create repository**: name `ghcr`, format
   *Docker*, mode **Remote**, region `REGION`, remote source *Docker* → *Custom* with URL
   `https://ghcr.io`, unauthenticated access. The image path becomes
   `REGION-docker.pkg.dev/PROJECT_ID/ghcr/taketetsu1982/copyeditor:v0.1.0`.
2. **IAM & Admin** → **Service accounts** → **Create service account**: name `copyeditor-run`,
   roles **Vertex AI User** and **Secret Manager Secret Accessor**. The attached service account
   replaces the ADC file; nothing is mounted.
3. The service URL is `https://copyeditor-PROJECT_NUMBER.REGION.run.app`. Open **Google Auth platform**,
   configure **Branding** and **Audience** (*Internal* requires a Google Workspace or Cloud Identity
   organization project; add test users for *External* in testing), then open **Clients** →
   **Create client** → *Web application*, and add
   exactly one authorized redirect URI: that URL followed by `/auth/callback`. Note the client ID.
   Copy the client secret for the next step only.
4. **Security** → **Secret Manager** → **Create secret** twice: `oauth-client-secret` with the
   client secret as its value, and `oauth-signing-key` with a random string of at least 32 bytes
   (48 characters from a password generator is fine). Do not store either value anywhere else.
5. **Cloud Run** → **Deploy container** → *Service*: image URL from step 1, service name
   `copyeditor`, region `REGION`, authentication **Allow unauthenticated invocations** (Cloud Run's
   own IAM check is off; the application performs OAuth and answers 401 to anonymous calls). Under
   *Containers, Networking, Security*:
   - container port `8080`;
   - *Variables & Secrets*: environment variables `COPYEDITOR_AUTH_MODE=google`,
     `GOOGLE_CLOUD_PROJECT=PROJECT_ID`, `BASE_URL=<service URL>`,
     `GOOGLE_OAUTH_CLIENT_ID=<client ID>`, `COPYEDITOR_ALLOWED_DOMAINS=["example.com"]`,
     `COPYEDITOR_ALLOWED_EMAILS=[]`; secrets exposed as environment variables
     `GOOGLE_OAUTH_CLIENT_SECRET` ← `oauth-client-secret:latest` and `OAUTH_SIGNING_KEY` ←
     `oauth-signing-key:latest`;
   - *Security*: service account `copyeditor-run`;
   - revision scaling **minimum 1, maximum 1**, with all traffic on one revision. OAuth state lives
     in memory: restart or replacement loses sessions and requires another login. This setting
     does not guarantee exactly one instance; Cloud Run can temporarily exceed the
     [maximum](https://docs.cloud.google.com/run/docs/configuring/max-instances).
   Deploy and confirm the URL matches step 3.
6. Before the first login, exclude callback requests from request logs: **Logging** → **Log
   Router** → `_Default` → **Edit sink** → **Add exclusion**, name `copyeditor-callback`, filter
   `resource.type="cloud_run_revision" AND resource.labels.service_name="copyeditor" AND httpRequest.requestUrl=~"/auth/callback([?]|$)"`.
   Apply the same exclusion to any organization-level sink.
7. Open `<service URL>/health` in a browser; expect `{"status":"ok"}`. Register the client with
   `<service URL>/mcp` as described in the README's *Client connection* section and complete the
   browser login with an allowed account.

## CLI

Install the Google Cloud CLI (`brew install --cask google-cloud-sdk` on macOS) and run
`gcloud init` once.

### 1. Project and billing

```sh
gcloud projects create PROJECT_ID --name="copyeditor"
gcloud config set project PROJECT_ID
gcloud billing accounts list
gcloud billing projects link PROJECT_ID --billing-account=XXXXXX-XXXXXX-XXXXXX
gcloud billing projects describe PROJECT_ID   # billingEnabled: true
```

### 2. Enable the Vertex AI API

```sh
gcloud services enable aiplatform.googleapis.com
# Cloud Run deployment only:
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
```

### 3. Grant model access

```sh
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="user:you@example.com" --role="roles/aiplatform.user"
```

### 4. Credentials for a local run

```sh
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=PROJECT_ID
export ADC_FILE=$HOME/.config/gcloud/application_default_credentials.json
```

Confirm model access once before starting the server:

```sh
curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
  -H "Content-Type: application/json" \
  "https://aiplatform.googleapis.com/v1/projects/$GOOGLE_CLOUD_PROJECT/locations/global/publishers/google/models/gemini-3.1-flash-lite:generateContent" \
  -d '{"contents":[{"role":"user","parts":[{"text":"hello"}]}]}'
```

A JSON candidate means the project can call the configured model. `PERMISSION_DENIED` points at
steps 2 or 3; `NOT_FOUND` at the model name or location (`COPYEDITOR_MODEL`,
`GOOGLE_CLOUD_LOCATION`).

### 5. Cloud Run with Google authentication

```sh
export P=PROJECT_ID R=REGION
export PN=$(gcloud projects describe $P --format='value(projectNumber)')
export BASE_URL=https://copyeditor-$PN.$R.run.app
export SA=copyeditor-run@$P.iam.gserviceaccount.com

# Pass-through repository for ghcr.io
gcloud artifacts repositories create ghcr --repository-format=docker --mode=remote-repository \
  --location=$R --remote-docker-repo=https://ghcr.io
export IMAGE=$R-docker.pkg.dev/$P/ghcr/taketetsu1982/copyeditor:v0.1.0

# Runtime service account (replaces the ADC file)
gcloud iam service-accounts create copyeditor-run
gcloud projects add-iam-policy-binding $P --member=serviceAccount:$SA --role=roles/aiplatform.user
gcloud projects add-iam-policy-binding $P --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor
```

Create the OAuth client in the Console (step 5.3 above) with the redirect URI
`$BASE_URL/auth/callback`; the CLI cannot create OAuth web clients. Then store the two secrets
without echoing them (the client secret is read from the clipboard on macOS):

```sh
pbpaste | tr -d '\n' | gcloud secrets create oauth-client-secret --data-file=-
openssl rand -base64 48 | tr -d '\n' | gcloud secrets create oauth-signing-key --data-file=-
```

Deploy, exclude callback logs, and verify:

```sh
gcloud run deploy copyeditor --region=$R --image=$IMAGE --service-account=$SA --port=8080 \
  --min-instances=1 --max-instances=1 --allow-unauthenticated \
  --set-secrets=GOOGLE_OAUTH_CLIENT_SECRET=oauth-client-secret:latest,OAUTH_SIGNING_KEY=oauth-signing-key:latest \
  --set-env-vars='^;^COPYEDITOR_AUTH_MODE=google;GOOGLE_CLOUD_PROJECT='$P';BASE_URL='$BASE_URL';GOOGLE_OAUTH_CLIENT_ID=<client ID>;COPYEDITOR_ALLOWED_DOMAINS=["example.com"];COPYEDITOR_ALLOWED_EMAILS=[]'

gcloud logging sinks update _Default \
  --add-exclusion='name=copyeditor-callback,filter=resource.type="cloud_run_revision" AND resource.labels.service_name="copyeditor" AND httpRequest.requestUrl=~"/auth/callback([?]|$)"'

curl --fail $BASE_URL/health
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE_URL/mcp   # 401
claude mcp add --transport http copyeditor $BASE_URL/mcp
```

`^;^` changes the separator so JSON lists can contain commas. The minimum instance incurs idle
charges; each `polish_text` call is billed by Vertex AI. The restart and scaling limitations in
Console step 5 also apply.

## 日本語

copyeditor を動かすための Google Cloud 側の準備です。課金付きのプロジェクト、Vertex AI API、
ローカル実行用の資格情報、そして任意で Google 認証付きの Cloud Run 配備を扱います。各手順を
Cloud Console 版と `gcloud` CLI 版の 2 通りで書いています。どちらか一方を選んでください。結果は
同じです。`PROJECT_ID`、`PROJECT_NUMBER`、`REGION`（例: `asia-northeast1`）、`example.com` は
自分の値に置き換えます。

始める前に必要なもの: プロジェクトを作れる Google アカウント（既存プロジェクトを使う場合は
管理者から課金・API 有効化・リソース作成・IAM 変更に必要な権限の付与を受ける）と、ローカル実行なら手元の Docker。

### コンソール

#### 1. プロジェクトと課金

1. コンソール上部のプロジェクト選択から **新しいプロジェクト** を作ります。**プロジェクト ID**
   と、ダッシュボードの「プロジェクト情報」カードにある **プロジェクト番号** を控えます。
2. 作ったプロジェクトを選んで **お支払い** を開き、未設定なら有効な請求先アカウントを
   紐付けます（[設定手順](https://docs.cloud.google.com/billing/docs/how-to/modify-project?hl=ja)）。
   Vertex AI の呼び出しは課金対象なので、課金なしのプロジェクトではモデルを呼べません。ボタンが押せない場合は、課金管理者にリンクを依頼するか、*請求先
   アカウント ユーザー* のロールを付けてもらいます。

#### 2. Vertex AI API を有効にする

**API とサービス** → **ライブラリ** を開き、ページ上部の検索ボックス（左側のカテゴリ フィルタ
ではありません）に API 名を入れて **有効にする** を押します。

- Vertex AI API（`aiplatform.googleapis.com`）

Cloud Run に配備する場合は次も有効にします。

- Cloud Run Admin API
- Artifact Registry API
- Secret Manager API

2026 年 4 月以降、コンソールでは Vertex AI の機能が *Gemini Enterprise Agent Platform* の下に
まとめられています。copyeditor が使う API 名とエンドポイントは変わっていません。

#### 3. モデルの利用権限

**IAM と管理** → **IAM** で自分のアカウント（または **アクセスを許可**）に **Vertex AI ユーザー**
（`roles/aiplatform.user`）を追加します。プロジェクトを自分で作った場合はオーナーなので不要です。

#### 4. ローカル実行用の資格情報

ローカルのコンテナは Application Default Credentials（ADC）を読みます。ADC はコンソールでは
作れないので、Google Cloud CLI を入れてターミナルで 1 回だけ実行します。

```sh
gcloud auth application-default login
```

資格情報は `~/.config/gcloud/application_default_credentials.json` に書かれます。README の
「ローカルDocker: none」のとおり `ADC_FILE` としてコンテナに渡し、image やリポジトリには
入れません。

#### 5. Google 認証付きの Cloud Run

Cloud Run は公開 `ghcr.io` image を直接取得できます。この手順では可用性のため推奨される
Artifact Registry のリモート リポジトリを使います（[対応レジストリ](https://docs.cloud.google.com/run/docs/deploying)）。
例は `v0.1.0` が公開済みである前提です。利用可能なリリースタグに置き換えてください。

1. **Artifact Registry** → **リポジトリ** → **リポジトリを作成**: 名前 `ghcr`、形式 *Docker*、
   モード **リモート**、リージョン `REGION`、リモートのソースは *Docker* → *カスタム* で URL
   `https://ghcr.io`、認証なし。image のパスは
   `REGION-docker.pkg.dev/PROJECT_ID/ghcr/taketetsu1982/copyeditor:v0.1.0` になります。
2. **IAM と管理** → **サービス アカウント** → **サービス アカウントを作成**: 名前 `copyeditor-run`、
   ロールは **Vertex AI ユーザー** と **Secret Manager のシークレット アクセサー**。この
   サービス アカウントが ADC ファイルの代わりになり、mount は不要です。
3. サービスの URL は `https://copyeditor-PROJECT_NUMBER.REGION.run.app` です。**Google 認証プラットフォーム**
   で **ブランディング** と **対象** を設定します（*内部* は Google Workspace または
   Cloud Identity の組織プロジェクトのみ。*外部* でテスト中ならテストユーザーを追加）。
   **クライアント** → **クライアントの作成** → *ウェブ アプリケーション* で、承認済みのリダイレクト URI に
   その URL + `/auth/callback` を 1 つだけ追加します。クライアント ID を控え、クライアント
   シークレットは次の手順でだけ使うためにコピーします。
4. **セキュリティ** → **Secret Manager** → **シークレットを作成** を 2 回: `oauth-client-secret`
   （値はクライアント シークレット）と `oauth-signing-key`（値は 32 バイト以上のランダム文字列。
   パスワード生成ツールの 48 文字で十分）。どちらの値も他の場所には保存しません。
5. **Cloud Run** → **コンテナをデプロイ** → *サービス*: image URL は手順 1 のもの、サービス名
   `copyeditor`、リージョン `REGION`、認証は **未認証の呼び出しを許可**（Cloud Run 側の IAM
   認証を外し、アプリの OAuth が認証を行い、匿名には 401 を返します）。「コンテナ、
   ネットワーキング、セキュリティ」で次を設定します。
   - コンテナ ポート `8080`
   - 「変数とシークレット」: 環境変数 `COPYEDITOR_AUTH_MODE=google`、
     `GOOGLE_CLOUD_PROJECT=PROJECT_ID`、`BASE_URL=<サービス URL>`、
     `GOOGLE_OAUTH_CLIENT_ID=<クライアント ID>`、`COPYEDITOR_ALLOWED_DOMAINS=["example.com"]`、
     `COPYEDITOR_ALLOWED_EMAILS=[]`。シークレットを環境変数として公開: `GOOGLE_OAUTH_CLIENT_SECRET`
     ← `oauth-client-secret:latest`、`OAUTH_SIGNING_KEY` ← `oauth-signing-key:latest`
   - 「セキュリティ」: サービス アカウント `copyeditor-run`
   - リビジョンのスケーリング **最小 1、最大 1**。トラフィックは 1 つのリビジョンに集めます。
     OAuth の状態はメモリだけなので、再起動や交換でセッションが失われ、再ログインが必要です。
     常に 1 台である保証はなく、一時的に[最大数](https://docs.cloud.google.com/run/docs/configuring/max-instances)を超える場合があります。
   デプロイし、URL が手順 3 と一致することを確認します。
6. 最初のログインより前に、コールバックのリクエストをログから除外します。**ロギング** →
   **ログルーター** → `_Default` → **シンクを編集** → **除外を追加**: 名前 `copyeditor-callback`、
   フィルタ
   `resource.type="cloud_run_revision" AND resource.labels.service_name="copyeditor" AND httpRequest.requestUrl=~"/auth/callback([?]|$)"`。
   組織レベルの集約シンクがあれば、そちらにも同じ除外を入れます。
7. ブラウザで `<サービス URL>/health` を開き `{"status":"ok"}` を確認します。README の
   「クライアント接続」のとおり `<サービス URL>/mcp` をクライアントに登録し、許可された
   アカウントでブラウザ ログインを完了します。

### CLI

Google Cloud CLI を入れ（macOS は `brew install --cask google-cloud-sdk`）、`gcloud init` を 1 回
実行しておきます。

#### 1. プロジェクトと課金

```sh
gcloud projects create PROJECT_ID --name="copyeditor"
gcloud config set project PROJECT_ID
gcloud billing accounts list
gcloud billing projects link PROJECT_ID --billing-account=XXXXXX-XXXXXX-XXXXXX
gcloud billing projects describe PROJECT_ID   # billingEnabled: true
```

#### 2. Vertex AI API を有効にする

```sh
gcloud services enable aiplatform.googleapis.com
# Cloud Run に配備する場合のみ:
gcloud services enable run.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com
```

#### 3. モデルの利用権限

```sh
gcloud projects add-iam-policy-binding PROJECT_ID \
  --member="user:you@example.com" --role="roles/aiplatform.user"
```

#### 4. ローカル実行用の資格情報

```sh
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT=PROJECT_ID
export ADC_FILE=$HOME/.config/gcloud/application_default_credentials.json
```

サーバーを起動する前に、モデルを 1 回呼んで確認します。

```sh
curl -s -X POST \
  -H "Authorization: Bearer $(gcloud auth application-default print-access-token)" \
  -H "Content-Type: application/json" \
  "https://aiplatform.googleapis.com/v1/projects/$GOOGLE_CLOUD_PROJECT/locations/global/publishers/google/models/gemini-3.1-flash-lite:generateContent" \
  -d '{"contents":[{"role":"user","parts":[{"text":"hello"}]}]}'
```

JSON の候補が返れば、設定したモデルを呼べています。`PERMISSION_DENIED` なら手順 2 か 3、
`NOT_FOUND` ならモデル名か location（`COPYEDITOR_MODEL`、`GOOGLE_CLOUD_LOCATION`）を見直します。

#### 5. Google 認証付きの Cloud Run

```sh
export P=PROJECT_ID R=REGION
export PN=$(gcloud projects describe $P --format='value(projectNumber)')
export BASE_URL=https://copyeditor-$PN.$R.run.app
export SA=copyeditor-run@$P.iam.gserviceaccount.com

# ghcr.io の中継リポジトリ
gcloud artifacts repositories create ghcr --repository-format=docker --mode=remote-repository \
  --location=$R --remote-docker-repo=https://ghcr.io
export IMAGE=$R-docker.pkg.dev/$P/ghcr/taketetsu1982/copyeditor:v0.1.0

# 実行用サービス アカウント（ADC ファイルの代わり）
gcloud iam service-accounts create copyeditor-run
gcloud projects add-iam-policy-binding $P --member=serviceAccount:$SA --role=roles/aiplatform.user
gcloud projects add-iam-policy-binding $P --member=serviceAccount:$SA --role=roles/secretmanager.secretAccessor
```

OAuth クライアントはコンソール（上の 5-3）で、リダイレクト URI `$BASE_URL/auth/callback` を
指定して作ります。CLI では OAuth ウェブ クライアントを作れません。次に、値を表示せずに秘密を
2 つ保存します（クライアント シークレットは macOS のクリップボードから読みます）。

```sh
pbpaste | tr -d '\n' | gcloud secrets create oauth-client-secret --data-file=-
openssl rand -base64 48 | tr -d '\n' | gcloud secrets create oauth-signing-key --data-file=-
```

デプロイ、コールバックのログ除外、確認:

```sh
gcloud run deploy copyeditor --region=$R --image=$IMAGE --service-account=$SA --port=8080 \
  --min-instances=1 --max-instances=1 --allow-unauthenticated \
  --set-secrets=GOOGLE_OAUTH_CLIENT_SECRET=oauth-client-secret:latest,OAUTH_SIGNING_KEY=oauth-signing-key:latest \
  --set-env-vars='^;^COPYEDITOR_AUTH_MODE=google;GOOGLE_CLOUD_PROJECT='$P';BASE_URL='$BASE_URL';GOOGLE_OAUTH_CLIENT_ID=<client ID>;COPYEDITOR_ALLOWED_DOMAINS=["example.com"];COPYEDITOR_ALLOWED_EMAILS=[]'

gcloud logging sinks update _Default \
  --add-exclusion='name=copyeditor-callback,filter=resource.type="cloud_run_revision" AND resource.labels.service_name="copyeditor" AND httpRequest.requestUrl=~"/auth/callback([?]|$)"'

curl --fail $BASE_URL/health
curl -s -o /dev/null -w '%{http_code}\n' -X POST $BASE_URL/mcp   # 401
claude mcp add --transport http copyeditor $BASE_URL/mcp
```

`^;^` は区切り文字を変更し、JSON 配列にカンマを含められるようにします。最小インスタンスは
待機中も課金され、`polish_text` の呼び出しごとに Vertex AI の従量課金がかかります。
コンソール版の手順 5 にある再起動・スケーリングの制限は CLI 版でも同じです。
