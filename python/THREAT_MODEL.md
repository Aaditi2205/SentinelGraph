# Threat model

| Threat | Consequence | Implemented control | Residual risk |
|---|---|---|---|
| Forged webhook | Attacker inserts fake payments | HMAC-SHA256 over exact raw body; reject before JSON/model | Webhook-secret theft remains critical |
| Replay/duplicate | Repeated graph and money action | `x-razorpay-event-id` idempotency set | Razorpay ingress state remains process-local and must move to a durable store |
| Out-of-order delivery | Captured payment regresses to authorized | Monotonic payment-state ranks; stale event audited but not applied | Unknown event types need explicit schema versioning |
| Future leakage | Inflated evaluation | Stable chronological split; score-before-insert; +24h label delay | Real chargeback delays vary by rail |
| Shared-entity overblocking | Legitimate customers harmed | Transaction-only authority; entity block rejected | Human reviewer can still make a bad decision |
| Proposal/approval mismatch | Reviewer approves one scope but a broader action executes | Canonical SHA-256 identities for both requested and enforced contracts; both identities are chained into the execution audit receipt | Production execution must bind the downstream payment operation to `enforcedIdentity` |
| Model/graph outage | Unsafe automated decision | Fail closed to human review; no cached score reuse | Review queue may saturate |
| Distribution drift | Badly calibrated action | Validation-to-current PSI on rolling/rate features and calibrated risk; monotonic totals excluded; pause at ≥0.25 | PSI does not detect every conditional shift |
| Hallucinated evidence | Misleading reviewer | Summary is assembled only from evidence IDs; deterministic fallback | Evidence source itself may be wrong |
| Audit tampering | Lost accountability | Each SHA-256 record includes previous hash; verifier endpoint; atomically persisted local state | Local files need signed external append-only storage |
| Feature-contract bypass | Gateway event receives a meaningless model score | Explicit Razorpay coverage contract blocks IEEE scoring until merchant-history, identity, address, and velocity groups exist | Production adapter schemas and versioning remain external |
| Feedback corruption/replay | Human labels compound entity risk or vanish on restart | Incident idempotency, reversible deltas, clear operation, atomic persistence, hash-chain receipt | Production requires analyst authentication, RBAC, and transactional storage |
| Prompt injection in analyst question | Question attempts to override policy or request entity-wide action | Question is retrieval data only; fixed system instruction, strict schema, action equality check, citation gate, deterministic fallback | Novel semantic attacks still require continuous adversarial evaluation |
| Hallucinated RAG claim | Unsupported accusation reaches the investigator | Every claim requires known evidence IDs plus lexical support; invalid output is discarded, not patched | Lexical entailment is conservative but not a full natural-language inference model |
| LLM data retention/exfiltration | Hashed incident context leaves merchant boundary | Gemini is optional, receives a minimal evidence packet, uses Interactions API `store:false`, low thinking, and a 20-second timeout; no key means no call | Production use still requires provider/legal review and merchant consent |
| Secret leakage | Account compromise | `.env` ignored; scripts require environment; test keys only | User workstation and CI secret hygiene remain external |

Production next steps: durable Redis/Postgres idempotency, KMS-managed secrets, signed immutable audit storage, schema registry, rate limits, authentication/RBAC, and alerting on queue saturation.
