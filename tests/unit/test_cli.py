from typer.testing import CliRunner

from archlens import __version__
from archlens.cli import app

runner = CliRunner()

COMMANDS = ("assess", "facts", "eval", "schema", "prompts", "serve", "worker")


def test_help_lists_every_command() -> None:
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    for command in COMMANDS:
        assert command in result.output


def test_version() -> None:
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.output.strip() == __version__
