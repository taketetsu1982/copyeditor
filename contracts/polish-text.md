# polish_text (v0.4.0)

The only MCP tool rewrites Japanese prose using Vertex AI. Callers decide what may be sent and whether to use the result.

## Input and output

`polish_text({"text": "..."})` accepts exactly one argument: a nonblank string of 1-12,000 Unicode code points. No trimming, normalization, language detection, or automatic splitting occurs. Other arguments and invalid types are rejected.

Success is exactly one MCP text content containing the rewritten body. An unnecessary rewrite returns the original string. There is no JSON envelope, structuredContent, outputSchema, diagnosis, or quality judgment.

Tool annotations: `readOnlyHint: true`, `destructiveHint: false`, `openWorldHint: true`.

Failures return `isError: true` and one text content with a fixed English message:

- Invalid input: `Provide nonblank text of 1 to 12,000 Unicode code points. Split longer text.`
- Missing authenticated identity: `Authentication failed.`
- Model request failure or invalid response: `The model request failed. Use the original text.`

Errors never include the submitted body or provider exception. Preserve the original on errors. Valid output is not a guarantee that meaning, facts, or names were preserved; compare before using it.

## Generation

Vertex AI uses ADC, location `global`, and model `gemini-3.7-flash` by default. The body is a separate user message. The system instruction limits edits to unintended implications, misuse or invented expressions, hard-to-follow jargon or metaphors, and formulaic AI prose. Commands inside the body are treated as text to edit.

Each request makes one generation call, except transient retries. There is no token estimation, splitting, judging, or corrective regeneration. Settings are temperature 0, thinking LOW, and 16,384 output tokens. The response JSON schema has exactly one required string property, `text`. Blocked, truncated, malformed, missing, or blank output fails.

Only HTTP 429, HTTP 5xx, and timeouts are retried: at most twice after 5 and 15 seconds. Each attempt has a 30-second limit; the entire operation, including waits, has a 90-second limit. SDK and transport retries are disabled.

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

Each tool invocation emits one JSON line to stdout: timestamp, result, input/output character counts, changed, model, latency_ms, retries, available token counts, and a pseudonymous user ID (null without authentication). The ID is an HMAC of the Google subject using the signing key. Submitted/generated text, email addresses, tokens, and exception strings are excluded. Startup warnings and fixed startup errors use stderr; library and access logging are disabled.

Text is sent only to Vertex AI. The global endpoint does not guarantee processing in Japan. Provider data handling policies still apply.
