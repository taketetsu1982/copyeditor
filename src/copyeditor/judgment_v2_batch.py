"""Canonical v2 requests and complete-phase admission before any provider send."""
from typing import NamedTuple

from .judgment import JudgmentBatch, JudgmentInput
from .judgment_batch import JudgmentBudgetError, _canonical
from .judgment_v2 import POLICY_ID, POLICIES
from .responses import valid


class BatchPlan(NamedTuple):
    version: str
    phase: str
    candidate_round: int
    batches: tuple[JudgmentBatch, ...]


class PreparedJudgments(NamedTuple):
    plan: BatchPlan
    requests: tuple[bytes, ...]


def _assemble(data, blocks, policy):
    detecting = data.phase == "detect"
    state = {"language": data.language,
             "background": {key: getattr(data.background, key) for key in policy["background_fields"]},
             "references": policy["references"], "texts": {}}
    if not detecting:
        state["originals"] = {}
    questions = {}
    for block in blocks:
        block_id = policy["block_id_format"].format(ordinal=block.ordinal)
        state["texts"][block_id] = {"text": block.source if detecting else block.candidate,
                                   "context": block.context}
        if not detecting:
            state["originals"][block_id] = block.source
        for predicate in policy["question_order"][data.phase]:
            key = policy["question_id_format"].format(block_id=block_id, predicate_id=predicate)
            instructions = (policy["prefix"], policy["formats"][data.format],
                            policy["targets"][predicate].format(block_id=block_id),
                            policy["questions"][predicate])
            questions[key] = {"type": policy["question_type"],
                              "instructions": policy["instruction_separator"].join(instructions)}
    payload = _canonical({"model": policy["model"], "state": state, "questions": questions})
    batch = JudgmentBatch(tuple(block.ordinal for block in blocks), len(payload) + 4096,
                          len(_canonical(state)) + max(len(_canonical(q))
                                                       for q in questions.values()) + 4096)
    return payload, batch


def prepare_judgments(data: JudgmentInput, *, candidate_round=0, policy_id=POLICY_ID,
                      remaining_calls=64, remaining_input_units=262144) -> PreparedJudgments:
    valid(type(policy_id) is str and policy_id in POLICIES)
    policy = POLICIES[policy_id]
    valid(data.phase in ("detect", "verify") and data.format in policy["formats"])
    valid(type(candidate_round) is int and candidate_round in
          ((0,) if data.phase == "detect" else (1, 2)))
    for allowance, maximum in ((remaining_calls, 64), (remaining_input_units, 262144)):
        valid(type(allowance) is int and 0 <= allowance <= maximum)
    previous = 0
    for block in data.blocks:
        valid(type(block.ordinal) is int and block.ordinal > previous)
        previous = block.ordinal
        if data.phase == "verify":
            valid(isinstance(block.candidate, str))
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
    # Return nothing until every batch in this phase fits the remaining request budget.
    if len(batches) > remaining_calls or sum(b.input_units for b in batches) > remaining_input_units:
        raise JudgmentBudgetError("request_budget")
    return PreparedJudgments(BatchPlan(policy["packing_version"], data.phase, candidate_round,
                                       tuple(batches)), tuple(payloads))
