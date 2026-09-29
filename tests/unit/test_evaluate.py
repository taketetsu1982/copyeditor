import importlib.util
import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from copyeditor.providers.vertex import Generation, ProviderFailure

SPEC = importlib.util.spec_from_file_location("copyeditor_evaluation", Path(__file__).parents[2] / "scripts/evaluate.py")
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)

ORIGINAL = """# Synthetic Report
[Synthetic Page](https://example.com/page)
- R1 を使う。数は 277 件で、比率は 1/700 である。
- 詳しくは <a href="https://example.com/a">Guide Page</a> を見る（補足）。

```text
CODE_ONLY_WORD
```
"""


def documents(count=2):
    return [{"id": f"doc-{index}", "text": ORIGINAL} for index in range(count)]


def test_metrics_report_lost_tokens_links_register_and_parentheses():
    output = ORIGINAL.replace("277", "多数").replace("[Synthetic Page]", "[Page]").replace("である。", "です。")
    output = output.replace("Guide Page", "Guide").replace("（補足）", "（補足）（追記）")
    result = evaluation.metrics(ORIGINAL, output)
    assert result["lost_tokens"] == ["277"]
    assert result["lost_link_texts"] == ["Guide Page", "Synthetic Page"]
    assert result["desu_masu_delta"] == 1 and result["fullwidth_paren_delta"] == 1
    assert result["line_ratio"] == 1.0


def test_prose_ratio_ignores_code_links_and_markup():
    doubled_code = ORIGINAL.replace("CODE_ONLY_WORD", "CODE_ONLY_WORD\n" * 50)
    moved_link = ORIGINAL.replace("https://example.com/page", "https://example.com/a-much-longer-target")
    assert evaluation.metrics(ORIGINAL, doubled_code)["prose_ratio"] == 1.0
    assert evaluation.metrics(ORIGINAL, moved_link)["prose_ratio"] == 1.0
    assert evaluation.metrics(ORIGINAL, ORIGINAL.replace("数は", "数はおよそ"))["prose_ratio"] > 1.0


@pytest.mark.asyncio
async def test_each_document_runs_the_requested_times_with_its_reader():
    docs = documents()
    docs[1]["reader"] = "Synthetic reader"
    provider = AsyncMock()
    provider.polish.return_value = Generation(ORIGINAL, retries=1, usage={"total_tokens": 9})
    progress = []
    report = await evaluation.evaluate(docs, provider, runs=3, progress=progress.append)
    assert provider.polish.await_count == 6
    readers = [call.args[1] for call in provider.polish.await_args_list]
    assert readers == [None] * 3 + ["Synthetic reader"] * 3
    assert [(row["id"], row["run"]) for row in report["results"]] == [(d["id"], n) for d in docs for n in (1, 2, 3)]
    assert report["documents"][0] == dict(id="doc-0", runs=3, successes=3, prose_ratio_min=1.0, prose_ratio_max=1.0,
                                          lost_tokens=[0, 0, 0], lost_link_texts=[0, 0, 0])
    assert progress == [dict(id=row["id"], run=row["run"], result="success") for row in report["results"]]


@pytest.mark.asyncio
async def test_failures_keep_usage_and_retries_without_exception_text():
    usage = {"prompt_tokens": 17, "total_tokens": 23}
    provider = AsyncMock()
    provider.polish.side_effect = [ProviderFailure(retries=2, usage=usage), RuntimeError("PRIVATE_EXCEPTION")]
    progress = []
    report = await evaluation.evaluate(documents(1), provider, runs=2, progress=progress.append)
    first, second = report["results"]
    assert first["result"] == second["result"] == "model_error"
    assert first["usage"] == usage and first["retries"] == 2 and first["text"] is None and first["metrics"] is None
    assert second["usage"] == {} and second["retries"] == 0
    assert report["documents"][0]["successes"] == 0 and report["documents"][0]["prose_ratio_min"] is None
    assert "PRIVATE_EXCEPTION" not in json.dumps(report) + json.dumps(progress)


@pytest.mark.parametrize("invalid", ["not_list", "empty", "too_many", "missing_id", "duplicate_id", "blank_text",
                                     "too_long", "blank_reader", "long_reader", "unknown_key"])
def test_invalid_external_documents_are_rejected(invalid):
    docs = documents()
    if invalid == "not_list":
        docs = {"id": "doc", "text": ORIGINAL}
    elif invalid == "empty":
        docs = []
    elif invalid == "too_many":
        docs = documents(51)
    elif invalid == "missing_id":
        del docs[0]["id"]
    elif invalid == "duplicate_id":
        docs[1]["id"] = docs[0]["id"]
    elif invalid == "blank_text":
        docs[0]["text"] = " \n"
    elif invalid == "too_long":
        docs[0]["text"] = "x" * 20001
    elif invalid == "blank_reader":
        docs[0]["reader"] = " "
    elif invalid == "long_reader":
        docs[0]["reader"] = "r" * 501
    else:
        docs[0]["tier"] = "unused"
    with pytest.raises(ValueError):
        evaluation.validate_documents(docs)


@pytest.mark.asyncio
@pytest.mark.parametrize("runs", [0, 11])
async def test_run_count_outside_range_is_rejected_before_generation(runs):
    provider = AsyncMock()
    with pytest.raises(ValueError):
        await evaluation.evaluate(documents(1), provider, runs=runs)
    provider.polish.assert_not_awaited()


def test_failed_atomic_write_preserves_previous_report(tmp_path, monkeypatch):
    path = tmp_path / "report.json"
    path.write_text('{"previous":true}')
    monkeypatch.setattr(evaluation.os, "replace", lambda *args: (_ for _ in ()).throw(OSError("Synthetic failure")))
    with pytest.raises(OSError):
        evaluation.atomic_write(path, {"new": True})
    assert json.loads(path.read_text()) == {"previous": True}
    assert list(tmp_path.iterdir()) == [path]
