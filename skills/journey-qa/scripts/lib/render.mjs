// Self-contained HTML rendering for run reports. Every field is escaped;
// screenshots from the run directory are embedded as data: URIs (size-capped).
import { existsSync, readFileSync, statSync } from "node:fs";
import { extname, join, normalize, relative } from "node:path";
import { VALIDATION_LIMIT } from "./report.mjs";

const esc = (v) =>
  String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);

const TONE = {
  passed: "ok", achieved: "ok", completed: "ok",
  failed: "bad", not_achieved: "bad", error: "bad", aborted: "bad",
  inconclusive: "warn", partial: "warn", abandoned: "warn", undetermined: "muted",
  blocked: "warn", not_evaluated: "muted", not_applicable: "muted",
};
// Labels follow the report language (`--lang`): Portuguese on request, English by default.
const LABELS = {
  en: {
    html: "en", content: "Content", mandatoryTag: "mandatory", expected: "Expected", observed: "Observed", impact: "Impact", reproduction: "Reproduction",
    noFindings: "No findings recorded.", notGiven: "not given", actor: "actor", noEvidence: "No evidence recorded.",
    ledgerOk: "intact", ledgerBad: "<strong>broken</strong>", entries: "entries", noLedger: "No ledger: evidence declared by the agent.",
    model: "model", adapter: "adapter", noHost: "Host not given (report 0.2)",
    invalid: (n) => `Invalid contract: ${n} error(s)`, valid: "Valid contract", warnings: (n) => `${n} warning(s)`,
    report: "report", run: "Run", verdict: "QA verdict", mode: "Mode", isolation: "Isolation", context: "context", browser: "browser", unchecked: "not checked",
    scope: "Scope", mandatory: "Mandatory:", none: "none", exclusions: "Exclusions:", byActor: "Outcome by actor",
    checks: "Checks", rule: "Rule", status: "Status", basis: "Basis", noChecks: "No check started.",
    findings: "Findings", limitations: "Limitations", noLimitations: "No limitation declared.", evidence: "Evidence",
    record: "Full record", reportJson: "Report JSON", limit: VALIDATION_LIMIT,
  },
  pt: {
    html: "pt-BR", content: "Conteúdo", mandatoryTag: "obrigatória", expected: "Esperado", observed: "Observado", impact: "Impacto", reproduction: "Reprodução",
    noFindings: "Nenhum achado registrado.", notGiven: "não informado", actor: "ator", noEvidence: "Nenhuma evidência registrada.",
    ledgerOk: "íntegro", ledgerBad: "<strong>com falhas</strong>", entries: "entradas", noLedger: "Sem ledger: evidências declaradas pelo agente.",
    model: "modelo", adapter: "adaptador", noHost: "Host não informado (relatório 0.2)",
    invalid: (n) => `Contrato inválido: ${n} erro(s)`, valid: "Contrato válido", warnings: (n) => `${n} aviso(s)`,
    report: "relatório", run: "Execução", verdict: "Veredito QA", mode: "Modalidade", isolation: "Isolamento", context: "contexto", browser: "navegador", unchecked: "não verificado",
    scope: "Escopo", mandatory: "Obrigatórias:", none: "nenhuma", exclusions: "Exclusões:", byActor: "Resultado por ator",
    checks: "Verificações", rule: "Regra", status: "Estado", basis: "Fundamento", noChecks: "Nenhuma verificação iniciada.",
    findings: "Achados", limitations: "Limitações", noLimitations: "Nenhuma limitação declarada.", evidence: "Evidências",
    record: "Registro integral", reportJson: "JSON do relatório",
    limit: "Validação de contrato, referências e integridade do ledger. Não prova que o produto se comportou como descrito nem que a máquina era confiável.",
  },
};

const pill = (v) => `<span class="pill ${TONE[v] ?? "muted"}">${esc(v)}</span>`;

function inside(runDir, rel) {
  const full = normalize(join(runDir, rel));
  return relative(runDir, full).startsWith("..") ? null : full;
}

function embed(runDir, ev, L) {
  if (!runDir || !ev.locator) return "";
  const path = inside(runDir, ev.locator);
  if (!path || !existsSync(path)) return "";
  const ext = extname(path).toLowerCase();
  const size = statSync(path).size;
  if (ev.kind === "screenshot" && [".png", ".jpg", ".jpeg"].includes(ext) && size <= 1_500_000) {
    const mime = ext === ".png" ? "image/png" : "image/jpeg";
    return `<img loading="lazy" alt="${esc(ev.summary)}" src="data:${mime};base64,${readFileSync(path).toString("base64")}">`;
  }
  if ([".txt", ".md", ".json", ".log"].includes(ext) && size <= 200_000) {
    return `<details><summary>${L.content}</summary><pre>${esc(readFileSync(path, "utf8"))}</pre></details>`;
  }
  return "";
}

