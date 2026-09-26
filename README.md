# copyeditor

An MCP server that rewrites Japanese text with Gemini. Send a body to `polish_text` and receive only the rewritten body. Compare it with the original before using it.

## Behavior

The prompt targets unintended implications, misused or invented expressions, hard-to-follow jargon or metaphors, and formulaic AI prose. Everything else should stay unchanged. The server checks response structure but does not judge meaning or quality.

- Input: `{"text": "Japanese prose"}`, 1-12,000 Unicode code points, not whitespace alone.
- Output: one MCP text content. No reasons, scores, or JSON wrapper.
- Errors: keep the original; split input longer than 12,000 characters yourself.
- Vertex AI: ADC, `global`, `gemini-3.7-flash`; temperature 0, thinking LOW.
- Transient failures: at most two retries after 5 and 15 seconds, within 90 seconds total.

Text goes to Vertex AI; the global endpoint does not guarantee processing in Japan. The server does not log submitted or generated text. Callers decide what may be sent and whether to adopt edits.

## Run and connect

Use Python 3.12 and an authenticated ADC environment:

```sh
python -m pip install -r requirements.generated.txt
export GOOGLE_CLOUD_PROJECT=your-project-id
PYTHONPATH=src python -m copyeditor
```

Connect an MCP client to `http://localhost:8080/mcp`. `GET /health` reports process health. No Skill or plugin ZIP is needed. Authentication defaults to `none` with a startup warning; use it only for local access. A remote instance uses Google login and an allowlist.

Configuration is environment-only. `COPYEDITOR_MODEL` overrides the model; `GOOGLE_CLOUD_LOCATION` defaults to `global`, and `PORT` to `8080`. For Google login set `COPYEDITOR_AUTH_MODE=google`, `BASE_URL`, and `GOOGLE_OAUTH_CLIENT_ID`. Supply `GOOGLE_OAUTH_CLIENT_SECRET` and `OAUTH_SIGNING_KEY` through your runtime secret manager. Register `BASE_URL/auth/callback` as the Google OAuth redirect URI. Set at least one of `COPYEDITOR_ALLOWED_EMAILS` or `COPYEDITOR_ALLOWED_DOMAINS` as a JSON array. OAuth state is in memory; redeployment or scale-to-zero requires login again.

The [public contract](contracts/polish-text.md) describes validation, errors, all settings, and logging. The Dockerfile builds the same HTTP service. Tag publication builds versioned GHCR images after CI; deploying an image is a separate step.

## Tests and evaluation

```sh
python -m pytest tests -q
PYTHONPATH=src python scripts/evaluate.py --input /path/to/private-cases.json --output /path/to/private-results.json
```

Tests use fake model responses and need no cloud keys. Evaluation makes real Vertex requests and must only use data you are authorized to send. Supply 15 cases (three per tier), each with `sent_text` and `tier`. Tiers are `変えない`, `ギリ変えない`, `ギリ変える`, `変える`, `AI臭すぎる`; the first two expect unchanged text, the others expect changes. Passing requires at least 9 matches and all three `変えない` cases unchanged. Inputs and outputs stay outside version control. If local certificate validation requires the OS trust store, install `truststore` locally and add `--truststore`; it is not a production dependency.

## Legacy versions

The previous implementation is available through v0.3.0 tags and images. v0.4.0 changes the tool contract and removes judging, linting, language rules, structural HTML handling, and Skills/plugins.

---

# copyeditor（日本語）

日本語の本文を Gemini で書き換える MCP サーバーです。`polish_text` に本文を渡すと、書き換えた本文だけを返します。採用する前に原文と比べてください。

## 動作

意図と逆の含みを持つ語、誤用や造語、追いにくい専門用語や比喩、AI らしい定型表現に対象を絞り、それ以外は変えないよう指示します。サーバーは応答の構造を確かめますが、意味や品質は判定しません。

- 入力は `text` だけ。1〜12,000 Unicode コードポイントで、空白のみは不可です。
- 出力は MCP の text content 一つ。理由・点数・JSON の包みは付きません。
- エラー時は原文を残してください。上限を超えた本文は呼び出し側で分けます。
- Vertex AI の ADC、`global`、`gemini-3.7-flash` を使います。temperature は0、thinking は LOW です。
- 一時的な失敗は5秒・15秒待って最大2回再試行し、全体で90秒以内に終了します。

本文は Vertex AI に送られます。global は日本国内での処理を保証しません。送信・生成した本文をサーバーのログには残しません。何を送るか、修正を採用するかは呼び出し側が判断します。

## 起動と接続

Python 3.12 と認証済みの ADC 環境で、上記の起動コマンドを実行してください。MCP クライアントは `http://localhost:8080/mcp` に接続します。`GET /health` はプロセスの稼働確認です。Skill や plugin の ZIP は不要です。認証の既定値 `none` は起動時に警告を出すローカル用です。遠隔から使う場合は Google ログインと許可リストを設定します。

設定は環境変数だけです。モデルの変更は `COPYEDITOR_MODEL`、location は `GOOGLE_CLOUD_LOCATION`（既定 global）、ポートは `PORT`（既定8080）です。Google ログインには `COPYEDITOR_AUTH_MODE=google`、`BASE_URL`、`GOOGLE_OAUTH_CLIENT_ID` を設定します。`GOOGLE_OAUTH_CLIENT_SECRET` と `OAUTH_SIGNING_KEY` は実行環境の秘密管理から注入してください。Google OAuth のリダイレクト URI は `BASE_URL/auth/callback` です。`COPYEDITOR_ALLOWED_EMAILS` または `COPYEDITOR_ALLOWED_DOMAINS` の一方以上を JSON 配列で指定します。OAuth の状態はメモリにあり、再デプロイやスケールゼロの後は再ログインが必要です。

入力検査・エラー・設定・ログの詳細は[公開契約](contracts/polish-text.md)に記載しています。Dockerfile は同じ HTTP サービスを構築します。タグ公開時に CI を通して版付き GHCR イメージを作り、デプロイは別途行います。

## テストと評価

上記のテスト・評価コマンドを使います。通常テストは偽の応答を使い、クラウドの鍵は不要です。評価は実 Vertex を呼ぶため、送信を許可されたデータだけを使ってください。

外部 JSON に、`sent_text` と `tier` を持つ15件（5段階各3件）を指定します。「変えない」「ギリ変えない」は無修正、「ギリ変える」「変える」「AI臭すぎる」は修正を期待します。一致9件以上かつ「変えない」3件すべて無修正が合格です。入力と結果は版管理しません。手元の証明書検証で OS の証明書ストアが必要な場合は、ローカルに `truststore` をインストールし `--truststore` を付けます。本番依存には含めません。

## 旧版

旧実装は v0.3.0 までのタグとイメージに残っています。v0.4.0 はツール契約を変更し、判定・lint・言語ルール・HTML 構造処理・Skill/plugin を削除します。
