---
name: copyeditor
description: Rewrite a Japanese document (Markdown, HTML, or plain text) for a chosen reader with the copyeditor connector's polish_text tool, then compare the result with the original and report what changed and what disappeared. Use when the user asks to proofread, polish, rewrite, or make Japanese text easier to read, to remove its "AI feel", or mentions copyeditor, 校正, 推敲, 読みやすく, or AIっぽさ. Do not use for English text or source code.
---

# copyeditor (v0.5.3)

`polish_text` sends a Japanese document to Gemini on Vertex AI and returns only the rewritten document. The server asks Gemini to restructure and reword the document for the reader. It also asks Gemini to keep these things and to stay no longer than the original:

- names, sources, and numbers
- certainty and meaning, including the role of each sentence
- register, notation, and format

The server does not check whether that happened. This skill does.

## 1. Before sending

- Confirm the text may be sent. It goes to Google Vertex AI (location `global`; processing in Japan is not guaranteed). Send only what the user asked to rewrite. If it contains passwords, API keys, or personal data, ask first.
- Decide the reader. If the conversation already says who will read the document, use that as `reader`. Otherwise ask once: who will read it? If the user has no preference, omit `reader`; the server then writes for a colleague who is unfamiliar with the topic.
- Send the whole document as `text`, including Markdown or HTML markup, code blocks, and links; the server is told to keep them. The only arguments are `text` (1 to 20,000 characters) and `reader` (up to 500 characters).
- If the document is longer than 20,000 characters, split it at headings or paragraph breaks and send the parts in order with the same reader.
- `polish_text` accepts no other instructions. If the user wants something else, such as a summary or a different tone, say that `polish_text` does not do that.

## 2. After it returns

Compare the result with the original and report in the user's language. Rewrites are often longer than asked (about 1.2 to 1.3 times the original) and sometimes drop items, so check every point below.

- **Summary**: say what changed overall (structure, rewording, replaced terms) and roughly how the length changed.
- **Lost or changed items**: list each item that is missing or different in the result:
  - English words, abbreviations, and numbers, including labels such as `P1` or `R1`, years, and percentages
  - titles, link texts, and file, product, or page names
  - reference notes that point to other material, such as 〜より, 〜と同等, or 〜を参照, mentions of attachments or other pages, and names of methods
  - hedging and status words that were added or removed, such as ほぼ, 概ね, 原理的に, 〜と言える, 約, 程度, 未実施, 保留, or 封印
  - limiter, negation, and strength words that were added or removed, such as のみ, だけ, 〜しない, 不能, 必ず, or 系統的
  - changes to claims: a different direction, cause, reason, or condition; an interpretation turned into an instruction; an example turned into a criterion
- **Consistent renaming**: for each term the rewrite replaced, check that it used one replacement everywhere, in headings, tables, lists, text, and explanations in parentheses. Check also that no two different terms got the same replacement. If the original term still appears in names, file lists, or code blocks, the text must show the pairing once at its first use, for example 表現のズレ（借文）. List every place that breaks these rules.
- **Style drift**: flag the polite register (です・ます) where the original used the plain style (である or 体言止め). Also flag parentheses that changed between full-width and half-width.
- **Evidence**: show each flagged item as "original → rewrite". Do not answer with the whole document alone.
- **Split documents**: if you sent the document in parts, check each seam for duplicated or missing text.

## 3. Proposed fixes

The server rewrites boldly and leaves preservation to this check, so after the report propose a fix for each flagged item. A fix only restores or aligns:

- restore what was lost, using the original's words: an English word, number, title, link text, reference note, hedging, limiter, or status word, or the original direction, condition, or role of a claim
- remove a hedge or limiter that the rewrite added and the original does not have
- align a renamed term on one replacement, and show "replacement (original)" once at its first use when the original stays in names, file lists, or code blocks
- return the register and the parentheses to the original's style

Keep everything else in the rewrite as it is; a fix does not reword further. Number the fixes and show each as "original → rewrite → fixed", so the user can accept them one by one or all at once.

Apply only the fixes the user accepts. Present the result as the `polish_text` output with the accepted fixes, and list which fixes you applied. Before writing it to a file or draft, confirm with the user.

## 4. On failure

Keep the original. Never present your own rewrite as the output of `polish_text`.

| Message | Meaning | What to do |
|---|---|---|
| `Provide nonblank text of 1 to 20,000 Unicode code points and, if given, a nonblank reader of up to 500. Split longer text.` | The text is empty or too long, or the reader is blank or too long | Split the document or shorten the reader, then send again |
| `Authentication failed.` | The login expired; this happens after a redeploy or an idle period | Ask the user to reconnect copyeditor under Settings → Connectors |
| `The model request failed. Use the original text.` | Gemini failed (busy, timed out, or broken response). The server has already retried | Report it and keep the original. Send again only if the user asks |
| (`polish_text` is not available) | The connector is not connected or is disabled | Ask the user to connect or enable copyeditor under Settings → Connectors |
