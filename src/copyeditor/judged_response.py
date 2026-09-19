import math
import json
import re
from copy import deepcopy

from jsonschema import Draft202012Validator, validators

from .config import MODEL
from .judgment import ACTION_CRITERIA, POLICIES, POLICY, THRESHOLDS
from .responses import valid
from .rewrite_response import rewrite_output_schema
from .judgment import JudgmentBlockResult, JudgmentChoice, _registry, definition_hash, classify_detection, classify_verification
from .responses import output_schema, validate_final
from .requests import ValidationError
from .providers.base import GenerationResult, Usage
from .diagnosis import parse_diagnoses
from .html import analyze


def judged_output_schema(*, limits=True):
    def obj(**properties):
        return dict(type="object", properties=properties, required=list(properties), additionalProperties=False)
    def nullable(schema):
        return {"anyOf": [schema, {"type": "null"}]}
    def pattern(expression):
        return {"type": "string", "pattern": "^(?:" + expression + r")(?![\s\S])"}
    def ordered(entries):
        return {"type": "array", "prefixItems": entries, "items": False,
                "minItems": len(entries), "maxItems": len(entries)}
    empty = {"type": "array", "maxItems": 0}
    number = {"type": "number", "minimum": 0, "maximum": 1}
    integer = {"type": "integer", "minimum": 0}
    action_id = {"enum": list(ACTION_CRITERIA)}
    action = obj(selected=action_id, probabilities=obj(**dict.fromkeys(ACTION_CRITERIA, number)),
                 confidence=number, effective=action_id, source={"enum": ["choice", "axis_fallback"]})
    detection = {"oneOf": [
        obj(status={"enum": ["eligible", "insufficient", "indeterminate"]}, reason={"const": "evaluated"},
            gate=obj(probability=number, result={"enum": ["present", "absent"]}),
            checks=ordered([obj(id={"const": axis}, probability=number) for axis in POLICY["axis_order"]]),
            action=action),
        obj(status={"const": "not_run"}, reason={"const": "no_editable_prose"},
            gate={"type": "null"}, checks=empty, action={"type": "null"})]}
    verification_ids = list(POLICY["verification"]["order"])
    verification = {"oneOf": [
        obj(status={"enum": ["pass", "fail", "indeterminate"]}, reason={"const": "evaluated"},
            checks=ordered([obj(id={"const": key}, probability=number,
                                result={"enum": ["pass", "fail", "indeterminate"]}) for key in verification_ids])),
        obj(status={"const": "not_run"}, reason={"enum": ["not_generated", "unfixable", "preservation_rejected"]},
            checks=empty)]}
    schema = rewrite_output_schema(limits=limits)
    for variant in schema["oneOf"]:
        properties = variant["properties"]
        row_fields = {key: deepcopy(properties[key]) for key in ("usage", "cost", "latency_ms")}
        rows = []
        for role, provider, model, maximum in (("editing", "vertex", pattern(MODEL), 17),
                                              ("judgment", "typesafe", {"const": "jev-1.13.0"}, 64)):
            rows.append(obj(role={"const": role}, provider={"const": provider}, model=model,
                            model_calls={**integer, "maximum": maximum},
                            estimation_calls=integer if role == "editing" else {"type": "integer", "const": 0},
                            **deepcopy(row_fields)))
        for key in ("model", "usage", "model_calls"):
            del properties[key]
        properties.update(schema_version={"type": "integer", "const": 3}, providers=ordered(rows),
                          degree={"enum": ["polish", "rewrite", None] if "error" in properties else ["polish", "rewrite"]},
                          policy_version={"enum": list(POLICIES)}, thresholds_version={"enum": list(THRESHOLDS)},
                          policy_hash=pattern("[0-9a-f]{64}"), thresholds_hash=pattern("[0-9a-f]{64}"))
        if "error" not in properties:
            target = properties["items"]["items"] if "items" in properties else variant
            fields = target["properties"]
            fields.update(diagnosis=nullable(fields["diagnosis"]), editing={"enum": ["not_run", "diagnosed_no_issue", "generated"]},
                          detection=deepcopy(detection), verification=deepcopy(verification))
            fields["flag"] = deepcopy(fields["flag"])
            fields["flag"]["anyOf"][0]["oneOf"].append(obj(
                kind={"const": "verification_rejected"},
                reason={"enum": ["Candidate verification failed.", "Candidate verification was inconclusive."]},
                checks={"type": "array", "items": {"enum": verification_ids}, "minItems": 1,
                        "maxItems": 4, "uniqueItems": True}))
            if not limits:
                fields["text"].pop("maxLength", None)
            target["required"] = list(fields)
        variant["required"] = list(properties)
    return schema


def validate_judged_shape(payload, *, limits=True):
    def scalar_tree(value):
        if type(value) is dict:
            return all(type(k) is str and scalar_tree(k) and scalar_tree(v) for k, v in value.items())
        if type(value) is list:
            return all(scalar_tree(v) for v in value)
        if type(value) is str:
            return re.search(r"[\ud800-\udfff]", value) is None
        return value is None or type(value) in (bool, int) or type(value) is float and math.isfinite(value)
    intact = False
    try:
        intact = scalar_tree(payload)
    except RecursionError:
        pass
    valid(intact)
    checker = Draft202012Validator.TYPE_CHECKER.redefine("integer", lambda _, value: type(value) is int)
    valid(validators.extend(Draft202012Validator, type_checker=checker)(judged_output_schema(limits=limits)).is_valid(payload))


