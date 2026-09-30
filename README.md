# copyeditor

An MCP server that rewrites Japanese documents for a reader with Gemini. Send a document (and optionally a reader description) to `polish_text` and receive only the rewritten document. Compare it with the original before using it.

## Behavior

The prompt asks Gemini to restructure and reword the document so the reader can follow it: unpack symbol-packed passages, explain or replace terms the reader does not know, and replace coined or formulaic AI terms. It asks to use one replacement per term throughout, and to keep names, sources and reference notes, numbers, certainty, meaning, the role of each sentence, register, notation, and Markdown or HTML format, and to stay no longer than the original. The server checks response structure but does not judge meaning or quality; in trials the result was 1.2-1.3 times the original length and sometimes dropped labels or changed the register.

- Input: `{"text": "Japanese document", "reader": "optional reader description"}`. `text` is 1-20,000 Unicode code points and `reader` 1-500, neither whitespace alone. Without `reader`, the document is written for a colleague unfamiliar with the topic.
- Output: one MCP text content. No reasons, scores, or JSON wrapper.
- Errors: keep the original; split documents longer than 20,000 characters yourself.
- Vertex AI: ADC, `global`, `gemini-3.7-flash`; thinking MEDIUM, up to 65,536 output tokens. Temperature and seed are not sent.
- Time limits: 150 seconds per attempt and 180 seconds in total. HTTP 429 and 5xx are retried at most twice after 5 and 15 seconds; timeouts are not retried.

Text goes to Vertex AI; the global endpoint does not guarantee processing in Japan. The server does not log submitted or generated text. Callers decide what may be sent and whether to adopt edits.

## Run and connect

Use Python 3.12 and an authenticated ADC environment:

```sh
python -m pip install -r requirements.generated.txt
export GOOGLE_CLOUD_PROJECT=your-project-id
PYTHONPATH=src python -m copyeditor
```

Connect an MCP client to `http://localhost:8080/mcp`. `GET /health` reports process health. Authentication defaults to `none` with a startup warning; use it only for local access. A remote instance uses Google login and an allowlist.

Configuration is environment-only. `COPYEDITOR_MODEL` overrides the model; `GOOGLE_CLOUD_LOCATION` defaults to `global`, and `PORT` to `8080`. For Google login set `COPYEDITOR_AUTH_MODE=google`, `BASE_URL`, and `GOOGLE_OAUTH_CLIENT_ID`. Supply `GOOGLE_OAUTH_CLIENT_SECRET` and `OAUTH_SIGNING_KEY` through your runtime secret manager. Register `BASE_URL/auth/callback` as the Google OAuth redirect URI. Set at least one of `COPYEDITOR_ALLOWED_EMAILS` or `COPYEDITOR_ALLOWED_DOMAINS` as a JSON array. OAuth state is in memory; redeployment or scale-to-zero requires login again.

The [public contract](contracts/polish-text.md) describes validation, errors, all settings, and logging. The Dockerfile builds the same HTTP service. Tag publication builds versioned GHCR images after CI; deploying an image is a separate step.

## Claude Desktop plugin

`plugin/` holds an optional Skill for Claude Desktop. It asks for the reader when the conversation does not show one. It splits documents longer than 20,000 characters and sends them. It then compares the result with the original and lists lost English words, numbers, titles, link texts, reference notes, hedging words, changed claims, terms renamed inconsistently, and register or parenthesis shifts. Connect the server as a connector first; the plugin does not register the server.

Build the upload archive with `.claude-plugin/plugin.json` at its top level; an archive that holds only `SKILL.md` is rejected:

```sh
cd plugin && zip -X -r ../copyeditor-plugin.zip .claude-plugin skills -x '*.DS_Store'
```

Upload `copyeditor-plugin.zip` in Claude Desktop under Your plugins → Upload a plugin.

## Tests and evaluation

```sh
python -m pytest tests -q
PYTHONPATH=src python scripts/evaluate.py --input /path/to/private-documents.json --output /path/to/private-results.json
```

Tests use fake model responses and need no cloud keys. Evaluation makes real Vertex requests and must only use data you are authorized to send. Supply a JSON array of 1-50 documents, each with `id`, `text`, and optionally `reader`. Each document is generated three times (`--runs` changes this). The report records, per run, the prose length ratio, the line ratio, ASCII words and numbers that disappeared, link texts that disappeared, and shifts to the polite register or full-width parentheses. There is no pass threshold: compare reports before and after a prompt change, because runs of the same prompt vary. Inputs and outputs stay outside version control. If local certificate validation requires the OS trust store, install `truststore` locally and add `--truststore`; it is not a production dependency.

## Earlier versions

v0.4.0 edited only four patterns and kept everything else; it remains available through its tag and image. The earlier implementation is available through v0.3.0 tags and images; v0.4.0 removed judging, linting, language rules, structural HTML handling, and Skills/plugins.

---

# copyeditor（日本語）

日本語の文書を、読者に合わせて Gemini で書き直す MCP サーバーです。`polish_text` に文書（と、任意で読者の説明）を渡すと、書き直した文書だけを返します。採用する前に原文と比べてください。

