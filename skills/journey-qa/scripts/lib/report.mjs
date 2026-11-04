// Contract rules for run reports and capability declarations.
// Validation proves internal consistency and, when a ledger exists, that every
// observed evidence item matches an untampered ledger entry. It does not prove
// that the product behaved as described.
import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { validateDocument } from "./schema.mjs";
import { verifyLedger } from "./ledger.mjs";

export const RUNTIME_MODES = new Set(["human_ui", "instrumented", "deterministic_regression"]);
const RUNTIME_EVIDENCE_KINDS = new Set(["screenshot", "dom", "tool_result", "network", "log", "file", "video", "transcript"]);
const OBSERVATION = new Set(["observe_dom", "observe_screenshot"]);
const BASELINE = ["navigate", "click", "type_text", "keyboard", "wait", "capture_evidence"];

export const VALIDATION_LIMIT =
  "Validates the contract, references and ledger integrity. It does not prove that the product behaved as described or that the machine was trustworthy.";

function uniq(values, path, errors) {
  const seen = new Set();
  for (const v of values) {
    if (seen.has(v)) errors.push(`${path}: duplicate identifier ${JSON.stringify(v)}`);
    seen.add(v);
  }
  return seen;
}

export function referencedEvidenceIds(report) {
  const ids = new Set();
  for (const r of report.rule_checks ?? []) r.evidence_ids?.forEach((i) => ids.add(i));
  for (const a of report.actor_outcomes ?? []) a.evidence_ids?.forEach((i) => ids.add(i));
  for (const f of report.findings ?? []) f.evidence_ids?.forEach((i) => ids.add(i));
  report.isolation?.evidence_ids?.forEach((i) => ids.add(i));
  return ids;
}

