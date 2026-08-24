# ADR 0005: Treat containment as a proposal-to-plan transform

Status: accepted

## Context

A graph detector can identify a suspicious relationship without establishing that every transaction sharing that relationship should be blocked. Entity-wide action is especially dangerous when cards, addresses or devices represent shared infrastructure. A browser-only blast-radius visualization would not prove that the authority boundary is enforced.

## Decision

Containment is implemented as a deterministic server-side intervention before action execution.

1. The merchant submits one of three bounded proposal types.
2. The server resolves the proposal against the incident graph and enumerates its affected transactions.
3. The authority engine returns `transform` for entity/component-wide scope and substitutes the transaction-level plan produced by deterministic policy.
4. Canonical JSON for the requested and enforced contracts receives separate SHA-256 identities.
5. A read-only preview does not mutate state. Execution through the decision endpoint stores both identities in the hash-chained audit record.
6. Historical truth may evaluate collateral damage after resolution but is excluded from both policy identities.

## Consequences

- The UI displays server output rather than manufacturing the product claim.
- Reviewers can distinguish what was requested from what is permitted to execute.
- The same contract is independently testable without a browser.
- The demo hash chain is tamper-evident, not an externally signed immutable ledger.
- Production execution must bind downstream payment actions and approvals to the enforced identity to close the full time-of-check/time-of-use boundary.
