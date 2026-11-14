"""Strict quote extraction adapters with credential-free fixture fallback."""

from .extractors import ExtractionProvider, FixtureQuoteExtractor, GeminiQuoteExtractor

__all__ = ["ExtractionProvider", "FixtureQuoteExtractor", "GeminiQuoteExtractor"]
