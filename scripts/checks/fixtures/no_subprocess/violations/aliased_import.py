# Fixture: aliased subprocess imports — VIOLATIONS (SHELL001).
#
# Story 3.8 review H1: the AST guard must catch ALL of:
#   * ``import subprocess as sp`` followed by ``sp.run(...)``
#   * ``from subprocess import run`` followed by bare ``run(...)``
#   * ``from subprocess import Popen as P`` followed by bare ``P(...)``
#
# Each call site below carries an unjustified shell escape via an aliased
# import; every one MUST surface a SHELL001 violation.
import subprocess as sp
from subprocess import Popen as P
from subprocess import run


def aliased_subprocess_module() -> None:
    """`sp.run(...)` after `import subprocess as sp` is a SHELL001."""
    sp.run(["true"], check=False)


def from_import_run() -> None:
    """Bare `run(...)` after `from subprocess import run` is a SHELL001."""
    run(["echo", "ok"], check=False)


def from_import_popen_aliased() -> None:
    """Bare `P(...)` after `from subprocess import Popen as P` is a SHELL001."""
    P(["true"]).wait()
