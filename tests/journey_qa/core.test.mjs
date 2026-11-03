// Contract, ledger, packet, architecture and infra tests. No browser, no network.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..", "..");
const FIX = join(fileURLToPath(new URL(".", import.meta.url)), "fixtures");
const S = join(ROOT, "skills", "journey-qa", "scripts");
const JQA = join(S, "jqa.mjs");
const { parseStrictJson } = await import(join(S, "lib", "json.mjs"));
const { loadTemplate, validateDocument, DOCUMENTS } = await import(join(S, "lib", "schema.mjs"));
const { validateReport, syncReport, checkCapabilities, isExplicitOrigin } = await import(join(S, "lib", "report.mjs"));
const { appendEvidence, verifyLedger } = await import(join(S, "lib", "ledger.mjs"));
const { leakCheck, buildPacket } = await import(join(S, "lib", "packet.mjs"));
const arch = await import(join(S, "lib", "arch.mjs"));

const tmp = () => mkdtempSync(join(tmpdir(), "jqa-test-"));
const clone = (x) => JSON.parse(JSON.stringify(x));
const jqa = (args, opts = {}) => spawnSync(process.execPath, [JQA, ...args], { encoding: "utf8", ...opts });

function validReport() {
  const r = loadTemplate("run-report");
  Object.assign(r, { run_status: "completed", qa_verdict: "passed", title: "Teste de contrato" });
  r.host = { name: "teste", version: null, model: null, adapter: "jqa_driver" };
  r.stop_reason = { code: "scope_complete", summary: "Fixture." };
  r.scope.mandatory_rule_ids = ["R1"];
  r.rule_checks = [{ rule_id: "R1", description: "Estado persistido", required: true, status: "passed", expected: "A", observed: "A", source_ref: "fixture:R1", evidence_ids: ["E1"], reason: "ok", oracle_type: "ui" }];
  r.evidence = [{ evidence_id: "E1", captured_at: "2026-10-23T12:00:00Z", kind: "dom", origin: "observed", locator: "fixture://1", summary: "Observação de teste", redaction: "none_needed", integrity_sha256: null, captured_by: "agent" }];
  r.actor_outcomes[0] = { ...r.actor_outcomes[0], outcome: "achieved", evidence_ids: ["E1"] };
  return r;
}
const errs = (r, o) => validateReport(r, o).errors;

test("strict JSON refuses duplicate keys, NaN and trailing garbage", () => {
  assert.throws(() => parseStrictJson('{"a":1,"a":2}'), /duplicate key/);
  assert.throws(() => parseStrictJson('{"a":NaN}'));
  assert.throws(() => parseStrictJson('{"a":1} x'));
  assert.deepEqual(parseStrictJson('{"a":[1,{"b":"ç\\n"}]}'), { a: [1, { b: "ç\n" }] });
});

test("every template follows its schema", () => {
  for (const n of DOCUMENTS) assert.deepEqual(validateDocument(loadTemplate(n), n), [], n);
});

test("a valid report passes; the 0.3 template needs host", () => {
  assert.deepEqual(errs(validReport()), []);
  const r = validReport();
  delete r.host;
  assert.match(errs(r).join(), /host/);
});

test("empty or only not_applicable never passes", () => {
  const r = validReport();
  r.rule_checks[0].status = "not_applicable";
  assert.match(errs(r).join(), /passed needs a substantive check/);
  const e = validReport();
  e.scope.mandatory_rule_ids = [];
  e.rule_checks = [];
  assert.ok(errs(e).length > 0);
});

test("a missing or inconclusive mandatory rule blocks passed; required follows the scope", () => {
  const r = validReport();
  r.scope.mandatory_rule_ids = ["R1", "R2"];
  assert.match(errs(r).join(), /obligation "R2"/);
  const s = validReport();
  s.rule_checks[0].required = false;
  assert.match(errs(s).join(), /required/);
});

