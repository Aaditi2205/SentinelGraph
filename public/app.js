const state = { metrics: null, incident: null, originalIncident: null, incidentMode: "locked", containmentPreview: null, visibleEvents: 0, timer: null, failureMode: "healthy", workbenchDirty: false, workbenchRiskSource: "sentinelgraph_locked_graph_model" };

const el = (id) => document.getElementById(id);

const CONSOLE_VIEWS = {
  overview: { title: "Overview", eyebrow: "RISK OPERATIONS" },
  incident: { title: "Decision firewall", eyebrow: "ACTIVE INVESTIGATION · SG-INC-042" },
  copilot: { title: "Evidence-grounded copilot", eyebrow: "GEMINI · POLICY RAG" },
  evaluation: { title: "Policy simulator", eyebrow: "SHADOW MODE · LOCKED OUTCOMES" },
  controls: { title: "Controls & audit", eyebrow: "FAILURE RECOVERY · RAZORPAY INGRESS" },
};

function setConsoleView(requested, updateHash = false) {
  const aliases = { top: "overview", proof: "evaluation", trust: "controls" };
  const route = CONSOLE_VIEWS[requested] ? requested : (aliases[requested] || "incident");
  document.querySelectorAll("[data-console-view]").forEach((view) => view.classList.toggle("is-active", view.dataset.consoleView === route));
  document.querySelectorAll("[data-console-route]").forEach((link) => {
    const active = link.dataset.consoleRoute === route;
    link.classList.toggle("active", active);
    if (active) link.setAttribute("aria-current", "page"); else link.removeAttribute("aria-current");
  });
  el("workspaceTitle").textContent = CONSOLE_VIEWS[route].title;
  el("workspaceEyebrow").textContent = CONSOLE_VIEWS[route].eyebrow;
  document.title = `${CONSOLE_VIEWS[route].title} · SentinelGraph`;
  if (updateHash && location.hash !== `#${route}`) history.replaceState(null, "", `#${route}`);
  window.scrollTo({ top: 0, behavior: "instant" });
}
const pct = (value, digits = 1) => `${(Number(value || 0) * 100).toFixed(digits)}%`;
const num = (value) => new Intl.NumberFormat("en-IN").format(Number(value || 0));
const money = (value) => new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 0 }).format(Number(value || 0));
const transactions = (count) => `${count} transaction${count === 1 ? "" : "s"}`;
const escapeHtml = (value) => String(value ?? "").replace(/[&<>'"]/g, (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", "'": "&#39;", '"': "&quot;" }[char]));

function loadMetrics(metrics) {
  state.metrics = metrics;
  const baseline = metrics.ablations[0];
  const graph = metrics.ablations[1];
  const riskQueue = metrics.operational_queues.risk_ranked;
  const valueQueue = metrics.operational_queues.expected_loss_ranked;
  const lift = graph.average_precision - baseline.average_precision;
  el("rowCount").textContent = num(metrics.dataset.rows);
  el("graphAp").textContent = graph.average_precision.toFixed(3);
  el("valueCapture").textContent = pct(valueQueue.fraud_value_capture, 0);
  el("capacityContext").innerHTML = `<strong>Why not the old 67.7%?</strong> IEEE-CIS fraud prevalence is ${pct(metrics.dataset.test_fraud_prevalence, 2)}: a 1% queue has 885 slots for ${num(metrics.dataset.test_fraud_rows)} frauds, so perfect ranking can cover at most ${pct(metrics.dataset.max_case_recall_at_1pct, 1)} of cases. Within this dataset, expected-loss ranking changes captured INR-normalized exposure from ${pct(riskQueue.fraud_value_capture, 1)} to ${pct(valueQueue.fraud_value_capture, 1)} (${money(riskQueue.fraud_value_captured)} → ${money(valueQueue.fraud_value_captured)}) without adding reviews. The old ULB result used a far rarer-fraud dataset and is not directly comparable.`;
  el("currencyContext").innerHTML = `<strong>Currency boundary:</strong> IEEE-CIS <code>TransactionAmt</code> is USD. Raw USD stays in the model; financial policy and display convert every amount at the fixed scenario rate <b>$1 = ₹${metrics.financial_units.usd_to_inr_rate.toFixed(2)}</b>, locked ${metrics.financial_units.assumption_locked_on}. This is a normalization assumption—not an observed historical FX rate.`;
  el("baseAp").textContent = baseline.average_precision.toFixed(3);
  el("fullAp").textContent = graph.average_precision.toFixed(3);
  el("baseRecall").textContent = pct(baseline.recall);
  el("fullRecall").textContent = pct(graph.recall);
  el("baseValue").textContent = pct(baseline.fraud_value_capture);
  el("fullValue").textContent = pct(graph.fraud_value_capture);
  el("baseBar").style.width = `${baseline.average_precision * 100}%`;
  el("fullBar").style.width = `${graph.average_precision * 100}%`;
  el("apLift").textContent = `${lift >= 0 ? "+" : ""}${lift.toFixed(3)} AP`;
  el("confidenceText").textContent = `95% paired moving-block interval: ${metrics.ap_delta_ci_95[0] >= 0 ? "+" : ""}${metrics.ap_delta_ci_95[0].toFixed(3)} to ${metrics.ap_delta_ci_95[1] >= 0 ? "+" : ""}${metrics.ap_delta_ci_95[1].toFixed(3)}`;
  el("ablationVerdict").textContent = metrics.ap_delta_ci_95[0] > 0 ? "Graph contribution is positive" : "Graph contribution is inconclusive";
  el("capacityRows").innerHTML = metrics.review_frontier.map((point) => {
    const deltaCount = point.graph.fraud_count_caught - point.transaction.fraud_count_caught;
    const deltaValue = point.graph.fraud_value_capture - point.transaction.fraud_value_capture;
    const winner = deltaCount > 0 || deltaValue > 0 ? "graph" : deltaCount < 0 || deltaValue < 0 ? "baseline" : "tie";
    return `<div class="capacity-row ${winner}"><strong>${point.budget_pct}%</strong><span>${point.graph.fraud_count_caught} frauds</span><span>${pct(point.graph.precision)} precision</span><span>${pct(point.graph.fraud_value_capture)} INR exposure</span><b>${deltaCount >= 0 ? "+" : ""}${deltaCount} vs baseline</b></div>`;
  }).join("");
  el("systemRows").innerHTML = metrics.system_comparison.map((item) => `<div class="system-row"><div><strong>${item.name}</strong><small>${item.kind}</small></div><span>${item.fraud_count_caught} caught</span><span>${pct(item.precision)} precision</span><span>${num(item.false_positive_count)} FP<small>${pct(item.false_positive_rate, 2)} FPR</small></span><span>${money(item.false_positive_review_cost)}<small>FP cost</small></span><b>${pct(item.fraud_value_capture)} exposure</b></div>`).join("");
  el("driftStatus").textContent = metrics.drift.status.toUpperCase();
  el("driftPsi").textContent = `max PSI ${metrics.drift.max_psi.toFixed(3)}`;
  const driftNarrative = metrics.drift.status === "pause"
    ? "The current window breaches the boundary, so a production controller pauses auto-action; the incident replay remains an evaluation demonstration."
    : metrics.drift.status === "watch"
      ? "The current window requires closer monitoring and tighter action limits."
      : "The current window remains inside the approved operating boundary.";
  el("driftBoundary").textContent = `${metrics.drift.boundary}. ${metrics.drift.reference} → ${metrics.drift.current}. ${driftNarrative}`;
  el("driftRows").innerHTML = metrics.drift.features.slice(0, 4).map((item) => `<div><span>${item.feature}</span><b>${item.psi.toFixed(3)}</b><i class="${item.status}">${item.status}</i></div>`).join("");
  el("recallCi").textContent = `${pct(metrics.graph_queue_ci_95.recall[0])}–${pct(metrics.graph_queue_ci_95.recall[1])}`;
  el("fixedFpr").textContent = `${pct(metrics.fixed_fpr.graph[0].recall)} recall · ${pct(metrics.fixed_fpr.graph[0].test_fpr, 2)} actual`;
  el("latencyP95").textContent = `${metrics.latency.inference.p95_batch_ms.toFixed(1)} ms / 128`;
  el("valueMissed").textContent = money(metrics.fraud_value_missed_at_1pct.expected_loss_ranked);
  el("featureThroughput").textContent = `${num(Math.round(metrics.latency.graph_rows_per_second))} rows/s`;
  el("merchantPolicy").innerHTML = metrics.merchant_policy_lab.policies.map((policy) => `<option value="${escapeHtml(policy.id)}" ${policy.id === "balanced" ? "selected" : ""}>${escapeHtml(policy.name)}</option>`).join("");
  renderGraphRescue(0);
  renderColdStart();
  renderPolicyLab(Number(el("capacitySlider").value));
}

function renderPolicyLab(index) {
  if (!state.metrics) return;
  const lab = state.metrics.merchant_policy_lab;
  const policy = lab.policies.find((item) => item.id === el("merchantPolicy").value) || lab.policies[1];
  const point = policy.points[Math.max(0, Math.min(index, policy.points.length - 1))];
  const useGraph = point.recommended_model === "graph";
  const winner = useGraph ? point.graph : point.baseline;
  const loser = useGraph ? point.baseline : point.graph;
  const delta = Math.abs(point.value_delta);
  el("selectedCapacity").textContent = `${point.capacity_pct.toFixed(point.capacity_pct < 1 ? 2 : point.capacity_pct === 1 ? 2 : 1)}%`;
  el("selectedModel").textContent = useGraph ? "TEMPORAL GRAPH QUEUE" : "TRANSACTION-ONLY QUEUE";
  el("selectedReviews").textContent = `${num(winner.reviewed)} / ${num(winner.capacity_slots)}`;
  el("selectedCaught").textContent = `${num(winner.fraud_count_caught)} / ${num(winner.fraud_count_total)}`;
  el("selectedExposure").textContent = money(winner.fraud_exposure_prevented);
  el("selectedFalsePositives").textContent = `${num(winner.false_positive_count)} · ${money(winner.legitimate_friction_cost)}`;
  const countDelta = winner.fraud_count_caught - loser.fraud_count_caught;
  el("selectedReason").textContent = `${useGraph ? "Graph" : "Baseline"} creates ${money(delta)} more scenario merchant value at this capacity (${countDelta >= 0 ? "+" : ""}${countDelta} fraud cases). ${lab.status}.`;
  el("policyAssumptions").textContent = `${policy.description} Controls: ${money(policy.review_cost)}/review · ${money(policy.legitimate_friction_cost)}/legitimate interruption · ${pct(policy.fraud_prevention_rate, 0)} prevention assumption.`;
}

function renderGraphRescue(index) {
  const packet = state.metrics?.graph_rescue_evidence;
  if (!packet?.examples?.length) return;
  const selectedIndex = Math.max(0, Math.min(index, packet.examples.length - 1));
  const selected = packet.examples[selectedIndex];
  el("rescueDefinition").textContent = packet.definition;
  el("rescueTabs").innerHTML = packet.examples.map((item, itemIndex) => `<button type="button" role="tab" aria-selected="${itemIndex === selectedIndex}" class="${itemIndex === selectedIndex ? "active" : ""}" data-rescue-index="${itemIndex}"><span>${escapeHtml(item.id)}</span><strong>${escapeHtml(item.transaction_id)}</strong><small>+${item.risk_lift_pp.toFixed(1)} pp</small></button>`).join("");
  el("rescueBaseRisk").textContent = pct(selected.transaction_risk);
  el("rescueGraphRisk").textContent = pct(selected.graph_risk);
  el("rescueBaseAction").textContent = selected.transaction_action.toUpperCase();
  el("rescueGraphAction").textContent = selected.graph_action.toUpperCase();
  el("rescueCaseId").textContent = `${selected.transaction_id} · ${money(selected.scenario_amount_inr)} scenario exposure`;
  el("rescueLift").textContent = `+${selected.risk_lift_pp.toFixed(1)} percentage points`;
  el("rescueQueueResult").textContent = `${selected.queue_result}. All relationship facts existed before dataset second ${num(selected.dataset_elapsed_second)}.`;
  el("rescueEvidence").innerHTML = selected.evidence.map((fact) => `<span>${escapeHtml(fact)}</span>`).join("");
  const noise = packet.graph_noise_case;
  if (noise) {
    el("graphNoiseTitle").textContent = `${noise.transaction_id} · legitimate · ${pct(noise.transaction_risk)} → ${pct(noise.graph_risk)}`;
    el("graphNoiseText").textContent = `${noise.verdict} Scenario amount ${money(noise.scenario_amount_inr)}.`;
  }
}

function renderColdStart() {
  const evaluation = state.metrics?.cold_start_evaluation;
  if (!evaluation) return;
  el("coldStartMethod").textContent = `${evaluation.definition} ${evaluation.threshold_scope}.`;
  el("coldStartRows").innerHTML = evaluation.buckets.map((bucket) => `<div><span><strong>${escapeHtml(bucket.label)}</strong><small>${num(bucket.rows)} events · ${num(bucket.frauds)} frauds</small></span><b>${pct(bucket.recall)}<small>recall</small></b><b>${pct(bucket.precision)}<small>precision</small></b><b>${pct(bucket.exposure_capture)}<small>exposure</small></b></div>`).join("");
}

function entitiesForEvent(incident, transactionId) {
  const values = { card: "", device: "", address: "" };
  incident.edges.filter((edge) => edge.source === transactionId).forEach((edge) => { values[edge.type] = edge.target; });
  return values;
}

function workbenchRow(event, incident, isTarget = false) {
  const entities = entitiesForEvent(incident, event.transactionId);
  const truth = event.truth || "";
  return `<tr data-workbench-row data-truth="${escapeHtml(truth)}" data-transaction-risk="${event.transactionRisk}" data-offset="${event.offsetMinutes}">
    <td><input type="radio" name="workbenchTarget" ${isTarget ? "checked" : ""} aria-label="Use ${escapeHtml(event.transactionId)} as target" /></td>
    <td><input data-field="transactionId" value="${escapeHtml(event.transactionId)}" aria-label="Payment ID" /></td>
    <td><input data-field="amount" type="number" min="0.01" step="0.01" value="${Number(event.amount).toFixed(2)}" aria-label="Amount in INR" /></td>
    <td><input data-field="risk" type="number" min="0" max="100" step="0.1" value="${(Number(event.ringRisk) * 100).toFixed(1)}" aria-label="Risk percent" /></td>
    <td><input data-field="card" value="${escapeHtml(entities.card)}" aria-label="Card token" /></td>
    <td><input data-field="device" value="${escapeHtml(entities.device)}" aria-label="Device token" /></td>
    <td><input data-field="address" value="${escapeHtml(entities.address)}" aria-label="Address token" /></td>
    <td><button class="workbench-remove" type="button" aria-label="Remove ${escapeHtml(event.transactionId)}">×</button></td>
  </tr>`;
}

function renderWorkbenchRows(incident) {
  el("workbenchRows").innerHTML = incident.events.map((event) => workbenchRow(event, incident, event.transactionId === incident.targetTransaction)).join("");
}

function markWorkbenchDirty() {
  state.workbenchDirty = true;
  state.workbenchRiskSource = "merchant_operator_input";
  el("workbenchSource").textContent = "UNCOMPILED MERCHANT INPUT";
  el("workbenchStatus").textContent = "Inputs changed. Compile to rebuild the graph and action scope on the server.";
  el("workbenchPrivacy").textContent = "NOT SENT YET";
}

function collectWorkbenchPayload(commit = false) {
  const rows = [...document.querySelectorAll("[data-workbench-row]")];
  const events = rows.map((row, index) => {
    const value = (field) => row.querySelector(`[data-field="${field}"]`).value.trim();
    const truth = row.dataset.truth;
    return {
      transactionId: value("transactionId"), amount: Number(value("amount")),
      contextualRisk: Number(value("risk")) / 100,
      transactionRisk: Number(row.dataset.transactionRisk || Number(value("risk")) / 100),
      card: value("card"), device: value("device"), address: value("address"),
      offsetMinutes: Number(row.dataset.offset || index * 10),
      riskSource: state.workbenchRiskSource,
      ...(truth ? { truth } : {}),
    };
  });
  const targetRow = rows.find((row) => row.querySelector('input[name="workbenchTarget"]').checked);
  return {
    incidentId: `SG-MERCHANT-${Date.now().toString(36).toUpperCase()}`,
    title: "Merchant-supplied containment simulation",
    targetTransaction: targetRow?.querySelector('[data-field="transactionId"]').value.trim(),
    proposal: el("blastProposal").value,
    events, commit,
  };
}

function renderImpactLedger(items) {
  el("impactLedger").innerHTML = items.map((item) => `<div class="impact-ledger-row ${item.affectedByProposal ? "affected" : "untouched"}">
    <span>${escapeHtml(item.transactionId)}<small>${item.affectedByProposal ? "IN PROPOSED SCOPE" : "OUTSIDE PROPOSED SCOPE"}</small></span>
    <b>${money(item.amount)}</b><b>${pct(item.risk)} risk</b><strong>${escapeHtml(item.policyAction.toUpperCase())}</strong>
  </div>`).join("");
}

async function compileWorkbench(commit = false) {
  const button = el("compileWorkbenchButton");
  button.disabled = true;
  button.textContent = commit ? "Hashing action receipt…" : "Validating → graphing → compiling…";
  el("workbenchStatus").textContent = "Server is validating the batch and rebuilding the entity graph.";
  try {
    const response = await fetch("/api/workbench/compile", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(collectWorkbenchPayload(commit)),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Workbench compilation failed");
    state.incidentMode = "workbench";
    state.workbenchDirty = false;
    loadIncident(result.incident, { skipPreview: true, preserveRows: true });
    renderContainmentContract(result.containment, commit);
    renderImpactLedger(result.impactLedger);
    el("workbenchSource").textContent = `LIVE MERCHANT BATCH · ${result.incident.events.length} PAYMENTS`;
    el("workbenchStatus").textContent = `${result.containment.requested.paymentsTouched} payments are inside the proposed scope; ${result.containment.operationalImpact.paymentsSparedFromBroadAction} are spared by the transaction plan.`;
    el("workbenchPrivacy").textContent = result.inputContract.persisted ? "PERSISTED" : "BATCH NOT PERSISTED";
    if (commit && result.audit) {
      el("firewallStatus").textContent = "ENTITY ACTION BLOCKED · SAFE PLAN AUDITED";
      el("firewallReceiptTitle").textContent = `Receipt chained · ${result.audit.recordHash.slice(0, 16)}…`;
      el("auditStatus").textContent = `Workbench transform recorded · ${result.audit.chainValid ? "chain verified" : "chain failed"}`;
      el("auditHash").textContent = result.audit.recordHash;
      document.querySelector(".decision-firewall").classList.add("simulated");
    }
    return result;
  } catch (error) {
    el("workbenchStatus").textContent = error.message;
    el("workbenchPrivacy").textContent = "REJECTED · NOTHING PERSISTED";
    throw error;
  } finally {
    button.disabled = false;
    button.textContent = "Compile new incident →";
  }
}

function restorePublicSample() {
  state.incidentMode = "locked";
  state.workbenchDirty = false;
  state.workbenchRiskSource = "sentinelgraph_locked_graph_model";
  renderWorkbenchRows(state.originalIncident);
  el("impactLedger").innerHTML = "";
  el("workbenchSource").textContent = `PUBLIC REAL-DATA STARTER · ${state.originalIncident.events.length} PAYMENTS`;
  el("workbenchStatus").textContent = "Edit a relationship, add a payment, or move the target—then compile.";
  el("workbenchPrivacy").textContent = "NOT PERSISTED";
  loadIncident(state.originalIncident);
}

function addWorkbenchPayment() {
  const body = el("workbenchRows");
  const rows = [...body.querySelectorAll("[data-workbench-row]")];
  const target = rows.find((row) => row.querySelector('input[name="workbenchTarget"]').checked) || rows[0];
  const sharedCard = target?.querySelector('[data-field="card"]')?.value || "shared-card";
  const index = rows.length + 1;
  const event = { transactionId: `PAY-CUSTOM-${index}`, amount: 2500, ringRisk: 0.12, transactionRisk: 0.12, offsetMinutes: index * 10, evidenceIds: [] };
  const incident = { edges: [{ source: event.transactionId, target: sharedCard, type: "card" }, { source: event.transactionId, target: `device-custom-${index}`, type: "device" }, { source: event.transactionId, target: `address-custom-${index}`, type: "address" }] };
  body.insertAdjacentHTML("beforeend", workbenchRow(event, incident, false));
  markWorkbenchDirty();
}

function loadIncident(incident, options = {}) {
  state.incident = incident;
  el("incidentId").textContent = incident.incidentId;
  el("incidentTitle").textContent = incident.title;
  el("eventSequence").textContent = `0 / ${incident.events.length} events`;
  const target = incident.events.find((event) => event.transactionId === incident.targetTransaction);
  el("heroRisk").textContent = pct(target.ringRisk, 0);
  el("heroDelta").textContent = `+${((target.ringRisk - target.transactionRisk) * 100).toFixed(1)} pp`;
  el("holdPlan").textContent = transactions(incident.blastRadius.hold.length);
  el("holdPlanReason").textContent = incident.blastRadius.hold.length ? "Highest-confidence target only; reversible pending review." : "No automatic hold: current expected-cost policy prefers human review.";
  el("reviewPlan").textContent = transactions(incident.blastRadius.review.length);
  el("allowPlan").textContent = transactions(incident.blastRadius.allow.length);
  const confidence = incident.operationalConfidence;
  if (confidence) {
    el("confidenceLevel").textContent = confidence.level.toUpperCase();
    el("confidenceLevel").className = confidence.level;
    el("confidenceReason").textContent = confidence.reasons.join(" · ");
    el("calibrationSupport").textContent = `${num(confidence.calibrationSupport)} nearby validation events · observed ${pct(confidence.nearbyValidationFraudRate)}`;
  } else {
    el("confidenceLevel").textContent = "UPSTREAM";
    el("confidenceLevel").className = "medium";
    el("confidenceReason").textContent = "Merchant-supplied risk has no local calibration-support contract.";
    el("calibrationSupport").textContent = "Human review required for score provenance";
  }
  const guard = incident.temporalGuard;
  if (guard) {
    el("temporalCutoff").textContent = `EVENT SECOND ${num(guard.targetDatasetSecond)} · FUTURE LABELS ${guard.futureLabelsAccessible ? "VISIBLE" : "LOCKED"}`;
    el("temporalRule").textContent = guard.guard;
    el("labelDelay").textContent = `Confirmed labels delayed ${guard.labelAvailabilityDelayHours}h`;
  } else {
    el("temporalCutoff").textContent = "MERCHANT BATCH CONTRACT";
    el("temporalRule").textContent = "Workbench risk is supplied upstream; SentinelGraph does not claim to reconstruct unavailable history.";
    el("labelDelay").textContent = "No future labels used";
  }
  if (!options.preserveRows) renderWorkbenchRows(incident);
  if (!options.skipPreview) updateBlastPreview();
  resetReplay();
}

function renderContainmentContract(contract, executed = false) {
  state.containmentPreview = contract;
  el("firewallRequestedTitle").textContent = contract.requested.title;
  el("firewallAffected").textContent = contract.requested.paymentsTouched;
  el("firewallVolume").textContent = money(contract.requested.volumeFrozenInr);
  el("firewallLegitimate").textContent = contract.resolvedReplayEvaluation.labelsAvailable ? contract.resolvedReplayEvaluation.knownLegitimateTouched : "N/A";
  el("firewallReviewCount").textContent = contract.safePlan.reviewCount;
  el("firewallHoldCount").textContent = contract.safePlan.holdCount;
  el("firewallAllowCount").textContent = contract.safePlan.untouchedCount;
  el("firewallSpared").textContent = money(contract.operationalImpact?.volumeSparedFromBroadActionInr ?? contract.resolvedReplayEvaluation.knownLegitimateVolumeSparedInr);
  el("firewallMethod").textContent = contract.operationalImpact?.method || "Resolved labels are evaluation-only and excluded from the live policy identity.";
  el("firewallInputIdentity").textContent = contract.inputIdentity.slice(0, 22) + "…";
  el("firewallInputIdentity").title = contract.inputIdentity;
  el("firewallEnforcedIdentity").textContent = contract.enforcedIdentity.slice(0, 22) + "…";
  el("firewallEnforcedIdentity").title = contract.enforcedIdentity;
  if (!executed) {
    el("firewallStatus").textContent = "SERVER PREVIEW READY";
    el("firewallReceiptTitle").textContent = "Read-only transform compiled";
    el("firewallReason").textContent = contract.reason;
    document.querySelector(".decision-firewall").classList.remove("simulated");
  }
}

async function updateBlastPreview() {
  if (!state.incident) return;
  if (state.incidentMode === "workbench") {
    try { await compileWorkbench(false); } catch { /* status is rendered by compileWorkbench */ }
    return;
  }
  const proposal = el("blastProposal").value;
  el("firewallStatus").textContent = "COMPILING ON SERVER";
  try {
    const response = await fetch("/api/containment/preview", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ proposal }),
    });
    const contract = await response.json();
    if (!response.ok) throw new Error(contract.error || "Containment preview failed");
    if (el("blastProposal").value === proposal) renderContainmentContract(contract);
  } catch (error) {
    el("firewallStatus").textContent = "PREVIEW FAILED CLOSED";
    el("firewallReceiptTitle").textContent = "No plan available";
    el("firewallReason").textContent = error.message;
  }
}

