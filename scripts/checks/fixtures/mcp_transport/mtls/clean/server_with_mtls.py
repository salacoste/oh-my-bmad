# Fixture: server __main__.py that defines _run_streamable_http and correctly
# imports and calls create_uvicorn_ssl_config from mtls — CLEAN (no violations).


def _run_streamable_http(mcp: object) -> None:
    from mtls import create_uvicorn_ssl_config

    ssl_config = create_uvicorn_ssl_config()
