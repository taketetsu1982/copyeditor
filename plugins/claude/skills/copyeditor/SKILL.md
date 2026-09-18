---
name: copyeditor
description: Polish writing when asked to proofread, improve readability, or make Japanese natural, including 校正して, 読みやすくして, and 自然な日本語に.
---

# Copyeditor

1. Confirm the text to polish and the requested language. Consult [shared preservation conditions](rules/common.md).
2. Call the connected `polish_text` tool using its discovered input schema and the client's normal approval flow. If approval is denied, do not send the text or retry through another route.
3. Present the original and returned candidate, including any flags or rejection reasons, for review. Do not apply changes automatically.

This distribution provides a minimal workflow. Detailed permission classification, extraction, item comparison, and selective application belong to a later implementation batch.