test("failed verdict and failed rule go together; failed keeps incomplete coverage", () => {
  const r = validReport();
  r.rule_checks[0].status = "failed";
  assert.match(errs(r).join(), /failed rule needs a failed verdict/);
  r.qa_verdict = "failed";
  r.scope.mandatory_rule_ids.push("R2");
  assert.deepEqual(errs(r), []);
});

test("not_evaluated does not hide a check and inconclusive needs an attempt", () => {
  const r = validReport();
  r.qa_verdict = "not_evaluated";
  assert.match(errs(r).join(), /not_evaluated/);
  const b = validReport();
  Object.assign(b, { qa_verdict: "inconclusive", rule_checks: [] });
  assert.match(errs(b).join(), /inconclusive/);
});

test("a normative source does not prove execution and synthetic evidence stays out of human_ui", () => {
  const r = validReport();
  r.rule_checks[0].oracle_type = "source";
  assert.match(errs(r).join(), /normative source/);
  const s = validReport();
  s.evidence[0].origin = "synthetic";
  assert.match(errs(s).join(), /synthetic/);
});

test("a confirmed defect needs a failed rule and shared evidence", () => {
  const r = validReport();
  r.findings = [{ finding_id: "F1", kind: "confirmed_defect", severity: "high", description: "x", rule_id: "R1", evidence_ids: ["E1"], minimal_steps: [], reproduction: "observed_once", impact: "x", expected: "a", observed: "b", confidence: "confirmed" }];
  assert.match(errs(r).join(), /confirmed defect needs a matching failed rule/);
});

test("declared isolation needs a check; pending redaction blocks; blocked never passes", () => {
  const r = validReport();
  r.isolation.model_context = "isolated";
  assert.match(errs(r).join(), /declared isolation/);
  const p = validReport();
  p.evidence[0].redaction = "pending";
  assert.match(errs(p).join(), /redaction/);
  const b = validReport();
  b.run_status = "blocked";
  assert.match(errs(b).join(), /blocked/);
});

test("a 0.2 report is still valid", () => {
  const r = validReport();
  r.schema_version = "0.2";
  r.skill_version = "1.0.0";
  delete r.host;
  delete r.evidence[0].captured_by;
  assert.deepEqual(errs(r), []);
});

test("ledger: intact chain, sync fills evidence and tampering is detected", () => {
  const dir = tmp();
  const a = appendEvidence(dir, { kind: "dom", captured_by: "driver", actor_id: "a1", content: "estado A", summary: "snapshot" });
  const b = appendEvidence(dir, { kind: "screenshot", captured_by: "driver", actor_id: "a1", content: Buffer.from([1, 2, 3]), ext: "jpg", summary: "tela" });
  assert.equal(verifyLedger(dir).ok, true);
  const r = validReport();
  r.evidence = [];
  r.rule_checks[0].evidence_ids = [a.evidence_id, b.evidence_id];
  r.actor_outcomes[0].actor_id = "a1";
  r.actor_outcomes[0].evidence_ids = [a.evidence_id];
  const { missing } = syncReport(r, dir);
  assert.deepEqual(missing, []);
  assert.equal(r.evidence[0].integrity_sha256, a.sha256);
  assert.deepEqual(errs(r, { runDir: dir }), []);
  // forged evidence not in ledger
  const f = clone(r);
  f.evidence.push({ evidence_id: "FAKE", captured_at: "2026-10-23T12:00:00Z", kind: "screenshot", origin: "observed", locator: "x", summary: "inventada", redaction: "none_needed", integrity_sha256: null, captured_by: "driver" });
  f.rule_checks[0].evidence_ids.push("FAKE");
  assert.match(errs(f, { runDir: dir }).join(), /missing from the run ledger/);
  // tamper file
  writeFileSync(join(dir, a.file), "estado B");
  assert.match(verifyLedger(dir).errors.join(), /altered/);
  assert.match(errs(r, { runDir: dir }).join(), /ledger/);
});

