from unittest.mock import AsyncMock, Mock

import pytest

from copyeditor.config import load_config


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, True])
async def test_startup_warns_without_auth_closes_provider_and_hides_exceptions(monkeypatch, capsys, failure):
    import copyeditor.__main__ as entry

    provider = AsyncMock()
    server = AsyncMock()
    if failure:
        server.run_http_async.side_effect = RuntimeError("PRIVATE_STARTUP_DETAIL")
    monkeypatch.setattr(entry, "load_config", lambda: load_config({"GOOGLE_CLOUD_PROJECT": "test", "PORT": "8123"}))
    monkeypatch.setattr(entry, "Vertex", Mock(return_value=provider))
    monkeypatch.setattr(entry, "build_server", Mock(return_value=server))
    assert await entry.run() == (1 if failure else 0)
    provider.aclose.assert_awaited_once()
    assert server.run_http_async.call_args.kwargs["port"] == 8123
    assert server.run_http_async.call_args.kwargs["uvicorn_config"]["access_log"] is False
    output = capsys.readouterr()
    assert "Authentication is disabled" in output.err
    assert "PRIVATE_STARTUP_DETAIL" not in output.out + output.err
