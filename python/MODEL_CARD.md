# SentinelGraph model card

## Intended use

Prioritize merchant fraud-review queues and provide transaction-scoped recommendations. The model is defense-only and must not autonomously block a shared card, device, address, or customer identity.

## Data and evaluation

- IEEE-CIS/Vesta: 590,540 real labelled e-commerce transactions, 20,663 fraud labels.
- Chronological 70/15/15 train/calibration/test split; the final 88,581 rows are locked test data.
- Temporal features are computed before inserting the current event. Confirmed-fraud neighbour labels arrive after 24 hours.
- Transaction model AP: 0.4499. Transaction + graph AP: 0.4575. A paired 1,024-event moving-block bootstrap gives an AP-delta 95% interval of −0.0014 to +0.0171. The overall lift is therefore inconclusive under temporal dependence; capacity-specific results are reported separately.
- IEEE-CIS `TransactionAmt` is USD and remains USD as a model input. Policy/display exposure is normalized at the explicitly fixed scenario rate $1 = ₹83, locked 2026-08-23; it is not an observed historical FX rate.
- At a 1% review budget the expected-loss queue reviews 885 cases, catches 394/3,083 fraud cases, and contains ₹13.04m / ₹38.98m INR-normalized fraudulent transaction exposure. This is 33.45%; it misses ₹25.94m. The risk queue catches 759 cases but contains only ₹4.61m (11.82%), demonstrating the case-versus-exposure tradeoff.
- In the balanced merchant-policy scenario at 1% capacity, the graph value queue produces ₹11.99m versus ₹11.16m for the baseline (+₹820,762). At 0.25%, the baseline wins. These values use declared review, friction, and prevention controls and are not realized savings.
- Five locked-test frauds meeting a predeclared risk-shift rule (transaction-only ≤25%, graph ≥30%, component pressure ≥4, and a prior device-card relationship) are emitted as inspectable rescue cases. One legitimate graph top-1% false positive is emitted alongside them. No training rows are used for this gallery.
- Cold-start slices use the same global threshold. The missing-device bucket contains 70,383 test events and has 1.0% recall, making identity coverage an explicit deployment blocker rather than a hidden aggregate weakness.
- Recall @1% 95% moving-block interval: 23.11%–26.24%. This interval preserves local event clustering but measures sampling uncertainty, not domain-transfer uncertainty.
- At the same 1% capacity, the amount-only rule creates 859 false positives (1.00% FPR; ₹103,080 scenario review cost), while the graph-risk queue creates 126 (0.15% FPR; ₹15,120).

## Operating boundary

The final 15% window is compared with the immediately preceding 15% validation window on rolling/rate features and calibrated risk. Monotonic lifetime counters are deliberately excluded. `component_pressure` has PSI 0.504 and triggers `pause`; calibrated model-risk PSI is only 0.002. This distinction prevents graph aging from mechanically manufacturing drift while still stopping automation on a materially shifted relational feature. The default incident therefore reports its model risk but executes `REVIEW`, not `HOLD`, until an analyst explicitly supersedes the gate.

## Known limitations

- The dataset is anonymized Vesta e-commerce data, not Indian UPI traffic.
- Card/address/device semantics are not recoverable and are never invented.
- Local graph masking is sensitivity analysis, not a causal explanation.
- Cost parameters (₹1,500 fee, review and friction costs) are scenario inputs, not observed dataset economics.
- INR exposure is a fixed-rate normalization of source USD amounts, not observed merchant loss, prevented loss, recovered money, or historical FX conversion.
- The public mirror has no stated license; production use requires data-rights review.

## Human control

Only `allow`, `review`, or reversible transaction-level `hold` are permitted. An entity-wide proposal receives a deterministic `transform` verdict; the canonical requested and enforced contracts receive distinct SHA-256 identities which are included in the chained audit record. Resolved truth is used only to evaluate replay collateral damage and is excluded from both identities. Model outage, missing identity, drift, or unavailable graph context fail to review. Analyst decisions persist across restarts, supersede automation for that incident, and reversibly update only linked entities; repeated clicks are idempotent and every change is hash-chained. The local JSON store is demo durability, not a production database.

## Generative-AI boundary

Gemini 3.5 Flash-Lite is optional and used only to draft an analyst-facing brief. It is selected for latency on this bounded summarization job, not for financial reasoning. It receives a minimal re-hashed evidence packet and three retrieved policy clauses through the stateless Interactions API with `store:false`. It cannot call payment tools or change the deterministic action. A post-generation gate requires the schema, known evidence IDs, lexical claim support and exact equality with the bounded action. Any failure discards the model result and produces a deterministic cited fallback. The 12-question retrieval set achieves 100% Recall@3 and 0.944 MRR, but is a routing unit test—not evidence of production answer quality.