test("ledger: removing an entry breaks the chain", () => {
  const dir = tmp();
  appendEvidence(dir, { kind: "note", content: "1", summary: "um" });
  appendEvidence(dir, { kind: "note", content: "2", summary: "dois" });
  appendEvidence(dir, { kind: "note", content: "3", summary: "três" });
  const file = join(dir, "evidence", "ledger.jsonl");
  const lines = readFileSync(file, "utf8").trim().split("\n");
  writeFileSync(file, `${lines[0]}\n${lines[2]}\n`);
  assert.equal(verifyLedger(dir).ok, false);
});

test("capabilities: the template is not ready; verified needs proof; minimums cannot disappear", () => {
  const b = loadTemplate("session-binding");
  assert.ok(checkCapabilities(b).length > 0);
  const ready = clone(b);
  ready.browser.connection_mode = "driver_launch";
  ready.browser.allowed_origins = ["https://example.invalid"];
  for (const c of ready.capabilities) if (["navigate", "click", "type_text", "keyboard", "wait", "capture_evidence", "observe_dom"].includes(c.capability)) Object.assign(c, { status: "verified", tool_name: "jqa", proof_ref: "EV-1" });
  assert.deepEqual(checkCapabilities(ready), []);
  const fake = clone(ready);
  fake.capabilities.find((c) => c.capability === "click").proof_ref = null;
  assert.match(checkCapabilities(fake).join(), /verified needs/);
  const shrink = clone(ready);
  shrink.required_capabilities = [];
  assert.match(checkCapabilities(shrink).join(), /missing minimums/);
  assert.equal(isExplicitOrigin("https://a.b"), true);
  for (const bad of ["https://a.b/path", " ", "ftp://a.b", "https://u:p@a.b", "https://*.a.b"]) assert.equal(isExplicitOrigin(bad), false, bad);
});

test("actor packet: leak heuristic and restricted wrapper", () => {
  const card = loadTemplate("actor-card");
  card.goal = "Comprar um caderno; o total registrado no pedido deve ser igual ao total revisado antes";
  const profile = { business_rules: [{ rule_id: "R-TOTAL", description: "O total registrado no pedido deve ser igual ao total revisado antes da confirmação" }] };
  assert.ok(leakCheck(card, [profile]).some((w) => /R-TOTAL/.test(w)));
  card.goal = "Comprar um caderno para a viagem";
  card.background = "Conhece o gabarito";
  assert.ok(leakCheck(card, [profile]).some((w) => /gabarito/.test(w)));
  const dir = tmp();
  card.background = "";
  const p = buildPacket(card, { runDir: dir });
  assert.ok(existsSync(join(dir, "actors", card.actor_id, "act.mjs")));
  assert.match(p.text, /node act\.mjs/);
  const act = join(dir, "actors", card.actor_id, "act.mjs");
  for (const argv of [["eval", "1"], ["--as=observer", "eval", "1"], ["snapshot", "--as=observer"], ["status"], ["click", "e1", "--run=/x"], ["note", "--as", "observer"]]) {
    const r = spawnSync(process.execPath, [act, ...argv], { encoding: "utf8" });
    assert.equal(r.status, 3, argv.join(" "));
  }
});

test("packet --strict with a leak writes nothing; IDs with a path are refused", () => {
  const dir = tmp();
  const card = loadTemplate("actor-card");
  card.background = "Conhece o gabarito";
  assert.throws(() => buildPacket(card, { runDir: dir, strict: true, out: join(dir, "p.md") }), /nothing was written/);
  assert.equal(existsSync(join(dir, "actors")), false);
  assert.equal(existsSync(join(dir, "p.md")), false);
  const bad = loadTemplate("actor-card");
  bad.actor_id = "../fora";
  assert.throws(() => buildPacket(bad, { runDir: dir }));
  const out = jqa(["actor", "outcome", "../../escape", "achieved", "motivo", "--run", dir]);
  assert.equal(out.status, 2);
  assert.equal(existsSync(join(dir, "..", "..", "escape")), false);
});

