from .auth_boundary import disable_library_logging

disable_library_logging()

import asyncio
import sys
from pathlib import Path

from .audit import AuditEvent, startup_error, warn_unauthenticated, write_audit
from .config import ConfigError, load_config


def report_startup_error(code, field):
    if field == "judgment.credentials" and code in ("missing_required", "invalid_config", "credentials_unavailable"):
        print(f"ERROR: {code} at judgment.credentials.", file=sys.stderr, flush=True)
    else:
        startup_error(code, field)


async def run(config_path=Path("/etc/copyeditor/config.yaml"), rules_path=Path("/app/rules"),
              overlay_path=Path("/etc/copyeditor/rules.d")):
    provider = judgment = None
    failure = ("invalid_config", "config")
    try:
        from .rules import load_rules
        config = load_config(config_path, rules_loader=lambda values: load_rules(
            rules_path, overlay_path, values["protected_terms"]))
        snapshot = config.rules
        failure = ("credentials_unavailable", "credentials")
        from .providers import create_provider
        provider = create_provider(config)
        if config["judgment.enabled"]:
            failure = ("credentials_unavailable", "judgment.credentials")
            from .providers.typesafe import TypeSafe
            judgment = TypeSafe(config.secrets["TYPESAFE_API_KEY"], timeout_ms=config["judgment.timeout_ms"])
        failure = ("invalid_config", "auth")
        from .auth import make_auth
        auth = make_auth(config)
        from .service import Service
        from .server import build_server
        server = build_server(config, snapshot, Service(config, snapshot, lambda: provider, judgment), auth,
                              lambda record: write_audit(AuditEvent(**record)))
        failure = ("invalid_config", "server")
        if config["auth.mode"] == "none":
            warn_unauthenticated()
        if judgment is not None:
            print("WARNING: copyeditor judgment is enabled; body, permitted context/background and candidates may be sent to TypeSafe AI. Provider retention and processing region follow its policy.", file=sys.stderr)
        await server.run_http_async(host=config["server.host"], port=config["server.port"], path="/mcp",
                                    show_banner=False, uvicorn_config={"log_config": None, "access_log": False})
        return 0
    except ConfigError as error:
        report_startup_error(error.code, error.field)
        return 1
    except (Exception, SystemExit):
        report_startup_error(*failure)
        return 1
    finally:
        for client in (judgment, provider):
            if client is not None:
                try:
                    await client.aclose()
                except Exception:
                    pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
