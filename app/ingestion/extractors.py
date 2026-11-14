"""Structured quote-extraction providers behind the same Pydantic boundary."""

from __future__ import annotations

import json
import os
from enum import StrEnum
from typing import Protocol

from google import genai
from google.genai import types

from app.domain.models import NormalizedQuote


class ExtractionProvider(StrEnum):
    """Local fixture parsing is default; Gemini remains explicit and optional."""

    FIXTURE = "fixture"
    GEMINI = "gemini"


class QuoteExtractor(Protocol):
    """Any provider must return a schema-validated normalized quote."""

    def extract(self, raw_quote: str) -> NormalizedQuote:
        """Extract exactly one supplier quote from untrusted content."""


class FixtureQuoteExtractor:
    """Parse a fixture JSON payload to exercise the same strict model boundary."""

    def extract(self, raw_quote: str) -> NormalizedQuote:
        return NormalizedQuote.model_validate(json.loads(raw_quote))


class GeminiQuoteExtractor:
    """Use Gemini structured output, then validate again before policy receives it."""

    def __init__(self, *, model_name: str | None = None) -> None:
        api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
        if not api_key:
            raise ValueError("Gemini extraction requires GOOGLE_API_KEY or GEMINI_API_KEY")
        self.model_name = model_name or os.getenv("MODEL_NAME", "gemini-3.8-flash")
        self.client = genai.Client(api_key=api_key)

    def extract(self, raw_quote: str) -> NormalizedQuote:
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=f"""Extract one supplier quotation from the following content.
Return only a JSON object matching the supplied schema. Never invent missing
commercial values: freight, duties, quantity, currency, lead time, and payment
terms must be explicit for the quote to validate.

Supplier content:
{raw_quote}""",
            config=types.GenerateContentConfig(
                temperature=0,
                response_mime_type="application/json",
                response_schema=NormalizedQuote,
            ),
        )
        if response.parsed is None:
            raise ValueError("Gemini returned no structured quote")
        return NormalizedQuote.model_validate(response.parsed)
