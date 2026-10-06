"""Built-in deterministic rules, one family per module; importing this package registers them."""

from archlens.rubric.rules import ci, files, sast, secrets, tests, vulns

__all__ = ["ci", "files", "sast", "secrets", "tests", "vulns"]
