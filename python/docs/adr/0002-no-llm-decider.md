# ADR 0002: no LLM in the money-decision path

Status: accepted.

Use calibrated gradient boosting for risk and a deterministic expected-cost policy for action. Natural-language output is evidence-ID templating with a safe fallback. An LLM or orchestration framework would add failure modes without improving this bounded decision.
