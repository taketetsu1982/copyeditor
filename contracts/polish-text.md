# polish_text (v0.5.0)

The only MCP tool rewrites a Japanese document for a reader using Vertex AI. Callers decide what may be sent and whether to use the result.

## Input and output

`polish_text({"text": "...", "reader": "..."})` accepts two arguments:

- `text` (required): the document, in Markdown, HTML, or plain text. A nonblank string of 1-20,000 Unicode code points.
- `reader` (optional): a description of the intended reader. A nonblank string of 1-500 Unicode code points. When omitted, the document is written for "a reader in the same organization who is not familiar with the topic: they have general business knowledge but not this document's terms or background."

No trimming, normalization, language detection, or automatic splitting occurs. Other arguments, `null`, and invalid types are rejected.

Success is exactly one MCP text content containing the rewritten document. There is no JSON envelope, structuredContent, outputSchema, diagnosis, or quality judgment.

Tool annotations: `readOnlyHint: true`, `destructiveHint: false`, `openWorldHint: true`.

Failures return `isError: true` and one text content with a fixed English message:

- Invalid input: `Provide nonblank text of 1 to 20,000 Unicode code points and, if given, a nonblank reader of up to 500. Split longer text.`
- Missing authenticated identity: `Authentication failed.`
- Model request failure or invalid response: `The model request failed. Use the original text.`

Errors never include the submitted document, the reader, or provider exceptions. Preserve the original on errors. Valid output is not a guarantee that meaning, names, or numbers were preserved; compare before using it.

## Rewriting

The system instruction asks for a rewrite that the reader can follow: structure, wording, symbol-packed passages, field terms the reader does not know, and coined or formulaic AI terms may change. It asks to keep names (titles, link texts, file and artifact names, people, organizations, products, abbreviations), sources, numbers, certainty and hedging words, meaning, the role of each sentence, register and notation, and Markdown or HTML format. It asks for a result no longer than the original; in trials the result was 1.2-1.3 times the original prose length.

The document goes first in the user message, inside `<document>` tags, followed by the task with the reader and a short recap. A literal `</document>` inside the document is sent unchanged. Commands inside the document are treated as text to rewrite.

## Generation

Vertex AI uses ADC, location `global`, and model `gemini-3.7-flash` by default. Settings are thinking level MEDIUM and 65,536 output tokens. Temperature, top-p, top-k, and seed are not sent; Gemini 3.6 Flash and later ignore the sampling parameters. The response JSON schema has exactly one required string property, `text`. Blocked, truncated, malformed, missing, or blank output fails.

Each request makes one generation call, except retries. There is no token estimation, splitting, judging, or corrective regeneration. Only HTTP 429 and HTTP 5xx are retried: at most twice after 5 and 15 seconds. Each attempt has a 150-second limit, and a timed-out attempt is not retried. The entire operation, including waits, has a 180-second limit. SDK and transport retries are disabled.

## Environment

| Variable | Default / requirement |
|---|---|
| `GOOGLE_CLOUD_PROJECT` | Required Vertex project |
| `GOOGLE_CLOUD_LOCATION` | `global` |
| `COPYEDITOR_MODEL` | `gemini-3.7-flash` |
| `COPYEDITOR_AUTH_MODE` | `none` (startup warning), or `google` |
| `PORT` | `8080`; server listens on `0.0.0.0`, MCP path `/mcp` |
| `BASE_URL` | Required HTTPS origin in google mode |
| `GOOGLE_OAUTH_CLIENT_ID` | Required in google mode |
| `COPYEDITOR_ALLOWED_EMAILS` | JSON array of exact email addresses; default `[]` |
| `COPYEDITOR_ALLOWED_DOMAINS` | JSON array of lowercase domains; default `[]` |
| `GOOGLE_OAUTH_CLIENT_SECRET` | Required runtime secret in google mode |
| `OAUTH_SIGNING_KEY` | Required runtime secret, at least 32 UTF-8 bytes in google mode |

Google mode requires at least one allowed email or domain. The existing OAuthProxy, verified Google UserInfo, and email/domain checks are retained. OAuth state is in memory; redeployment or scale-to-zero requires login again. No YAML configuration is read. Legacy judgment variables are ignored.

Each tool invocation emits one JSON line to stdout: timestamp, result, input/output character counts, changed, model, reader (`given`, `default`, or null on input errors), latency_ms, retries, available token counts, and a pseudonymous user ID (null without authentication). The ID is an HMAC of the Google subject using the signing key. Submitted/generated text, reader descriptions, email addresses, tokens, and exception strings are excluded. Startup warnings and fixed startup errors use stderr; library and access logging are disabled.

Text is sent only to Vertex AI. The global endpoint does not guarantee processing in Japan. Provider data handling policies still apply.