export function validateReport(report, { runDir = null } = {}) {
  const warnings = [];
  const errors = validateDocument(report, "run-report");
  if (errors.length) return { errors, warnings };
  const v3 = report.schema_version === "0.3";
  if (v3) {
    if (!report.host) errors.push("$.host: required in report 0.3 (host, model and adapter)");
    report.evidence.forEach((e, i) => {
      if (!e.captured_by) errors.push(`$.evidence[${i}].captured_by: required in report 0.3`);
    });
  }
  const evidenceIds = uniq(report.evidence.map((e) => e.evidence_id), "$.evidence", errors);
  const evidence = new Map(report.evidence.map((e) => [e.evidence_id, e]));
  const ruleIds = uniq(report.rule_checks.map((r) => r.rule_id), "$.rule_checks", errors);
  const rules = new Map(report.rule_checks.map((r) => [r.rule_id, r]));
  const actors = uniq(report.actor_outcomes.map((a) => a.actor_id), "$.actor_outcomes", errors);
  uniq(report.findings.map((f) => f.finding_id), "$.findings", errors);
  const mandatory = uniq(report.scope.mandatory_rule_ids, "$.scope.mandatory_rule_ids", errors);
  const mode = report.execution_mode;

  const refs = (ids, path, requireProof = false) => {
    uniq(ids, path, errors);
    for (const id of ids) if (!evidenceIds.has(id)) errors.push(`${path}: nonexistent evidence ${JSON.stringify(id)}`);
    if (!requireProof) return;
    const origins = RUNTIME_MODES.has(mode) ? ["observed"] : mode === "synthetic" ? ["synthetic"] : ["observed", "provided"];
    const ok = ids.map((id) => evidence.get(id)).filter(Boolean).some(
      (e) => origins.includes(e.origin) && (!RUNTIME_MODES.has(mode) || RUNTIME_EVIDENCE_KINDS.has(e.kind)),
    );
    if (!ok) errors.push(`${path}: a conclusion needs proof compatible with ${mode}`);
  };

  report.evidence.forEach((e, i) => {
    if (e.redaction === "pending") errors.push(`$.evidence[${i}]: redaction of sensitive data pending`);
    if (RUNTIME_MODES.has(mode) && e.origin === "synthetic") errors.push(`$.evidence[${i}]: synthetic proof in a real run`);
    if (mode === "synthetic" && e.origin !== "synthetic") errors.push(`$.evidence[${i}]: synthetic mode needs synthetic origin`);
  });

  report.rule_checks.forEach((r, i) => {
    const path = `$.rule_checks[${i}]`;
    if (r.required !== mandatory.has(r.rule_id)) errors.push(`${path}.required: differs from scope.mandatory_rule_ids`);
    refs(r.evidence_ids, `${path}.evidence_ids`, r.status === "passed" || r.status === "failed");
    if (!r.reason.trim()) errors.push(`${path}.reason: empty justification`);
    if ((r.status === "passed" || r.status === "failed") && !r.source_ref.trim()) errors.push(`${path}.source_ref: a concluded rule needs a reference`);
    if (RUNTIME_MODES.has(mode) && (r.status === "passed" || r.status === "failed") && r.oracle_type === "source") {
      errors.push(`${path}.oracle_type: a normative source alone does not prove execution`);
    }
  });

  const verdict = report.qa_verdict;
  const failed = report.rule_checks.filter((r) => r.status === "failed");
  const passed = report.rule_checks.filter((r) => r.status === "passed");
  const substantive = report.rule_checks.filter((r) => r.status !== "not_applicable");
  if (failed.length && verdict !== "failed") errors.push("$.qa_verdict: a failed rule needs a failed verdict");
  if (verdict === "failed" && !failed.length) errors.push("$.qa_verdict: failed needs at least one failed rule");
  if (verdict === "not_evaluated" && substantive.length) errors.push("$.qa_verdict: not_evaluated is incompatible with substantive checks");
  if (verdict === "inconclusive" && !substantive.length) errors.push("$.qa_verdict: inconclusive needs a substantive check that was started");
  if (verdict === "passed") {
    if (!passed.length) errors.push("$.qa_verdict: passed needs a substantive check that passed");
    if (!mandatory.size) errors.push("$.scope.mandatory_rule_ids: a pass needs a non-empty mandatory scope");
    for (const id of [...mandatory].sort()) {
      const r = rules.get(id);
      if (!r || !["passed", "not_applicable"].includes(r.status)) errors.push(`$.qa_verdict: obligation ${JSON.stringify(id)} missing or not concluded`);
    }
    if (report.run_status === "blocked") errors.push("$.run_status: blocked cannot be passed");
  }

  report.actor_outcomes.forEach((a, i) => refs(a.evidence_ids, `$.actor_outcomes[${i}].evidence_ids`, a.outcome !== "undetermined"));

  const iso = report.isolation;
  refs(iso.evidence_ids, "$.isolation.evidence_ids", iso.checked);
  if ([iso.model_context, iso.browser_sessions].includes("isolated") && !iso.checked) {
    errors.push("$.isolation: declared isolation needs checked and evidence");
  }
  if (actors.size > 1 && [iso.model_context, iso.browser_sessions].includes("single_actor")) {
    errors.push("$.isolation: single_actor is incompatible with multiple actors");
  }

  report.findings.forEach((f, i) => {
    const path = `$.findings[${i}]`;
    refs(f.evidence_ids, `${path}.evidence_ids`, f.kind === "confirmed_defect");
    if (f.rule_id !== null && !ruleIds.has(f.rule_id)) errors.push(`${path}.rule_id: nonexistent reference`);
    if (f.kind === "confirmed_defect") {
      const r = rules.get(f.rule_id);
      if (!r || r.status !== "failed") errors.push(`${path}: a confirmed defect needs a matching failed rule`);
      else if (!f.evidence_ids.some((id) => r.evidence_ids.includes(id))) errors.push(`${path}: the defect and the failed rule must share evidence`);
    }
  });
  report.interventions.forEach((it, i) => {
    if (!actors.has(it.actor_id) && it.actor_id !== "coordinator") errors.push(`$.interventions[${i}].actor_id: nonexistent actor`);
  });

  // Ledger cross-check.
  const dir = runDir ?? null;
  const hasLedger = dir && existsSync(join(dir, "evidence", "ledger.jsonl"));
  if (hasLedger) {
    const lv = verifyLedger(dir);
    for (const e of lv.errors) errors.push(`ledger: ${e}`);
    const byId = new Map(lv.entries.map((e) => [e.evidence_id, e]));
    report.evidence.forEach((e, i) => {
      const path = `$.evidence[${i}] ${e.evidence_id}`;
      const l = byId.get(e.evidence_id);
      if (!l) {
        if (e.origin === "observed") errors.push(`${path}: observed evidence missing from the run ledger`);
        return;
      }
      if ((e.integrity_sha256 ?? null) !== (l.sha256 ?? null)) errors.push(`${path}: integrity_sha256 differs from the ledger`);
      if (e.kind !== l.kind || e.origin !== l.origin) errors.push(`${path}: kind/origin differ from the ledger`);
      if (e.captured_by && e.captured_by !== l.captured_by) errors.push(`${path}: captured_by differs from the ledger`);
      if (e.captured_at !== l.captured_at) errors.push(`${path}: captured_at differs from the ledger`);
    });
    if (report.ledger && lv.head && report.ledger.head_hash !== lv.head) {
      warnings.push("ledger: the report was synced before the latest evidence; run `jqa report sync`");
    }
    // An actor that has its own act.mjs acts through it. Actions typed into that actor's context from anywhere else
    // (the coordinator "helping") are interventions the report must declare, or the actor outcome is not the actor's.
    const traceFile = join(dir, "trace.jsonl");
    if (existsSync(traceFile)) {
      const driven = new Map();
      for (const line of readFileSync(traceFile, "utf8").split("\n")) {
        let t;
        try { t = JSON.parse(line); } catch { continue; }
        if (t.via !== "direct" || (t.as ?? "actor") !== "actor" || !t.actor_id || ["snapshot", "note", "screenshot", "text", "tabs"].includes(t.cmd)) continue;
        if (!existsSync(join(dir, "actors", t.actor_id, "act.mjs"))) continue;
        driven.set(t.actor_id, (driven.get(t.actor_id) ?? 0) + 1);
      }
      for (const [a, n] of driven) {
        if (!report.interventions.some((it) => it.actor_id === a || it.actor_id === "coordinator")) {
          warnings.push(`trace: ${n} actions in ${a}'s browser context did not come from its act.mjs; declare them in interventions[] or run that actor as assisted (model_context: shared)`);
        }
      }
    }
    const ledgerActors = new Set(lv.entries.map((e) => e.actor_id).filter(Boolean));
    for (const a of ledgerActors) if (!actors.has(a)) warnings.push(`ledger: actor ${a} has evidence but no actor_outcome`);
  } else if (RUNTIME_MODES.has(mode) && report.rule_checks.some((r) => ["passed", "failed"].includes(r.status))) {
    warnings.push("No ledger: evidence is declared by the agent and could not be cross-checked against the run's captures.");
  }
  return { errors, warnings };
}

