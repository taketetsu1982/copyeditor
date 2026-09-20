# copyeditor
Your agent writes, a polishing model rewrites, and you confirm the meaning. An MCP server + agent skill for copyediting AI-written text with swappable per-language rules. Japanese first.

Reference implementation: Claude Code or Codex CLI as the writing agent, Gemini on Vertex AI as the polishing model.

**Version 0.2.0.** The server and both plugins use the same version. Judgment remains off by default (`judgment.enabled=false`): polish keeps v1 and rewrite keeps v2. Enabling judgment requires a v3-compatible client or the bundled Skill; both degrees then return `schema_version=3`. Live gate and verification calibration, native Japanese review, and real-client acceptance remain pending. Offline CI success does not establish these results. This version change does not publish a release; tagging and publication are separate owner operations.

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

**Optional judgment.** Judgment defaults to off; enable `judgment.enabled` (or `COPYEDITOR_JUDGMENT_ENABLED=true`) only after reviewing the additional destination. The model is pinned to `jev-1.13.0`, with `reference-gate-action-v1` and `gate-verify-v1`; moving model aliases and unknown registry IDs are rejected. See [judgment settings](contracts/config.md#judgment-fields).

Supply `TYPESAFE_API_KEY` only through the runtime secret environment, never config, placeholders, Docker build arguments, image contents, or committed files. Disabled mode does not read or require this key. Rollback means setting `judgment.enabled=false` (or `COPYEDITOR_JUDGMENT_ENABLED=false` when no explicit config overrides it) and restarting: subsequent requests return to v1/v2 without changing Vertex ADC or OAuth requirements. There is no per-request switch or change to an in-flight request.

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

### Claude Code plugin

1. Check out the repository and enter it:

```sh
git clone https://github.com/taketetsu1982/copyeditor.git
cd copyeditor
```

2. In your checkout, edit `plugins/claude/.mcp.json`: replace `https://copyeditor.invalid/mcp` with your server's HTTPS endpoint ending in `/mcp`. The placeholder is not a hosted service. Complete the Google authentication and callback log setup above before login.
3. Register this local checkout and install the plugin:

```sh
claude plugin marketplace add .
claude plugin install copyeditor@copyeditor-local
```

4. Start a new Claude Code session, open `/mcp`, select the server belonging to the `copyeditor` plugin, and complete its browser OAuth sign-in and consent with an allowed account. Check the displayed server name rather than assuming it matches a separately registered MCP server.
5. Confirm that this plugin exposes `polish_text` and `lint_text`. Try non-sensitive text through the plugin's Skill, review the approval request and returned candidate, and confirm `lint_text` independently. Installation alone does not verify the connection.

### Codex plugin

1. Check out the repository and enter it:

```sh
git clone https://github.com/taketetsu1982/copyeditor.git
cd copyeditor
```

2. In your checkout, edit `plugins/codex/.mcp.json`: replace `https://copyeditor.invalid/mcp` with your server's HTTPS endpoint ending in `/mcp`. Complete the Google authentication and callback log setup above before login.
3. Register this local checkout and install the compatibility plugin:

```sh
codex plugin marketplace add .
codex plugin add copyeditor@copyeditor-local
```

4. Start a new Codex session and complete the installed plugin's OAuth sign-in prompt with an allowed account, including proxy consent. Confirm the plugin's actual server identity in the client's MCP status; do not authenticate a separate direct registration as a substitute. The version-specific plugin OAuth interaction still requires live verification.
5. Confirm `polish_text` and `lint_text` under the installed plugin. Try a small non-sensitive text with the Skill, review the tool approval and candidate, and check `lint_text` independently.

The command forms above were checked with Claude Code 2.1.276 and codex-cli 0.154.0; live plugin authentication and invocation remain operator checks. See the [Claude plugin reference](https://code.claude.com/docs/en/plugins-reference) and [OpenAI package documentation](https://developers.openai.com/plugins/build/plugins).

For updates, keep your endpoint configuration outside version control, update the original checkout, then preserve or reapply its `.mcp.json` URL before reinstalling through the same local marketplace. Remove the old plugin through the client's plugin manager if needed, reinstall, and start a new session. Do not edit cached plugin copies or commit your deployment settings.

### Submission permission and results

Put a scoped permission statement in your project's `CLAUDE.md` or `AGENTS.md`, for example: “When I request copyediting, you may send only the non-confidential paragraphs I explicitly select to my configured copyeditor server and its Vertex AI provider. Ask before sending anything else.” This permits a content scope; it does not bypass client approval. Do not add automatic tool approvals or permission-skip settings.

The server does not persist text, candidates, or diagnoses; provider retention and infrastructure logs remain outside that guarantee, as described below. Both plugin Skills classify submission permission, exclude confidential or protected content, record source locations and related items, compare returned candidates, and apply only authorized local changes. They recheck the original before applying edits and keep related changes together. A client approval refusal ends the attempt; the Skill does not switch routes or relax permissions.

**When judgment is enabled**, body, permitted context/background, and candidates may also go to TypeSafe AI. Both degrees return `schema_version=3`; `lint_text` is unchanged. The Skill checks discovery for each request, includes TypeSafe AI in permission and reporting, and treats unknown disclosure as potentially enabled. Vertex-only permission does not cover this additional destination. Startup stderr, initialization instructions and the tool description disclose it, but **direct MCP calls have no guaranteed per-request consent**; operators must inform those users before enabling it.

Judgment is neither adoption permission nor proof that meaning was preserved. A verification pass does not replace the Skill's meaning comparison or the user's approval. Distinguish insufficient grounds for change and checks not run (unchanged originals) from `verification_rejected` (a discarded candidate, original retained); do not describe the candidate's failed checks as defects in the original. Classification and response rules are in [CTR-01](contracts/tools.md#registered-threshold-classification).

**Diagnosed rewrite (judgment disabled).** Ordinary proofreading uses `degree=polish` (the default) and v1 responses. Only an explicit request to rewrite uses `degree=rewrite` and `schema_version=2`. The server first diagnoses expressions, then rewrites using those fixed diagnoses; the caller supplies no diagnosis. For example, send this to `polish_text` after the normal permission checks:

```json
{"text":"The team will carry out a review of the draft.","language":"en","degree":"rewrite"}
```

Before sending, discovery must explicitly advertise `rewrite` in the degree enum. If support is absent or unclear, leave the text unsent and unprocessed; do not probe with body text or silently substitute polish. A diagnosis with `status=issue` gives an expression and reason to review. `status=no_issue` requires the exact original; it is different from a flag or a lint finding. Diagnosis, model success, and zero lint findings do not authorize adoption or prove meaning preservation.

**Limits and failures.** Rewrite keeps the existing input budgets: at most 32 items, 12,000 decoded Unicode code points of body text in total, 1,000 per-item context, 4,000 total context, and 4,000 total background; context plus background also total at most 4,000; body/context/background combined are at most 16,000. Background fields each allow 1,000. The raw HTTP body limit remains 262,144 bytes. Typical Skill batches target 16 items / 6,000 body code points while satisfying every hard limit. See [CTR-01 limits](contracts/tools.md#requests).

HTML always uses one complete `text` request with `format=html`, never items or a partial document. If the whole document cannot be authorized and submitted within the limits, leave it unprocessed. The server diagnoses once, generates candidates in batches of four, and allows at most one shared preservation/HTML regeneration per item. A request-level error discards every candidate and diagnosis, including earlier successful batches; clients keep all originals.

Only confirmed `input_limit`, `generation_truncated`, or rewrite `output_limit` permits one generation of smaller text/Markdown child batches at meaningful boundaries. A known body-limit HTTP 413 is input-limit-equivalent. No grandchild retries, duplicate submissions, or HTML split retries are allowed. `request_budget`, provider/authentication/connection errors, timeouts, invalid input/responses, and unsupported language stop automatic retry; do not switch provider or reinterpret a generic error as truncation.

**Accounting and acceptance.** Rewrite allows at most 17 generation calls, with a 240-second request deadline and fixed cumulative input/output reservations; these are engineering limits, not latency guarantees. CountTokens preflight sends data to Vertex AI even if generation never starts, so `model_called=false` does not mean “nothing was sent.” `model_calls` counts generations only. Usage and cost include diagnosis and regeneration; unknown usage stays unknown. Token estimates and cancellation do not prove billed cost or cancel charges for remotely accepted work. See [rewrite accounting](contracts/tools.md#rewrite-limits-and-accounting).

Japanese has a fixed 24-example synthetic evaluation set, intended for five independent repetitions. Offline fixture success is not native or live quality acceptance. Owner review of examples/invariants, live Vertex evaluation with independent judgments, and real-client permission/refusal/application checks remain pending. English and Chinese rewrite quality is unverified; the English request above demonstrates syntax only.

Under [CTR-02](rules/common.md#meaning-and-adoption), a **skip** leaves the original unchanged when safe adoption cannot be established. A **flag** marks a candidate for review, not permission to apply it; a **rejection** reports a failed check. Never adopt a flagged or rejected candidate. A successful check alone does not prove that meaning is preserved.

For a server-only connection without the plugin Skill, choose one client; for local none mode:

```sh
claude mcp add --transport http copyeditor http://127.0.0.1:8080/mcp
codex mcp add copyeditor --url http://127.0.0.1:8080/mcp
```

For Google mode, use your HTTPS origin plus `/mcp` instead and complete the client's OAuth login. Discover `polish_text` and `lint_text`; use a small non-sensitive text for the first polish call and inspect the result before applying it. Client approval is still required for external text submission. `lint_text` does not call a model. Tool inputs, limits and outputs are specified in [CTR-01](contracts/tools.md).

Per-client steps for Claude Desktop, Claude Code, the ChatGPT desktop app, Codex CLI and the Codex app are in [deployment/mcp-clients.md](deployment/mcp-clients.md).

## Derived images and language rules

In a separate build directory, prepare a non-secret `config.yaml` and an additive `rules/ja.md` following [CTR-03](rules/README.md#overlays), with unique lint IDs. Use this Dockerfile for either auth mode (selected by config):

```dockerfile
FROM copyeditor:local
COPY config.yaml /etc/copyeditor/config.yaml
COPY rules/ja.md /etc/copyeditor/rules.d/ja.md
```

Build there with `docker build -t copyeditor:custom .`, then substitute `copyeditor:custom` in the run command. Files must be readable by UID 65532. Overlays append rules and protected terms; they cannot weaken [common preservation](rules/common.md). Do not overwrite `/app/rules/`. Restart after config/rule changes. Examples live in [examples/](examples/) and are not loaded on tool calls.

## Retention and operational logs

The server does not persist submitted text or candidates. It emits only the [CTR-01 audit fields](contracts/tools.md#audit-log) to stdout; startup diagnostics go to stderr. This does not cover provider retention or infrastructure logs: review those policies separately. Text sent for polishing reaches Vertex AI; enabled judgment adds TypeSafe AI, whose retention and processing region follow its own policy. Judgment values and reasons are not added to audit logs; full per-provider accounting is in the v3 response.

Before login, configure every reverse proxy/load balancer to omit query strings from request logs (or disable callback request logging). On Cloud Run, use [Cloud Logging sink exclusions](https://cloud.google.com/run/docs/logging) for callback request entries: filter `resource.type="cloud_run_revision" AND httpRequest.requestUrl=~"/auth/callback([?]|$)"`, scoped to your service, on every sink that stores or exports those entries, including `_Default`. This excludes whole entries, not individual query fields. Check inherited/organization sinks and upstream proxy logs too; a local app setting does not control them. Verify using synthetic callback traffic with no real code/token that no destination stores its query. Exclusions do not remove previously stored logs.

## License and acknowledgements

[MIT License](LICENSE).

Acknowledgements: natural-japanese (coji/natural-japanese) informed this project's approach.
Before publication, the owner must confirm that the public rules and examples do not reuse its wording. Its code, including `lint.py`, is not incorporated.

The owner records approval in one PR comment with a `copyeditor-provenance-v1` fenced JSON block: `head` (current PR commit SHA), `comparison_revision` (the reviewed coji/natural-japanese commit SHA), `non_reuse` (`confirmed`), and `blobs` (every public `rules/` and `examples/` file path mapped to its Git blob SHA). Both SHAs must be full 40-character commit IDs. Before publication, leave this one PR comment and run `python scripts/check_evidence.py provenance --pr <number> --owner <login>` once at that time to validate it. The publication workflow and `release` acceptance do not check provenance. This verifies recorded evidence, not originality automatically.

## 日本語

エージェントが書き、校正モデルが推敲し、人が意味を確認します。言語ごとにルールを差し替えられる、AI生成文の校正用MCPサーバーとエージェントSkillです。日本語を最初の対象にしています。

参照実装: 執筆エージェントはClaude CodeまたはCodex CLI、校正モデルはVertex AI上のGeminiです。

**Version 0.2.0。** サーバーと両pluginの版数を揃えています。判定は引き続き既定でoff（`judgment.enabled=false`）で、polishはv1、rewriteはv2を維持します。有効化にはv3対応clientまたは同梱Skillが必要で、両degreeとも `schema_version=3` を返します。実機でのgate・verify校正、日本語のnativeレビュー、実clientでの受入は未確認です。offline CIの成功はこれらの確認を意味しません。版数の変更だけでは公開されず、tagと公開は所有者が別途行います。

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

**任意の判定。** 判定は既定でoffです。追加送信先を確認したうえで `judgment.enabled`（または `COPYEDITOR_JUDGMENT_ENABLED=true`）を有効にします。対応モデルは `jev-1.13.0`、定義は `reference-gate-action-v1` と `gate-verify-v1` に固定し、追従型モデルaliasや未知のregistry IDは拒否します。詳細は[判定設定](contracts/config.md#judgment-fields)を参照してください。

`TYPESAFE_API_KEY` は実行時のsecret環境変数だけから渡し、config、placeholder、Docker build引数、image、commitするファイルには含めません。無効時はこのkeyを参照せず、要求もしません。元へ戻すには、`judgment.enabled=false`（明示configが優先していなければ `COPYEDITOR_JUDGMENT_ENABLED=false`）にして再起動します。以後の依頼はv1/v2へ戻り、Vertex ADCやOAuthの要件は変わりません。依頼単位の切替や、処理中の依頼への途中適用はありません。

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

#### Claude Code plugin

1. リポジトリをcheckoutし、そのdirectoryへ移動します。

```sh
git clone https://github.com/taketetsu1982/copyeditor.git
cd copyeditor
```

2. 自分のcheckout内の `plugins/claude/.mcp.json` を編集し、`https://copyeditor.invalid/mcp` を自分のサーバーの `/mcp` で終わるHTTPS endpointに替えます。初期URLは稼働サービスではありません。login前に、上記のGoogle認証とcallbackログの設定を済ませます。
3. このローカルcheckoutを登録し、pluginを導入します。

```sh
claude plugin marketplace add .
claude plugin install copyeditor@copyeditor-local
```

4. 新しいClaude Code sessionを開き、`/mcp` で `copyeditor` pluginのserverを選び、許可されたアカウントでブラウザのOAuth loginと同意を完了します。server名は画面で確認し、別途直接登録したMCP serverと同じ名前だと決めつけないでください。
5. pluginに `polish_text` と `lint_text` が表示されることを確認します。秘密を含まない本文でpluginのSkillを試し、承認要求と候補を確認して、`lint_text` も個別に試します。導入だけでは接続確認にはなりません。

#### Codex plugin

1. リポジトリをcheckoutし、そのdirectoryへ移動します。

```sh
git clone https://github.com/taketetsu1982/copyeditor.git
cd copyeditor
```

2. 自分のcheckout内の `plugins/codex/.mcp.json` を編集し、`https://copyeditor.invalid/mcp` を自分のサーバーの `/mcp` で終わるHTTPS endpointに替えます。login前に、上記のGoogle認証とcallbackログの設定を済ませます。
3. このローカルcheckoutを登録し、compatibility pluginを導入します。

```sh
codex plugin marketplace add .
codex plugin add copyeditor@copyeditor-local
```

4. 新しいCodex sessionを開き、導入したpluginのOAuth login案内に従い、許可されたアカウントでproxy consentまで完了します。clientのMCP状態表示でpluginの実際のserver名を確認し、別の直接登録に対する認証で代用しないでください。版ごとのplugin OAuth操作は実機での確認が必要です。
5. pluginの `polish_text` と `lint_text` を確認します。秘密を含まない短文でSkillを試し、toolの承認と候補を確認して、`lint_text` も個別に試します。

上のコマンド形はClaude Code 2.1.276とcodex-cli 0.154.0で確認しました。pluginの実認証と起動は運用者が確認してください。[Claude plugin reference](https://code.claude.com/docs/en/plugins-reference)と[OpenAI package documentation](https://developers.openai.com/plugins/build/plugins)も参照できます。

更新時はendpoint設定を版管理の外に控え、元のcheckoutを更新し、その `.mcp.json` のURLを保持または再設定してから同じlocal marketplace経由で再導入します。必要ならclientのplugin管理画面で旧pluginを削除し、再導入後は新しいsessionを開きます。cache内のコピーを直接編集したり、デプロイ設定をcommitしたりしないでください。

#### 送信許可と結果の扱い

projectの `CLAUDE.md` または `AGENTS.md` に、例えば「校正を依頼したときは、私が明示的に選んだ機密ではない段落だけを、設定済みのcopyeditorサーバーとそのVertex AI providerへ送信してよい。それ以外は送信前に確認する」と記します。これは本文の対象範囲の許可で、clientの承認を省くものではありません。toolの自動承認やpermission skipは追加しません。

サーバーは本文・候補・診断を永続保存しません。providerの保持やインフラログはその保証に含まれず、後述の確認が必要です。両pluginのSkillは、送信許可の判定、機密・保護対象の除外、原文位置と関連itemの記録、候補の比較、許可された局所変更の反映を行います。反映前に原文が変わっていないか確認し、関連する変更はまとめて扱います。clientが承認を拒否したら、その試行は終了します。別経路への切り替えや権限の緩和は行いません。

**判定を有効にすると**、本文・許可されたcontext/背景・候補はTypeSafe AIにも送信される場合があります。両degreeとも `schema_version=3` を返し、`lint_text` は変わりません。Skillは依頼ごとにdiscoveryを確認し、TypeSafe AIを送信許可と報告に含め、不明時も有効の可能性があるものとして扱います。Vertexだけへの許可は追加先を含みません。起動時stderr、初期化の説明、tool定義で開示しますが、**直接MCPを呼ぶ利用者の依頼ごとの同意は保証しません**。運用者は有効化前に利用者へ知らせてください。

判定は採用の許可でも、意味を保持できた証明でもありません。検証passでもSkillの意味比較や人の承認は省きません。変更根拠不足・検査未実施による原文維持と、`verification_rejected` による候補の見送り・原文保持を区別し、候補への不合格判定を原文の欠陥として説明しないでください。分類・応答の規則は[CTR-01](contracts/tools.md#registered-threshold-classification)を参照してください。

**診断つき書き直し（判定off時）。** 通常の推敲は既定の `degree=polish` を使い、応答はv1です。明示的に書き直しを依頼した場合だけ `degree=rewrite` を使い、`schema_version=2` の応答を受け取ります。サーバーが先に表現を診断し、その診断を固定して書き直します。呼び出す側は診断を入力しません。通常の送信許可を確認した後、例えば次を `polish_text` に渡します。

```json
{"text":"The team will carry out a review of the draft.","language":"en","degree":"rewrite"}
```

送信前に、discoveryのdegree enumが `rewrite` を明示していることを確認します。非対応・不明なら未送信・未処理とし、本文を試しに送ったり、黙ってpolishへ替えたりしません。`status=issue` の診断には確認対象の表現と理由が入ります。`status=no_issue` では原文の完全一致が必須です。これはflagやlintの指摘とは別のものです。診断があること、モデル呼出しの成功、lint指摘ゼロのいずれも、採用許可や意味保持の証明にはなりません。

**上限と失敗時の扱い。** 書き直しでも入力上限は変わりません。最大32 items、本文合計12,000 Unicodeコードポイント、itemごとのcontextは1,000、context合計4,000、背景合計4,000、contextと背景の合計も4,000、本文・context・背景の合計16,000です。背景の各fieldは1,000までです。raw HTTP bodyの上限は262,144 bytesです。Skillの通常目安は16 items / 本文6,000コードポイントですが、すべての厳密な上限を満たす必要があります。[CTR-01の入力上限](contracts/tools.md#requests)を参照してください。

HTMLは常に全文を一つの `text` として `format=html` で送り、itemsや部分文書にはしません。全文の許可を得られない、または上限内で送れない場合は未処理にします。サーバーは一度診断し、4 itemsずつ候補を生成します。保持・HTML検査による再生成は共通で各item一度までです。request全体のエラーでは、先に成功したbatchも含めて候補と診断をすべて破棄し、clientは全原文を残します。

確認できた `input_limit`、`generation_truncated`、rewriteの `output_limit` に限り、text/Markdownを意味の境界で小さな子batchへ分け、一世代だけ再送できます。body上限によるHTTP 413と判明している場合もinput_limit相当です。孫への再分割、重複送信、HTMLの分割再送は行いません。`request_budget`、provider・認証・接続エラー、timeout、不正入力・応答、未対応言語では自動再送を止めます。providerを替えたり、一般的なエラーを打ち切りと推測したりしません。

**計量と受入。** 書き直しの生成呼出しは最大17回、requestの期限は240秒で、累積の入力・出力予約にも固定上限があります。これらは工学的な上限で、待ち時間の保証ではありません。CountTokensの事前見積もりでもVertex AIへデータを送るため、生成開始前の `model_called=false` は「何も送信していない」という意味ではありません。`model_calls` は生成だけを数えます。usageとcostには診断・再生成を含め、usage不明は不明のまま扱います。token見積もりやcancelは、請求額の証明やリモートで受理済みの処理の課金取消しを保証しません。[書き直しの計量](contracts/tools.md#rewrite-limits-and-accounting)を参照してください。

日本語には固定24例を5回ずつ反復する合成評価セットがあります。offline fixtureの成功はnative確認やlive品質受入の代わりにはなりません。例文・不変事項の所有者確認、人の独立判定を伴う実Vertex評価、実clientでの許可・拒否・反映の確認は未完了です。英語・中国語の書き直し品質は未検証で、上の英語リクエストは構文例にすぎません。

[CTR-02](rules/common.md#meaning-and-adoption)では、安全に採用できると確認できない場合の**見送り**は原文を残すことです。**flag**は確認が必要な候補を示し、反映の許可ではありません。**拒否**は検査不合格を示します。flag付き・拒否された候補は採用しません。検査成功だけでは意味の保持を証明できません。

pluginのSkillを使わないサーバー単体の接続では、ローカルnone用にclientを1つ選びます。

```sh
claude mcp add --transport http copyeditor http://127.0.0.1:8080/mcp
codex mcp add copyeditor --url http://127.0.0.1:8080/mcp
```

GoogleではURLをHTTPS originと `/mcp` に替え、clientのOAuth loginを完了します。`polish_text` と `lint_text` をdiscoveryで確認し、秘密を含まない短文で最初のpolishを試して、反映前に結果を確認します。外部への本文送信にはclientの承認が必要です。`lint_text` はモデルを呼びません。入力・上限・出力は[CTR-01](contracts/tools.md)に従います。

Claude Desktop、Claude Code、ChatGPT デスクトップ、Codex CLI、Codex アプリの接続手順は [deployment/mcp-clients.md](deployment/mcp-clients.md) を参照してください。

### 派生imageと言語ルール

別のbuild directoryに秘密を含まない `config.yaml` と、[CTR-03](rules/README.md#overlays)に従う追加用 `rules/ja.md` を用意し、lint IDを重複させません。両認証modeとも次のDockerfileを使い、configでmodeを選びます。

```dockerfile
FROM copyeditor:local
COPY config.yaml /etc/copyeditor/config.yaml
COPY rules/ja.md /etc/copyeditor/rules.d/ja.md
```

そのdirectoryで `docker build -t copyeditor:custom .` を実行し、起動コマンドのimageを `copyeditor:custom` に替えます。ファイルはUID 65532が読めるようにします。overlayはルールとprotected termsを追加でき、[共通保持条件](rules/common.md)は弱められません。`/app/rules/` を上書きせず、config・rules変更後は再起動します。[examples/](examples/)は例文で、tool呼出し時には読みません。

### 保持範囲と運用ログ

サーバーは送信本文・候補を永続保存しません。stdoutには[CTR-01の監査項目](contracts/tools.md#audit-log)だけ、stderrには起動診断を出します。providerの保持方針やインフラログはこの範囲に含まれないため、別途確認してください。校正に送った本文はVertex AIへ渡り、判定有効時はTypeSafe AIも追加されます。その保持方針と処理地域は同providerの方針に従います。判定値や理由は監査ログに追加せず、provider別の全計量はv3応答で確認します。

login前に、すべてのreverse proxy / load balancerのrequest logからqueryを除くか、callbackのrequest logを無効にします。Cloud Runでは[Cloud Loggingのsink除外](https://cloud.google.com/run/docs/logging)を使い、`resource.type="cloud_run_revision" AND httpRequest.requestUrl=~"/auth/callback([?]|$)"` を対象serviceに絞って、`_Default` を含む保存・export先の全sinkに設定します。query fieldだけでなくentry全体を除外します。継承・organizationのsinkと上流proxyのログも確認してください。アプリの設定では制御できません。本物のcode/tokenを含まない合成callback通信で、どの宛先にもqueryが保存されないことを確認します。除外設定は過去のログを削除しません。

### ライセンスと謝辞

[MIT License](LICENSE)。

謝辞: natural-japanese（coji/natural-japanese）を本プロジェクトの方針の参考にしました。
公開前に、公開するrulesとexamplesへ文言を流用していないことを所有者が確認します。`lint.py`を含むコードは取り込んでいません。

所有者はPRコメント1件の `copyeditor-provenance-v1` JSONコードブロックに、`head`（現在のPR commit SHA）、`comparison_revision`（確認したcoji/natural-japaneseのcommit SHA）、`non_reuse`（`confirmed`）、`blobs`（公開する `rules/` と `examples/` の全ファイルパスとGit blob SHAの対応）を記録します。両commit SHAは40文字の完全なIDです。公開前にこのPRコメントを1件残し、その時点で `python scripts/check_evidence.py provenance --pr <number> --owner <login>` を1度実行して整合を確認します。公開workflowと最終受入の `release` はprovenanceを検査しません。記録された証拠の検査であり、独自性を自動判定するものではありません。
