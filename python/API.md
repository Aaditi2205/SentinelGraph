# Local API contract

## Read endpoints

- `GET /api/health` — process/model readiness.
- `GET /api/metrics` — locked evaluation artifact.
- `GET /api/incident` — deterministic test-derived replay.
- `GET /api/drift` — computed PSI report and boundary.
- `GET /api/audit` and `/api/audit/verify` — last 50 receipts and chain verification.
- `GET /api/feedback` — restart-persistent incident/entity feedback state.
- `GET /api/razorpay/status` — whether the independent webhook secret is configured.
- `GET /api/razorpay/coverage` — gateway fields, missing model feature groups, and score-eligibility decision.
- `GET /api/copilot/status` — RAG evaluation, generator configuration, storage and authority boundaries.

## Decision and feedback

`POST /api/containment/preview`

```json
{"proposal":"block_card"}
```

Allowed proposals are `block_card`, `block_address`, and `hold_component`. This is a read-only, server-computed authority check. It returns the entity scope, affected payments and INR-normalized volume, the permitted transaction-level rewrite, post-resolution collateral evaluation, and distinct canonical `inputIdentity`/`enforcedIdentity` hashes. Historical labels never enter either identity.

`POST /api/agent/decide`

```json
{"failureMode":"healthy","requestEntityBlock":true,"proposal":"block_card","costs":{"chargebackFee":1500}}
```

Failure modes: `model`, `graph`, `evidence`, `identity`, and `drift`. Every response contains the deterministic action/costs, validated facts, observable tool trace, permission result, and chained audit receipt. An unsafe proposal also returns the containment transform; its requested and enforced identities are included in the audit record.

`POST /api/feedback`

```json
{"incidentId":"SG-INC-042","decision":"mark_legitimate","analyst":"name@merchant.test","note":"customer verified"}
```

Decisions are limited to `confirm_fraud`, `mark_legitimate`, and `request_more_evidence`.

`POST /api/feedback/reset` clears an incident-level analyst resolution and reverses only the linked-entity contribution made by that resolution. Feedback writes are idempotent, atomically persisted to `artifacts/runtime_state.json`, and appended to the audit chain. The runtime file is excluded from Git and submission packages.

## Evidence-grounded Investigation Copilot

`POST /api/copilot/brief`

```json
{"question":"Which policy applies, and what may the analyst safely do next?"}
```

The question is untrusted retrieval input, never an instruction. Word/bigram TF-IDF and BM25 retrieve three versioned policy clauses. When `GEMINI_API_KEY` is configured, Gemini's current Interactions API produces a schema-constrained brief with `store:false`; otherwise an extractive generator runs locally. Every claim must cite known evidence IDs, the recommended action must exactly match the deterministic policy result, and failed validation discards the model output. Brief generation itself has no money authority and receives a chained audit receipt.

## Razorpay webhook

`POST /api/razorpay/webhook` consumes the exact raw JSON body with headers `X-Razorpay-Signature` and `x-razorpay-event-id`. It requires `RAZORPAY_WEBHOOK_SECRET`; the API key secret is intentionally not accepted as a fallback. It verifies before parsing, rejects duplicates, canonicalizes payment fields, and refuses state regression. Razorpay payloads are not silently forced into IEEE-CIS features: the response marks a feature adapter as required.

`POST /api/razorpay/simulate` exercises the same ingestion state machine with an official-shaped local fixture for the demo failure lab.
