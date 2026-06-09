# Fixture: server __main__.py that imports create_uvicorn_ssl_config but never
# calls it — VIOLATION (MTLS001).
from mtls import create_uvicorn_ssl_config


def _run_streamable_http(mcp: object) -> None:
    pass