## 動作

読者が追えるように、構成と言い回しを整え、記号で詰めた箇所をほどき、読者の知らない用語や AI らしい造語を言い換えるよう指示します。言い換えは文書全体で 1 つの語に 1 つにそろえ、名前・出典と参照の注記・数値・確度・意味・文の役割・語調・表記・Markdown や HTML の形式は保ち、原文より長くしないよう指示します。サーバーは応答の構造を確かめますが、意味や品質は判定しません。試行では原文の 1.2〜1.3 倍の長さになり、ラベルが落ちたり語調が変わったりする回がありました。

- 入力は `text` と任意の `reader`。`text` は 1〜20,000、`reader` は 1〜500 Unicode コードポイントで、どちらも空白のみは不可です。`reader` を省くと、テーマに詳しくない同じ組織の読者に向けて書き直します。
- 出力は MCP の text content 一つ。理由・点数・JSON の包みは付きません。
- エラー時は原文を残してください。20,000 字を超える文書は呼び出し側で分けます。
- Vertex AI の ADC、`global`、`gemini-3.7-flash` を使います。thinking は MEDIUM、出力は最大 65,536 トークンです。temperature と seed は送りません。
- 1 回の呼び出しは 150 秒、全体は 180 秒までです。HTTP 429 と 5xx は 5 秒・15 秒待って最大 2 回再試行し、時間切れは再試行しません。

本文は Vertex AI に送られます。global は日本国内での処理を保証しません。送信・生成した本文をサーバーのログには残しません。何を送るか、修正を採用するかは呼び出し側が判断します。

## 起動と接続

Python 3.12 と認証済みの ADC 環境で、上記の起動コマンドを実行してください。MCP クライアントは `http://localhost:8080/mcp` に接続します。`GET /health` はプロセスの稼働確認です。認証の既定値 `none` は起動時に警告を出すローカル用です。遠隔から使う場合は Google ログインと許可リストを設定します。

設定は環境変数だけです。モデルの変更は `COPYEDITOR_MODEL`、location は `GOOGLE_CLOUD_LOCATION`（既定 global）、ポートは `PORT`（既定8080）です。Google ログインには `COPYEDITOR_AUTH_MODE=google`、`BASE_URL`、`GOOGLE_OAUTH_CLIENT_ID` を設定します。`GOOGLE_OAUTH_CLIENT_SECRET` と `OAUTH_SIGNING_KEY` は実行環境の秘密管理から注入してください。Google OAuth のリダイレクト URI は `BASE_URL/auth/callback` です。`COPYEDITOR_ALLOWED_EMAILS` または `COPYEDITOR_ALLOWED_DOMAINS` の一方以上を JSON 配列で指定します。OAuth の状態はメモリにあり、再デプロイやスケールゼロの後は再ログインが必要です。

入力検査・エラー・設定・ログの詳細は[公開契約](contracts/polish-text.md)に記載しています。Dockerfile は同じ HTTP サービスを構築します。タグ公開時に CI を通して版付き GHCR イメージを作り、デプロイは別途行います。

## Claude Desktop 用 plugin

`plugin/` に、Claude Desktop で使う任意の Skill を置いています。会話から読者が分からなければ読者を尋ね、20,000 字を超える文書は分けて送ります。結果を原文と比べ、消えた英字の語・数字・タイトル・リンクの文字列・参照の注記・程度の語、変わった主張、そろっていない言い換え、語調や括弧の変化を示します。先にサーバーをコネクタとして接続してください。plugin はサーバーを登録しません。

アップロードする ZIP は、一番上に `.claude-plugin/plugin.json` を置いて作ります。`SKILL.md` だけの ZIP は受け付けられません。

```sh
cd plugin && zip -X -r ../copyeditor-plugin.zip .claude-plugin skills -x '*.DS_Store'
```

Claude Desktop の Your plugins → Upload a plugin から `copyeditor-plugin.zip` をアップロードします。

## テストと評価

上記のテスト・評価コマンドを使います。通常テストは偽の応答を使い、クラウドの鍵は不要です。評価は実 Vertex を呼ぶため、送信を許可されたデータだけを使ってください。

外部 JSON に、`id`・`text`・任意の `reader` を持つ文書を 1〜50 件指定します。文書ごとに 3 回（`--runs` で変更可）生成し、回ごとに地の文の字数比・行数比・消えた英字と数字の語・消えたリンクの文字列・です・ます調と全角括弧の増減を記録します。合否の閾値はありません。同じ指示でも回ごとに結果が変わるので、指示を変える前と後の報告を並べて比べます。入力と結果は版管理しません。手元の証明書検証で OS の証明書ストアが必要な場合は、ローカルに `truststore` をインストールし `--truststore` を付けます。本番依存には含めません。

## 以前の版

v0.4.0 は 4 つの型だけを直し、それ以外は変えない版で、タグとイメージに残っています。それより前の実装は v0.3.0 までのタグとイメージに残っています。v0.4.0 で判定・lint・言語ルール・HTML 構造処理・Skill/plugin を削除しました。
