"""Strict, ephemeral source-document contracts for supplier quote ingestion.

Raw supplier artifacts are accepted only long enough to derive a validated
``NormalizedQuote``. They are intentionally not part of the durable RFQ snapshot
or safe operational logs.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.domain.models import QuoteSource


class DocumentContentEncoding(StrEnum):
    """Transport encoding for one supplier source artifact."""

    PLAIN_TEXT = "plain_text"
    BASE64 = "base64"


class SupplierQuoteDocument(BaseModel):
    """One untrusted supplier quote artifact submitted for an RFQ comparison.

    ``pdf`` means a base64-encoded PDF invoice/quotation. ``pdf_text`` is an
    already extracted text artifact for an explicitly trusted upstream extractor;
    it is still treated as untrusted supplier content by QuoteHawk.
    """

    model_config = ConfigDict(frozen=True, str_strip_whitespace=True, extra="forbid")

    source: QuoteSource
    content: str = Field(min_length=1, max_length=2_000_000)
    content_encoding: DocumentContentEncoding = DocumentContentEncoding.PLAIN_TEXT
    filename: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def require_supported_source_and_encoding(self) -> SupplierQuoteDocument:
        supported_sources = {
            QuoteSource.CSV,
            QuoteSource.EMAIL,
            QuoteSource.PDF,
            QuoteSource.PDF_TEXT,
        }
        if self.source not in supported_sources:
            raise ValueError("supplier document source must be csv, email, pdf, or pdf_text")
        if self.source is QuoteSource.PDF:
            if self.content_encoding is not DocumentContentEncoding.BASE64:
                raise ValueError("PDF supplier documents must use base64 content_encoding")
        elif self.content_encoding is not DocumentContentEncoding.PLAIN_TEXT:
            raise ValueError("CSV, email, and PDF text supplier documents must use plain_text")
        return self
