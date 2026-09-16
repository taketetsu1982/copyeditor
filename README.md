# copyeditor
Your agent writes, a polishing model rewrites, and you confirm the meaning. An MCP server + agent skill for copyediting AI-written text with swappable per-language rules. Japanese first.

Reference implementation: Claude Code or Codex CLI as the writing agent, Gemini on Vertex AI as the polishing model.

## Acknowledgements

Acknowledgements: natural-japanese (coji/natural-japanese) informed this project's approach.
Before publication, the owner must confirm that the public rules and examples do not reuse its wording. Its code, including `lint.py`, is not incorporated.

The owner records approval in one PR comment with a `copyeditor-provenance-v1` fenced JSON block: `head` (current PR commit SHA), `comparison_revision` (the reviewed coji/natural-japanese commit SHA), `non_reuse` (`confirmed`), and `blobs` (every public `rules/` and `examples/` file path mapped to its Git blob SHA). Both SHAs must be full 40-character commit IDs. Run `python scripts/check_evidence.py provenance --pr <number> --owner <login>` before publication; changed assets require a new review. This verifies recorded evidence, not originality automatically.

## 日本語

エージェントが書き、校正モデルが推敲し、人が意味を確認します。言語ごとにルールを差し替えられる、AI生成文の校正用MCPサーバーとエージェントSkillです。日本語を最初の対象にしています。

参照実装: 執筆エージェントはClaude CodeまたはCodex CLI、校正モデルはVertex AI上のGeminiです。

### 謝辞

謝辞: natural-japanese（coji/natural-japanese）を本プロジェクトの方針の参考にしました。
公開前に、公開するrulesとexamplesへ文言を流用していないことを所有者が確認します。`lint.py`を含むコードは取り込んでいません。

所有者はPRコメント1件の `copyeditor-provenance-v1` JSONコードブロックに、`head`（現在のPR commit SHA）、`comparison_revision`（確認したcoji/natural-japaneseのcommit SHA）、`non_reuse`（`confirmed`）、`blobs`（公開する `rules/` と `examples/` の全ファイルパスとGit blob SHAの対応）を記録します。両commit SHAは40文字の完全なIDです。公開前に `python scripts/check_evidence.py provenance --pr <number> --owner <login>` を実行し、資産変更時は再確認します。記録された証拠の検査であり、独自性を自動判定するものではありません。
