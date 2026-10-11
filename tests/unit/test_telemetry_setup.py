"""telemetry.setup(): exports only when enabled, and never samples spans away."""

from typing import Any

import pytest

from archlens import telemetry
from archlens.config import Settings


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # pyright: ignore[reportCallIssue]


def test_setup_keeps_every_span(monkeypatch: pytest.MonkeyPatch) -> None:
    import azure.monitor.opentelemetry as distro

    calls: list[dict[str, Any]] = []
    monkeypatch.setattr(distro, "configure_azure_monitor", lambda **kw: calls.append(kw))
    monkeypatch.setattr(telemetry, "_configured", False)
    assert telemetry.setup(settings()) is False  # disabled by default
    assert telemetry.setup(settings(otel_enabled=True)) is False  # no connection string
    on = settings(
        otel_enabled=True,
        applicationinsights_connection_string="InstrumentationKey=00000000-0000-0000-0000-000000000000",
    )
    assert telemetry.setup(on) is True and telemetry.setup(on) is True  # configured once
    (kwargs,) = calls
    assert kwargs["sampling_ratio"] == 1.0  # not the distro's per-span rate limiter
