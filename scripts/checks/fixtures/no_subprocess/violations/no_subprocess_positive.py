# Fixture: subprocess and os shell entry points used WITHOUT a `# noqa: SHELL001`
# suppression — VIOLATIONS (SHELL001).
#
# Demonstrates every detection class the AST guard catches:
#   1. ``import subprocess``
#   2. ``from subprocess import ...``
#   3. ``subprocess.<attr>`` access (run/Popen/check_output/etc.)
#   4. ``os.system(...)``
#   5. ``os.popen(...)``
#   6. ``os.exec*(...)`` family
import os
import subprocess
from subprocess import Popen


def shell_out_lots() -> None:
    """Every line below is an unjustified shell escape."""
    subprocess.run(["echo", "x"], check=False)
    subprocess.check_output(["ls"])
    Popen(["true"])
    os.system("echo unsafe")
    os.popen("ls -la").read()
    os.execvp("/bin/sh", ["/bin/sh", "-c", "true"])
