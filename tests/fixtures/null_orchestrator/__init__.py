"""Null-orchestrator package — pass-through test fixture (Story 2.15 / FR35 / NFR-M5).

Empty package marker so ``python -m null_orchestrator`` works (the
Dockerfile copies ``null_orchestrator.py`` + ``__init__.py`` + ``__main__.py``
into ``/app/null_orchestrator/`` and invokes the package via the ``-m`` form
when desired). The module-level mypy override
``[mypy-tests.fixtures.null_orchestrator.*]`` in ``mypy.ini`` requires this
file's presence to match the dotted-package path mypy uses after replacing
the directory's hyphen with an underscore.
"""
