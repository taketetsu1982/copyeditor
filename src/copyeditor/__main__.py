from .auth_boundary import disable_library_logging

disable_library_logging()

import asyncio
import sys

from .auth import make_auth
from .config import load_config
from .providers.vertex import Vertex
from .server import build_server


async def run():
    provider = None
    try:
        config = load_config()
        auth = make_auth(config)
        provider = Vertex(config)
        server = build_server(config, provider, auth)
        if config["auth.mode"] == "none":
            print("WARNING: Authentication is disabled.", file=sys.stderr, flush=True)
        await server.run_http_async(host="0.0.0.0", port=config["server.port"], path="/mcp",
                                    show_banner=False, uvicorn_config={"log_config": None, "access_log": False})
        return 0
    except (Exception, SystemExit):
        print("Server startup or runtime failed. Check configuration and ADC.", file=sys.stderr, flush=True)
        return 1
    finally:
        if provider is not None:
            try:
                await provider.aclose()
            except Exception:
                pass


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
