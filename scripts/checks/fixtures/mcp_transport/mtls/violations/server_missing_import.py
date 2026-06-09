# Fixture: server __main__.py that defines _run_streamable_http but does NOT
# import create_uvicorn_ssl_config from mtls — VIOLATION (MTLS001).


def _run_streamable_http(mcp: object) -> None:
    pass
