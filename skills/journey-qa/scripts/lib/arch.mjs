// Architecture mode: ADR/RFC discovery, review scaffolding/validation/rendering,
// architect-facing questions, ADR→rule traceability, and read-only infra checks
// (terraform plan JSON, Kubernetes manifests JSON). Zero dependencies.
import { createHash } from "node:crypto";
import { existsSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, extname, join, relative, resolve, sep } from "node:path";
import { mkdirSync } from "node:fs";
import { ContractError, readJson } from "./json.mjs";
import { loadTemplate, validateDocument } from "./schema.mjs";
import { appendEvidence } from "./ledger.mjs";

const posix = (p) => p.split(sep).join("/");
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const SEV_ORDER = { critical: 0, high: 1, medium: 2, low: 3, info: 4 };

// ------------------------------------------------------------------ review contract
export function validateReview(review) {
  const errors = validateDocument(review, "architecture-review");
  const warnings = [];
  if (errors.length) return { errors, warnings };
  const uniq = (vals, path) => {
    const seen = new Set();
    for (const v of vals) { if (seen.has(v)) errors.push(`${path}: duplicate identifier ${v}`); seen.add(v); }
    return seen;
  };
  const sources = uniq(review.sources.map((s) => s.source_id), "$.sources");
  const decisions = uniq(review.decisions.map((d) => d.decision_id), "$.decisions");
  uniq(review.assumptions.map((a) => a.assumption_id), "$.assumptions");
  uniq(review.items.map((i) => i.item_id), "$.items");
  const verifications = uniq(review.items.flatMap((i) => i.proposed_verification.map((v) => v.verification_id)), "$.items[].proposed_verification");
  const known = new Set([...sources, ...decisions, ...review.assumptions.map((a) => a.assumption_id)]);
  review.decisions.forEach((d, i) => { if (!sources.has(d.source_id)) errors.push(`$.decisions[${i}].source_id: unknown source ${d.source_id}`); });
  review.items.forEach((it, i) => {
    const p = `$.items[${i}] ${it.item_id}`;
    for (const r of it.refs) {
      const id = r.split(/[#§:]/)[0];
      if (!known.has(id)) warnings.push(`${p}: reference ${r} does not point to a known source, decision or assumption`);
    }
    if (!it.refs.length) errors.push(`${p}: every item must point to the source or decision that prompted it`);
    if (it.kind === "question" && !it.question_for_architect) errors.push(`${p}: kind question needs question_for_architect`);
    if (["risk", "gap", "contradiction"].includes(it.kind) && ["critical", "high"].includes(it.severity) && it.status === "open" &&
      !it.proposed_verification.length && !it.question_for_architect) {
      errors.push(`${p}: open ${it.severity} risk with no proposed verification and no question for the architect`);
    }
    if (it.kind === "testability_requirement" && !it.proposed_verification.length) errors.push(`${p}: testability requirement needs a proposed verification`);
    if (it.status === "answered" && !it.answer) errors.push(`${p}: status answered needs answer`);
  });
  const openBlocking = review.items.filter((i) => ["risk", "contradiction", "gap"].includes(i.kind) && i.status === "open" && ["critical", "high"].includes(i.severity));
  const v = review.summary.verdict;
  if (v === "proceed" && openBlocking.length) errors.push(`$.summary.verdict: proceed with ${openBlocking.length} open critical/high item(s) (${openBlocking.map((i) => i.item_id).join(", ")})`);
  if (v === "block" && !review.items.some((i) => i.severity === "critical" && i.status === "open")) errors.push("$.summary.verdict: block needs an open critical item");
  if (v === "proceed_with_conditions" && !review.summary.conditions.length) errors.push("$.summary.conditions: proceed_with_conditions needs conditions");
  if (v !== "not_assessed" && !review.items.length) errors.push("$.items: verdict issued with no item analysed");
  review.traceability.forEach((t, i) => {
    if (!decisions.has(t.decision_id)) errors.push(`$.traceability[${i}]: unknown decision ${t.decision_id}`);
    for (const vid of t.verification_ids) if (!verifications.has(vid)) errors.push(`$.traceability[${i}]: unknown verification ${vid}`);
  });
  review.conformance.forEach((c, i) => {
    if (!decisions.has(c.decision_id)) errors.push(`$.conformance[${i}]: unknown decision ${c.decision_id}`);
    if (["conforms", "drift"].includes(c.status) && !c.evidence_refs.length) errors.push(`$.conformance[${i}]: ${c.status} needs evidence`);
  });
  for (const d of review.decisions) {
    const covered = review.items.some((i) => i.refs.some((r) => r.startsWith(d.decision_id))) || review.traceability.some((t) => t.decision_id === d.decision_id);
    if (d.reversibility === "one_way" && !covered) warnings.push(`Irreversible decision ${d.decision_id} has no item and no traceability: review it first`);
    if (!d.consequences_stated.length) warnings.push(`Decision ${d.decision_id} states no consequences`);
  }
  for (const q of review.quality_attributes) if (!q.target) warnings.push(`Attribute ${q.attribute} has no measurable target (${q.scenario.slice(0, 60)})`);
  return { errors, warnings };
}

// ------------------------------------------------------------------ discovery
const ADR_DIR = /(^|\/)(adrs?|decisions?|architecture|arch|rfcs?|design|docs\/(adr|rfc|design|architecture))(\/|$)/i;
const ADR_FILE = /(^|\/)((adr|rfc)[-_ ]?\d+[^/]*|\d{3,4}[-_][^/]+|[^/]*\.(adr|rfc)\.md)$/i;
const SKIP = new Set(["node_modules", ".git", "dist", "build", ".next", "vendor", ".venv", "target", ".terraform", "coverage"]);

function walk(dir, root, out, depth = 0) {
  if (depth > 8 || out.length > 5000) return;
  let entries;
  try { entries = readdirSync(dir, { withFileTypes: true }); } catch { return; }
  for (const e of entries) {
    if (SKIP.has(e.name) || (e.name.startsWith(".") && ![".github", ".gitlab-ci.yml"].includes(e.name))) continue;
    const full = join(dir, e.name);
    if (e.isDirectory()) walk(full, root, out, depth + 1);
    else if (e.isFile()) out.push(posix(relative(root, full)));
  }
}

function parseDoc(path) {
  const text = readFileSync(path, "utf8");
  const title = text.match(/^#\s+(.+)$/m)?.[1]?.trim() ?? basename(path);
  const fm = text.match(/^---\n([\s\S]*?)\n---/)?.[1] ?? "";
  const statusRaw = fm.match(/^status:\s*(.+)$/im)?.[1] ??
    text.match(/^\s*(?:[-*]\s+)?(?:\*\*)?(?:status|estado|situação)(?:\*\*)?\s*[:：]\s*(.+)$/im)?.[1] ??
    text.match(/^#{2,3}\s*(?:status|estado|situação)\s*\n+\s*([^\n#]+)/im)?.[1] ?? "";
  const s = statusRaw.toLowerCase();
  const status = /supersed|substitu/.test(s) ? "superseded" : /deprecat|obsolet/.test(s) ? "deprecated" : /reject|rejeit/.test(s) ? "rejected" :
    /accept|aceit|aprovad|approved|adopted|agreed|implemented|implementad/.test(s) ? "accepted" : /propos|draft|rascunho|review|discuss|pending|pendente|em an[aá]lise|\bwip\b/.test(s) ? "proposed" : "unknown";
  const section = (names) => {
    const re = new RegExp(`^#{2,3}\\s*(?:${names})\\b[^\\n]*\\n([\\s\\S]*?)(?=^#{1,3}\\s|$(?![\\s\\S]))`, "im");
    return text.match(re)?.[1]?.trim() ?? "";
  };
  const decision = section("decisions?|decisão|decisao|decision outcome|chosen option|op[çc][ãa]o escolhida|recommendation|recomenda[çc][ãa]o|proposal|proposta|solution|solução|design");
  const consequences = section("consequences|consequências|consequencias|trade-?offs|implications|implicações|pros and cons|pr[óo]s e contras|impact|impacto");
  const alternatives = section("alternatives|alternativas|options considered|opções consideradas|considered options|considered alternatives|alternativas consideradas|options|opções|opcoes");
  const context = section("context|contexto|problem|problema|motivation|motivação|background|why|por que");
  const date = fm.match(/^date:\s*(.+)$/im)?.[1] ?? text.match(/\b(20\d{2}-\d{2}-\d{2})\b/)?.[1] ?? null;
  const supersededBy = text.match(/(?:supersed(?:ed)? by|replaced by|substitu[íi]d[oa] por)[^\n]*?((?:adr|rfc)[-_ ]?\d+|\d{3,4})/i)?.[1] ?? null;
  return { title, status, date, decision, consequences, alternatives, context, supersededBy, text };
}

const QA_WORDS = {
  availability: /disponibilidade|availability|uptime|\bsla\b|\bslo\b|failover|multi-?az|redund/i,
  latency: /lat[êe]ncia|latency|p9[59]|tempo de resposta|response time/i,
  throughput: /throughput|vaz[ãa]o|\brps\b|\btps\b|requests? per second/i,
  scalability: /escal|scal(e|ing|ability)|autoscal|\bhpa\b|sharding|partition/i,
  security: /seguran|security|\biam\b|rbac|auth|mtls|encrypt|criptograf|secret|\bkms\b/i,
  privacy: /privacidade|privacy|lgpd|gdpr|\bpii\b|dados pessoais/i,
  cost: /custo|cost|finops|budget|or[çc]amento|savings|spot\b/i,
  observability: /observabil|observability|\blogs?\b|tracing|m[ée]tricas|metrics|alert|monitor/i,
  recoverability: /backup|restore|\brpo\b|\brto\b|disaster|\bdr\b|rollback|recupera|recover/i,
  consistency: /consist[êe]ncia|consistency|idempot|exactly-once|at-least-once|transa[çc]/i,
  compliance: /compliance|conformidade|auditoria|audit|regulat|bacen|pci\b|sox\b/i,
  operability: /operabil|runbook|on-?call|deploy|migra[çc]|migration|upgrade|manuten|maintenance/i,
  maintainability: /manutenib|maintainab|acoplamento|coupling|modular|d[íi]vida t[ée]cnica|tech debt/i,
  testability: /testabil|testab|contract test|teste de contrato|e2e|staging|ambiente de teste|test environment/i,
};

export function scanRepo(root) {
  const files = [];
  walk(root, root, files);
  const docs = files.filter((f) => f.endsWith(".md") && (ADR_FILE.test(f) || (ADR_DIR.test(f) && !/readme\.md$/i.test(f) && /\d/.test(basename(f)))))
    .map((f) => {
      const d = parseDoc(join(root, f));
      const kind = /rfc/i.test(f) ? "rfc" : /design|proposal|proposta/i.test(f) ? "proposal" : "adr";
      const attrs = Object.entries(QA_WORDS).filter(([, re]) => re.test(d.text)).map(([k]) => k);
      const issues = [];
      if (d.status === "unknown") issues.push("missing status");
      if (!d.decision) issues.push("decision section not found");
      if (!d.consequences) issues.push("consequences not stated");
      if (!d.alternatives) issues.push("alternatives not recorded");
      if (d.decision && !/\d/.test(d.text.slice(d.text.indexOf(d.decision)))) issues.push("no numeric targets (SLO, cost, capacity)");
      return { path: f, kind, title: d.title, status: d.status, date: d.date, superseded_by: d.supersededBy, quality_attributes: attrs, issues };
    });
  const infra = {
    terraform: files.filter((f) => f.endsWith(".tf")).length,
    kubernetes: files.filter((f) => /\.(ya?ml)$/.test(f) && /^\s*kind:\s*(Deployment|StatefulSet|DaemonSet|Service|Ingress|CronJob|Job)\b/m.test(safeHead(join(root, f)))).length,
    helm: files.filter((f) => basename(f) === "Chart.yaml").length,
    docker: files.filter((f) => /(^|\/)Dockerfile[^/]*$/.test(f)).length,
    ci: files.filter((f) => f.startsWith(".github/workflows/") || f.endsWith(".gitlab-ci.yml") || basename(f) === "Jenkinsfile").length,
    openapi: files.filter((f) => /(openapi|swagger)[^/]*\.(ya?ml|json)$/i.test(f)).length,
  };
  return { root: resolve(root), docs, infra };
}
function safeHead(p) {
  try { return readFileSync(p, "utf8").slice(0, 4000); } catch { return ""; }
}

export function scaffoldReview({ title, sources = [], root = "." }) {
  const review = loadTemplate("architecture-review");
  review.review_id = `arch-${new Date().toISOString().slice(0, 10)}-${createHash("sha1").update(title + sources.join()).digest("hex").slice(0, 6)}`;
  review.title = title;
  review.reviewed_at = new Date().toISOString();
  review.sources = [];
  review.decisions = [];
  review.quality_attributes = [];
  sources.forEach((s, i) => {
    const path = resolve(root, s);
    if (!existsSync(path)) throw new ContractError(`Source not found: ${s}`);
    const d = parseDoc(path);
    const sid = `SRC-${i + 1}`;
    const kind = /rfc/i.test(s) ? "rfc" : /\.tf$/.test(s) ? "iac" : /runbook/i.test(s) ? "runbook" : /design|proposal|proposta/i.test(s) ? "proposal" : "adr";
    review.sources.push({ source_id: sid, kind, locator: s, revision: `sha256:${createHash("sha256").update(readFileSync(path)).digest("hex").slice(0, 16)}`, status: d.status });
    if (d.decision) {
      const first = d.decision.split(/\n\s*\n/)[0].replace(/\s+/g, " ").slice(0, 600);
      review.decisions.push({
        decision_id: `D-${i + 1}`, source_id: sid, statement: first,
        alternatives_considered: d.alternatives ? d.alternatives.split("\n").filter((l) => /^\s*([-*]|\d+\.)\s+/.test(l)).map((l) => l.replace(/^\s*([-*]|\d+\.)\s+/, "").slice(0, 200)).slice(0, 8) : [],
        reversibility: "unknown",
        consequences_stated: d.consequences ? d.consequences.split("\n").filter((l) => /^\s*([-*]|\d+\.)\s+/.test(l)).map((l) => l.replace(/^\s*([-*]|\d+\.)\s+/, "").slice(0, 240)).slice(0, 12) : [],
      });
    }
    for (const [attr, re] of Object.entries(QA_WORDS)) {
      const m = d.text.match(new RegExp(`[^.\\n]*(?:${re.source})[^.\\n]*`, "i"));
      if (m && !m[0].trim().startsWith("#") && !review.quality_attributes.some((q) => q.attribute === attr)) {
        const target = m[0].match(/\d+[.,]?\d*\s*(%|ms|s\b|min|h\b|rps|tps|req\/s|usd|us\$|r\$|gb|tb|x)/i)?.[0] ?? null;
        review.quality_attributes.push({ attribute: attr, scenario: m[0].trim().slice(0, 300), target, stated_by_source: true, source_ref: sid });
      }
    }
    if (i === 0 && d.context) review.context.problem = d.context.replace(/\s+/g, " ").slice(0, 800);
  });
  review.limitations = ["Draft extracted automatically: decisions, attributes and targets need a human or LLM read before any conclusion.", "No risk item was generated automatically."];
  return review;
}

// ------------------------------------------------------------------ outputs
export function architectQuestions(review, format = "md") {
  const open = review.items.filter((i) => i.status === "open" && (i.question_for_architect || ["risk", "contradiction", "gap"].includes(i.kind)))
    .sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity]);
  const blocking = open.filter((i) => ["critical", "high"].includes(i.severity));
  const lines = [];
  const b = format === "slack" ? (s) => `*${s}*` : (s) => `**${s}**`;
  lines.push(`${b(`QA review — ${review.title}`)}`);
  lines.push(`Sources: ${review.sources.map((s) => `${s.locator}${s.revision ? ` (${s.revision})` : ""}`).join(", ")}`);
  lines.push(`Current reading: ${review.summary.verdict}${review.summary.conditions.length ? ` — conditions: ${review.summary.conditions.join("; ")}` : ""}`);
  lines.push("");
  if (blocking.length) lines.push(`${b(`I need your decision on ${blocking.length} point(s) before going on:`)}`);
  open.forEach((i, n) => {
    const ask = i.question_for_architect ?? `Can you confirm how the proposal handles this? ${i.description.split(/(?<=[.!?])\s/)[0]}`;
    lines.push(`${n + 1}. [${i.severity}] ${i.title} (${i.refs.join(", ")})`);
    lines.push(`   ${ask}`);
    if (i.proposed_verification.length) {
      lines.push("   How I plan to verify it:");
      for (const v of i.proposed_verification) lines.push(`   - ${v.kind}: ${v.description}`);
    }
  });
  const testability = review.items.filter((i) => i.kind === "testability_requirement" && i.status === "open");
  if (testability.length) {
    lines.push("");
    lines.push(b("Testability requirements I propose adding to the design:"));
    testability.forEach((i) => lines.push(`- ${i.title}: ${i.description}`));
  }
  lines.push("");
  lines.push("I can turn the answers into verifiable rules and test journeys once we close these points.");
  return lines.join("\n");
}

export function reviewToRules(review) {
  const byId = new Map();
  for (const it of review.items) {
    for (const v of it.proposed_verification) {
      if (!["journey", "contract_test", "monitoring_slo", "fitness_function", "infra_check", "load_test", "chaos_experiment", "security_test"].includes(v.kind)) continue;
      const id = v.linked_rule_id ?? `ARCH-${v.verification_id}`;
      const rule = byId.get(id) ?? {
        rule_id: id,
        description: it.title,
        source: `${review.review_id} · ${it.item_id} · ${it.refs.join(", ")}`,
        scope: it.quality_attribute ? `Attribute ${it.quality_attribute}` : "Architecture decision",
        confidence: ["accepted", "answered", "mitigated"].includes(it.status) ? "confirmed" : "inferred",
        expected_effect: "",
      };
      rule.expected_effect = [rule.expected_effect, `${v.kind}: ${v.description}`].filter(Boolean).join(" | ").slice(0, 1500);
      byId.set(id, rule);
    }
  }
  return [...byId.values()];
}

// Labels follow the review language (`--lang`): Portuguese on request, English by default.
const REVIEW_LABELS = {
  en: {
    html: "en", reviewer: "reviewer", architect: "architect", reading: "QA reading", topRisks: "Top risks", conditions: "Conditions",
    invalid: (n) => `Invalid contract (${n})`, valid: "Valid contract", warnings: (n) => `${n} warning(s)`,
    context: "Context", scope: "Scope", nonGoals: "Out of scope", sources: "Sources", decisions: "Decisions",
    consequences: "Stated consequences", noConsequences: "No consequences stated.", noDecisions: "No decision extracted.",
    qualities: "Quality attributes", attribute: "Attribute", scenario: "Scenario", target: "Target", source: "Source", noTarget: "no target",
    inferred: "inferred by QA", none: "None.", items: "Review items", question: "Question for the architect", answer: "Answer",
    automatable: "automatable", noItems: "No items.", assumptions: "Assumptions", ifFalse: "if false", noAssumptions: "None recorded.",
    traceability: "Traceability and conformance", decision: "Decision", rules: "Rules", journeys: "Journeys", verifications: "Verifications",
    conformance: "Conformance", limitations: "Limitations",
  },
  pt: {
    html: "pt-BR", reviewer: "revisor", architect: "arquiteto", reading: "Leitura de QA", topRisks: "Principais riscos", conditions: "Condições",
    invalid: (n) => `Contrato inválido (${n})`, valid: "Contrato válido", warnings: (n) => `${n} aviso(s)`,
    context: "Contexto", scope: "Escopo", nonGoals: "Fora de escopo", sources: "Fontes", decisions: "Decisões",
    consequences: "Consequências declaradas", noConsequences: "Sem consequências declaradas.", noDecisions: "Nenhuma decisão extraída.",
    qualities: "Atributos de qualidade", attribute: "Atributo", scenario: "Cenário", target: "Meta", source: "Fonte", noTarget: "sem meta",
    inferred: "inferido pelo QA", none: "Nenhum.", items: "Itens da revisão", question: "Pergunta ao arquiteto", answer: "Resposta",
    automatable: "automatizável", noItems: "Nenhum item.", assumptions: "Premissas", ifFalse: "se falsa", noAssumptions: "Nenhuma registrada.",
    traceability: "Rastreabilidade e conformidade", decision: "Decisão", rules: "Regras", journeys: "Jornadas", verifications: "Verificações",
    conformance: "Conformidade", limitations: "Limitações",
  },
};

export function renderReview(review, validation, lang = "en") {
  const L = REVIEW_LABELS[lang] ?? REVIEW_LABELS.en;
  const pill = (v) => `<span class="pill p-${esc(v)}">${esc(v)}</span>`;
  const items = [...review.items].sort((a, b) => SEV_ORDER[a.severity] - SEV_ORDER[b.severity]);
  const verdictTone = { proceed: "ok", proceed_with_conditions: "warn", revise: "warn", block: "bad", not_assessed: "muted" }[review.summary.verdict];
  return `<!doctype html><html lang="${L.html}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>${esc(review.title)}</title><style>
:root{--bg:#f6f7f9;--paper:#fff;--ink:#16202a;--muted:#5b6672;--line:#d8dee5;--ok:#0f6b3f;--okbg:#e3f4ea;--bad:#a1271c;--badbg:#fbe6e3;--warn:#8a5a00;--warnbg:#fdf1d8}
@media (prefers-color-scheme:dark){:root{--bg:#0f1418;--paper:#172027;--ink:#e6edf3;--muted:#9aa7b4;--line:#2b3742;--ok:#6fd49c;--okbg:#123222;--bad:#ff9b8f;--badbg:#3a1714;--warn:#f2c46d;--warnbg:#382a0d}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}main{max-width:1180px;margin:0 auto;padding:28px 16px 64px}
section{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:18px;margin:16px 0}h1{font-size:1.6rem;margin:0 0 6px}h2{font-size:1.1rem;margin:0 0 10px}.muted{color:var(--muted)}
.pill{display:inline-block;border-radius:999px;padding:0 9px;font-size:.8rem;font-weight:650;background:var(--line)}.p-critical,.p-block,.p-drift{background:var(--badbg);color:var(--bad)}.p-high,.p-revise{background:var(--warnbg);color:var(--warn)}.p-proceed,.p-conforms,.p-mitigated,.p-accepted{background:var(--okbg);color:var(--ok)}
.verdict{font-size:1.2rem;font-weight:700}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:720px}th,td{border-top:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}th{font-size:.8rem;color:var(--muted)}
.item{border-left:4px solid var(--line);padding:2px 0 2px 12px;margin:14px 0}.item.critical{border-color:var(--bad)}.item.high{border-color:var(--warn)}code{font:12.5px ui-monospace,Menlo,monospace}blockquote{margin:6px 0;padding:6px 10px;border-left:3px solid var(--line);color:var(--muted)}
</style></head><body><main>
<h1>${esc(review.title)}</h1><p class="muted">${esc(review.review_id)} · ${esc(review.status)} · ${esc(review.reviewed_at)} · ${L.reviewer} ${esc(review.reviewer.name)}${review.reviewer.model ? ` (${esc(review.reviewer.model)})` : ""}${review.architect.name ? ` · ${L.architect} ${esc(review.architect.name)}` : ""}</p>
<section><p class="verdict ${verdictTone}">${L.reading}: ${esc(review.summary.verdict)}</p><p>${esc(review.summary.rationale)}</p>
${review.summary.top_risks.length ? `<p><strong>${L.topRisks}</strong></p><ul>${review.summary.top_risks.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
${review.summary.conditions.length ? `<p><strong>${L.conditions}</strong></p><ul>${review.summary.conditions.map((r) => `<li>${esc(r)}</li>`).join("")}</ul>` : ""}
${validation ? `<p class="${validation.errors.length ? "bad" : "ok"}">${validation.errors.length ? L.invalid(validation.errors.length) : L.valid}${validation.warnings.length ? ` · ${L.warnings(validation.warnings.length)}` : ""}</p>${[...validation.errors, ...validation.warnings].map((w) => `<div class="muted">• ${esc(w)}</div>`).join("")}` : ""}</section>
<section><h2>${L.context}</h2><p>${esc(review.context.problem)}</p><p class="muted">${L.scope}: ${esc(review.context.scope)}</p>${review.context.non_goals.length ? `<p class="muted">${L.nonGoals}: ${review.context.non_goals.map(esc).join("; ")}</p>` : ""}
<p class="muted">${L.sources}: ${review.sources.map((s) => `<code>${esc(s.source_id)}</code> ${esc(s.kind)} ${esc(s.locator)} ${pill(s.status)}`).join(" · ")}</p></section>
<section><h2>${L.decisions}</h2>${review.decisions.map((d) => `<p><code>${esc(d.decision_id)}</code> ${pill(d.reversibility)} ${esc(d.statement)}</p>${d.consequences_stated.length ? `<blockquote>${L.consequences}: ${d.consequences_stated.map(esc).join("; ")}</blockquote>` : `<blockquote>${L.noConsequences}</blockquote>`}`).join("") || `<p class="muted">${L.noDecisions}</p>`}</section>
<section><h2>${L.qualities}</h2><div class="scroll"><table><thead><tr><th>${L.attribute}</th><th>${L.scenario}</th><th>${L.target}</th><th>${L.source}</th></tr></thead><tbody>${review.quality_attributes.map((q) => `<tr><td>${esc(q.attribute)}</td><td>${esc(q.scenario)}</td><td>${q.target ? esc(q.target) : `<span class="warn">${L.noTarget}</span>`}</td><td>${q.stated_by_source ? esc(q.source_ref) : L.inferred}</td></tr>`).join("") || `<tr><td colspan="4" class="muted">${L.none}</td></tr>`}</tbody></table></div></section>
<section><h2>${L.items}</h2>${items.map((i) => `<div class="item ${esc(i.severity)}"><p><strong>${esc(i.item_id)} · ${esc(i.title)}</strong> ${pill(i.kind)} ${pill(i.severity)} ${pill(i.status)} <span class="muted">${esc(i.refs.join(", "))}${i.quality_attribute ? ` · ${esc(i.quality_attribute)}` : ""}</span></p><p>${esc(i.description)}</p>
${i.question_for_architect ? `<blockquote>${L.question}: ${esc(i.question_for_architect)}</blockquote>` : ""}${i.answer ? `<blockquote>${L.answer}: ${esc(i.answer)}</blockquote>` : ""}
${i.proposed_verification.length ? `<ul>${i.proposed_verification.map((v) => `<li><code>${esc(v.verification_id)}</code> ${esc(v.kind)}${v.automatable ? ` · ${L.automatable}` : ""}: ${esc(v.description)}${v.linked_rule_id ? ` → <code>${esc(v.linked_rule_id)}</code>` : ""}</li>`).join("")}</ul>` : ""}</div>`).join("") || `<p class="muted">${L.noItems}</p>`}</section>
<section><h2>${L.assumptions}</h2>${review.assumptions.map((a) => `<p><code>${esc(a.assumption_id)}</code> ${pill(a.validated)} ${esc(a.statement)} <span class="muted">— ${L.ifFalse}: ${esc(a.risk_if_false)}</span></p>`).join("") || `<p class="muted">${L.noAssumptions}</p>`}</section>
<section><h2>${L.traceability}</h2><div class="scroll"><table><thead><tr><th>${L.decision}</th><th>${L.rules}</th><th>${L.journeys}</th><th>${L.verifications}</th><th>${L.conformance}</th></tr></thead><tbody>${review.decisions.map((d) => {
    const t = review.traceability.find((x) => x.decision_id === d.decision_id);
    const c = review.conformance.find((x) => x.decision_id === d.decision_id);
    return `<tr><td><code>${esc(d.decision_id)}</code></td><td>${esc(t?.rule_ids.join(", ") ?? "—")}</td><td>${esc(t?.journey_ids.join(", ") ?? "—")}</td><td>${esc(t?.verification_ids.join(", ") ?? "—")}</td><td>${c ? `${pill(c.status)} ${esc(c.note)}` : pill("not_verified")}</td></tr>`;
  }).join("")}</tbody></table></div></section>
<section><h2>${L.limitations}</h2><ul>${review.limitations.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></section>
</main></body></html>
`;
}

// ------------------------------------------------------------------ infra: terraform
const STATEFUL = /^(aws_(db_instance|rds_cluster|rds_cluster_instance|s3_bucket|dynamodb_table|efs_file_system|elasticache_(cluster|replication_group)|ebs_volume|kms_key|docdb_cluster|neptune_cluster|opensearch_domain|elasticsearch_domain|msk_cluster|redshift_cluster|backup_vault|secretsmanager_secret)|google_(sql_database_instance|storage_bucket|bigquery_dataset|spanner_instance|kms_crypto_key)|azurerm_(.*database.*|storage_account|cosmosdb_account|key_vault))$/;
const TAGGABLE = /^aws_(instance|db_instance|rds_cluster|s3_bucket|eks_cluster|eks_node_group|lb|alb|nat_gateway|ebs_volume|elasticache_.*|dynamodb_table|lambda_function|ecs_service|autoscaling_group|launch_template|efs_file_system|opensearch_domain|msk_cluster)$/;

function policyStatements(doc) {
  try {
    const p = typeof doc === "string" ? JSON.parse(doc) : doc;
    return [].concat(p?.Statement ?? []);
  } catch { return []; }
}

export function analyzeTerraformPlan(plan) {
  if (!plan || !Array.isArray(plan.resource_changes)) throw new ContractError("Input does not look like `terraform show -json PLAN` (resource_changes missing).");
  const findings = [];
  const add = (severity, address, rule, message, recommendation) => findings.push({ severity, address, rule, message, recommendation });
  const counts = { create: 0, update: 0, delete: 0, replace: 0, "no-op": 0, read: 0 };
  for (const rc of plan.resource_changes) {
    const actions = rc.change?.actions ?? [];
    const kind = actions.includes("delete") && actions.includes("create") ? "replace" : actions[0] ?? "no-op";
    counts[kind] = (counts[kind] ?? 0) + 1;
    const a = rc.change?.after ?? {};
    const before = rc.change?.before ?? {};
    const t = rc.type;
    if ((kind === "delete" || kind === "replace") && STATEFUL.test(t)) {
      add("critical", rc.address, "stateful-destroy", `${kind === "replace" ? "Replacement" : "Removal"} of a stateful resource (${t}).`,
        `Confirm a verified backup/snapshot, a window and a rollback plan; consider lifecycle.prevent_destroy. Attributes forcing replacement: ${(rc.change?.replace_paths ?? []).map((p) => p.join(".")).join(", ") || "see plan"}.`);
    } else if (kind === "replace") add("medium", rc.address, "replace", `Resource will be recreated (${t}).`, "Check downtime impact and dependencies (create_before_destroy?).");
    if (kind === "delete" || kind === "no-op" || kind === "read") continue;
    if (t === "aws_security_group_rule" || t === "aws_vpc_security_group_ingress_rule" || t === "aws_security_group") {
      const rules = t === "aws_security_group" ? [].concat(a.ingress ?? []) : [a];
      for (const r of rules) {
        if (t !== "aws_security_group" && r.type && r.type !== "ingress") continue;
        const cidrs = [].concat(r.cidr_blocks ?? [], r.cidr_ipv4 ?? [], r.ipv6_cidr_blocks ?? [], r.cidr_ipv6 ?? []);
        if (cidrs.some((c) => c === "0.0.0.0/0" || c === "::/0")) {
          const from = r.from_port ?? 0;
          const to = r.to_port ?? 65535;
          const web = from >= 80 && to <= 443 && [80, 443].includes(from) && [80, 443].includes(to);
          add(web ? "low" : "high", rc.address, "open-ingress", `Ingress open to the Internet on ports ${from}-${to}.`, web ? "Expected only for a public edge (LB/CDN); confirm." : "Restrict the CIDR or use a source SG/PrivateLink.");
        }
      }
    }
    if (t === "aws_db_instance" || t === "aws_rds_cluster") {
      if (a.publicly_accessible === true) add("high", rc.address, "db-public", "Database publicly accessible.", "Turn off publicly_accessible; reach it over a private network.");
      if (a.storage_encrypted === false) add("high", rc.address, "db-unencrypted", "Database storage not encrypted.", "storage_encrypted = true with a managed KMS key.");
      if (a.deletion_protection === false) add("medium", rc.address, "db-no-deletion-protection", "Database without deletion_protection.", "Turn it on outside ephemeral environments.");
      if (a.skip_final_snapshot === true) add("medium", rc.address, "db-skip-final-snapshot", "skip_final_snapshot = true.", "Keep a final snapshot outside ephemeral environments.");
      if ((a.backup_retention_period ?? 1) === 0) add("high", rc.address, "db-no-backup", "Automatic backups turned off.", "backup_retention_period ≥ 7 according to the RPO.");
      if (t === "aws_db_instance" && a.multi_az === false) add("low", rc.address, "db-single-az", "Single-AZ instance.", "Confirm the availability target accepts single-AZ.");
    }
    if (t === "aws_ebs_volume" && a.encrypted === false) add("high", rc.address, "ebs-unencrypted", "EBS volume not encrypted.", "encrypted = true.");
    if (t === "aws_s3_bucket_public_access_block") {
      for (const k of ["block_public_acls", "block_public_policy", "ignore_public_acls", "restrict_public_buckets"]) {
        if (a[k] === false) add("high", rc.address, "s3-public-access", `${k} = false.`, "Keep public access blocked unless a static site justifies it.");
      }
    }
    if ((t === "aws_s3_bucket_acl" && /public/.test(a.acl ?? "")) || (t === "aws_s3_bucket" && /public/.test(a.acl ?? ""))) add("high", rc.address, "s3-public-acl", `Public ACL (${a.acl}).`, "Use OAC/CloudFront or signed URLs.");
    if (/^aws_iam_(role_)?policy$|^aws_iam_(user|group)_policy$/.test(t) || t === "aws_iam_policy_document") {
      for (const st of policyStatements(a.policy ?? a.json)) {
        if (st.Effect !== "Allow") continue;
        const acts = [].concat(st.Action ?? []);
        const res = [].concat(st.Resource ?? []);
        if (acts.includes("*") || acts.some((x) => /^[a-z0-9-]+:\*$/.test(x))) add(acts.includes("*") ? "critical" : "high", rc.address, "iam-wildcard-action", `Allow with a wildcard action (${acts.filter((x) => x.includes("*")).join(", ")}).`, "Least privilege: list the actions needed.");
        else if (res.includes("*") && acts.some((x) => !/^(logs|cloudwatch|xray|ec2:Describe|sts:GetCallerIdentity)/.test(x))) add("medium", rc.address, "iam-wildcard-resource", "Allow on Resource \"*\".", "Restrict the ARNs.");
      }
    }
    if (t === "aws_eks_cluster") {
      const vpc = [].concat(a.vpc_config ?? [])[0] ?? {};
      if (vpc.endpoint_public_access !== false && [].concat(vpc.public_access_cidrs ?? ["0.0.0.0/0"]).includes("0.0.0.0/0")) add("medium", rc.address, "eks-public-endpoint", "EKS API endpoint public to 0.0.0.0/0.", "Restrict public_access_cidrs or use a private endpoint.");
      if (!(a.enabled_cluster_log_types ?? []).length) add("low", rc.address, "eks-no-control-plane-logs", "Control plane logs turned off.", "Turn on at least audit and authenticator.");
    }
    if (t === "aws_lb" && a.internal === false && a.drop_invalid_header_fields === false) add("low", rc.address, "alb-headers", "Public ALB without drop_invalid_header_fields.", "Turn it on to mitigate request smuggling.");
    if (TAGGABLE.test(t) && kind === "create" && !Object.keys(a.tags_all ?? a.tags ?? {}).length) add("low", rc.address, "finops-untagged", "Resource without tags (cost allocation).", "Apply the mandatory tags (owner, cost-center, env) through default_tags.");
    if (t === "aws_instance" && /\.(metal|24xlarge|32xlarge|48xlarge)$/.test(a.instance_type ?? "")) add("medium", rc.address, "finops-large-instance", `Large instance (${a.instance_type}).`, "Confirm the sizing and usage commitment.");
    if (t === "aws_nat_gateway" && kind === "create") add("info", rc.address, "finops-nat", "New NAT Gateway (fixed cost plus per GB).", "Consider VPC endpoints for S3/DynamoDB/ECR traffic.");
    void before;
  }
  findings.sort((x, y) => SEV_ORDER[x.severity] - SEV_ORDER[y.severity]);
  return { kind: "terraform_plan", terraform_version: plan.terraform_version ?? null, counts, findings };
}

// ------------------------------------------------------------------ infra: kubernetes
function k8sObjects(input) {
  if (Array.isArray(input)) return input.flatMap(k8sObjects);
  if (input?.kind === "List" && Array.isArray(input.items)) return input.items.flatMap(k8sObjects);
  return input?.kind ? [input] : [];
}

export function analyzeK8s(input) {
  const objs = k8sObjects(input);
  if (!objs.length) throw new ContractError("No Kubernetes object. Convert YAML to JSON: `kubectl create --dry-run=client -o json -f file.yaml` or `yq -o=json`.");
  const findings = [];
  const add = (severity, ref, rule, message, recommendation) => findings.push({ severity, address: ref, rule, message, recommendation });
  const pdbs = objs.filter((o) => o.kind === "PodDisruptionBudget");
  const hpas = objs.filter((o) => o.kind === "HorizontalPodAutoscaler" || o.kind === "ScaledObject");
  for (const o of objs) {
    const ref = `${o.kind}/${o.metadata?.namespace ? `${o.metadata.namespace}/` : ""}${o.metadata?.name ?? "?"}`;
    const workload = ["Deployment", "StatefulSet", "DaemonSet", "Job", "CronJob", "Pod", "ReplicaSet"].includes(o.kind);
    if (!workload) {
      if (o.kind === "Service" && o.spec?.type === "LoadBalancer" && !Object.keys(o.metadata?.annotations ?? {}).some((k) => /internal|scheme/.test(k))) add("medium", ref, "public-lb", "LoadBalancer Service without an internal-scheme annotation (likely public exposure).", "Confirm the intent; for internal traffic use the provider annotation.");
      if (o.kind === "Secret" && o.data && !o.metadata?.annotations?.["sealedsecrets.bitnami.com/managed"]) add("high", ref, "secret-in-manifest", "Secret with data in a versioned manifest.", "Use External Secrets/Sealed Secrets/CSI; never version the values.");
      continue;
    }
    if (!o.metadata?.namespace && o.kind !== "Pod") add("low", ref, "no-namespace", "No explicit namespace.", "Declare the namespace to avoid deploying into default.");
    const podSpec = o.kind === "CronJob" ? o.spec?.jobTemplate?.spec?.template?.spec : o.kind === "Pod" ? o.spec : o.spec?.template?.spec;
    if (!podSpec) continue;
    const batch = ["Job", "CronJob"].includes(o.kind);
    if (podSpec.hostNetwork) add("high", ref, "host-network", "hostNetwork enabled.", "Remove it unless justified (CNI, agents).");
    if ((podSpec.volumes ?? []).some((v) => v.hostPath)) add("high", ref, "host-path", "hostPath volume mounted.", "Use managed volumes; hostPath breaks isolation.");
    if (podSpec.automountServiceAccountToken !== false && !podSpec.serviceAccountName) add("low", ref, "default-sa-token", "Uses the default ServiceAccount with a mounted token.", "Set a dedicated SA and automountServiceAccountToken: false if it does not use the API.");
    const containers = [...(podSpec.containers ?? []), ...(podSpec.initContainers ?? []).map((c) => ({ ...c, __init: true }))];
    for (const c of containers) {
      const cref = `${ref}#${c.name}`;
      const img = c.image ?? "";
      const tagless = !img.includes("@") && (!/:[^/]+$/.test(img) || /:latest$/.test(img));
      if (tagless) add("high", cref, "image-latest", `Image without a pinned version (${img}).`, "Pin an immutable tag or digest (@sha256).");
      if (!c.resources?.requests?.cpu || !c.resources?.requests?.memory) add("medium", cref, "no-requests", "No CPU/memory requests.", "Set requests for predictable scheduling and capacity.");
      if (!c.resources?.limits?.memory) add("medium", cref, "no-memory-limit", "No memory limit.", "Set limits.memory (predictable OOM, no harm to neighbours).");
      if (!c.__init && !batch) {
        if (!c.readinessProbe) add("medium", cref, "no-readiness", "No readinessProbe.", "Without readiness, rollouts and Services cannot tell when the process is ready.");
        if (!c.livenessProbe && !c.startupProbe) add("low", cref, "no-liveness", "No liveness/startup probe.", "Consider a conservative liveness probe for hangs.");
      }
      const sc = { ...(podSpec.securityContext ?? {}), ...(c.securityContext ?? {}) };
      if (c.securityContext?.privileged) add("critical", cref, "privileged", "Privileged container.", "Remove privileged; use minimal capabilities.");
      if (c.securityContext?.allowPrivilegeEscalation !== false) add("medium", cref, "priv-escalation", "allowPrivilegeEscalation is not false.", "Set allowPrivilegeEscalation: false.");
      if (sc.runAsNonRoot !== true && !(sc.runAsUser > 0)) add("medium", cref, "run-as-root", "May run as root.", "runAsNonRoot: true and runAsUser > 0.");
      if (c.securityContext?.readOnlyRootFilesystem !== true) add("low", cref, "writable-rootfs", "Writable root filesystem.", "readOnlyRootFilesystem: true with explicit volumes for writes.");
      for (const e of c.env ?? []) {
        if (e.value && /(pass(word)?|secret|token|api[_-]?key|private[_-]?key|credential)/i.test(e.name)) add("high", cref, "secret-in-env", `Literal secret in env (${e.name}).`, "Use secretKeyRef/External Secrets.");
      }
    }
    if (o.kind === "Deployment" || o.kind === "StatefulSet") {
      const replicas = o.spec?.replicas ?? 1;
      const hpa = hpas.find((h) => h.spec?.scaleTargetRef?.name === o.metadata?.name);
      const min = hpa?.spec?.minReplicas ?? hpa?.spec?.minReplicaCount ?? replicas;
      if (min === 0) add("medium", ref, "scale-to-zero", "Scales to zero replicas (KEDA).", "Measure cold start against the latency target; consider a minimum ≥ 1 at peak hours.");
      else if (min < 2) add("medium", ref, "single-replica", `Only ${min} minimum replica(s).`, "≥ 2 replicas for availability during deploys and node failures.");
      const labels = o.spec?.template?.metadata?.labels ?? {};
      const hasPdb = pdbs.some((p) => Object.entries(p.spec?.selector?.matchLabels ?? {}).every(([k, v]) => labels[k] === v) && Object.keys(p.spec?.selector?.matchLabels ?? {}).length);
      if (min >= 2 && !hasPdb) add("low", ref, "no-pdb", "No matching PodDisruptionBudget.", "A PDB prevents downtime during node drains and upgrades.");
      if (min >= 2 && !podSpec.topologySpreadConstraints && !podSpec.affinity?.podAntiAffinity) add("low", ref, "no-spread", "No topologySpread/antiAffinity.", "Spread replicas across nodes and zones.");
      if (o.kind === "Deployment" && o.spec?.strategy?.type === "Recreate") add("medium", ref, "recreate-strategy", "Recreate strategy causes downtime on deploy.", "Use RollingUpdate unless a constraint justifies it.");
    }
  }
  findings.sort((x, y) => SEV_ORDER[x.severity] - SEV_ORDER[y.severity]);
  return { kind: "kubernetes", objects: objs.length, by_kind: objs.reduce((m, o) => ({ ...m, [o.kind]: (m[o.kind] ?? 0) + 1 }), {}), findings };
}

export const CHECKLIST = {
  resilience: ["Failure modes per dependency (timeout, retry with backoff and jitter, circuit breaker, bulkhead)", "Idempotent side-effecting operations with a deduplication key", "Degraded behaviour defined and communicated to the user", "Capacity: expected peak × margin; load tests with an exit criterion", "Multi-AZ/region per the target; failover exercised, not only designed"],
  data: ["Explicit RPO/RTO and a tested restore (not only a backup)", "Reversible or expand-contract schema migrations; legacy data", "Consistency across services (outbox, sagas, compensation)", "Retention, LGPD/GDPR (legal basis, minimisation, deletion) and data residency", "Versioned event contracts and backward compatibility"],
  security: ["Threat model of the change (STRIDE) and exposed surface", "Least privilege (IAM/RBAC), secrets out of the code, rotation", "Encryption in transit and at rest; managed keys", "Tenant isolation verifiable by a test", "Supply chain: images pinned by digest, SBOM, scanning"],
  observability: ["SLIs/SLOs per critical journey and an error budget", "Structured logs with correlation (trace id) and no PII", "Actionable alerts linked to runbooks", "Dashboards per journey, not only per resource", "Telemetry to validate the consequences stated in the ADR"],
  delivery: ["Feature flags and a kill switch for the change", "Deployment strategy (canary/blue-green) with an automatic rollback criterion", "Tested rollback plan and reversibility window", "Representative test environments (data, integrations, scale)", "Contract tests between producers and consumers"],
  cost: ["Unit cost (per transaction/user/tenant) estimated and monitored", "Mandatory allocation tags and a budget with an alert", "Sizing and autoscaling with limits", "Cross-AZ/egress/NAT traffic assessed", "Commitments (Savings Plans/RI/Spot) compatible with the reversibility of the decision"],
  governance: ["ADR with status, alternatives, consequences and a review criterion", "One-way-door decisions with approval and an exit plan", "Automated fitness functions in CI for the critical decisions", "An owner and a reassessment date for each accepted risk", "Post-implementation conformance: does the runtime respect the ADR?"],
};

// ------------------------------------------------------------------ CLI
function record(runDir, kind, summary, content, ext) {
  if (!runDir) return null;
  return appendEvidence(runDir, { kind, origin: "observed", captured_by: "tool", summary, content, ext }).evidence_id;
}

export async function cmdArch({ pos, flags }, { out, runDirFrom, langFrom = () => "en" }) {
  const sub = pos[0];
  if (sub === "scan") {
    const r = scanRepo(pos[1] ?? ".");
    const lines = [`ADRs/RFCs/proposals: ${r.docs.length} · infra: terraform ${r.infra.terraform} · k8s ${r.infra.kubernetes} · helm ${r.infra.helm} · docker ${r.infra.docker} · CI ${r.infra.ci} · openapi ${r.infra.openapi}`];
    for (const d of r.docs) lines.push(`- [${d.status}] ${d.path} — ${d.title}${d.quality_attributes.length ? ` · ${d.quality_attributes.join(",")}` : ""}${d.issues.length ? `\n    ! ${d.issues.join("; ")}` : ""}`);
    const proposed = r.docs.filter((d) => d.status === "proposed");
    if (proposed.length) lines.push(`\nReview proactively (proposed status): ${proposed.map((d) => d.path).join(", ")}`);
    if (r.infra.terraform) lines.push("Terraform found: run `terraform show -json PLAN > plan.json && jqa infra terraform plan.json` on every change.");
    if (r.infra.kubernetes || r.infra.helm) lines.push("Kubernetes found: `kubectl create --dry-run=client -o json -f DIR > k8s.json && jqa infra k8s k8s.json` (or `helm template … | yq -o=json`).");
    out(flags, r, lines.join("\n"));
    return 0;
  }
  if (sub === "init") {
    if (!flags.title) throw new ContractError("Usage: arch init --title \"Title\" [--source ADR.md ...] [--out review.json]", "bad_args");
    const sources = [].concat(flags.source ?? []).flatMap((s) => String(s).split(","));
    const review = scaffoldReview({ title: flags.title, sources, root: flags.root ?? "." });
    const target = flags.out ?? `${review.review_id}.json`;
    mkdirSync(dirname(resolve(target)), { recursive: true });
    writeFileSync(target, `${JSON.stringify(review, null, 2)}\n`, { flag: flags.force ? "w" : "wx" });
    out(flags, { created: target, decisions: review.decisions.length, quality_attributes: review.quality_attributes.length },
      `review created: ${target}\n${review.decisions.length} decision(s) and ${review.quality_attributes.length} attribute(s) extracted, to confirm. Fill in items, assumptions and verdict; then \`jqa arch validate ${target}\`.`);
    return 0;
  }
  const file = pos[1];
  if (!file) throw new ContractError("Usage: arch validate|render [--lang pt|en]|questions|rules REVIEW.json", "bad_args");
  const review = readJson(file);
  if (sub === "validate") {
    const v = validateReview(review);
    out(flags, { valid: !v.errors.length, ...v }, `${v.errors.length ? "INVALID" : "VALID"} review ${file}${v.errors.map((e) => `\n✗ ${e}`).join("")}${v.warnings.map((w) => `\n! ${w}`).join("")}`);
    return v.errors.length ? 2 : 0;
  }
  if (sub === "render") {
    const lang = langFrom(flags);
    const v = validateReview(review);
    const target = flags.out ?? (file.endsWith(".json") ? file.replace(/\.json$/, ".html") : `${file}.html`);
    if (resolve(target) === resolve(file)) throw new ContractError("The HTML must not overwrite the review.", "bad_args");
    mkdirSync(dirname(resolve(target)), { recursive: true });
    writeFileSync(target, renderReview(review, v, lang));
    out(flags, { created: target, valid: !v.errors.length }, `review HTML: ${resolve(target)}`);
    return 0;
  }
  if (sub === "questions") {
    const text = architectQuestions(review, flags.format ?? "md");
    if (flags.out) {
      mkdirSync(dirname(resolve(flags.out)), { recursive: true });
      writeFileSync(flags.out, `${text}\n`);
    }
    out(flags, { text }, text);
    return 0;
  }
  if (sub === "rules") {
    const rules = reviewToRules(review);
    if (flags.merge) {
      const profile = readJson(flags.merge);
      const have = new Set(profile.business_rules.map((r) => r.rule_id));
      const added = rules.filter((r) => !have.has(r.rule_id));
      profile.business_rules.push(...added);
      const errors = validateDocument(profile, "product-profile");
      if (errors.length) throw new ContractError(errors.join("\n"));
      writeFileSync(flags.merge, `${JSON.stringify(profile, null, 2)}\n`);
      out(flags, { merged: added.length, into: flags.merge }, `${added.length} architecture rule(s) added to ${flags.merge}`);
      return 0;
    }
    out(flags, rules, JSON.stringify(rules, null, 2));
    return 0;
  }
  throw new ContractError(`Unknown subcommand: arch ${sub}`, "bad_args");
}

export async function cmdInfra({ pos, flags }, { out, runDirFrom }) {
  const sub = pos[0];
  if (sub === "checklist") {
    const topics = flags.topic && flags.topic !== "all" ? [flags.topic] : Object.keys(CHECKLIST);
    for (const t of topics) if (!CHECKLIST[t]) throw new ContractError(`Unknown topic: ${t}. Use ${Object.keys(CHECKLIST).join(", ")} or all.`, "bad_args");
    const text = topics.map((t) => `## ${t}\n${(CHECKLIST[t] ?? []).map((i) => `- [ ] ${i}`).join("\n")}`).join("\n\n");
    out(flags, Object.fromEntries(topics.map((t) => [t, CHECKLIST[t]])), text);
    return 0;
  }
  const file = pos[1];
  if (!["terraform", "k8s"].includes(sub) || !file) throw new ContractError("Usage: infra terraform plan.json | infra k8s objects.json | infra checklist [--topic T]", "bad_args");
  if ([".yaml", ".yml"].includes(extname(file))) throw new ContractError("YAML input: convert it to JSON (`kubectl create --dry-run=client -o json -f X.yaml` or `yq -o=json X.yaml`).", "bad_args");
  const data = readJson(file);
  const result = sub === "terraform" ? analyzeTerraformPlan(data) : analyzeK8s(data);
  const runDir = runDirFrom(flags, false);
  const text = [
    sub === "terraform" ? `plan: ${Object.entries(result.counts).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join(" · ")}` : `objects: ${result.objects} (${Object.entries(result.by_kind).map(([k, n]) => `${k} ${n}`).join(", ")})`,
    ...result.findings.map((f) => `[${f.severity}] ${f.address} · ${f.rule}: ${f.message}\n    → ${f.recommendation}`),
    result.findings.length ? "" : "no findings from the static rules (not a substitute for review)",
    "Static analysis of the artifact; it runs and applies nothing.",
  ].join("\n");
  const ev = record(runDir, "tool_result", `infra ${sub}: ${result.findings.length} finding(s) in ${basename(file)}`, `${text}\n\n${JSON.stringify(result, null, 2)}`, "txt");
  out(flags, { ...result, evidence_id: ev, source: file, statSize: statSync(file).size }, `${text}${ev ? `\n# evidence ${ev}` : ""}`);
  const failOn = flags["fail-on"];
  if (failOn && SEV_ORDER[failOn] === undefined) throw new ContractError(`invalid --fail-on: ${failOn}. Use critical|high|medium|low|info.`, "bad_args");
  if (failOn && result.findings.some((f) => SEV_ORDER[f.severity] <= SEV_ORDER[failOn])) return 1;
  return 0;
}
