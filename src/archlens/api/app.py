"""FastAPI application factory."""

from fastapi import FastAPI

from archlens import __version__


def create_app() -> FastAPI:
    """The API app. No routes read settings or storage yet, so creating it never fails."""
    app = FastAPI(title="ArchLens", version=__version__, docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok", "version": __version__}

    return app
