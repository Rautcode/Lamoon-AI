# ADR 0005 — Gemini 2.5 Flash as default model

**Status:** Accepted · 2026-08-06

## Context
Cost is the stated #1 constraint. Resume screening runs on every application,
potentially hundreds per opening. The task — extract structured fields, score,
match to a JD — is well within a fast model's ability when given a good prompt
and a strict output schema.

## Decision
Gemini 2.5 Flash is the default for all screening and generation. Higher-cost
models (Pro) are used **only on explicit demand** (a human "deep review"),
debited against AI credits. Embeddings use `text-embedding-004`.

## Consequences
- Per-resume cost stays near zero; combined with hash-caching (analyze once per
  `resume_sha256 + recipe_hash`) and stored `extracted_text` (no re-OCR),
  screening is effectively free at SME volumes.
- AI screening ships as an included feature, not a metered premium — a
  competitive wedge against incumbents.
- Quality ceiling is Flash's; mitigated by strong prompts, structured output,
  and the deterministic job-match layer (60% AI / 40% rule-based match).
- Model choice is isolated behind `AIProvider` (ADR references platform §2), so
  raising the default later is a config change.

## Alternatives considered
- **Gemini Pro as default** — rejected: ~20× cost for marginal gain on a
  bounded extraction/scoring task; destroys the cost model.
- **Self-hosted open model** — rejected for V1: GPU hosting cost and MLOps
  burden exceed API cost at this scale. Revisit only if API spend becomes the
  dominant line item.
