import json

from .judgment import _json_value

class JudgmentBudgetError(Exception):
    code = "request_budget"

def _canonical(value):
    return json.dumps(_json_value(value), sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False, allow_nan=False).encode("utf-8")