async function runDecisionFirewall() {
  const button = el("simulateBlastButton");
  button.disabled = true;
  button.textContent = "Validating authority →";
  el("firewallStatus").textContent = "CHECKING POLICY";
  try {
    if (state.incidentMode === "workbench") {
      await compileWorkbench(true);
      return;
    }
    const response = await fetch("/api/agent/decide", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ failureMode: "healthy", requestEntityBlock: true, proposal: el("blastProposal").value }),
    });
    const decision = await response.json();
    if (!response.ok) throw new Error(decision.error || "Containment simulation failed");
    renderDecision(decision);
    renderContainmentContract(decision.containment, true);
    el("firewallStatus").textContent = "BLOCKED → SAFE PLAN BUILT";
    el("firewallReceiptTitle").textContent = `${decision.guardrail.status.toUpperCase()} · ${decision.action.toUpperCase()} staged`;
    el("firewallReason").textContent = decision.guardrail.reason;
    document.querySelector(".decision-firewall").classList.add("simulated");
  } catch (error) {
    el("firewallStatus").textContent = "SIMULATION FAILED";
    el("firewallReason").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Run containment diff →";
  }
}

function hashPosition(text, radius, centerX, centerY, offset = 0) {
  let hash = 0;
  for (const char of text) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  const angle = ((Math.abs(hash) % 360) + offset) * Math.PI / 180;
  return { x: centerX + Math.cos(angle) * radius, y: centerY + Math.sin(angle) * radius };
}