export function syncReport(report, runDir) {
  const lv = verifyLedger(runDir);
  const byId = new Map(lv.entries.map((e) => [e.evidence_id, e]));
  const wanted = new Set([...referencedEvidenceIds(report), ...report.evidence.map((e) => e.evidence_id)]);
  const fromLedger = [];
  const others = [];
  for (const e of report.evidence) if (!byId.has(e.evidence_id)) others.push(e);
  for (const id of wanted) {
    const l = byId.get(id);
    if (!l) continue;
    fromLedger.push({
      evidence_id: l.evidence_id,
      captured_at: l.captured_at,
      kind: l.kind,
      origin: l.origin,
      locator: l.file ?? l.locator,
      summary: l.summary,
      redaction: l.redaction,
      integrity_sha256: l.sha256,
      captured_by: l.captured_by,
      actor_id: l.actor_id,
      ledger_seq: l.seq,
    });
  }
  fromLedger.sort((a, b) => a.ledger_seq - b.ledger_seq);
  const missing = [...wanted].filter((id) => !byId.has(id) && !others.some((e) => e.evidence_id === id));
  report.evidence = [...fromLedger, ...others];
  report.ledger = { path: "evidence/ledger.jsonl", entries: lv.entries.length, head_hash: lv.head, verified: lv.ok };
  if (existsSync(join(runDir, "trace.jsonl"))) report.trace_file = "trace.jsonl";
  return { missing, ledgerErrors: lv.errors };
}

export function checkCapabilities(binding) {
  const errors = validateDocument(binding, "session-binding");
  if (errors.length) return errors;
  uniq(binding.capabilities.map((c) => c.capability), "$.capabilities", errors);
  uniq(binding.required_capabilities, "$.required_capabilities", errors);
  const verified = new Set();
  binding.capabilities.forEach((c, i) => {
    if (c.status !== "verified") return;
    if (!(c.tool_name ?? "").trim() || !(c.proof_ref ?? "").trim()) errors.push(`$.capabilities[${i}]: verified needs a tool and a proof`);
    else verified.add(c.capability);
  });
  const missing = BASELINE.filter((c) => !binding.required_capabilities.includes(c));
  if (missing.length) errors.push(`$.required_capabilities: missing minimums: ${missing.join(", ")}`);
  for (const c of new Set([...BASELINE, ...binding.required_capabilities])) {
    if (!verified.has(c)) errors.push(`$.required_capabilities: unverified capability ${JSON.stringify(c)}`);
  }
  if (![...verified].some((c) => OBSERVATION.has(c) && binding.observation_alternatives.includes(c))) {
    errors.push("$.observation_alternatives: needs a verified observe_dom or observe_screenshot");
  }
  if (!binding.browser.allowed_origins.length) errors.push("$.browser.allowed_origins: no authorised destination");
  for (const o of binding.browser.allowed_origins) if (!isExplicitOrigin(o)) errors.push(`$.browser.allowed_origins: invalid origin ${JSON.stringify(o)}`);
  if (binding.browser.connection_mode === "unknown") errors.push("$.browser.connection_mode: unknown connection");
  for (const [field, cap] of [["model_context", "isolate_model_context"], ["browser_sessions", "isolate_browser_sessions"]]) {
    if (binding.isolation[field] === "isolated" && (!verified.has(cap) || !binding.isolation.proof_refs.some((p) => p.trim()))) {
      errors.push(`$.isolation.${field}: isolation needs a verified capability and a proof`);
    }
  }
  return errors;
}

export function isExplicitOrigin(origin) {
  if (typeof origin !== "string" || /[\s\x00-\x1f\x7f]/.test(origin)) return false;
  let u;
  try {
    u = new URL(origin);
  } catch {
    return false;
  }
  return (
    (u.protocol === "http:" || u.protocol === "https:") && !!u.hostname && !/[%*\\]/.test(u.hostname) &&
    !u.username && !u.password && (u.pathname === "/" || u.pathname === "") && !u.search && !u.hash &&
    origin.replace(/\/$/, "") === u.origin
  );
}
