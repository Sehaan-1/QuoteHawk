"""FastAPI surface for QuoteHawk's deterministic, draft-only workflow."""

from .routes import configure_quotehawk_api, router

__all__ = ["configure_quotehawk_api", "router"]
