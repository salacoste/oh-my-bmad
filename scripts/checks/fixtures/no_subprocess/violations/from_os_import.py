# Fixture: `from os import system` style direct imports — VIOLATIONS (SHELL001).
#
# Story 3.8 review H2: the AST guard must catch:
#   * ``from os import system`` followed by bare ``system(...)``
#   * ``from os import popen`` / ``execv`` / etc.
from os import execvp, popen, system


def fire_system() -> None:
    """`system(...)` after `from os import system` is a SHELL001."""
    system("echo unsafe")


def fire_popen() -> None:
    """`popen(...)` after `from os import popen` is a SHELL001."""
    popen("ls").read()


def fire_execvp() -> None:
    """`execvp(...)` after `from os import execvp` is a SHELL001."""
    execvp("/bin/sh", ["/bin/sh", "-c", "true"])