test("CLI: render does not overwrite an input without .json; --out creates folders; repeated and validated flags", () => {
  const dir = tmp();
  const r = validReport();
  writeFileSync(join(dir, "rr"), JSON.stringify(r));
  assert.equal(jqa(["report", "render", join(dir, "rr")]).status, 0);
  assert.ok(readFileSync(join(dir, "rr"), "utf8").startsWith("{"));
  assert.ok(existsSync(join(dir, "rr.html")));
  const base = join(FIX, "architecture");
  const out = join(dir, "a", "b", "rev.json");
  const init = jqa(["arch", "init", "--title", "t", "--source", "docs/adr/0007-pagamentos-assincronos.md", "--source", "plan.json", "--out", out, "--root", base], { cwd: base });
  assert.equal(init.status, 0, init.stderr);
  assert.equal(JSON.parse(readFileSync(out, "utf8")).sources.length, 2);
  assert.equal(jqa(["infra", "checklist", "--topic", "nope"]).status, 2);
  assert.equal(jqa(["infra", "terraform", join(base, "plan.json"), "--fail-on", "bogus"]).status, 2);
  assert.equal(jqa(["infra", "terraform", join(base, "plan.json"), "--fail-on", "critical"]).status, 1);
  assert.equal(jqa(["doctor", "--quick", "--json"]).status === 2, false);
  assert.equal(jqa(["evidence", "add", "--run", dir, "--kind", "note", "--summary", "x", "--redaction", "bogus", "--text", "a"]).status, 2);
  assert.equal(jqa(["evidence", "add", "--run", dir, "--kind", "screenshot", "--summary", "x", "--file", join(dir, "nao-existe.png")]).status, 2);
});

test("spawn: commands do not carry the prompt as an argument and run outside the repository", async () => {
  const { spawnCommand, actorWorkdir } = await import(join(S, "lib", "packet.mjs"));
  for (const h of ["claude", "pi"]) assert.ok(spawnCommand(h).args.every((a) => a.length < 200), h);
  assert.ok(spawnCommand("codex").stdin);
  assert.ok(!spawnCommand("claude").args.includes("--bare"));
  assert.ok(spawnCommand("claude", { bare: true }).args.includes("--bare"));
  assert.ok(!actorWorkdir(ROOT, "a").startsWith(ROOT));
});

test("CLI: init creates a run without overwriting; validate detects the type; exit codes", () => {
  const dir = tmp();
  const out = join(dir, "run");
  assert.equal(jqa(["init", "--product", "p1", "--out", out, "--no-current"]).status, 0);
  assert.equal(jqa(["init", "--product", "p1", "--out", out, "--no-current"]).status, 2);
  assert.equal(jqa(["init", "--product", "../x", "--out", join(dir, "y"), "--no-current"]).status, 2);
  assert.equal(jqa(["validate", join(out, "run-report.json")]).status, 0);
  assert.equal(jqa(["validate", join(out, "session-binding.json")]).status, 2);
  const r = validReport();
  r.title = "<script>alert(1)</script>";
  writeFileSync(join(out, "r.json"), JSON.stringify(r));
  assert.equal(jqa(["report", "render", join(out, "r.json"), "--out", join(out, "r.html")]).status, 0);
  const html = readFileSync(join(out, "r.html"), "utf8");
  assert.ok(!html.includes("<script>alert"));
  assert.ok(html.includes("&lt;script&gt;"));
});

test("architecture review: an open high risk needs a question or a verification; proceed cannot coexist with critical", () => {
  const review = JSON.parse(readFileSync(join(FIX, "architecture", "review-adr-0007.json"), "utf8"));
  assert.deepEqual(arch.validateReview(review).errors, []);
  const r = clone(review);
  r.items[0].question_for_architect = null;
  r.items[0].proposed_verification = [];
  assert.match(arch.validateReview(r).errors.join(), /no proposed verification/);
  const p = clone(review);
  p.summary.verdict = "proceed";
  assert.match(arch.validateReview(p).errors.join(), /proceed with/);
  const c = clone(review);
  c.conformance[0].status = "conforms";
  assert.match(arch.validateReview(c).errors.join(), /needs evidence/);
  assert.equal(arch.reviewToRules(review).length, 6);
  assert.match(arch.architectQuestions(review), /I need your decision/);
});

