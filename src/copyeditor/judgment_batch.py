import json
from typing import NamedTuple

from .judgment import (
    BatchPlan, JudgmentBatch, JudgmentInput, POLICY_ID, POLICIES, _json_value,
)
from .responses import valid


class JudgmentBudgetError(Exception):
    code = "request_budget"


class PreparedJudgments(NamedTuple):
    plan: BatchPlan
    requests: tuple[bytes, ...]


def _canonical(value):
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")


def _assemble(data, blocks, policy):
    detecting = data.phase == "detect"
    state = {"language": data.language, "background": data.background._asdict(),
             "desired_style": data.desired_style}
    targets, questions = {}, {}
    state["texts" if detecting else "pairs"] = targets
    if detecting:
        state["references"] = policy["references"]
    for block in blocks:
        block_id = policy["block_id_format"].format(ordinal=block.ordinal)
        targets[block_id] = ({"text": block.source, "context": block.context} if detecting
                            else {"original": block.source, "candidate": block.candidate,
                                  "action": block.action, "context": block.context})
        for template in policy["questions"][data.phase]:
            parts = [policy["prefix"], policy["formats"][data.format],
                     policy["targets"][data.phase].format(block_id=block_id),
                     template["instructions"]]
            if not detecting:
                parts += [block.action, policy["action_instructions"][block.action]]
            question = {"type": template["type"],
                        "instructions": policy["instruction_separator"].join(parts)}
            if "criteria" in template:
                question["criteria"] = template["criteria"]
            key = policy["question_id_format"].format(
                block_id=block_id, predicate_id=template["id"])
            questions[key] = question
    payload = _canonical({"model": "jev-1.13.0", "state": state, "questions": questions})
    batch = JudgmentBatch(tuple(block.ordinal for block in blocks), len(payload) + 4096,
                          len(_canonical(state)) + max(map(lambda q: len(_canonical(q)),
                                                           questions.values())) + 4096)
    return payload, batch


def prepare_judgments(data: JudgmentInput, *, policy_id=POLICY_ID,
                      remaining_calls=64, remaining_input_units=262144) -> PreparedJudgments:
    valid(type(policy_id) is str and policy_id in POLICIES)
    policy = POLICIES[policy_id]
    valid(policy["packing_version"] == "request-pack-v1")
    valid(data.phase in ("detect", "verify") and data.format in policy["formats"])
    for allowance, maximum in ((remaining_calls, 64), (remaining_input_units, 262144)):
        valid(type(allowance) is int and 0 <= allowance <= maximum)
    previous = 0
    for block in data.blocks:
        valid(type(block.ordinal) is int and block.ordinal > previous)
        previous = block.ordinal
        if data.phase == "verify":
            valid(isinstance(block.candidate, str) and block.action in policy["action_instructions"])
    payloads, batches, pending = [], [], ()
    accepted = None
    for block in data.blocks:
        candidate = pending + (block,)
        payload, batch = _assemble(data, candidate, policy)
        if batch.input_units > 64000 or batch.state_question_units > 32000:
            if accepted is not None:
                payloads.append(accepted[0])
                batches.append(accepted[1])
            candidate = (block,)
            payload, batch = _assemble(data, candidate, policy)
            if batch.input_units > 64000 or batch.state_question_units > 32000:
                raise JudgmentBudgetError("request_budget")
        pending, accepted = candidate, (payload, batch)
    if accepted is not None:
        payloads.append(accepted[0])
        batches.append(accepted[1])
    if len(batches) > remaining_calls or sum(b.input_units for b in batches) > remaining_input_units:
        raise JudgmentBudgetError("request_budget")
    return PreparedJudgments(BatchPlan(policy["packing_version"], data.phase, tuple(batches)),
                             tuple(payloads))