function graphLayout(nodes) {
  const positions = {};
  const txNodes = nodes.filter((node) => node.kind === "transaction");
  const entityNodes = nodes.filter((node) => node.kind !== "transaction");
  txNodes.forEach((node, index) => {
    const angle = (index / Math.max(txNodes.length, 1)) * Math.PI * 2 - Math.PI / 2;
    positions[node.id] = { x: 380 + Math.cos(angle) * 128, y: 235 + Math.sin(angle) * 128 };
  });
  entityNodes.forEach((node, index) => {
    positions[node.id] = hashPosition(`${node.id}-${index}`, 210 + (index % 2) * 28, 380, 235, index * 13);
  });
  return positions;
}

function renderGraph() {
  const visible = state.incident.events.slice(0, state.visibleEvents);
  const txIds = new Set(visible.map((event) => event.transactionId));
  const edges = state.incident.edges.filter((edge) => txIds.has(edge.source));
  const nodeIds = new Set([...txIds, ...edges.flatMap((edge) => [edge.source, edge.target])]);
  const nodes = state.incident.nodes.filter((node) => nodeIds.has(node.id));
  const positions = graphLayout(nodes);
  const svg = el("graphSvg");
  svg.innerHTML = "";

  edges.forEach((edge) => {
    const source = positions[edge.source];
    const target = positions[edge.target];
    if (!source || !target) return;
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", source.x); line.setAttribute("y1", source.y);
    line.setAttribute("x2", target.x); line.setAttribute("y2", target.y);
    line.setAttribute("class", `edge edge-${edge.type}`);
    svg.appendChild(line);
  });

  nodes.forEach((node) => {
    const position = positions[node.id];
    const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
    const classes = ["graph-node", node.kind];
    if (node.isTarget) classes.push("target-node");
    if (node.truth === "fraud") classes.push("truth-fraud");
    group.setAttribute("class", classes.join(" "));
    group.setAttribute("transform", `translate(${position.x},${position.y})`);
    const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
    circle.setAttribute("r", node.kind === "transaction" ? (node.isTarget ? 24 : 18) : 10);
    const label = document.createElementNS("http://www.w3.org/2000/svg", "text");
    label.setAttribute("y", node.kind === "transaction" ? 4 : 26);
    label.textContent = node.kind === "transaction" ? node.label : node.id.split("_")[0];
    group.append(circle, label);
    svg.appendChild(group);
  });
  el("graphEmpty").hidden = nodes.length > 0;
}

