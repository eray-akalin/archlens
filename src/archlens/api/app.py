"""FastAPI application factory."""

from fastapi import FastAPI

from archlens import __version__
from archlens.api.routes import ApiState, router


def create_app(state: ApiState | None = None) -> FastAPI:
    """The API app. Without `state` it serves only `GET /healthz` (the image's HEALTHCHECK works
    before any storage or keys are configured)."""
    app = FastAPI(title="ArchLens", version=__version__, docs_url=None, redoc_url=None)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:  # pyright: ignore[reportUnusedFunction]
        return {"status": "ok", "version": __version__}

    if state is not None:
        app.state.archlens = state
        app.include_router(router)
    return app