def validate_judged_final(payload, originals=(), *, format="text"):
    validate_judged_shape(payload, limits=False)
    policy, threshold = _registry(payload["policy_version"], payload["thresholds_version"])
    valid(payload["policy_hash"] == definition_hash(policy) and payload["thresholds_hash"] == definition_hash(threshold))
    versions = dict(policy_id=payload["policy_version"], threshold_id=payload["thresholds_version"])
    editing, judgment = payload["providers"]
    for row in payload["providers"]:
        if row["model_calls"] == 0:
            valid(all(value == 0 for value in row["usage"].values()) and row["cost"] is None)
        if row["model_calls"] + row["estimation_calls"] == 0:
            valid(row["latency_ms"] == 0)
        if any(row["usage"][key] is None for key in ("input_tokens", "output_tokens")):
            valid(row["cost"] is None)
    valid(not judgment["model_calls"] or judgment["usage"]["total_tokens"] is None)
    called = [row for row in payload["providers"] if row["model_calls"]]
    costs = [row["cost"] for row in called]
    if not costs or None in costs or len({cost["currency"] for cost in costs}) != 1:
        valid(payload["cost"] is None)
    else:
        valid(payload["cost"] is not None and payload["cost"]["currency"] == costs[0]["currency"])
    calls = editing["model_calls"]
    rewrite = payload["degree"] == "rewrite"
    valid(calls <= (17 if rewrite else 2))
    if payload["status"] == "error":
        valid(payload["model_called"] == bool(called))
        retry = payload["regeneration_attempted"]
        valid(not retry or calls >= 3) if rewrite else valid(retry == (calls == 2))
        if payload["error"]["code"] in ("invalid_input", "unsupported_language", "input_limit"):
            valid(not called and editing["estimation_calls"] == 0 and not retry)
        entries = []
    else:
        entries = payload.get("items", [payload])
        valid(bool(originals) and len(entries) == len(originals))
        valid(len({o.id for o in originals}) == len(originals))
        if "items" in payload:
            valid([item["id"] for item in entries] == [o.id for o in originals])
        diagnostics, admitted, evaluated, verified = [], [], 0, 0
        for ordinal, (item, original) in enumerate(zip(entries, originals), 1):
            detection, verification, flag = item["detection"], item["verification"], item["flag"]
            if detection["status"] == "not_run":
                html = analyze(original.text) if format == "html" else None
                valid(html is not None and html.accepted and None not in html.signature)
            else:
                evaluated += 1
                action = detection["action"]
                block = JudgmentBlockResult(ordinal, (("gate", detection["gate"]["probability"]),) +
                    tuple((c["id"], c["probability"]) for c in detection["checks"]),
                    JudgmentChoice(action["selected"], action["probabilities"], action["confidence"]))
                valid(detection == classify_detection(block, **versions))
            eligible = detection["status"] == "eligible"
            if rewrite and eligible:
                valid(item["diagnosis"] is not None)
                diagnostics.append(dict(id=original.id, **item["diagnosis"]))
                admitted.append(original)
            else:
                valid(item["diagnosis"] is None)
            no_issue = rewrite and eligible and item["diagnosis"]["status"] == "no_issue"
            if not eligible or no_issue:
                valid(item["editing"] == ("diagnosed_no_issue" if no_issue else "not_run"))
                valid(item["text"] == original.text and flag is None and not item["regenerated"])
                reason = "not_generated"
            else:
                valid(item["editing"] == "generated")
                reason = {"unfixable": "unfixable", "rejected": "preservation_rejected"}.get(flag["kind"] if flag else "")
            if reason:
                valid(verification == dict(status="not_run", reason=reason, checks=[]))
            else:
                verified += 1
                block = JudgmentBlockResult(ordinal, tuple((c["id"], c["probability"]) for c in verification["checks"]), None)
                valid(verification == classify_verification(block, **versions))
                status = verification["status"]
                expected = None if status == "pass" else dict(kind="verification_rejected",
                    reason="Candidate verification failed." if status == "fail" else "Candidate verification was inconclusive.",
                    checks=[c["id"] for c in verification["checks"] if c["result"] != "pass"])
                valid(flag == expected)
            if flag:
                valid(item["text"] == original.text)
        generated = sum(item["editing"] == "generated" for item in entries)
        retried = any(item["regenerated"] for item in entries)
        batches = (generated + 3) // 4
        if rewrite:
            valid(int(bool(admitted)) + batches <= calls <= int(bool(admitted)) + 2 * batches)
            valid(retried == (calls > int(bool(admitted)) + batches))
        else:
            valid(calls == (1 + int(retried) if generated else 0))
        valid(generated or editing["estimation_calls"] == 0 or bool(admitted))
        valid((1 + int(bool(verified)) <= judgment["model_calls"] <= evaluated + verified) if evaluated else judgment["model_calls"] == 0)
        # Legacy call equations do not describe v3 KEEP; project only common output integrity.
        legacy = output_schema("polish_text")["oneOf"][0]["properties"]
        projection = {key: deepcopy(value) for key, value in payload.items() if key in legacy}
        projection.update(schema_version=1, model=editing["model"], usage=Usage(0, 0, 0)._asdict(), cost=None,
                          model_calls=2 if retried else 1)
        projected = [{key: deepcopy(value) for key, value in item.items() if key in legacy or key == "id"} for item in entries]
        for item in projected:
            if item["flag"] and item["flag"]["kind"] == "verification_rejected":
                item["flag"] = None
        if "items" in payload:
            projection["items"] = projected
        else:
            projection.update(projected[0])
            projection["schema_version"] = 1
        validate_final(projection, check_limits=False)
        if diagnostics:
            parse_diagnoses(GenerationResult(json.dumps({"diagnoses": diagnostics}), "stop", Usage(None, None, None)), admitted)
    if sum(len(item["text"]) for item in entries) > 16000 or len(json.dumps(payload, ensure_ascii=False, allow_nan=False,
                                                                            separators=(",", ":")).encode()) > 1048576:
        raise ValidationError("output_limit")