function eventBrief(event, index) {
  const currentEdges = state.incident.edges.filter((edge) => edge.source === event.transactionId);
  const earlierIds = new Set(state.incident.events.slice(0, index).map((item) => item.transactionId));
  const repeated = currentEdges.map((edge) => {
    const priorCount = state.incident.edges.filter((candidate) => candidate.target === edge.target && earlierIds.has(candidate.source)).length;
    return { type: edge.type, priorCount };
  }).filter((item) => item.priorCount > 0);
  const deltaPp = (event.ringRisk - event.transactionRisk) * 100;
  const movement = Math.abs(deltaPp) < 0.05 ? "did not materially change" : deltaPp > 0 ? "increased" : "reduced";
  const relationshipText = repeated.length
    ? repeated.map((item) => `${item.type} seen in ${item.priorCount} prior payment${item.priorCount === 1 ? "" : "s"}`).join("; ")
    : "all linked entities are new in the visible stream";
  return {
    summary: `${event.transactionId} arrived at T+${event.offsetMinutes} min with ${currentEdges.length} visible relationship${currentEdges.length === 1 ? "" : "s"}; ${relationshipText}. Graph context ${movement} calibrated risk from ${pct(event.transactionRisk)} to ${pct(event.ringRisk)} (${deltaPp >= 0 ? "+" : ""}${deltaPp.toFixed(1)} pp).`,
    chips: [
      `EVENT-${String(index + 1).padStart(2, "0")}`,
      ...event.evidenceIds,
      ...(repeated.length ? repeated.map((item) => `PRIOR-${item.type.toUpperCase()}-${item.priorCount}`) : ["NOVEL-ENTITIES"]),
    ],
  };
}

