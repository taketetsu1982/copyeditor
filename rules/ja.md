---
language: ja
revision: 1
native_reviewed: false
---
# ja writing rules
Draft: awaiting the owner's section-by-section native review.

## Vocabulary
Prefer a direct verb when a longer expression adds no distinction. Retain technical terms, names, and wording that distinguishes permission from ability.
<a id="ja-vocabulary-001"></a>
- bad: 一覧画面から申請の状況を確認することが可能です。
- good: 一覧画面から申請の状況を確認できます。
- reason: Both describe the same available action; the shorter ending removes no condition.
The detector flags a phrase for review, not an unconditional replacement, including in quotations.

## Syntax
Make the actor and the scope of each condition clear. Retain negation, sequence, responsibility, and qualifications; ask when the intended reading is uncertain.
- bad: 担当者は、申請を確認した後で、承認する場合は理由を記録します。
- good: 担当者は申請を確認し、承認する場合は理由を記録します。
- reason: The actor and order remain explicit, and recording the reason still depends on approval.

## Structure
Put the reader's immediate action before supporting explanation when the purpose calls for it. Retain every fact and exception; preserve chronology when order carries meaning.
- bad: 送信後は内容を変更できません。送信前に入力内容を確認してください。
- good: 送信前に入力内容を確認してください。送信後は内容を変更できません。
- reason: The instruction comes first and its irreversible consequence remains visible.

## Translation artifacts
Replace cumbersome descriptions of purpose with ordinary Japanese expressions when the same claim survives. Retain emphasis, attribution, and deliberate technical distinctions.
- bad: この画面は、申請の確認を行うために設計されています。
- good: この画面は、申請を確認するために設計されています。
- reason: The purpose and design claim remain; the nominalized action becomes a verb.
Do not infer that an unfamiliar expression is a translation error without context.

## Context weights

Default style: Use natural, direct Japanese. Prefer familiar, concrete words and omit formulaic padding while preserving meaning and register.
Use audience, purpose, tone, and message to choose among equally accurate expressions. Retain suitable politeness and established terminology; never invent benefits to strengthen promotional copy.
- bad: 申請内容をご確認のうえ、送信してください。
- good: 申請内容をご確認のうえ、送信してください。
- reason: A public application form benefits from a courteous instruction; no change is needed.
For landing pages, protect actual product names while allowing surrounding generic prose to improve.

## Machine-readable rules
```json
{
  "schema_version": 1,
  "protected_terms": [],
  "rules": [
    {"id": "ja-vocabulary-001", "section": "vocabulary",
     "description": "Consider a direct verb if the distinction between permission and ability is unchanged.",
     "detector": {"kind": "literal", "value": "することが可能です"}}
  ]
}
```