test("scan and scaffold extract status, decision and attributes from a Portuguese ADR", () => {
  const base = join(FIX, "architecture");
  const s = arch.scanRepo(base);
  assert.equal(s.docs[0].status, "proposed");
  const rv = arch.scaffoldReview({ title: "t", sources: ["docs/adr/0007-pagamentos-assincronos.md"], root: base });
  assert.equal(rv.decisions.length, 1);
  assert.equal(rv.decisions[0].alternatives_considered.length, 3);
  assert.ok(rv.quality_attributes.some((q) => q.attribute === "latency" && q.target === "8 s"));
  assert.deepEqual(arch.validateReview(rv).errors, []);
});

test("infra: terraform and kubernetes find the seeded risks", () => {
  const base = join(FIX, "architecture");
  const tf = arch.analyzeTerraformPlan(JSON.parse(readFileSync(join(base, "plan.json"), "utf8")));
  const rules = tf.findings.map((f) => f.rule);
  for (const r of ["stateful-destroy", "open-ingress", "iam-wildcard-action", "db-skip-final-snapshot", "finops-untagged"]) assert.ok(rules.includes(r), r);
  assert.equal(tf.findings[0].severity, "critical");
  const k = arch.analyzeK8s(JSON.parse(readFileSync(join(base, "k8s.json"), "utf8")));
  const kr = k.findings.map((f) => f.rule);
  for (const r of ["image-latest", "secret-in-env", "no-requests", "no-readiness", "scale-to-zero"]) assert.ok(kr.includes(r), r);
  assert.throws(() => arch.analyzeK8s({}), /Convert YAML/);
  assert.throws(() => arch.analyzeTerraformPlan({}), /terraform show/);
});