function showEvent(index) {
  const event = state.incident.events[index];
  state.visibleEvents = index + 1;
  renderGraph();
  el("eventSequence").textContent = `${state.visibleEvents} / ${state.incident.events.length} events`;
  el("eventTime").textContent = `T+${event.offsetMinutes} min`;
  el("eventProgress").style.width = `${state.visibleEvents / state.incident.events.length * 100}%`;
  const toast = el("eventToast");
  toast.innerHTML = `<span>${event.transactionId}</span><strong>${money(event.amount)}</strong><small>${pct(event.ringRisk)} contextual risk</small>`;
  toast.classList.remove("show");
  requestAnimationFrame(() => toast.classList.add("show"));
  const liveBrief = eventBrief(event, index);
  const isTarget = event.transactionId === state.incident.targetTransaction;
  el("transactionRisk").textContent = pct(event.transactionRisk);
  el("ringRisk").textContent = pct(event.ringRisk);
  el("briefLabel").textContent = isTarget ? "TARGET VERIFICATION BRIEF" : "LIVE EVENT BRIEF";
  el("currentEventLabel").textContent = `${event.transactionId} · ${index + 1}/${state.incident.events.length}`;
  el("agentSummary").textContent = liveBrief.summary;
  el("factChips").innerHTML = liveBrief.chips.map((chip) => `<span>${escapeHtml(chip)}</span>`).join("");
  el("agentState").innerHTML = `<i></i>${isTarget ? "Verifying target" : `Observing ${index + 1}/${state.incident.events.length}`}`;
  el("agentAction").textContent = isTarget ? "CHECK POLICY" : "ACCUMULATE";
  el("agentReason").textContent = isTarget
    ? "Target context is complete; requesting the bounded expected-cost action."
    : "No action is committed during replay. The event is scored before it enters future graph state.";
  el("allowCost").textContent = "—"; el("reviewCost").textContent = "—"; el("holdCost").textContent = "—";
  el("counterfactualRows").innerHTML = isTarget
    ? "<p>Compiling relationship ablations from the completed target context…</p>"
    : "<p>Ablation waits for the target; current event context is still accumulating.</p>";
}

async function replay() {
  resetReplay();
  el("replayButton").disabled = true;
  let index = 0;
  state.timer = setInterval(async () => {
    showEvent(index);
    index += 1;
    if (index >= state.incident.events.length) {
      clearInterval(state.timer);
      state.timer = null;
      el("replayButton").disabled = false;
      if (state.incidentMode === "workbench") renderWorkbenchDecision();
      else await requestDecision(false);
    }
  }, 520);
}

