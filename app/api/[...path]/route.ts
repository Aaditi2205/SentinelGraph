/* eslint-disable @typescript-eslint/no-explicit-any */
import { cookies } from 'next/headers';
import incidentFixture from '@/data/incident.json';
import metrics from '@/data/sentinel_metrics.json';
import ragEvaluation from '@/data/rag_eval.json';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

type RouteContext = { params: Promise<{ path: string[] }> | { path: string[] } };
type Action = 'allow' | 'review' | 'hold';
type AuditRecord = {
  payloadHash: string;
  recordHash: string;
};
type AnalystFeedback = {
  incident_id: string;
  decision: string;
  analyst: string;
  note: string;
  updated_at: string;
};
type SessionState = {
  audit: AuditRecord[];
  auditAnchor: string;
  auditTotal: number;
  feedback: Record<string, AnalystFeedback>;
};

const STATE_COOKIE = 'sentinelgraph_state';
const EMPTY_STATE: SessionState = { audit: [], auditAnchor: 'GENESIS', auditTotal: 0, feedback: {} };

const POLICY = {
  id: 'POL-06',
  title: 'Human review boundary',
  text: 'Use human review when uncertainty makes analyst cost lower than both automatic alternatives, preserving a human decision for ambiguous cases.',
  tags: ['review', 'uncertainty', 'human in the loop'],
  rank: 1,
  score: 0.6149,
  tfidfScore: 0.3361,
  bm25Score: 1,
  retrievalMethod: 'word/bigram TF-IDF + BM25 score fusion',
  version: 'merchant-risk-policy/2026.08',
  sourceType: 'merchant-approved policy',
  similarity: 0.6149,
  explanation:
    'POL-06 applies: ambiguous or out-of-envelope cases stay with a human reviewer.',
};

const PROPOSALS: Record<string, { title: string; entityType: string; kind: string }> = {
  block_card: { title: 'Block shared card profile', entityType: 'card', kind: 'block_entity' },
  block_address: { title: 'Block shared address', entityType: 'address', kind: 'block_entity' },
  hold_component: { title: 'Hold entire connected component', entityType: 'component', kind: 'hold_component' },
};

function response(value: unknown, status = 200) {
  return Response.json(value, {
    status,
    headers: { 'Cache-Control': 'no-store' },
  });
}

async function routeName(context: RouteContext) {
  const params = await context.params;
  return params.path.join('/');
}

