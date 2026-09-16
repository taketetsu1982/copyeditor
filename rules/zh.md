---
language: zh
revision: 1
native_reviewed: false
---
# zh writing rules
Draft: not reviewed by a native speaker.

## Vocabulary
Remove unnecessary verb phrases when a direct action is equally precise. Retain official names, technical terms, and expressions whose legal or procedural force matters.
<a id="zh-vocabulary-001"></a>
- bad: 您可以在列表中对已归档的申请进行查看。
- good: 您可以在列表中查看已归档的申请。
- reason: The direct verb retains the available action and the archived status of the requests.
This detector is advisory; quotations and deliberate emphasis may justify retaining the phrase.

## Syntax
Make the actor and the scope of a condition clear. Retain negation and sequence; do not add a subject or causal link that the source does not establish.
- bad: 审核人员在检查申请之后，如果批准，就记录批准理由。
- good: 审核人员先检查申请；如果批准，就记录批准理由。
- reason: The sequence is explicit and recording a reason still depends on approval.

## Structure
Arrange instructions for the reader's next action when appropriate. Retain all facts and warnings; preserve the original order when it expresses chronology or priority.
- bad: 提交后无法修改申请内容。请在提交前核对信息。
- good: 请在提交前核对信息。提交后无法修改申请内容。
- reason: The instruction is easier to find without removing the warning.

## Translation artifacts
Use idiomatic Simplified Chinese constructions instead of cumbersome translated phrasing. Retain the source's intent and level of certainty; do not strengthen an intended purpose into a promised outcome.
- bad: 这个页面的设计目的是显示申请状态。
- good: 这个页面旨在显示申请状态。
- reason: The concise purpose statement preserves intention rather than promising a result.

## Context weights
Choose wording for the audience and setting. Retain suitable courtesy and field-specific terms, and do not invent benefits for promotional text.
- bad: 请核对申请信息后再提交。
- good: 请核对申请信息后再提交。
- reason: The concise and courteous instruction already suits an application form.

## Machine-readable rules
```json
{
  "schema_version": 1,
  "protected_terms": [],
  "rules": [
    {"id": "zh-vocabulary-001", "section": "vocabulary",
     "description": "Consider a shorter expression if the meaning and emphasis remain unchanged.",
     "detector": {"kind": "literal", "value": "进行查看"}}
  ]
}
```