function renderWorkbenchDecision() {
  const decision = state.incident.proposedAction;
  const target = state.incident.events.find((event) => event.transactionId === state.incident.targetTransaction);
  el("agentState").innerHTML = "<i></i>Compiled";
  el("transactionRisk").textContent = pct(target.transactionRisk);
  el("ringRisk").textContent = pct(target.ringRisk);
  el("briefLabel").textContent = "MERCHANT POLICY BRIEF";
  el("currentEventLabel").textContent = `${target.transactionId} · UPSTREAM RISK`;
  el("agentAction").textContent = decision.action.toUpperCase();
  el("agentReason").textContent = decision.reason;
  el("allowCost").textContent = money(decision.costs.allow);
  el("reviewCost").textContent = money(decision.costs.review);
  el("holdCost").textContent = money(decision.costs.hold);
  el("agentSummary").textContent = state.incident.agentSummary;
  el("factChips").innerHTML = state.incident.facts.map((fact) => `<span title="${escapeHtml(fact.text)}">${escapeHtml(fact.id)}</span>`).join("");
  el("counterfactualRows").innerHTML = "<p>Not run—this workbench accepts an explicit upstream risk contract and does not claim to rescore missing merchant model features.</p>";
  el("guardrailBox").classList.remove("blocked");
  el("guardrailBox").querySelector(":scope > span").textContent = "✓";
  el("guardrailText").textContent = "The upstream score may choose a transaction action; it may not directly block a shared entity.";
  el("toolTrace").innerHTML = state.incident.toolTrace.map((step, index) => `<div class="trace-row ${step.status}"><span>${String(index + 1).padStart(2, "0")}</span><div><strong>${escapeHtml(step.tool)}</strong><small>${escapeHtml(step.result)}</small></div><b>${escapeHtml(step.status)}</b></div>`).join("");
  el("auditStatus").textContent = "Preview only · run containment diff to append a receipt";
  el("auditHash").textContent = state.containmentPreview?.enforcedIdentity || "—";
}

function resetReplay() {
  if (state.timer) clearInterval(state.timer);
  state.timer = null;
  state.visibleEvents = 0;
  if (state.incident) {
    el("eventSequence").textContent = `0 / ${state.incident.events.length} events`;
  }
  el("eventTime").textContent = "T+0 min";
  el("eventProgress").style.width = "0%";
  el("transactionRisk").textContent = "—";
  el("ringRisk").textContent = "—";
  el("briefLabel").textContent = "LIVE EVENT BRIEF";
  el("currentEventLabel").textContent = "NO EVENT SELECTED";
  el("agentSummary").textContent = "Run the replay to watch the commander update on every payment.";
  el("factChips").innerHTML = "";
  el("counterfactualRows").innerHTML = "<p>Waiting for graph context.</p>";
  el("agentAction").textContent = "WAITING";
  el("agentReason").textContent = "No money-impacting action has been proposed.";
  el("allowCost").textContent = "—"; el("reviewCost").textContent = "—"; el("holdCost").textContent = "—";
  el("agentState").innerHTML = "<i></i>Waiting";
  el("toolTrace").innerHTML = "";
  el("auditStatus").textContent = "No action yet";
  el("auditHash").textContent = "—";
  el("graphSvg").innerHTML = "";
  el("graphEmpty").hidden = false;
}

function renderDecision(decision) {
  el("agentState").innerHTML = `<i></i>${decision.degraded ? "Degraded" : "Complete"}`;
  if (decision.scores) {
    el("transactionRisk").textContent = decision.scores.transactionRisk == null ? "UNAVAILABLE" : pct(decision.scores.transactionRisk);
    el("ringRisk").textContent = decision.scores.graphRisk == null ? "UNAVAILABLE" : pct(decision.scores.graphRisk);
  }
  el("briefLabel").textContent = decision.containment
    ? "CONTAINMENT REWRITE BRIEF"
    : decision.analystResolution ? "HUMAN RESOLUTION BRIEF"
      : decision.degraded ? "SAFE DEGRADATION BRIEF" : "EVIDENCE-GROUNDED INCIDENT BRIEF";
  el("currentEventLabel").textContent = `${state.incident.targetTransaction} · ${String(decision.failureMode || "healthy").toUpperCase()}`;
  el("agentAction").textContent = decision.action.toUpperCase();
  el("agentReason").textContent = decision.reason;
  el("allowCost").textContent = decision.degraded ? "—" : money(decision.costs.allow);
  el("reviewCost").textContent = money(decision.costs.review);
  el("holdCost").textContent = decision.degraded ? "—" : money(decision.costs.hold);
  el("agentSummary").textContent = decision.explanation;
  el("factChips").innerHTML = decision.facts.map((fact) => `<span title="${fact.text}">${fact.id}</span>`).join("");
  el("counterfactualRows").innerHTML = decision.graphCounterfactuals.length
    ? decision.graphCounterfactuals.map((item) => {
      const calibratedFlat = Math.abs(item.riskDeltaPp) < 0.05;
      const rawMoved = Math.abs(item.rawScoreDeltaPp || 0) >= 0.01;
      const diagnostic = calibratedFlat && rawMoved
        ? `calibration plateau · raw model score changes ${item.rawScoreDeltaPp.toFixed(2)} pp`
        : calibratedFlat ? "no measurable raw or calibrated model response" : `risk becomes ${pct(item.riskAfterRemoval)} · raw score Δ ${item.rawScoreDeltaPp.toFixed(2)} pp`;
      return `<div><span>Remove ${item.removed}</span><strong>${item.riskDeltaPp >= 0 ? "−" : "+"}${Math.abs(item.riskDeltaPp).toFixed(1)} pp</strong><small>${diagnostic}</small></div>`;
    }).join("")
    : "<p>Unavailable—SentinelGraph will not invent relational explanations.</p>";
  el("guardrailBox").classList.toggle("blocked", decision.guardrail.status === "blocked");
  el("guardrailBox").querySelector(":scope > span").textContent = decision.guardrail.status === "blocked" ? "!" : "✓";
  el("guardrailText").textContent = decision.guardrail.reason;
  el("toolTrace").innerHTML = decision.toolTrace.map((step, index) => `
    <div class="trace-row ${step.status}"><span>${String(index + 1).padStart(2, "0")}</span><div><strong>${step.tool}</strong><small>${step.result}</small></div><b>${step.status}</b></div>`).join("");
  el("auditStatus").textContent = `${decision.action.toUpperCase()} recorded · ${decision.audit.chainValid ? "chain verified" : "chain failed"}`;
  el("auditHash").textContent = decision.audit.recordHash;
  if (decision.analystResolution) {
    el("feedbackStatus").textContent = `${decision.analystResolution.decision.replaceAll("_", " ")} · authoritative for this incident`;
  }
}

