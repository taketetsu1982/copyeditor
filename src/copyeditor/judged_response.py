import math
import re
from copy import deepcopy

from jsonschema import Draft202012Validator, validators

from .config import MODEL
from .judgment import ACTION_CRITERIA, POLICIES, POLICY, THRESHOLDS
from .responses import valid
from .rewrite_response import rewrite_output_schema


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
