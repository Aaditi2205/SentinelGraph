# Research and design rationale

## Why coordinated abuse, not another classifier

Razorpay already markets transaction-risk, dispute-response, RTO and recovery products. A generic classifier or responder would look like a smaller copy. The gap SentinelGraph targets is the operational layer between an individual risk score and a coordinated merchant incident: discover the relationship, prove which context changed the score, then contain it without globally blocking shared infrastructure.

## What past standouts suggest

- Razorpay OAuth moved from the Status 402 hackathon toward a real platform feature. The useful lesson is productizable infrastructure, not spectacle.
- Ezetap by Razorpay's HAWK connected social signals, OCR, translation, external verification and an investigator repository. It won attention because it converted weak signals into a complete verified workflow.
- DrishtiPay combined a memorable human problem with difficult operational constraints, security and usability.

SentinelGraph therefore centers one visual, judge-testable moment: a low/moderate-risk payment crosses an action boundary only after a past-only graph expansion, then the permission validator refuses the tempting but unsafe shared-device block.

## Open-source implementation benchmark

The project was checked against established and official repositories to avoid confusing a dense dashboard with a product.

| Reference | What it implements well | What SentinelGraph borrows |
|---|---|---|
| [Fraud Detection Handbook](https://github.com/Fraud-Detection-Handbook/fraud-detection-handbook) | Reproducible sequential-fraud experiments, operational metrics and model-selection methodology | Chronological evaluation, reviewer-capacity comparisons, honest baselines and reproducible artifacts |
| [Neo4j P2P fraud demo](https://github.com/neo4j-product-examples/demo-fraud-detection-with-p2p) | An investigation sequence from connected data through entity resolution, communities, centrality/similarity and prediction | Incident-first graph exploration and explicit entity relationships; not a decorative network chart |
| [Jube](https://github.com/jube-home/aml-fraud-transaction-monitoring) | Hybrid rules + ML, low-latency state, TTL/suppression, workflow case management, escalation and audit | Separation of scoring from deterministic rules, explicit analyst workflow and audit-visible failure handling |
| [Feast](https://github.com/feast-dev/feast) | Point-in-time-correct feature retrieval and training/serving consistency | Score-before-insert temporal graph features and delayed outcome visibility |
| [Evidently](https://github.com/evidentlyai/evidently) | Monitoring reports that become executable test suites and pass/fail thresholds | PSI is an automation gate that returns `PAUSE`, not merely a chart |
| [Alibi](https://github.com/SeldonIO/alibi) | Structured explanation objects and multiple local/counterfactual explanation methods | Relationship masking returns typed evidence and is labelled local sensitivity, not causal recourse |
| [Microsoft Agent Governance Toolkit](https://github.com/microsoft/agent-governance-toolkit) | Deterministic `allow`/`warn`/`transform`/`deny`/`escalate` semantics plus separate identities for input and transformed actions | The Decision Firewall returns `transform`, hashes the requested and enforced contracts separately, and fails closed |
| [AWS/NVIDIA graph-fraud blueprint](https://github.com/aws-samples/sample-financial-fraud-detection-with-nvidia) | GraphSAGE + XGBoost, managed training/registration, real-time serving and Shapley output | The useful pattern is the tabular/relational ablation and approval boundary; a GPU GNN stack is deliberately out of scope for a two-day solo build |

The common pattern is depth around one operational primitive. SentinelGraph's primitive is now the **proposal-to-safe-plan transform**. Kafka, Redis, Neo4j, LangChain and a GNN were not added as résumé keywords because the current build cannot honestly operate those systems at production scale in two days.

## Research translated into implementation

### Evolving graphs

Spade describes how real transaction graphs change continuously and why recomputing a static graph is unsuitable for production fraud detection. SentinelGraph uses an in-memory streaming feature builder for the buildathon: score the event, emit features, then mutate graph state.

Source: <https://arxiv.org/abs/2211.06977>

### Leakage safety

Graph evaluation is easy to invalidate by building a graph over the whole dataset before splitting it. SentinelGraph uses chronological 70/15/15 partitions, past-only insertion and a 24-hour delay before a prior fraud label becomes available. Unit tests assert both boundaries.

Related research: <https://arxiv.org/abs/2603.06632>

### Agent boundaries

Razorpay Agent Studio's published principles emphasize first-party data, merchant-defined permissions, platform validation, review-first mode, action logging and human approval for sensitive actions. SentinelGraph implements the same separation at demo scale: ML estimates uncertainty; deterministic code owns permissions and money-impacting action selection.

Source: <https://razorpay.com/blog/?p=26508>

### Evaluation, not benchmark theatre

Razorpay's evaluation writing argues for representative corpora, stored outcomes and confidence intervals rather than one model score. SentinelGraph reports a transaction-only ablation, five review-capacity points, fraud count/value, and a 500-draw bootstrap interval. It leaves the losing 1% operating point visible.

Source: <https://razorpay.com/blog/?p=27428>

## Dataset decision record

The first build used ULB/Worldline. It was rejected as the final dataset because its `V1`–`V28` PCA components cannot support honest card/device/address relationships or human-readable relational counterfactuals.

IEEE-CIS/Vesta was selected because the joined transaction and identity tables contain the linkable anonymized fields required by the use case. The official Kaggle competition files require accepting rules. The reproducibility fallback uses a public mirror, records that provenance, and warns that the mirror lists no license.

- Official: <https://www.kaggle.com/competitions/ieee-fraud-detection/data>
- Mirror used for this build: <https://www.kaggle.com/datasets/lnasiri007/ieeecis-fraud-detection>

## Claims intentionally not made

- No claim that the data represents current Indian UPI behavior.
- No claim that masked graph features establish causality.
- No claim that graph context wins every review capacity.
- No claim that a SHA-256 in-process hash chain is an immutable external ledger.
- No claim that TF-IDF over a small policy corpus is enterprise RAG.
- No claim that LangChain, an LLM or a graph neural network is necessary merely because the product is called AI.

## Primary sources

- Razorpay Status 402: <https://razorpay.com/blog/razorpay-hackathon-status-402-24-hours-of-innovation/>
- Razorpay HAWK: <https://razorpay.com/blog/fighting-payment-fraud-with-ai-powered-social-monitoring-hints-from-a-hackathon/>
- Razorpay Agent Studio principles: <https://razorpay.com/blog/?p=26508>
- Razorpay evaluation framework: <https://razorpay.com/blog/?p=27428>
- Spade evolving-graph paper: <https://arxiv.org/abs/2211.06977>
- IEEE-CIS data page: <https://www.kaggle.com/competitions/ieee-fraud-detection/data>