export function renderReport(report, { runDir = null, validation = null, lang = "en" } = {}) {
  const L = LABELS[lang] ?? LABELS.en;
  const actors = report.actor_outcomes
    .map((a) => `<li><strong>${esc(a.actor_id)}</strong> <span class="muted">(${esc(a.role)})</span> ${pill(a.outcome)}<br>${esc(a.reason)}</li>`)
    .join("");
  const rules = report.rule_checks
    .map(
      (r) => `<tr><td><code>${esc(r.rule_id)}</code>${r.required ? ` <span class="tag">${L.mandatoryTag}</span>` : ""}<br><span class="muted">${esc(r.description)}</span></td>
<td>${pill(r.status)}</td><td>${esc(r.expected)}</td><td>${esc(r.observed)}</td><td>${esc(r.reason)}<br><span class="muted">${esc(r.oracle_type)} · ${esc(r.source_ref)}</span><br>${r.evidence_ids.map((i) => `<a href="#${esc(i)}">${esc(i)}</a>`).join(" ")}</td></tr>`,
    )
    .join("");
  const findings =
    report.findings
      .map(
        (f) => `<article class="finding ${esc(f.kind)}"><h3>${esc(f.finding_id)} · ${esc(f.kind)} <span class="tag">${esc(f.severity)}</span> <span class="tag">${esc(f.confidence)}</span></h3>
<p>${esc(f.description)}</p><dl><dt>${L.expected}</dt><dd>${esc(f.expected)}</dd><dt>${L.observed}</dt><dd>${esc(f.observed)}</dd><dt>${L.impact}</dt><dd>${esc(f.impact)}</dd><dt>${L.reproduction}</dt><dd>${esc(f.reproduction)}</dd></dl>
${f.minimal_steps.length ? `<ol>${f.minimal_steps.map((s) => `<li>${esc(s)}</li>`).join("")}</ol>` : ""}
<p>${f.evidence_ids.map((i) => `<a href="#${esc(i)}">${esc(i)}</a>`).join(" ")}</p></article>`,
      )
      .join("") || `<p class="muted">${L.noFindings}</p>`;
  const evidence =
    report.evidence
      .map(
        (e) => `<figure id="${esc(e.evidence_id)}"><figcaption><strong>${esc(e.evidence_id)}</strong> · ${esc(e.kind)} · ${esc(e.origin)} · ${esc(e.captured_by ?? L.notGiven)}${e.actor_id ? ` · ${L.actor} ${esc(e.actor_id)}` : ""}<br>${esc(e.summary)}<br><span class="muted">${esc(e.captured_at)} · <code>${esc(e.locator)}</code>${e.integrity_sha256 ? ` · sha256 ${esc(e.integrity_sha256.slice(0, 16))}…` : ""}</span></figcaption>${embed(runDir, e, L)}</figure>`,
      )
      .join("") || `<p class="muted">${L.noEvidence}</p>`;
  const list = (items, empty) => (items.length ? `<ul>${items.map((i) => `<li>${esc(i)}</li>`).join("")}</ul>` : `<p class="muted">${empty}</p>`);
  const ledger = report.ledger
    ? `Ledger ${report.ledger.verified ? L.ledgerOk : L.ledgerBad} · ${esc(report.ledger.entries)} ${L.entries} · head ${esc((report.ledger.head_hash ?? "").slice(0, 16))}…`
    : L.noLedger;
  const host = report.host ? `${esc(report.host.name)} · ${L.model} ${esc(report.host.model ?? L.notGiven)} · ${L.adapter} ${esc(report.host.adapter)}` : L.noHost;
  const val = validation
    ? `<p class="${validation.errors.length ? "bad" : "ok"} box">${validation.errors.length ? L.invalid(validation.errors.length) : L.valid}${validation.warnings.length ? ` · ${L.warnings(validation.warnings.length)}` : ""}</p>${list([...validation.errors, ...validation.warnings], "")}`
    : "";
  return `<!doctype html>
<html lang="${L.html}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data:; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>${esc(report.title)}</title>
<style>
:root{--bg:#f6f7f9;--paper:#fff;--ink:#16202a;--muted:#5b6672;--line:#d8dee5;--ok:#0f6b3f;--okbg:#e3f4ea;--bad:#a1271c;--badbg:#fbe6e3;--warn:#8a5a00;--warnbg:#fdf1d8;--accent:#1f4f7a}
@media (prefers-color-scheme:dark){:root{--bg:#0f1418;--paper:#172027;--ink:#e6edf3;--muted:#9aa7b4;--line:#2b3742;--ok:#6fd49c;--okbg:#123222;--bad:#ff9b8f;--badbg:#3a1714;--warn:#f2c46d;--warnbg:#382a0d;--accent:#8cc4f5}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif}
main{max-width:1180px;margin:0 auto;padding:28px 16px 64px}h1{font-size:1.7rem;line-height:1.2;margin:0 0 6px}h2{font-size:1.15rem;margin:0 0 12px}h3{font-size:1rem;margin:0 0 6px}
section{background:var(--paper);border:1px solid var(--line);border-radius:10px;padding:18px;margin:16px 0}
.muted{color:var(--muted)}.pill{display:inline-block;border-radius:999px;padding:1px 10px;font-weight:650;font-size:.85rem}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.pill.ok{background:var(--okbg)}.pill.bad{background:var(--badbg)}.pill.warn{background:var(--warnbg)}.pill.muted{background:var(--line);color:var(--ink)}
.tag{font-size:.75rem;border:1px solid var(--line);border-radius:6px;padding:0 6px;color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px}.kpi{border:1px solid var(--line);border-radius:8px;padding:12px}.kpi b{display:block;font-size:.8rem;color:var(--muted);font-weight:600;text-transform:uppercase;letter-spacing:.04em}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;min-width:760px}th,td{border-top:1px solid var(--line);padding:8px;text-align:left;vertical-align:top}th{font-size:.8rem;color:var(--muted)}
.finding{border-left:4px solid var(--line);padding:4px 0 4px 12px;margin:12px 0}.finding.confirmed_defect{border-color:var(--bad)}.finding.ux_friction,.finding.hypothesis{border-color:var(--warn)}
dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;margin:6px 0}dt{color:var(--muted)}dd{margin:0}
figure{margin:0 0 14px;border:1px solid var(--line);border-radius:8px;padding:10px}figure img{max-width:100%;height:auto;border:1px solid var(--line);border-radius:6px;margin-top:8px}
pre,code{font:12.5px/1.45 ui-monospace,SFMono-Regular,Menlo,monospace;white-space:pre-wrap;overflow-wrap:anywhere}pre{background:var(--bg);padding:10px;border-radius:6px;max-height:420px;overflow:auto}
a{color:var(--accent)}.box{padding:8px 12px;border-radius:8px;border:1px solid var(--line);font-weight:600}
</style></head><body><main>
<h1>${esc(report.title)}</h1>
<p class="muted">${esc(report.product_id)} · ${esc(report.journey_id)} · ${esc(report.run_id)} · ${L.report} ${esc(report.schema_version)} · skill ${esc(report.skill_version)}</p>
<section><div class="grid">
<div class="kpi"><b>${L.run}</b>${pill(report.run_status)}<br><span class="muted">${esc(report.stop_reason.code)}</span></div>
<div class="kpi"><b>${L.verdict}</b>${pill(report.qa_verdict)}</div>
<div class="kpi"><b>${L.mode}</b>${esc(report.execution_mode)}<br><span class="muted">${esc(report.environment.name)} · ${esc(report.environment.kind)}</span></div>
<div class="kpi"><b>${L.isolation}</b>${L.context} ${esc(report.isolation.model_context)} · ${L.browser} ${esc(report.isolation.browser_sessions)}${report.isolation.checked ? "" : `<br><span class="muted">${L.unchecked}</span>`}</div>
</div><p>${esc(report.stop_reason.summary)}</p><p class="muted">${host}<br>${ledger}</p>${val}</section>
<section><h2>${L.scope}</h2><p>${esc(report.scope.objective)}</p><p><strong>${L.mandatory}</strong> ${report.scope.mandatory_rule_ids.map((i) => `<code>${esc(i)}</code>`).join(", ") || L.none}</p>${report.scope.exclusions.length ? `<p><strong>${L.exclusions}</strong></p>${list(report.scope.exclusions, "")}` : ""}</section>
<section><h2>${L.byActor}</h2><ul>${actors}</ul></section>
<section><h2>${L.checks}</h2><div class="scroll"><table><thead><tr><th>${L.rule}</th><th>${L.status}</th><th>${L.expected}</th><th>${L.observed}</th><th>${L.basis}</th></tr></thead><tbody>${rules || `<tr><td colspan="5" class="muted">${L.noChecks}</td></tr>`}</tbody></table></div></section>
<section><h2>${L.findings}</h2>${findings}</section>
<section><h2>${L.limitations}</h2>${list(report.limitations, L.noLimitations)}</section>
<section><h2>${L.evidence}</h2>${evidence}</section>
<section><h2>${L.record}</h2><details><summary>${L.reportJson}</summary><pre>${esc(JSON.stringify(report, null, 2))}</pre></details><p class="muted">${esc(L.limit)}</p></section>
</main></body></html>
`;
}
