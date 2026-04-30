# Fixture: dynamic shell-module imports — VIOLATIONS (SHELL001).
#
# Story 3.8 review H3: the AST guard must catch:
#   * ``__import__("subprocess")``
#   * ``importlib.import_module("subprocess")``
#   * ``getattr(os, "system")`` (literal-string-arg reflection)
#
# Reflective ``eval``/``exec`` of arbitrary strings is OUT of scope per
# Story 3.8 N1 (intentionally — gate would need a much heavier static
# analyser to catch it).
import importlib
import os


def via_dunder_import() -> None:
    """`__import__("subprocess")` is a SHELL001."""
    sp = __import__("subprocess")
    sp.run(["true"], check=False)


def via_importlib_import_module() -> None:
    """`importlib.import_module("subprocess")` is a SHELL001."""
    sp = importlib.import_module("subprocess")
    sp.run(["true"], check=False)


def via_getattr_literal() -> None:
    """`getattr(os, "system")` with a literal string is a SHELL001."""
    fn = getattr(os, "system")  # noqa: B009 — fixture intentionally exercises reflective path
    fn("echo unsafe")
