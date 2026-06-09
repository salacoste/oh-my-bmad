"""Clean fixture: no cert paths at all — NO violation."""
import ssl


def get_context() -> ssl.SSLContext:
    return ssl.create_default_context()