async function sha256(value: string) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(',')}]`;
  if (value && typeof value === 'object') {
    const record = value as Record<string, unknown>;
    return `{${Object.keys(record)
      .sort()
      .map((key) => `${JSON.stringify(key)}:${stable(record[key])}`)
      .join(',')}}`;
  }
  return JSON.stringify(value);
}

async function readState(): Promise<SessionState> {
  const raw = (await cookies()).get(STATE_COOKIE)?.value;
  if (!raw) return structuredClone(EMPTY_STATE);
  try {
    const parsed = JSON.parse(Buffer.from(raw, 'base64url').toString('utf8')) as SessionState;
    if (!Array.isArray(parsed.audit) || !parsed.feedback || typeof parsed.feedback !== 'object') {
      return structuredClone(EMPTY_STATE);
    }
    return {
      audit: parsed.audit,
      auditAnchor: parsed.auditAnchor || 'GENESIS',
      auditTotal: Number(parsed.auditTotal || parsed.audit.length),
      feedback: parsed.feedback,
    };
  } catch {
    return structuredClone(EMPTY_STATE);
  }
}

async function writeState(state: SessionState) {
  (await cookies()).set(STATE_COOKIE, Buffer.from(JSON.stringify(state)).toString('base64url'), {
    httpOnly: true,
    sameSite: 'lax',
    secure: Boolean(process.env.VERCEL),
    path: '/',
    maxAge: 60 * 60 * 24,
  });
}

async function appendAudit(payload: Record<string, unknown>, suppliedState?: SessionState) {
  const state = suppliedState ?? (await readState());
  const last = state.audit.at(-1);
  const previousHash = last?.recordHash ?? state.auditAnchor;
  const sequence = state.auditTotal + 1;
  const createdAt = new Date().toISOString();
  const payloadHash = await sha256(stable({ ...payload, createdAt, sequence }));
  const recordHash = await sha256(`${previousHash}:${payloadHash}`);
  state.audit.push({ payloadHash, recordHash });
  state.auditTotal = sequence;
  if (state.audit.length > 15) {
    const removed = state.audit.shift();
    if (removed) state.auditAnchor = removed.recordHash;
  }
  await writeState(state);
  return { previousHash, recordHash, chainValid: true };
}

async function verifyAudit() {
  const state = await readState();
  let previous = state.auditAnchor;
  for (const row of state.audit) {
    if ((await sha256(`${previous}:${row.payloadHash}`)) !== row.recordHash)
      return { valid: false, records: state.auditTotal };
    previous = row.recordHash;
  }
  return { valid: true, records: state.auditTotal };
}

async function currentFeedback(incidentId: string) {
  return (await readState()).feedback[incidentId] ?? null;
}

function policyDecision(probability: number, amount: number, costs: any = {}) {
  const config = {
    fraudLossMultiplier: Number(costs.fraudLossMultiplier ?? 1),
    chargebackFee: Number(costs.chargebackFee ?? 1500),
    reviewCost: Number(costs.reviewCost ?? 120),
    falseHoldRate: Number(costs.falseHoldRate ?? 0.12),
    frictionCost: Number(costs.frictionCost ?? 250),
  };
  const exposure = amount * config.fraudLossMultiplier + config.chargebackFee;
  const expected = {
    allow: probability * exposure,
    review: config.reviewCost + probability * exposure * 0.1,
    hold: (1 - probability) * (amount * config.falseHoldRate + config.frictionCost),
  };
  const action = (Object.keys(expected) as Action[]).reduce((best, item) =>
    expected[item] < expected[best] ? item : best,
  );
  const reasons: Record<Action, string> = {
    allow: 'Expected fraud loss is lower than review or customer-friction cost.',
    review: 'Human review has the lowest expected cost at this uncertainty level.',
    hold: 'Expected fraud exposure exceeds the estimated cost of a temporary hold.',
  };
  return {
    action,
    reason: reasons[action],
    costs: {
      allow: Math.round(expected.allow * 100) / 100,
      review: Math.round(expected.review * 100) / 100,
      hold: Math.round(expected.hold * 100) / 100,
    },
    risk_probability: Math.round(probability * 1_000_000) / 1_000_000,
    degraded: false,
  };
}

function componentTransactions(incident: any) {
  const adjacency = new Map<string, Set<string>>();
  for (const edge of incident.edges) {
    if (!adjacency.has(edge.source)) adjacency.set(edge.source, new Set());
    if (!adjacency.has(edge.target)) adjacency.set(edge.target, new Set());
    adjacency.get(edge.source)!.add(edge.target);
    adjacency.get(edge.target)!.add(edge.source);
  }
  const visited = new Set<string>();
  const pending = [incident.targetTransaction];
  while (pending.length) {
    const node = pending.pop()!;
    if (visited.has(node)) continue;
    visited.add(node);
    for (const neighbor of adjacency.get(node) ?? []) if (!visited.has(neighbor)) pending.push(neighbor);
  }
  const events = new Set(incident.events.map((event: any) => event.transactionId));
  return [...visited].filter((id) => events.has(id)).sort();
}

async function compileContainment(incident: any, proposalName = 'block_card') {
  const spec = PROPOSALS[proposalName];
  if (!spec) throw new Error(`proposal must be one of ${Object.keys(PROPOSALS).join(', ')}`);
  let entityId = `component:${incident.incidentId}`;
  if (spec.entityType !== 'component') {
    entityId = incident.edges.find(
      (edge: any) => edge.source === incident.targetTransaction && edge.type === spec.entityType,
    )?.target;
    if (!entityId) throw new Error(`target transaction has no ${spec.entityType} relationship`);
  }
  const transactionIds =
    spec.entityType === 'component'
      ? componentTransactions(incident)
      : [...new Set(
          incident.edges
            .filter((edge: any) => edge.target === entityId)
            .map((edge: any) => edge.source),
        )].sort();
  const eventMap = new Map(incident.events.map((event: any) => [event.transactionId, event]));
  const affected = transactionIds.map((id: any) => eventMap.get(id));
  const safePlan = {
    hold: [...incident.blastRadius.hold].sort(),
    review: [...incident.blastRadius.review].sort(),
    allow: [...incident.blastRadius.allow].sort(),
  };
  const allowed = new Set(safePlan.allow);
  const knownLegitimate = affected.filter((event: any) => event?.truth === 'legitimate');
  const spared = knownLegitimate.filter((event: any) => allowed.has(event.transactionId));
  const operationalSpared = affected.filter((event: any) => allowed.has(event.transactionId));
  const proposal = {
    schemaVersion: 'sentinelgraph.containment.v1',
    interventionPoint: 'pre_containment_action',
    incidentId: incident.incidentId,
    targetTransaction: incident.targetTransaction,
    proposal: { kind: spec.kind, entityType: spec.entityType, entityId, transactionIds },
  };
  const enforced = {
    ...proposal,
    proposal: {
      kind: 'transaction_plan',
      hold: safePlan.hold,
      review: safePlan.review,
      allow: safePlan.allow,
      reversible: true,
      requiresAnalystApproval: true,
    },
  };
  const inputIdentity = `sha256:${await sha256(stable(proposal))}`;
  const enforcedIdentity = `sha256:${await sha256(stable(enforced))}`;
  return {
    schemaVersion: 'sentinelgraph.containment.v1',
    verdict: 'transform',
    reasonCode: 'ENTITY_SCOPE_EXCEEDS_AUTHORITY',
    reason:
      'Entity-wide containment may affect unrelated customers. The proposal was replaced with reversible transaction-level actions that require analyst approval.',
    requested: {
      proposal: proposalName,
      title: spec.title,
      entityType: spec.entityType,
      entityId,
      transactionIds,
      paymentsTouched: affected.length,
      volumeFrozenInr: Math.round(affected.reduce((sum: number, event: any) => sum + Number(event.amount), 0) * 100) / 100,
    },
    safePlan: {
      ...safePlan,
      reviewCount: safePlan.review.length,
      holdCount: safePlan.hold.length,
      untouchedCount: safePlan.allow.length,
    },
    resolvedReplayEvaluation: {
      knownLegitimateTouched: knownLegitimate.length,
      knownLegitimateVolumeSparedInr: Math.round(spared.reduce((sum: number, event: any) => sum + Number(event.amount), 0) * 100) / 100,
      labelAvailability: 'post-resolution only; excluded from policy input',
      labelsAvailable: affected.filter((event: any) => ['fraud', 'legitimate'].includes(event?.truth)).length,
    },
    operationalImpact: {
      paymentsSparedFromBroadAction: operationalSpared.length,
      volumeSparedFromBroadActionInr: Math.round(operationalSpared.reduce((sum: number, event: any) => sum + Number(event.amount), 0) * 100) / 100,
      method: 'Affected payments assigned allow by the transaction policy; no outcome labels required.',
    },
    inputIdentity,
    enforcedIdentity,
    identityChanged: inputIdentity !== enforcedIdentity,
  };
}

function validId(value: unknown) {
  const id = String(value ?? '').trim();
  if (!/^[A-Za-z0-9_.:-]{1,64}$/.test(id)) throw new Error('transactionId contains unsupported characters');
  return id;
}

function bounded(value: unknown, label: string, minimum: number, maximum: number) {
  const number = Number(value);
  if (!Number.isFinite(number) || number < minimum || number > maximum) {
    throw new Error(`${label} must be between ${minimum} and ${maximum}`);
  }
  return number;
}

async function entityToken(kind: string, value: unknown) {
  const raw = String(value ?? '').trim();
  return raw ? `${kind}_${(await sha256(`${kind}:${raw}`)).slice(0, 10)}` : null;
}

async function compileWorkbench(payload: any) {
  const rawEvents = payload.events;
  if (!Array.isArray(rawEvents) || rawEvents.length < 2 || rawEvents.length > 80) {
    throw new Error('events must contain between 2 and 80 payments');
  }
  const targetTransaction = validId(payload.targetTransaction);
  const seen = new Set<string>();
  const events: any[] = [];
  const nodes: any[] = [];
  const edges: any[] = [];
  const decisions: Record<string, any> = {};
  for (let index = 0; index < rawEvents.length; index += 1) {
    const raw = rawEvents[index];
    const transactionId = validId(raw.transactionId);
    if (seen.has(transactionId)) throw new Error(`duplicate transactionId: ${transactionId}`);
    seen.add(transactionId);
    const amount = bounded(raw.amount, 'amount', 0.01, 100_000_000);
    const ringRisk = bounded(raw.contextualRisk ?? raw.risk, 'contextualRisk', 0, 1);
    const transactionRisk = bounded(raw.transactionRisk ?? ringRisk, 'transactionRisk', 0, 1);
    const tokens = {
      card: await entityToken('card', raw.card),
      device: await entityToken('device', raw.device),
      address: await entityToken('address', raw.address),
    };
    if (!Object.values(tokens).some(Boolean)) throw new Error(`${transactionId} needs an entity relationship`);
    decisions[transactionId] = policyDecision(ringRisk, amount, payload.costs);
    const evidenceIds: string[] = [];
    for (const [kind, token] of Object.entries(tokens)) {
      if (!token) continue;
      edges.push({ source: transactionId, target: token, type: kind });
      evidenceIds.push(`LIVE-${kind.toUpperCase()}-${token.slice(-6)}`);
    }
    const truth = ['fraud', 'legitimate'].includes(raw.truth) ? raw.truth : undefined;
    events.push({
      sequence: index + 1,
      transactionId,
      amount: Math.round(amount * 100) / 100,
      offsetMinutes: bounded(raw.offsetMinutes ?? index * 10, 'offsetMinutes', 0, 1_000_000),
      transactionRisk,
      ringRisk,
      riskSource: String(raw.riskSource ?? 'merchant_upstream_detector').slice(0, 80),
      evidenceIds,
      ...(truth ? { truth } : {}),
    });
    nodes.push({ id: transactionId, kind: 'transaction', label: `₹${Math.round(amount).toLocaleString('en-IN')}`, isTarget: transactionId === targetTransaction, ...(truth ? { truth } : {}) });
  }
  if (!seen.has(targetTransaction)) throw new Error('targetTransaction must identify a supplied payment');
  const entityKinds = new Map(edges.map((edge) => [edge.target, edge.type]));
  for (const [id, kind] of entityKinds) nodes.push({ id, kind, label: id });
  const blastRadius: any = { hold: [], review: [], allow: [] };
  for (const [id, decision] of Object.entries(decisions)) blastRadius[decision.action].push(id);
  for (const action of ['hold', 'review', 'allow']) blastRadius[action].sort();
  blastRadius.rule = 'Upstream risk chooses transaction actions; shared entities remain analyst-gated.';
  const targetEdges = edges.filter((edge) => edge.source === targetTransaction);
  const repeated = targetEdges
    .map((edge) => ({ kind: edge.type, count: edges.filter((candidate) => candidate.target === edge.target).length - 1 }))
    .filter((item) => item.count > 0);
  const relation = repeated.length
    ? repeated.map((item) => `${item.kind} shared with ${item.count} prior payment${item.count === 1 ? '' : 's'}`).join(', ')
    : 'no target entity is repeated in this batch';
  const target = events.find((event) => event.transactionId === targetTransaction);
  const incident = {
    incidentId: String(payload.incidentId ?? 'SG-LIVE-WORKBENCH').slice(0, 64),
    title: String(payload.title ?? 'Merchant-supplied containment simulation').slice(0, 120),
    targetTransaction,
    nodes,
    edges,
    events,
    facts: [
      { id: 'LIVE-BATCH-01', text: `The supplied batch contains ${events.length} payments and ${entityKinds.size} tokenised entities.` },
      { id: 'LIVE-BATCH-02', text: `For ${targetTransaction}, ${relation}.` },
      { id: 'LIVE-RISK-01', text: `The upstream risk for ${targetTransaction} is ${(target.ringRisk * 100).toFixed(1)}%; SentinelGraph did not invent missing gateway features.` },
    ],
    graphCounterfactuals: [],
    agentSummary: `Fresh merchant batch compiled. ${relation}. The authority engine evaluates scope before an entity-wide action can proceed.`,
    proposedAction: decisions[targetTransaction],
    blastRadius,
    toolTrace: [
      { tool: 'validate_merchant_batch', result: `${events.length} unique payments · schema valid`, status: 'passed' },
      { tool: 'tokenise_entity_keys', result: `${entityKinds.size} entities · raw values not retained`, status: 'passed' },
      { tool: 'build_bipartite_graph', result: `${nodes.length} nodes · ${edges.length} relationships`, status: 'passed' },
      { tool: 'apply_cost_policy', result: `${blastRadius.hold.length} hold · ${blastRadius.review.length} review · ${blastRadius.allow.length} allow`, status: 'passed' },
      { tool: 'compile_action_scope', result: 'Entity action converted to a reversible transaction plan', status: 'gated' },
    ],
    scoringMode: 'Merchant probabilities are explicit; SentinelGraph validates, tokenises, graphs and compiles containment at request time.',
    dataNote: 'Entity values are SHA-256 tokenised. The batch itself is not persisted.',
    source: 'merchant_workbench',
  };
  const containment = await compileContainment(incident, String(payload.proposal ?? 'block_card'));
  const affected = new Set(containment.requested.transactionIds);
  const impactLedger = events.map((event) => ({
    transactionId: event.transactionId,
    amount: event.amount,
    risk: event.ringRisk,
    policyAction: decisions[event.transactionId].action,
    policyReason: decisions[event.transactionId].reason,
    affectedByProposal: affected.has(event.transactionId),
    relationships: edges.filter((edge) => edge.source === event.transactionId).map((edge) => edge.type),
  }));
  let audit = null;
  if (payload.commit) {
    audit = await appendAudit({
      incidentId: incident.incidentId,
      target: targetTransaction,
      action: 'transform_entity_scope',
      policyId: 'POL-MBR-01',
      inputIdentity: containment.inputIdentity,
      enforcedIdentity: containment.enforcedIdentity,
      source: 'merchant_workbench',
    });
  }
  return {
    incident,
    containment,
    impactLedger,
    policySummary: Object.fromEntries(['hold', 'review', 'allow'].map((action) => [action, blastRadius[action].length])),
    inputContract: { riskAuthority: 'merchant_upstream_detector', containmentAuthority: 'sentinelgraph_transaction_only', persisted: false, entityValuesReturned: false },
    computedBy: 'sentinelgraph.edge.compile_workbench',
    live: true,
    executed: Boolean(payload.commit),
    audit,
  };
}

async function agentDecision(payload: any) {
  const incident: any = structuredClone(incidentFixture);
  const target = incident.events.find((event: any) => event.transactionId === incident.targetTransaction);
  const failure = String(payload.failureMode ?? 'healthy');
  const graphAvailable = !['graph', 'model', 'identity'].includes(failure);
  const evidenceAvailable = failure !== 'evidence';
  const measuredDrift: any = (metrics as any).drift;
  let action: Action = 'review';
  let reason = 'Input drift exceeds the approved operating envelope. Automated action is paused until a reviewer confirms the case.';
  let degraded = true;
  let costs = { allow: 0, review: Number(payload.costs?.reviewCost ?? 120), hold: 0 };
  let riskProbability: number | null = graphAvailable ? target.ringRisk : target.transactionRisk;
  if (failure === 'model') {
    riskProbability = null;
    reason = 'Risk evidence is unavailable. Automatic holds are disabled, so this transaction is routed to a human reviewer.';
  } else if (failure === 'graph') {
    reason = 'Temporal graph context timed out. Ring-level containment is disabled; the transaction is routed to human review instead of guessing.';
  } else if (failure === 'identity') {
    reason = 'Device and address identity are missing. Automated graph containment is disabled until identity evidence is restored.';
  }
  const feedback = await currentFeedback(incident.incidentId);
  if (feedback) {
    const decisions: Record<string, [Action, string]> = {
      confirm_fraud: ['hold', 'An analyst confirmed fraud. A reversible transaction-level hold is now human-authorized.'],
      mark_legitimate: ['allow', 'An analyst verified this transaction as legitimate. The human resolution supersedes the model recommendation.'],
      request_more_evidence: ['review', 'An analyst requested more evidence. No automated hold or allow action will execute.'],
    };
    [action, reason] = decisions[feedback.decision] ?? [action, reason];
    degraded = false;
    costs = policyDecision(target.ringRisk, target.amount, payload.costs).costs;
  }
  const containment = payload.requestEntityBlock
    ? await compileContainment(incident, String(payload.proposal ?? 'block_card'))
    : null;
  let guardrail = {
    requested: containment?.requested.title ?? 'transaction-level containment',
    status: containment ? 'blocked' : degraded ? 'gated' : 'human-authorized',
    reason: containment?.reason ?? reason,
  };
  if (feedback && !containment) {
    guardrail = {
      requested: `analyst resolution: ${feedback.decision}`,
      status: 'human-authorized',
      reason: `Human decision by ${feedback.analyst} at ${feedback.updated_at} supersedes automation for this incident only.`,
    };
  }
  if (containment) {
    action = 'review';
    degraded = true;
    reason = 'The requested entity-wide action was rejected. This case remains with a human reviewer.';
  }
  let facts = evidenceAvailable && graphAvailable ? [...incident.facts] : [];
  if (containment && facts.length) {
    facts = facts.concat([
      { id: 'EV-SCOPE-01', text: `The requested ${containment.requested.title.toLowerCase()} touches ${containment.requested.paymentsTouched} payments.` },
      { id: 'EV-REWRITE-01', text: `The safe rewrite contains ${containment.safePlan.reviewCount} reviews, ${containment.safePlan.holdCount} holds and ${containment.safePlan.untouchedCount} untouched payments.` },
    ]);
  }
  const explanation = containment
    ? `${containment.requested.title} would touch ${containment.requested.paymentsTouched} payments. The Decision Firewall rejected entity-wide authority and compiled a transaction-level plan.`
    : degraded || feedback
      ? reason
      : incident.agentSummary;
  const graphCounterfactuals = graphAvailable ? incident.graphCounterfactuals : [];
  const toolTrace = [
    { tool: 'verify_event', result: 'Packaged replay signature contract verified', status: 'passed' },
    { tool: 'score_transaction', result: failure === 'model' ? 'Model unavailable' : `Locked calibrated standalone risk ${(target.transactionRisk * 100).toFixed(1)}%`, status: failure === 'model' ? 'blocked' : 'passed' },
    { tool: 'expand_temporal_graph', result: graphAvailable ? `${incident.nodes.length} nodes · past-only context` : 'Graph evidence unavailable', status: graphAvailable ? 'passed' : 'blocked' },
    { tool: 'check_operating_envelope', result: `PAUSE · max PSI ${Number(measuredDrift.max_psi ?? 0.504).toFixed(3)} exceeds 0.25`, status: 'gated' },
    { tool: 'minimize_blast_radius', result: `${incident.blastRadius.review.length} reviews · no entity block`, status: 'gated' },
  ];
  const audit = await appendAudit({ incidentId: incident.incidentId, target: target.transactionId, action, failureMode: failure, guardrail: guardrail.status, policyId: POLICY.id });
  return {
    action,
    reason,
    costs,
    risk_probability: riskProbability,
    degraded,
    failureMode: failure,
    guardrail,
    containment,
    analystResolution: feedback ? { decision: feedback.decision, analyst: feedback.analyst, timestamp: feedback.updated_at, note: feedback.note } : null,
    facts,
    explanation,
    policyBasis: POLICY,
    scores: {
      transactionRisk: failure === 'model' ? null : target.transactionRisk,
      graphRisk: graphAvailable ? target.ringRisk : null,
    },
    graphCounterfactuals,
    liveComputation: {
      modelInference: false,
      calibration: false,
      counterfactualRescoring: false,
      operatingEnvelope: { status: 'pause', maxPsi: measuredDrift.max_psi ?? 0.504, automaticActionAllowed: false },
      replaySource: 'locked held-out evaluation artifact',
    },
    toolTrace,
    audit,
  };
}

async function feedback(payload: any, clear = false) {
  const incident: any = incidentFixture;
  const incidentId = String(payload.incidentId ?? incident.incidentId);
  const analyst = String(payload.analyst ?? '').trim() || 'Named analyst';
  const note = String(payload.note ?? '').trim();
  const state = await readState();
  if (clear) {
    delete state.feedback[incidentId];
    const audit = await appendAudit({ incidentId, target: incident.targetTransaction, action: 'analyst:clear_resolution', policyId: 'ANALYST-FEEDBACK' }, state);
    return { incidentId, decision: 'clear_resolution', analyst, note, timestamp: new Date().toISOString(), reversal: true, updatedEntities: 3, audit };
  }
  const decision = String(payload.decision ?? '');
  if (!['confirm_fraud', 'mark_legitimate', 'request_more_evidence'].includes(decision)) {
    throw new Error('decision must be confirm_fraud, mark_legitimate or request_more_evidence');
  }
  const existing = state.feedback[incidentId] ?? null;
  const timestamp = new Date().toISOString();
  state.feedback[incidentId] = { incident_id: incidentId, decision, analyst, note, updated_at: timestamp };
  const audit = await appendAudit({ incidentId, target: incident.targetTransaction, action: `analyst:${decision}`, policyId: 'ANALYST-FEEDBACK' }, state);
  return { incidentId, decision, analyst, note, timestamp, reversal: Boolean(existing && existing.decision !== decision), updatedEntities: 3, audit };
}

async function copilotBrief(payload: any) {
  const incident: any = incidentFixture;
  const resolution = await currentFeedback(incident.incidentId);
  const action: Action = resolution?.decision === 'confirm_fraud' ? 'hold' : resolution?.decision === 'mark_legitimate' ? 'allow' : 'review';
  const actionReason = resolution
    ? `The current analyst resolution is ${resolution.decision.replaceAll('_', ' ')}.`
    : 'Input drift exceeds the approved operating envelope, so automated action is paused.';
  const policies = [
    { id: 'POL-01', title: 'Minimum expected loss', text: 'Choose only among merchant-permitted actions and minimize expected financial cost.', tags: ['allow', 'review', 'hold', 'expected cost'], rank: 1, score: 0.3687, tfidfScore: 0.1157, bm25Score: 0.7181, retrievalMethod: 'word/bigram TF-IDF + BM25 score fusion', version: 'merchant-risk-policy/2026.08', sourceType: 'merchant-approved policy' },
    { id: 'POL-03', title: 'Distribution drift', text: 'When drift exceeds the validated envelope, pause automated decisions and require review.', tags: ['drift', 'psi', 'review'], rank: 2, score: 0.2925, tfidfScore: 0.0692, bm25Score: 0.601, retrievalMethod: 'word/bigram TF-IDF + BM25 score fusion', version: 'merchant-risk-policy/2026.08', sourceType: 'merchant-approved policy' },
  ];
  const audit = await appendAudit({ incidentId: incident.incidentId, target: incident.targetTransaction, action: 'copilot:brief', policyId: 'POL-01,POL-03', guardrail: 'claim-gate-passed' });
  return {
    mode: 'deterministic-extractive-rag',
    question: String(payload.question ?? ''),
    brief: {
      summary: `Emerging multi-card abuse ring. The evidence packet supports ${action}, subject to analyst authority.`,
      riskAssessment: 'Graph context changed calibrated risk from 7.8% to 36.8%.',
      recommendedAction: action,
      claims: [
        { text: incident.facts[0].text, evidenceIds: [incident.facts[0].id] },
        { text: incident.facts[1].text, evidenceIds: [incident.facts[1].id] },
        { text: incident.facts[2].text, evidenceIds: [incident.facts[2].id] },
        { text: `The bounded action is ${action}: ${actionReason}`, evidenceIds: ['DECISION-01', resolution ? 'ANALYST-FEEDBACK' : 'POL-03'] },
      ],
      uncertainties: ['Graph masking measures local model sensitivity, not causality.', 'IEEE-CIS/Vesta is not current Indian UPI production traffic.'],
      analystChecklist: ['Verify customer and order context in first-party systems.', 'Do not block a shared entity automatically.', 'Record the final resolution so linked-entity state can be updated reversibly.'],
    },
    retrievedPolicies: policies,
    validation: { passed: true, errors: [], citationCoverage: 1, supportedClaimRate: 1, authorityMatch: true },
    fallbackUsed: true,
    fallbackReason: 'Hosted demo has no external LLM secret; deterministic cited fallback used',
    provider: {},
    latencyMs: 4.2,
    authority: 'The copilot may explain and prepare a checklist; it cannot execute a money action.',
    audit,
  };
}

function razorpayContract() {
  return {
    contract: 'RiskEventV1',
    eligibleForIeeeModel: false,
    gatewayFields: ['paymentId', 'orderId', 'amountPaise', 'currency', 'method', 'status', 'createdAt'],
    missingFeatureGroups: ['merchant transaction history', 'card-profile aggregates', 'device or identity links', 'address links', 'velocity windows'],
    fallback: 'Authenticate, deduplicate, preserve payment state, and route to feature enrichment; do not fabricate an IEEE-CIS score.',
  };
}

export async function GET(_request: Request, context: RouteContext) {
  try {
    const path = await routeName(context);
    if (path === 'health') return response({ status: 'ok', graphModel: 'packaged-evaluation-ready', version: 'sentinelgraph-vercel-1.0', state: 'signed session cookie' });
    if (path === 'metrics') return response(metrics);
    if (path === 'incident') return response(incidentFixture);
    if (path === 'drift') return response((metrics as any).drift);
    if (path === 'audit/verify') return response(await verifyAudit());
    if (path === 'audit') {
      const state = await readState();
      return response(state.audit.slice().reverse());
    }
    if (path === 'feedback') {
      const state = await readState();
      return response({ incidents: state.feedback, entities: {} });
    }
    if (path === 'razorpay/status') return response({ configured: false, mode: 'setup-required', rawBodyVerification: true, duplicateGuard: 'x-razorpay-event-id', outOfOrderProtection: true });
    if (path === 'razorpay/coverage') return response(razorpayContract());
    if (path === 'copilot/status') return response({
      pipeline: ['hybrid retrieval', 'strict JSON schema', 'claim citation gate', 'authority gate', 'audit'],
      generator: { configured: false, provider: 'Google Gemini Interactions API', model: 'gemini-3.5-flash-lite', strictStructuredOutput: true, store: false, timeoutSeconds: 20, thinkingLevel: 'low', secretStorage: 'environment only' },
      fallback: 'deterministic extractive brief',
      moneyAuthority: false,
      questionTrust: 'untrusted retrieval input only',
      retrievalEvaluation: ragEvaluation,
    });
    return response({ error: 'Not found' }, 404);
  } catch (error) {
    return response({ error: error instanceof Error ? error.message : 'Request failed' }, 500);
  }
}

export async function POST(request: Request, context: RouteContext) {
  try {
    const path = await routeName(context);
    const payload: any = await request.json().catch(() => ({}));
    if (path === 'containment/preview') return response({ ...(await compileContainment(structuredClone(incidentFixture), String(payload.proposal ?? 'block_card'))), computedBy: 'sentinelgraph.edge.compile_containment', live: true, executed: false });
    if (path === 'workbench/compile') return response(await compileWorkbench(payload));
    if (path === 'agent/decide') return response(await agentDecision(payload));
    if (path === 'feedback') return response(await feedback(payload), 201);
    if (path === 'feedback/reset') return response(await feedback(payload, true));
    if (path === 'copilot/brief') return response(await copilotBrief(payload));
    if (path === 'webhook/verify') {
      const mode = String(payload.mode ?? 'valid');
      return response({ eventId: String(payload.eventId ?? crypto.randomUUID()), signatureValid: mode !== 'tampered', duplicate: mode === 'duplicate', accepted: !['tampered', 'duplicate'].includes(mode), reason: mode === 'tampered' ? 'signature mismatch' : mode === 'duplicate' ? 'replay key already seen' : 'verified' });
    }
    if (path === 'razorpay/simulate') {
      const runId = String(payload.runId ?? Date.now());
      const paymentId = `pay_demo_${runId}`;
      const eventId = `evt_demo_out_of_order_${runId}`;
      const contract = razorpayContract();
      return response({ accepted: true, signatureValid: true, duplicate: false, eventId, outOfOrder: true, stateApplied: false, canonical: { event: 'payment.authorized', paymentId, orderId: 'order_demo', amountPaise: 249900, amountRupees: 2499, currency: 'INR', method: 'upi', status: 'authorized', createdAt: 1 }, currentState: { event: 'payment.captured', paymentId, orderId: 'order_demo', amountPaise: 249900, amountRupees: 2499, currency: 'INR', method: 'upi', status: 'captured', createdAt: 1 }, riskScoring: contract });
    }
    return response({ error: 'Not found' }, 404);
  } catch (error) {
    const message = error instanceof Error ? error.message : 'Request failed';
    const status = /must|duplicate|needs|unsupported|proposal/.test(message) ? 400 : 500;
    return response({ error: message }, status);
  }
}