async function requestDecision(unsafe) {
  if (state.failureMode === "out_of_order") {
    const result = await fetch("/api/razorpay/simulate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "out_of_order" }) }).then((response) => response.json());
    el("agentState").innerHTML = "<i></i>Contained";
    el("agentAction").textContent = "IGNORE STALE STATE";
    el("agentReason").textContent = `The signed ${result.canonical.event} event was accepted for audit, but stateApplied=${result.stateApplied}; payment state remains ${result.currentState.event}.`;
    el("agentSummary").textContent = "An older payment lifecycle event arrived after capture. SentinelGraph preserved monotonic state and did not rescore or regress the payment.";
    el("briefLabel").textContent = "PAYMENT STATE BRIEF";
    el("currentEventLabel").textContent = "OUT-OF-ORDER EVENT";
    el("transactionRisk").textContent = "NOT RUN";
    el("ringRisk").textContent = "NOT RUN";
    el("toolTrace").innerHTML = `<div class="trace-row passed"><span>01</span><div><strong>verify_raw_body</strong><small>HMAC valid · event ID unseen</small></div><b>passed</b></div><div class="trace-row blocked"><span>02</span><div><strong>apply_payment_state</strong><small>Out-of-order transition refused</small></div><b>gated</b></div>`;
    el("auditStatus").textContent = "Stale transition contained";
    el("auditHash").textContent = `${result.eventId} · current=${result.currentState.event}`;
    return;
  }
  if (["duplicate", "tampered"].includes(state.failureMode)) {
    const verification = await fetch("/api/webhook/verify", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode: state.failureMode, eventId: `evt_sg_${state.failureMode}` }),
    }).then((response) => response.json());
    renderWebhookRejection(verification);
    return;
  }
  const response = await fetch("/api/agent/decide", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ failureMode: state.failureMode, requestEntityBlock: unsafe }),
  });
  renderDecision(await response.json());
}

async function submitFeedback(decision) {
  const endpoint = decision === "clear_resolution" ? "/api/feedback/reset" : "/api/feedback";
  const response = await fetch(endpoint, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ incidentId: state.incident.incidentId, decision, analyst: el("analystName").value, note: el("feedbackNote").value }) });
  const result = await response.json();
  if (!response.ok) { el("feedbackStatus").textContent = result.error || "Feedback rejected"; return; }
  el("feedbackStatus").textContent = `${result.decision.replaceAll("_", " ")} · ${result.reversal ? "reversal recorded" : "recorded"}`;
  el("entityUpdate").textContent = `${result.updatedEntities} linked entities updated. Audit ${result.audit.chainValid ? "verified" : "failed"}: ${result.audit.recordHash.slice(0, 18)}…`;
  el("auditStatus").textContent = `Analyst ${result.decision.replaceAll("_", " ")} · chain verified`;
  el("auditHash").textContent = result.audit.recordHash;
  await requestDecision(false);
  if (decision === "clear_resolution") el("feedbackStatus").textContent = "Resolution cleared · automated policy restored";
}

async function verifyAuditChain() {
  const button = el("verifyAuditButton");
  button.disabled = true;
  button.textContent = "Recomputing hashes…";
  try {
    const response = await fetch("/api/audit/verify");
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Audit verification failed");
    el("auditStatus").textContent = result.valid ? `CHAIN VALID · ${num(result.records)} RECORDS` : `CHAIN FAILED · ${num(result.records)} RECORDS`;
    el("auditHash").textContent = result.valid ? "Every record hash and previousHash link recomputed successfully." : "At least one receipt does not match its recorded payload or predecessor.";
    document.querySelector(".audit-panel").classList.toggle("audit-failed", !result.valid);
  } catch (error) {
    el("auditStatus").textContent = "VERIFICATION ERROR";
    el("auditHash").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Verify entire chain";
  }
}

function renderWebhookRejection(result) {
  el("agentState").innerHTML = "<i></i>Rejected";
  el("agentAction").textContent = "REJECT EVENT";
  el("agentReason").textContent = result.signatureValid ? "The replay key was already observed, so no scoring or money action was executed." : "The HMAC signature does not match the event body, so processing stopped before model inference.";
  el("agentSummary").textContent = "No incident brief was generated because event authenticity failed before the AI workflow began.";
  el("briefLabel").textContent = "INGRESS REJECTION BRIEF";
  el("currentEventLabel").textContent = result.eventId;
  el("transactionRisk").textContent = "NOT RUN";
  el("ringRisk").textContent = "NOT RUN";
  el("factChips").innerHTML = "";
  el("counterfactualRows").innerHTML = "<p>Not run—untrusted events never enter graph state.</p>";
  el("toolTrace").innerHTML = `<div class="trace-row blocked"><span>01</span><div><strong>verify_event</strong><small>${result.reason} · accepted=${result.accepted}</small></div><b>blocked</b></div><div class="trace-row unavailable"><span>02</span><div><strong>score_transaction</strong><small>Not invoked</small></div><b>skipped</b></div>`;
  el("auditStatus").textContent = "Ingress rejected";
  el("auditHash").textContent = `${result.eventId} · ${result.reason}`;
}

function loadCopilotStatus(status) {
  const generator = status.generator;
  el("generatorState").innerHTML = generator.configured ? "<i></i>Gemini live" : "<i></i>Cited fallback mode";
  el("generatorState").classList.toggle("offline", !generator.configured);
  el("copilotMode").textContent = generator.configured ? `LIVE · ${generator.provider} · ${generator.model}` : "LOCAL SAFE FALLBACK · GEMINI ADAPTER READY";
  el("copilotRuntimeText").textContent = generator.configured
    ? `Strict structured output · store=${generator.store} · ${generator.timeoutSeconds}s timeout. Invalid claims are discarded.`
    : "No LLM secret is configured, so no external call is attempted. Retrieval, citations, authority validation and audit remain live.";
  const evaluation = status.retrievalEvaluation || {};
  el("ragRecall").textContent = evaluation.recallAtK == null ? "NOT RUN" : pct(evaluation.recallAtK, 0);
  el("ragCases").textContent = evaluation.cases == null ? "—" : String(evaluation.cases);
}

function renderCopilot(result) {
  const live = result.mode === "gemini-interactions-structured-rag";
  el("copilotResultMode").textContent = live ? "GEMINI STRUCTURED RAG · LIVE" : "DETERMINISTIC EXTRACTIVE RAG · FALLBACK";
  el("copilotGate").textContent = result.validation.passed ? "CLAIM GATE · PASSED" : "CLAIM GATE · BLOCKED";
  el("copilotGate").classList.toggle("passed", result.validation.passed);
  el("copilotSummary").textContent = result.brief.summary;
  el("copilotClaims").innerHTML = result.brief.claims.map((claim, index) => `<div class="copilot-claim"><span>${String(index + 1).padStart(2, "0")}</span><div><p>${escapeHtml(claim.text)}</p><small>SUPPORTED BY · ${claim.evidenceIds.map(escapeHtml).join(" + ")}</small></div></div>`).join("");
  el("copilotSources").innerHTML = result.retrievedPolicies.map((policy) => `<div class="copilot-source"><b>${escapeHtml(policy.id)} · SCORE ${Number(policy.score).toFixed(3)}</b><strong>${escapeHtml(policy.title)}</strong><small>${escapeHtml(policy.version)} · ${escapeHtml(policy.retrievalMethod)}</small></div>`).join("");
  const validation = result.validation;
  el("copilotValidation").innerHTML = `<span class="${validation.passed ? "ok" : ""}">Schema ${validation.passed ? "✓" : "✕"}</span><span class="${validation.citationCoverage === 1 ? "ok" : ""}">Citations ${pct(validation.citationCoverage, 0)}</span><span class="${validation.authorityMatch ? "ok" : ""}">Authority ${validation.authorityMatch ? "MATCH" : "BLOCK"}</span><span class="${result.audit.chainValid ? "ok" : ""}">Audit ${result.audit.chainValid ? "CHAINED" : "FAILED"}</span>`;
  el("copilotRuntimeText").textContent = result.fallbackUsed ? `${result.fallbackReason}. Completed in ${result.latencyMs} ms; no unsupported LLM text was shown.` : `Structured response validated in ${result.latencyMs} ms. Provider storage disabled; usage is recorded in the API response.`;
}

