---
language: en
revision: 1
native_reviewed: false
---
# en writing rules
Draft: not reviewed by a native speaker.

## Vocabulary
Prefer a shorter familiar expression when it preserves the same relationship. Retain technical terms, names, and emphasis that matters to the reader.
<a id="en-vocabulary-001"></a>
- bad: Use the search box in order to find archived requests.
- good: Use the search box to find archived requests.
- reason: The shorter connector keeps the purpose of using the search box.
This detector is advisory; quotations and deliberate emphasis may justify retaining the phrase.

## Syntax
Place modifiers near the action or noun they describe. Keep the actor, sequence, conditions, and negation; do not resolve an ambiguous actor by guessing.
- bad: The reviewer, after checking the request, records the decision.
- good: After checking the request, the reviewer records the decision.
- reason: The introductory phrase keeps the sequence clear without interrupting the subject and verb.

## Structure
Lead with the reader's immediate action when that suits the task. Retain warnings and exceptions; keep a warning first when it is the main message.
- bad: You cannot edit a request after submission. Check the details before submitting.
- good: Check the details before submitting. You cannot edit a request after submission.
- reason: The instruction comes first while the consequence remains explicit.

## Translation artifacts
Replace cumbersome purpose statements with ordinary verbs when meaning survives. Retain qualifications and intentional technical distinctions; unfamiliar wording alone does not prove a translation error.
- bad: This page serves the purpose of displaying the status of your request.
- good: This page displays the status of your request.
- reason: The direct verb states the same function without the roundabout purpose phrase.

## Context weights
Match the audience, purpose, and tone without adding claims. Retain appropriate courtesy and established terms; clear wording does not need a change merely to look edited.
- bad: Please check your application details before submitting.
- good: Please check your application details before submitting.
- reason: A courteous instruction suits a public form, so the original is retained.

## Machine-readable rules
```json
{
  "schema_version": 1,
  "protected_terms": [],
  "rules": [
    {"id": "en-vocabulary-001", "section": "vocabulary",
     "description": "Consider a shorter expression if the meaning and emphasis remain unchanged.",
     "detector": {"kind": "literal", "value": "in order to"}}
  ]
}
```
