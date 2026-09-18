# copyeditor
Your agent writes, a polishing model rewrites, and you confirm the meaning. An MCP server + agent skill for copyediting AI-written text with swappable per-language rules. Japanese first.

Reference implementation: Claude Code or Codex CLI as the writing agent, Gemini on Vertex AI as the polishing model.

## Setup and Vertex AI

Step-by-step Google Cloud setup for the Console and CLI is in [deployment/google-cloud.md](deployment/google-cloud.md).

Use Docker and a Google Cloud project with billing and the Vertex AI API enabled. Grant the runtime identity model invocation permission (for example, `roles/aiplatform.user`) and configure [Application Default Credentials (ADC)](https://cloud.google.com/docs/authentication/provide-credentials-adc). For local development, use `gcloud auth application-default login`; for deployment, prefer an attached service account or workload identity. Confirm access to the configured model with a deployment smoke test; health alone does not call Vertex.

Build locally with `docker build -t copyeditor:local .`. No published image or live Google acceptance result is asserted here. The container runs `python -m copyeditor` as UID/GID 65532 and requires runtime ADC readable by that user. Keep credential files outside the build context; never bake credentials into any image layer.

## Configuration

Start from [config.example.yaml](config.example.yaml). The only config path is `/etc/copyeditor/config.yaml`. Explicit values override environment variables, which override defaults; an exact `${NAME}` placeholder reads that environment variable. Lists/maps in environment variables are JSON. See [CTR-04](contracts/config.md#fields) for validation and the complete schema.

| Config fields | Environment variables |
|---|---|
| `provider`, `model`, `thinking` | `COPYEDITOR_PROVIDER`, `COPYEDITOR_MODEL`, `COPYEDITOR_THINKING` |
| `default_language`, `protected_terms` | `COPYEDITOR_DEFAULT_LANGUAGE`, `COPYEDITOR_PROTECTED_TERMS` |
| `length_ratio.min`, `length_ratio.max` | `COPYEDITOR_LENGTH_RATIO_MIN`, `COPYEDITOR_LENGTH_RATIO_MAX` |
| `vertex.project`, `vertex.location` | `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` |
| `auth.mode`, `auth.base_url`, `auth.client_id` | `COPYEDITOR_AUTH_MODE`, `BASE_URL`, `GOOGLE_OAUTH_CLIENT_ID` |
| `auth.allowed_domains`, `auth.allowed_emails` | `COPYEDITOR_ALLOWED_DOMAINS`, `COPYEDITOR_ALLOWED_EMAILS` |
| `pricing`, `server.host`, `server.port` | `COPYEDITOR_PRICING`, `COPYEDITOR_HOST`, `PORT` |

The example fixes `auth.mode: none`; change it before using Google authentication. Its other explicit defaults also override environment settings. OAuth secrets are environment-only: `GOOGLE_OAUTH_CLIENT_SECRET` and `OAUTH_SIGNING_KEY` (at least 32 UTF-8 bytes, generated with sufficient entropy). Neither has a config key; secret placeholders are rejected. Supply them through your deployment's secret manager, never Dockerfile `ARG`/`ENV` or committed files.

## Local Docker: none

Set `GOOGLE_CLOUD_PROJECT` to your project and `ADC_FILE` to the absolute path of your ADC file outside this repository. Ensure the mount is readable by UID 65532 without making credentials public. This base-image command uses environment defaults, with no mounted config:

```sh
docker run --rm -p 127.0.0.1:8080:8080 -e GOOGLE_CLOUD_PROJECT \
  -e COPYEDITOR_AUTH_MODE=none -e GOOGLE_APPLICATION_CREDENTIALS=/run/adc.json \
  --mount "type=bind,src=$ADC_FILE,dst=/run/adc.json,readonly" copyeditor:local
curl --fail http://127.0.0.1:8080/health
```

Run curl in another terminal; expect `{"status":"ok"}`. `none` emits `WARNING: copyeditor is listening without authentication.` once on stderr. Keep it on a trusted local interface; it provides no authentication. Configuration, rules and ADC are checked before listening; startup errors exit nonzero with a fixed stderr diagnostic.

## Google authentication

Before exposing the service, configure an HTTPS TLS terminator and set `BASE_URL` to its origin. Create a Google OAuth web client, configure its consent screen/audience, and register exactly that origin plus `/auth/callback` as its authorized redirect URI. Set `COPYEDITOR_AUTH_MODE=google`, `GOOGLE_OAUTH_CLIENT_ID`, and at least one of `COPYEDITOR_ALLOWED_DOMAINS` / `COPYEDITOR_ALLOWED_EMAILS` (JSON arrays). Domains must be lowercase ASCII DNS names; emails must match Google's returned address exactly, including case and `+` suffixes. A domain match requires verified `hd` and matching email domain; an allowed email is an alternative without `hd`.

Inject both OAuth secret variables at runtime. For local Docker behind your TLS terminator, replace the none flag above with `-e COPYEDITOR_AUTH_MODE=google -e BASE_URL -e GOOGLE_OAUTH_CLIENT_ID -e COPYEDITOR_ALLOWED_DOMAINS -e COPYEDITOR_ALLOWED_EMAILS -e GOOGLE_OAUTH_CLIENT_SECRET -e OAUTH_SIGNING_KEY`, after setting those host variables (use `[]` for an unused list). Retain Vertex ADC. Route the HTTPS origin to the container and apply callback log exclusions below before the first login. If using a config file, replace its auth mapping with the commented Google mapping in the example; do not leave `mode: none` overriding the environment.

Connect with an OAuth-capable MCP client and complete browser login and proxy consent. Verify anonymous `/mcp` calls return HTTP 401, allowed users can call `polish_text`, and disallowed users cannot. `/health` stays unauthenticated. These are operator checks, not a claim of completed live acceptance. OAuth state, client registrations and tokens use MemoryStore only: run one process/instance. Restart, scale-to-zero or instance replacement requires reauthentication, even with the same signing key.

## Client connection

Choose one client; for local none mode:

```sh
claude mcp add --transport http copyeditor http://127.0.0.1:8080/mcp
codex mcp add copyeditor --url http://127.0.0.1:8080/mcp
```

For Google mode, use your HTTPS origin plus `/mcp` instead and complete the client's OAuth login. Discover `polish_text` and `lint_text`; use a small non-sensitive text for the first polish call and inspect the result before applying it. Client approval is still required for external text submission. `lint_text` does not call a model. Tool inputs, limits and outputs are specified in [CTR-01](contracts/tools.md).

## Derived images and language rules

In a separate build directory, prepare a non-secret `config.yaml` and an additive `rules/ja.md` following [CTR-03](rules/README.md#overlays), with unique lint IDs. Use this Dockerfile for either auth mode (selected by config):

```dockerfile
FROM copyeditor:local
COPY config.yaml /etc/copyeditor/config.yaml
COPY rules/ja.md /etc/copyeditor/rules.d/ja.md
```

Build there with `docker build -t copyeditor:custom .`, then substitute `copyeditor:custom` in the run command. Files must be readable by UID 65532. Overlays append rules and protected terms; they cannot weaken [common preservation](rules/common.md). Do not overwrite `/app/rules/`. Restart after config/rule changes. Examples live in [examples/](examples/) and are not loaded on tool calls.

## Retention and operational logs

The server does not persist submitted text or candidates. It emits only the [CTR-01 audit fields](contracts/tools.md#audit-log) to stdout; startup diagnostics go to stderr. This does not cover provider retention or infrastructure logs: review those policies separately. Text sent for polishing reaches Vertex AI.

Before login, configure every reverse proxy/load balancer to omit query strings from request logs (or disable callback request logging). On Cloud Run, use [Cloud Logging sink exclusions](https://cloud.google.com/run/docs/logging) for callback request entries: filter `resource.type="cloud_run_revision" AND httpRequest.requestUrl=~"/auth/callback([?]|$)"`, scoped to your service, on every sink that stores or exports those entries, including `_Default`. This excludes whole entries, not individual query fields. Check inherited/organization sinks and upstream proxy logs too; a local app setting does not control them. Verify using synthetic callback traffic with no real code/token that no destination stores its query. Exclusions do not remove previously stored logs.

## License and acknowledgements

[MIT License](LICENSE).

Acknowledgements: natural-japanese (coji/natural-japanese) informed this project's approach.
Before publication, the owner must confirm that the public rules and examples do not reuse its wording. Its code, including `lint.py`, is not incorporated.

The owner records approval in one PR comment with a `copyeditor-provenance-v1` fenced JSON block: `head` (current PR commit SHA), `comparison_revision` (the reviewed coji/natural-japanese commit SHA), `non_reuse` (`confirmed`), and `blobs` (every public `rules/` and `examples/` file path mapped to its Git blob SHA). Both SHAs must be full 40-character commit IDs. Before publication, leave this one PR comment and run `python scripts/check_evidence.py provenance --pr <number> --owner <login>` once at that time to validate it. The publication workflow and `release` acceptance do not check provenance. This verifies recorded evidence, not originality automatically.

## 日本語

エージェントが書き、校正モデルが推敲し、人が意味を確認します。言語ごとにルールを差し替えられる、AI生成文の校正用MCPサーバーとエージェントSkillです。日本語を最初の対象にしています。

参照実装: 執筆エージェントはClaude CodeまたはCodex CLI、校正モデルはVertex AI上のGeminiです。

### 準備とVertex AI

Google Cloud のコンソール版・CLI 版の設定手順は [deployment/google-cloud.md](deployment/google-cloud.md) を参照してください。

Dockerと、課金およびVertex AI APIを有効にしたGoogle Cloudプロジェクトを用意します。実行identityにモデル呼出し権限（例: `roles/aiplatform.user`）を付与し、[ADC](https://cloud.google.com/docs/authentication/provide-credentials-adc)を設定します。ローカル開発は `gcloud auth application-default login`、デプロイは接続済みservice accountやworkload identityを使います。モデルの利用権限はデプロイ時のsmoke testで確認します。healthはVertexを呼びません。

`docker build -t copyeditor:local .` でローカルbuildします。公開済みimageや実Googleの受入実績を示すものではありません。コンテナはUID/GID 65532で `python -m copyeditor` を実行します。このユーザーが読めるADCを実行時に渡し、認証ファイルはbuild contextの外に置いて、どのimage layerにも含めないでください。

### 設定

[config.example.yaml](config.example.yaml)を基にします。設定pathは `/etc/copyeditor/config.yaml` のみです。明示値、環境変数、既定値の順に優先します。完全一致の `${NAME}` はその環境変数を読み、環境変数の配列・mapはJSONです。検証条件と全schemaは[CTR-04](contracts/config.md#fields)を参照してください。

| Config fields | Environment variables |
|---|---|
| `provider`, `model`, `thinking` | `COPYEDITOR_PROVIDER`, `COPYEDITOR_MODEL`, `COPYEDITOR_THINKING` |
| `default_language`, `protected_terms` | `COPYEDITOR_DEFAULT_LANGUAGE`, `COPYEDITOR_PROTECTED_TERMS` |
| `length_ratio.min`, `length_ratio.max` | `COPYEDITOR_LENGTH_RATIO_MIN`, `COPYEDITOR_LENGTH_RATIO_MAX` |
| `vertex.project`, `vertex.location` | `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` |
| `auth.mode`, `auth.base_url`, `auth.client_id` | `COPYEDITOR_AUTH_MODE`, `BASE_URL`, `GOOGLE_OAUTH_CLIENT_ID` |
| `auth.allowed_domains`, `auth.allowed_emails` | `COPYEDITOR_ALLOWED_DOMAINS`, `COPYEDITOR_ALLOWED_EMAILS` |
| `pricing`, `server.host`, `server.port` | `COPYEDITOR_PRICING`, `COPYEDITOR_HOST`, `PORT` |

設定例は `auth.mode: none` を固定しているため、Google認証では変更します。他の明示した既定値も環境変数より優先します。OAuthの秘密は環境変数 `GOOGLE_OAUTH_CLIENT_SECRET` と `OAUTH_SIGNING_KEY`（十分な乱数で生成した32 UTF-8 bytes以上）のみから渡します。対応するconfig keyはなく、秘密のplaceholderも拒否します。デプロイ環境のsecret managerを使い、Dockerfileの `ARG` / `ENV` やcommitするファイルには書きません。

### ローカルDocker: none

`GOOGLE_CLOUD_PROJECT` にproject、`ADC_FILE` にリポジトリ外のADCファイルの絶対pathを設定します。認証情報を公開せずUID 65532が読めるようにします。以下はconfigをmountせず、環境変数と既定値を使う基底imageの例です。

```sh
docker run --rm -p 127.0.0.1:8080:8080 -e GOOGLE_CLOUD_PROJECT \
  -e COPYEDITOR_AUTH_MODE=none -e GOOGLE_APPLICATION_CREDENTIALS=/run/adc.json \
  --mount "type=bind,src=$ADC_FILE,dst=/run/adc.json,readonly" copyeditor:local
curl --fail http://127.0.0.1:8080/health
```

curlは別terminalで実行し、`{"status":"ok"}` を確認します。`none` はstderrに `WARNING: copyeditor is listening without authentication.` を1回出します。認証がないため、信頼できるローカルinterfaceに限定します。listen前にconfig・rules・ADCを検査し、起動エラーでは固定診断をstderrに出して非ゼロで終了します。

### Google認証

公開前にHTTPSのTLS終端を用意し、`BASE_URL` にそのoriginを設定します。Google OAuthのweb clientを作成し、同意画面と対象ユーザーを設定して、originに `/auth/callback` を足したURLをauthorized redirect URIに登録します。`COPYEDITOR_AUTH_MODE=google`、`GOOGLE_OAUTH_CLIENT_ID`、`COPYEDITOR_ALLOWED_DOMAINS` / `COPYEDITOR_ALLOWED_EMAILS` の少なくとも一方（JSON配列）を設定します。domainは小文字ASCII DNS名、emailはGoogleの返す値と大文字小文字・`+` suffixまで完全一致させます。domain経路は検証済み `hd` とemail domainの一致が必要です。許可emailの一致なら `hd` は不要です。

OAuthの秘密2変数は実行時に注入します。TLS終端の背後でローカルDockerを使う場合、上のnone指定を `-e COPYEDITOR_AUTH_MODE=google -e BASE_URL -e GOOGLE_OAUTH_CLIENT_ID -e COPYEDITOR_ALLOWED_DOMAINS -e COPYEDITOR_ALLOWED_EMAILS -e GOOGLE_OAUTH_CLIENT_SECRET -e OAUTH_SIGNING_KEY` に替え、各host変数を事前設定します（使わない一覧は `[]`）。Vertex ADCは引き続き必要です。HTTPS originをコンテナへ転送し、初回login前に後述のcallbackログ除外を適用します。configを使う場合はauth mappingを設定例のコメント内のGoogle用mappingに置換し、環境変数より優先される `mode: none` を残さないでください。

OAuth対応MCP clientで接続し、ブラウザloginとproxy consentを完了します。匿名 `/mcp` がHTTP 401、許可ユーザーは `polish_text` を実行でき、不許可ユーザーは実行できないことを確認します。`/health` は認証不要です。これは運用者の確認手順で、実機受入済みという意味ではありません。OAuth state・client登録・tokenはMemoryStoreだけに保存するため、1 process / 1 instanceで運用します。再起動・scale-to-zero・instance切替では、署名鍵が同じでも再認証が必要です。

### クライアント接続

ローカルnoneでは、使うclientを1つ選びます。

```sh
claude mcp add --transport http copyeditor http://127.0.0.1:8080/mcp
codex mcp add copyeditor --url http://127.0.0.1:8080/mcp
```

GoogleではURLをHTTPS originと `/mcp` に替え、clientのOAuth loginを完了します。`polish_text` と `lint_text` をdiscoveryで確認し、秘密を含まない短文で最初のpolishを試して、反映前に結果を確認します。外部への本文送信にはclientの承認が必要です。`lint_text` はモデルを呼びません。入力・上限・出力は[CTR-01](contracts/tools.md)に従います。

### 派生imageと言語ルール

別のbuild directoryに秘密を含まない `config.yaml` と、[CTR-03](rules/README.md#overlays)に従う追加用 `rules/ja.md` を用意し、lint IDを重複させません。両認証modeとも次のDockerfileを使い、configでmodeを選びます。

```dockerfile
FROM copyeditor:local
COPY config.yaml /etc/copyeditor/config.yaml
COPY rules/ja.md /etc/copyeditor/rules.d/ja.md
```

そのdirectoryで `docker build -t copyeditor:custom .` を実行し、起動コマンドのimageを `copyeditor:custom` に替えます。ファイルはUID 65532が読めるようにします。overlayはルールとprotected termsを追加でき、[共通保持条件](rules/common.md)は弱められません。`/app/rules/` を上書きせず、config・rules変更後は再起動します。[examples/](examples/)は例文で、tool呼出し時には読みません。

### 保持範囲と運用ログ

サーバーは送信本文・候補を永続保存しません。stdoutには[CTR-01の監査項目](contracts/tools.md#audit-log)だけ、stderrには起動診断を出します。providerの保持方針やインフラログはこの範囲に含まれないため、別途確認してください。校正に送った本文はVertex AIへ渡ります。

login前に、すべてのreverse proxy / load balancerのrequest logからqueryを除くか、callbackのrequest logを無効にします。Cloud Runでは[Cloud Loggingのsink除外](https://cloud.google.com/run/docs/logging)を使い、`resource.type="cloud_run_revision" AND httpRequest.requestUrl=~"/auth/callback([?]|$)"` を対象serviceに絞って、`_Default` を含む保存・export先の全sinkに設定します。query fieldだけでなくentry全体を除外します。継承・organizationのsinkと上流proxyのログも確認してください。アプリの設定では制御できません。本物のcode/tokenを含まない合成callback通信で、どの宛先にもqueryが保存されないことを確認します。除外設定は過去のログを削除しません。

### ライセンスと謝辞

[MIT License](LICENSE)。

謝辞: natural-japanese（coji/natural-japanese）を本プロジェクトの方針の参考にしました。
公開前に、公開するrulesとexamplesへ文言を流用していないことを所有者が確認します。`lint.py`を含むコードは取り込んでいません。

所有者はPRコメント1件の `copyeditor-provenance-v1` JSONコードブロックに、`head`（現在のPR commit SHA）、`comparison_revision`（確認したcoji/natural-japaneseのcommit SHA）、`non_reuse`（`confirmed`）、`blobs`（公開する `rules/` と `examples/` の全ファイルパスとGit blob SHAの対応）を記録します。両commit SHAは40文字の完全なIDです。公開前にこのPRコメントを1件残し、その時点で `python scripts/check_evidence.py provenance --pr <number> --owner <login>` を1度実行して整合を確認します。公開workflowと最終受入の `release` はprovenanceを検査しません。記録された証拠の検査であり、独自性を自動判定するものではありません。