test("packet language follows the card locale: pt* gets Portuguese, anything else English; leak check works in English", async () => {
  const { spawnActor } = await import(join(S, "lib", "packet.mjs"));
  const act = (dir, id, argv) => spawnSync(process.execPath, [join(dir, "actors", id, "act.mjs"), ...argv], { encoding: "utf8" });
  const pt = loadTemplate("actor-card");
  assert.equal(pt.locale, "pt-BR");
  const ptDir = tmp();
  const p = buildPacket(pt, { runDir: ptDir });
  assert.match(p.text, /## Sua ficha/);
  assert.match(act(ptDir, pt.actor_id, ["eval"]).stderr, /Indisponível para o ator/);
  assert.match(act(ptDir, pt.actor_id, ["help"]).stdout, /^Uso: node act\.mjs/);
  assert.match(spawnActor(ptDir, pt.actor_id, { host: "claude" }).command, /Comece agora a sua jornada/);
  for (const locale of ["en-US", "fr-FR"]) {
    const card = { ...loadTemplate("actor-card"), locale };
    const dir = tmp();
    const e = buildPacket(card, { runDir: dir });
    assert.match(e.text, /## Your card/, locale);
    assert.doesNotMatch(e.text, /Sua ficha|Como operar/, locale);
    assert.match(act(dir, card.actor_id, ["eval"]).stderr, /Unavailable to the actor: command "eval"/, locale);
    assert.equal(act(dir, card.actor_id, ["note", "--as", "observer"]).status, 3, locale);
    assert.match(act(dir, card.actor_id, ["help"]).stdout, /^Usage: node act\.mjs/, locale);
    assert.match(spawnActor(dir, card.actor_id, { host: "claude" }).command, /Start your journey now/, locale);
  }
  const en = { ...loadTemplate("actor-card"), locale: "en-US", goal: "Buy a notebook; the total recorded on the order must equal the total reviewed before" };
  const profile = { business_rules: [{ rule_id: "R-TOTAL", description: "The total recorded on the order must equal the total reviewed before confirmation" }] };
  assert.ok(leakCheck(en, [profile]).some((w) => /R-TOTAL/.test(w)));
  en.goal = "Buy a notebook for the trip";
  en.background = "Knows the answer key";
  assert.ok(leakCheck(en, [profile]).some((w) => /answer key/.test(w)));
  assert.throws(() => buildPacket(en, { runDir: tmp(), strict: true }), /nothing was written/);
});

test("render language: English by default, --lang pt gives Portuguese labels, other values exit 2", () => {
  const dir = tmp();
  writeFileSync(join(dir, "r.json"), JSON.stringify(validReport()));
  assert.equal(jqa(["report", "render", join(dir, "r.json")]).status, 0);
  const en = readFileSync(join(dir, "r.html"), "utf8");
  assert.match(en, /<html lang="en">/);
  assert.match(en, /QA verdict/);
  assert.equal(jqa(["report", "render", join(dir, "r.json"), "--lang", "pt", "--out", join(dir, "r-pt.html")]).status, 0);
  const pt = readFileSync(join(dir, "r-pt.html"), "utf8");
  assert.match(pt, /<html lang="pt-BR">/);
  assert.match(pt, /Veredito QA/);
  assert.equal(jqa(["report", "render", join(dir, "r.json"), "--lang", "xx", "--out", join(dir, "r-xx.html")]).status, 2);
  const review = join(FIX, "architecture", "review-adr-0007.json");
  assert.equal(jqa(["arch", "render", review, "--out", join(dir, "a.html")]).status, 0);
  const aen = readFileSync(join(dir, "a.html"), "utf8");
  assert.match(aen, /<html lang="en">/);
  assert.match(aen, /QA reading/);
  assert.doesNotMatch(aen, /Leitura de QA|Premissas/);
  assert.equal(jqa(["arch", "render", review, "--lang", "pt", "--out", join(dir, "a-pt.html")]).status, 0);
  const apt = readFileSync(join(dir, "a-pt.html"), "utf8");
  assert.match(apt, /<html lang="pt-BR">/);
  assert.match(apt, /Leitura de QA/);
  assert.equal(jqa(["arch", "render", review, "--lang", "xx", "--out", join(dir, "a-xx.html")]).status, 2);
  assert.equal(existsSync(join(dir, "a-xx.html")), false);
});

test("scan and scaffold extract status, decision, alternatives and attributes from an English ADR", () => {
  const base = tmp();
  mkdirSync(join(base, "docs", "adr"), { recursive: true });
  writeFileSync(join(base, "docs", "adr", "0012-async-payments.md"), `# ADR-0012: Asynchronous payment confirmation with a queue

Status: Accepted
Date: 2026-10-01

## Context

Checkout calls the payment provider synchronously, and p99 latency reaches 8 s at peak.

## Decision

We will confirm payments asynchronously through a queue and idempotent workers.

## Options considered

- Keep the synchronous call with a longer timeout
- Rely on the provider webhook only
- Queue with workers (chosen)

## Consequences

- The order page shows a pending state until the payment is confirmed.
- The checkout request answers in under 300 ms at p99.
`);
  const s = arch.scanRepo(base);
  assert.equal(s.docs.length, 1);
  assert.equal(s.docs[0].status, "accepted");
  assert.deepEqual(s.docs[0].issues, []);
  assert.ok(s.docs[0].quality_attributes.includes("latency"));
  const rv = arch.scaffoldReview({ title: "t", sources: ["docs/adr/0012-async-payments.md"], root: base });
  assert.equal(rv.sources[0].status, "accepted");
  assert.equal(rv.decisions.length, 1);
  assert.match(rv.decisions[0].statement, /asynchronously through a queue/);
  assert.equal(rv.decisions[0].alternatives_considered.length, 3);
  assert.equal(rv.decisions[0].consequences_stated.length, 2);
  assert.match(rv.context.problem, /payment provider/);
  assert.ok(rv.quality_attributes.some((q) => q.attribute === "latency" && q.target === "8 s"));
  assert.deepEqual(arch.validateReview(rv).errors, []);
});

test("pi and custom actors run jailed: HOME empty, env cleared, only the actor folder writable, run not mounted", async () => {
  const { jailArgs } = await import("../../skills/journey-qa/scripts/lib/packet.mjs");
  const home = mkdtempSync(join(tmpdir(), "jqa-home-"));
  const a = jailArgs("/tmp/actor", { platform: "linux", home, runDir: "/srv/proj/qa-runs/r1", has: () => true, bind: [home], scratch: [home], passEnv: ["OPENROUTER_API_KEY"], env: { AWS_SECRET_ACCESS_KEY: "x", OPENROUTER_API_KEY: "k", OPENAI_API_KEY: "o", SSH_AUTH_SOCK: "/s", LANG: "C" } });
  const s = a.join(" ");
  assert.ok(s.includes(`--tmpfs ${home}`) && s.includes("--tmpfs /run") && s.includes("--clearenv") && s.includes("--unshare-ipc"));
  assert.ok(s.includes("--bind /tmp/actor /tmp/actor"));
  assert.ok(s.includes("--tmpfs /srv/proj/qa-runs") && !s.includes("--tmpfs /srv/proj/qa-runs/r1"), "the whole qa-runs folder is masked wherever it lives");
  assert.ok(!s.includes("OPENAI_API_KEY"), "only the chosen provider's key passes");
  assert.ok(s.includes("--setenv OPENROUTER_API_KEY k") && s.includes("--setenv LANG C"));
  assert.ok(!s.includes("AWS_SECRET_ACCESS_KEY") && !s.includes("SSH_AUTH_SOCK"));
  assert.ok(s.includes(`--overlay-src ${home} --tmp-overlay ${home}`));
  const { providerKeyFor } = await import("../../skills/journey-qa/scripts/lib/packet.mjs");
  assert.equal(providerKeyFor("openrouter/deepseek/x"), "OPENROUTER_API_KEY");
  assert.equal(providerKeyFor(null), null);
  assert.equal(jailArgs("/tmp/a", { platform: "darwin", home, has: () => true }), null);
  assert.equal(jailArgs("/tmp/a", { platform: "linux", home, has: () => false }), null);
});

test("actor spawn plans pi inside bwrap and refuses without a jail unless --unsandboxed", () => {
  const run = mkdtempSync(join(tmpdir(), "jqa-jail-"));
  const r0 = spawnSync(process.execPath, [JQA, "init", "--product", "p", "--out", join(run, "r"), "--no-current"], { encoding: "utf8" });
  assert.equal(r0.status, 0, r0.stderr);
  const card = { ...JSON.parse(readFileSync(join(ROOT, "skills", "journey-qa", "assets", "templates", "actor-card.json"), "utf8")), actor_id: "ana" };
  writeFileSync(join(run, "card.json"), JSON.stringify(card));
  assert.equal(spawnSync(process.execPath, [JQA, "actor", "packet", join(run, "card.json"), "--run", join(run, "r")], { encoding: "utf8" }).status, 0);
  const plan = spawnSync(process.execPath, [JQA, "actor", "spawn", "ana", "--host", "pi", "--run", join(run, "r"), "--json"], { encoding: "utf8" });
  const hasBwrap = spawnSync("sh", ["-c", "command -v bwrap"]).status === 0;
  if (process.platform === "linux" && hasBwrap) {
    assert.equal(plan.status, 0, plan.stderr);
    assert.match(JSON.parse(plan.stdout).command, /^bwrap .* -- pi -p /);
  } else {
    assert.equal(plan.status, 3);
  }
  const free = spawnSync(process.execPath, [JQA, "actor", "spawn", "ana", "--host", "pi", "--unsandboxed", "--run", join(run, "r"), "--json"], { encoding: "utf8" });
  assert.match(JSON.parse(free.stdout).command, /^pi -p /);
});
