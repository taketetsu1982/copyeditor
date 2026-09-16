from .auth_boundary import disable_library_logging

disable_library_logging()

import asyncio
from pathlib import Path

from .audit import AuditEvent, startup_error, warn_unauthenticated, write_audit
from .config import ConfigError, load_config


async def run(config_path=Path("/etc/copyeditor/config.yaml"), rules_path=Path("/app/rules"),
              overlay_path=Path("/etc/copyeditor/rules.d")):
    provider = None
    failure = ("invalid_config", "config")
    try:
        config = load_config(config_path)
        failure = ("invalid_rules", "rules")
        from .rules import load_rules
        snapshot = load_rules(rules_path, overlay_path, config["protected_terms"])
        if config["default_language"] not in snapshot.languages:
            raise ConfigError("invalid_config", "default_language")
        failure = ("credentials_unavailable", "credentials")
        from .providers import create_provider
        provider = create_provider(config)
        failure = ("invalid_config", "auth")
        from .auth import make_auth
        auth = make_auth(config)
        from .service import Service
        from .server import build_server
        server = build_server(config, snapshot, Service(config, snapshot, lambda: provider), auth,
                              lambda record: write_audit(AuditEvent(**record)))
        failure = ("invalid_config", "server")
        if config["auth.mode"] == "none":
            warn_unauthenticated()
        await server.run_http_async(host=config["server.host"], port=config["server.port"], path="/mcp",
                                    show_banner=False, uvicorn_config={"log_config": None, "access_log": False})
        return 0
    except ConfigError as error:
        startup_error(error.code, error.field)
        return 1
    except (Exception, SystemExit):
        startup_error(*failure)
        return 1
    finally:
        if provider is not None:
            try:
                await provider.aclose()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
