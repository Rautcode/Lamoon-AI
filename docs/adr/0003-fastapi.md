# ADR 0003 — FastAPI over NestJS / Spring Boot

**Status:** Accepted · 2026-08-06

## Context
The backend is AI- and data-heavy: resume parsing (PyMuPDF, pdfplumber,
Tesseract), Gemini SDK, embeddings, payroll math. It needs first-class OpenAPI
for the Next.js client and a Postman export (spec requirement).

## Decision
FastAPI (Python) with SQLAlchemy and Pydantic.

## Consequences
- The AI/OCR/ML ecosystem is native Python — no cross-language bridge for the
  part of the product that *is* the product.
- OpenAPI/Swagger and JSON schema are generated automatically → `/docs` and
  Postman import come free (satisfies the API-spec deliverable).
- Pydantic gives request/response validation and typed AI output parsing in one
  library.
- Async support fits the I/O-bound external calls (Gemini, Drive, SMTP).
- Team must hold Python typing discipline; mitigated by Pydantic + mypy in CI.

## Alternatives considered
- **NestJS (TypeScript)** — rejected: would share language with the frontend,
  but every AI/OCR/payroll library would be a second-class port or a subprocess
  call to Python anyway. The core work lives in Python.
- **Spring Boot (Java)** — rejected: heavier, slower iteration, weakest AI/ML
  ecosystem of the three; overkill for SME-scale services.