async function askCopilot() {
  const button = el("copilotButton");
  button.disabled = true;
  button.textContent = "Retrieving → drafting → validating…";
  el("copilotGate").textContent = "CLAIM GATE · RUNNING";
  try {
    const response = await fetch("/api/copilot/brief", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question: el("copilotQuestion").value }) });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "Copilot request failed");
    renderCopilot(result);
  } catch (error) {
    el("copilotGate").textContent = "CLAIM GATE · ERROR";
    el("copilotSummary").textContent = error.message;
  } finally {
    button.disabled = false;
    button.textContent = "Generate grounded brief";
  }
}

function bind() {
  setConsoleView(location.hash.slice(1), true);
  window.addEventListener("hashchange", () => setConsoleView(location.hash.slice(1), false));
  el("replayButton").addEventListener("click", replay);
  el("blastProposal").addEventListener("change", updateBlastPreview);
  el("simulateBlastButton").addEventListener("click", runDecisionFirewall);
  el("compileWorkbenchButton").addEventListener("click", () => compileWorkbench(false).catch(() => {}));
  el("loadSampleButton").addEventListener("click", restorePublicSample);
  el("addPaymentButton").addEventListener("click", addWorkbenchPayment);
  el("workbenchRows").addEventListener("input", markWorkbenchDirty);
  el("workbenchRows").addEventListener("change", markWorkbenchDirty);
  el("workbenchRows").addEventListener("click", (event) => {
    const button = event.target.closest(".workbench-remove");
    if (!button) return;
    const rows = el("workbenchRows").querySelectorAll("[data-workbench-row]");
    if (rows.length <= 2) { el("workbenchStatus").textContent = "At least two payments are required."; return; }
    const row = button.closest("[data-workbench-row]");
    const wasTarget = row.querySelector('input[name="workbenchTarget"]').checked;
    row.remove();
    if (wasTarget) el("workbenchRows").querySelector('input[name="workbenchTarget"]').checked = true;
    markWorkbenchDirty();
  });
  el("resetButton").addEventListener("click", resetReplay);
  el("unsafeButton").addEventListener("click", () => requestDecision(true));
  document.querySelectorAll("[data-feedback]").forEach((button) => button.addEventListener("click", () => submitFeedback(button.dataset.feedback)));
  el("failureMode").addEventListener("change", () => {
    state.failureMode = el("failureMode").value;
    const descriptions = {
      healthy: "All model, graph, evidence and policy services are available.",
      model: "Model inference times out. No cached score is silently reused; the case routes to review.",
      graph: "Graph context becomes unavailable. Automatic ring containment must stop.",
      evidence: "The score survives, but natural-language claims must fall back to a deterministic notice.",
      identity: "Device and address identity are absent. Transaction scoring survives, but graph containment is gated.",
      drift: "Historical PSI exceeds the approved operating envelope. Automatic actions pause for human review.",
      out_of_order: "A signed authorization arrives after capture. Audit it, but never regress payment state.",
      duplicate: "A replayed event is rejected idempotently before graph state or money actions change.",
      tampered: "An invalid HMAC signature stops the workflow before any model or agent is invoked.",
    };
    el("failureDescription").textContent = descriptions[state.failureMode];
    document.querySelector(".system-state span").textContent = state.failureMode === "healthy" ? "Graph online" : "Degraded mode";
    document.querySelector(".system-state").classList.toggle("degraded", state.failureMode !== "healthy");
  });
  el("rerunButton").addEventListener("click", () => requestDecision(false));
  el("copilotButton").addEventListener("click", askCopilot);
  el("capacitySlider").addEventListener("input", () => renderPolicyLab(Number(el("capacitySlider").value)));
  el("merchantPolicy").addEventListener("change", () => renderPolicyLab(Number(el("capacitySlider").value)));
  el("rescueTabs").addEventListener("click", (event) => {
    const button = event.target.closest("[data-rescue-index]");
    if (button) renderGraphRescue(Number(button.dataset.rescueIndex));
  });
  el("verifyAuditButton").addEventListener("click", verifyAuditChain);
}

async function init() {
  bind();
  try {
    const fetchJson = async (url, attempt = 1) => {
      const controller = new AbortController();
      const timeout = setTimeout(() => controller.abort(), 8000);
      try {
        const result = await fetch(url, { signal: controller.signal, cache: "no-store" });
        if (!result.ok) throw new Error(`${url} returned ${result.status}`);
        return await result.json();
      } catch (error) {
        if (attempt < 2) return fetchJson(url, attempt + 1);
        throw error;
      } finally {
        clearTimeout(timeout);
      }
    };
    const [metricsPayload, incidentPayload, razorpay, coverage, copilotStatus] = await Promise.all([
      fetchJson("/api/metrics"), fetchJson("/api/incident"), fetchJson("/api/razorpay/status"),
      fetchJson("/api/razorpay/coverage"), fetchJson("/api/copilot/status"),
    ]);
    loadMetrics(metricsPayload);
    state.originalIncident = incidentPayload;
    loadIncident(state.originalIncident);
    el("razorpayWebhookState").textContent = razorpay.configured ? "Webhook secret configured" : "Adapter ready · webhook secret not set";
    el("featureContractState").textContent = coverage.eligibleForIeeeModel ? "Eligible for model scoring" : "SCORING BLOCKED · ENRICHMENT REQUIRED";
    el("featureContractText").textContent = `${coverage.gatewayFields.length} gateway fields verified; missing ${coverage.missingFeatureGroups.join(", ")}. ${coverage.fallback}`;
    loadCopilotStatus(copilotStatus);
  } catch (error) {
    document.body.classList.add("api-degraded");
    el("workspaceEyebrow").textContent = "LOCKED EVALUATION VISIBLE · LIVE API RETRY REQUIRED";
    el("workbenchStatus").textContent = `Live API did not respond after two attempts: ${error.message}. The embedded held-out metrics remain visible; reload to retry.`;
  }
}

init();
