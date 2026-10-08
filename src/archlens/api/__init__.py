"""HTTP API (docs/ARCHITECTURE.md §6). M4.1 ships `GET /healthz` for the image's HEALTHCHECK; the
assessment routes come with M4.3."""

from archlens.api.app import create_app

__all__ = ["create_app"]
