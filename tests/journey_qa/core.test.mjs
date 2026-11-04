// Contract, ledger, packet, architecture and infra tests. No browser, no network.
import { test } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, readFileSync, writeFileSync, existsSync, symlinkSync, truncateSync, statSync, chmodSync, lstatSync, readdirSync, realpathSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve, sep } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..", "..");
const FIX = join(fileURLToPath(new URL(".", import.meta.url)), "fixtures");
const S = join(ROOT, "skills", "journey-qa", "scripts");
const JQA = join(S, "jqa.mjs");
// import() takes a URL: a bare Windows path (D:\...) reads as the scheme "d:" and is refused.
const lib = (...p) => import(pathToFileURL(join(S, "lib", ...p)).href);
const { parseStrictJson } = await lib("json.mjs");
const { loadTemplate, validateDocument, DOCUMENTS } = await lib("schema.mjs");
const { validateReport, syncReport, checkCapabilities, isExplicitOrigin } = await lib("report.mjs");
const { appendEvidence, verifyLedger } = await lib("ledger.mjs");
const { leakCheck, buildPacket } = await lib("packet.mjs");
const arch = await lib("arch.mjs");

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
  const { spawnCommand, actorWorkdir } = await lib("packet.mjs");
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
  const { spawnActor } = await lib("packet.mjs");
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
  // jailArgs resolves the run path on the host (D:\srv\... on Windows), so the expectation goes through the same resolve().
  const runs = resolve("/srv/proj/qa-runs");
  assert.ok(s.includes(`--tmpfs ${runs}`) && !s.includes(`--tmpfs ${join(runs, "r1")}`), "the whole qa-runs folder is masked wherever it lives");
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

// Upload staging: what an actor token may hand to the browser.
function uploadSetup() {
  const base = tmp();
  const actor = join(base, "actor");
  const uploads = join(base, "uploads");
  mkdirSync(actor);
  writeFileSync(join(base, "outside.txt"), "secret");
  return { base, actor, uploads };
}

test("upload staging: a file inside the actor folder is staged", async () => {
  const { stageUploads } = await import("../../skills/journey-qa/scripts/lib/driver/upload.mjs");
  const { actor, uploads } = uploadSetup();
  writeFileSync(join(actor, "photo.txt"), "fictitious");
  const r = stageUploads(actor, uploads, [join(actor, "photo.txt")]);
  assert.equal(r.ok, true);
  assert.equal(r.paths.length, 1);
});

test("upload staging: a file outside the actor folder is refused as privileged", async () => {
  const { stageUploads } = await import("../../skills/journey-qa/scripts/lib/driver/upload.mjs");
  const { base, actor, uploads } = uploadSetup();
  const r = stageUploads(actor, uploads, [join(base, "outside.txt")]);
  assert.equal(r.ok, false);
  assert.equal(r.code, "privileged");
  assert.equal(stageUploads(actor, uploads, [join(base, "missing.txt")]).code, "privileged");
  assert.equal(existsSync(uploads), false, "nothing is staged on refusal");
});

test("upload staging: a symlink pointing outside the actor folder is refused", async (t) => {
  const { stageUploads } = await import("../../skills/journey-qa/scripts/lib/driver/upload.mjs");
  const { base, actor, uploads } = uploadSetup();
  try { symlinkSync(join(base, "outside.txt"), join(actor, "link.txt")); } catch { return t.skip("symlinks unavailable"); }
  const r = stageUploads(actor, uploads, [join(actor, "link.txt")]);
  assert.equal(r.ok, false);
  assert.equal(r.code, "privileged");
});

test("upload staging: a file over 50 MB is refused (sparse file, nothing written)", async () => {
  const { stageUploads, MAX_UPLOAD_BYTES } = await import("../../skills/journey-qa/scripts/lib/driver/upload.mjs");
  assert.equal(MAX_UPLOAD_BYTES, 50 * 1024 * 1024);
  const { actor, uploads } = uploadSetup();
  const big = join(actor, "big.bin");
  writeFileSync(big, "");
  truncateSync(big, MAX_UPLOAD_BYTES + 1);
  assert.equal(stageUploads(actor, uploads, [big]).code, "privileged");
  truncateSync(big, 1024);
  assert.equal(stageUploads(actor, uploads, [big]).ok, true, "a file under the limit passes");
});

test("upload staging: the browser receives a private copy with the same bytes", async () => {
  const { stageUploads } = await import("../../skills/journey-qa/scripts/lib/driver/upload.mjs");
  const { actor, uploads } = uploadSetup();
  const src = join(actor, "photo.txt");
  writeFileSync(src, "original bytes");
  const r = stageUploads(actor, uploads, [src]);
  const copy = r.paths[0];
  assert.ok(copy.startsWith(uploads + "/") || copy.startsWith(uploads + "\\"), "the copy lives under the staging folder");
  assert.notEqual(copy, src);
  assert.equal(readFileSync(copy, "utf8"), "original bytes");
  writeFileSync(src, "changed after the check");
  assert.equal(readFileSync(copy, "utf8"), "original bytes", "changing the source later does not change what is uploaded");
  if (process.platform !== "win32") {
    assert.equal(statSync(copy).mode & 0o777, 0o600);
    assert.equal(statSync(join(copy, "..")).mode & 0o777, 0o700);
  }
});

test("coordination commands are refused when sent as an actor, allowed for the coordinator or the CLI", async () => {
  const { assertRole, COORDINATOR } = await import("../../skills/journey-qa/scripts/lib/driver/guard.mjs");
  for (const cmd of ["actor-add", "actor-save", "budget", "allow", "handshake"]) {
    assert.ok(COORDINATOR.has(cmd), cmd);
    assert.throws(() => assertRole(cmd, "actor"), (e) => e.code === "privileged", cmd);
    assert.doesNotThrow(() => assertRole(cmd, "coordinator"), cmd);
    assert.doesNotThrow(() => assertRole(cmd, undefined), `${cmd} from the CLI carries no --as`);
  }
  assert.doesNotThrow(() => assertRole("click", "actor"), "interface commands are the actor's");
});

test("browser allow adds only explicit origins, with the same error as start", async () => {
  const { addOrigins } = await import("../../skills/journey-qa/scripts/lib/driver/guard.mjs");
  const allow = new Set();
  addOrigins(allow, ["https://app.example.com/", "http://127.0.0.1:8080"]);
  assert.deepEqual([...allow].sort(), ["http://127.0.0.1:8080", "https://app.example.com"]);
  for (const bad of ["https://app.example.com/path?x=1", "example.com", "javascript:alert(1)", "https://a.example.com b"]) {
    const next = new Set(["https://keep.example.com"]);
    assert.throws(() => addOrigins(next, [bad]), (e) => e.code === "bad_args" && /--allow needs an explicit origin/.test(e.message), bad);
    assert.deepEqual([...next], ["https://keep.example.com"], "a refused URL adds nothing");
  }
  const mixed = new Set();
  assert.throws(() => addOrigins(mixed, ["https://ok.example.com", "nope"]));
  assert.equal(mixed.size, 0, "one bad URL refuses the whole call");
});

test("an ended actor leaves no workdir and no driver token behind", async () => {
  const { actorWorkdir, cleanupActorWorkdir, prepareActorWorkdir } = await lib("packet.mjs");
  // The folders are made the way spawn makes them, under a private root of the test's own: a plain mkdir into the
  // user's real root would leave a 0755 folder there that every later spawn refuses.
  const env = { XDG_RUNTIME_DIR: tmp() };
  const run = join(tmp(), "run");
  const wd = prepareActorWorkdir(run, "ana", { env });
  assert.equal(wd, actorWorkdir(run, "ana", { env }));
  writeFileSync(join(wd, ".driver.json"), JSON.stringify({ port: 1, token: "secret" }));
  writeFileSync(join(wd, "PACKET.md"), "x");
  const other = prepareActorWorkdir(run, "bruno", { env });
  assert.deepEqual(cleanupActorWorkdir(run, "ana", wd), { removed: wd });
  assert.equal(existsSync(wd), false);
  assert.equal(existsSync(other), true, "another actor's folder is untouched");
  assert.deepEqual(cleanupActorWorkdir(run, "ana", wd), { removed: null }); // already gone: no error
  // Handed another actor's folder, or none, the cleanup of ana removes nothing and says why.
  for (const dir of [other, undefined]) {
    const r = cleanupActorWorkdir(run, "ana", dir);
    assert.equal(typeof r.reason, "string", String(dir));
    assert.equal(r.removed, undefined, String(dir));
  }
  assert.equal(existsSync(other), true, "handed to ana's cleanup, bruno's folder stays");
  cleanupActorWorkdir(run, "bruno", other);
  assert.equal(existsSync(other), false);
});

// The actor folder holds the driver token and a runner that gets executed: it must be this user's alone.
function workdirParts(actorWorkdir, base, run) {
  return relative(realpathSync(base), actorWorkdir(run, "ana", { env: { XDG_RUNTIME_DIR: base } })).split(sep);
}

test("actor workdir: the root is a private per-user folder, never the shared temporary folder", async (t) => {
  const { actorRoot } = await lib("packet.mjs");
  const mine = realpathSync(tmp());
  assert.equal(actorRoot({ env: { XDG_RUNTIME_DIR: mine } }), join(mine, "journey-qa-actors"));
  // A symlinked base is resolved when checked, so repointing the link later does not move the actors.
  const link = join(tmp(), "runtime");
  let linked = true;
  try { symlinkSync(mine, link, process.platform === "win32" ? "junction" : "dir"); } catch { linked = false; }
  if (linked) assert.equal(actorRoot({ env: { XDG_RUNTIME_DIR: link } }), join(mine, "journey-qa-actors"));
  if (process.platform === "win32") return t.skip("no POSIX owner or mode on Windows: the root stays in the user's temporary folder");
  chmodSync(mine, 0o777);
  assert.notEqual(actorRoot({ env: { XDG_RUNTIME_DIR: mine } }), join(mine, "journey-qa-actors"), "a base others can write is skipped");
  if (process.getuid() !== 0) {
    for (const foreign of ["/", "/tmp"]) {
      if (existsSync(foreign)) assert.notEqual(actorRoot({ env: { XDG_RUNTIME_DIR: foreign } }), join(foreign, "journey-qa-actors"), foreign);
    }
  }
  assert.ok(!actorRoot().startsWith(ROOT), "outside the repository");
});

test("actor workdir: a symlink planted anywhere on the path is refused and nothing is written through it", async (t) => {
  const { actorWorkdir, prepareActorWorkdir } = await lib("packet.mjs");
  const run = join(tmp(), "run");
  for (let depth = 1; depth <= 3; depth++) {
    const base = tmp();
    const parts = workdirParts(actorWorkdir, base, run);
    assert.equal(parts.length, 3);
    let dir = base;
    for (const p of parts.slice(0, depth - 1)) { dir = join(dir, p); mkdirSync(dir, { mode: 0o700 }); }
    const target = tmp();
    try { symlinkSync(target, join(dir, parts[depth - 1]), process.platform === "win32" ? "junction" : "dir"); } catch { return t.skip("symlinks unavailable"); }
    assert.throws(() => prepareActorWorkdir(run, "ana", { env: { XDG_RUNTIME_DIR: base } }), (e) => e.code === "unsafe_workdir", `symlink at ${parts[depth - 1]}`);
    assert.deepEqual(readdirSync(target), [], "nothing is created through the link");
  }
});

test("actor workdir: a folder that group or others can enter is refused", async (t) => {
  if (process.platform === "win32") return t.skip("no POSIX modes on Windows");
  const { actorWorkdir, prepareActorWorkdir } = await lib("packet.mjs");
  const run = join(tmp(), "run");
  for (const [depth, mode] of [[1, 0o755], [2, 0o770], [3, 0o701]]) {
    const base = tmp();
    const parts = workdirParts(actorWorkdir, base, run);
    let dir = base;
    for (const p of parts.slice(0, depth)) { dir = join(dir, p); mkdirSync(dir, { mode: 0o700 }); }
    chmodSync(dir, mode);
    assert.throws(() => prepareActorWorkdir(run, "ana", { env: { XDG_RUNTIME_DIR: base } }), (e) => e.code === "unsafe_workdir" && /lets other users in/.test(e.message), `${parts[depth - 1]} ${mode.toString(8)}`);
  }
});

test("actor workdir: a folder owned by another user is refused", async (t) => {
  if (process.platform === "win32") return t.skip("no POSIX owner on Windows");
  // Creating a folder that another user owns needs root, so the owner the check expects is changed instead.
  const { assertPrivateDir } = await lib("packet.mjs");
  const dir = tmp();
  assert.doesNotThrow(() => assertPrivateDir(dir));
  assert.throws(() => assertPrivateDir(dir, process.getuid() + 1), (e) => e.code === "unsafe_workdir" && /belongs to uid/.test(e.message));
});

// A shared temporary folder the way /tmp is one: sticky, everyone can write.
function sharedTmp() {
  const dir = tmp();
  chmodSync(dir, 0o1777);
  return dir;
}

test("actor workdir: a shared temporary folder gets a 0700 folder of this user before the cache folder", async (t) => {
  if (process.platform === "win32") return t.skip("no POSIX owner or mode on Windows: the root stays in the user's temporary folder");
  const { actorRoot } = await lib("packet.mjs");
  const home = tmp();
  const name = `journey-qa-${process.getuid()}`;
  // The cache folder puts HOME among the actor folder's ancestors, where Claude's upward search finds ~/CLAUDE.md.
  const shared = sharedTmp();
  const mine = join(realpathSync(shared), name);
  assert.equal(actorRoot({ env: {}, tmp: shared, home }), join(mine, "journey-qa-actors"));
  assert.equal(lstatSync(mine).mode & 0o777, 0o700, "created 0700");
  assert.equal(actorRoot({ env: {}, tmp: shared, home }), join(mine, "journey-qa-actors"), "found again by the next call");
  // Planted before journey-qa came: a symlink, or a folder others can enter. Refused, left as found, and the cache is used.
  for (const plant of ["symlink", "open folder"]) {
    const other = sharedTmp();
    const p = join(other, name);
    if (plant === "symlink") symlinkSync(tmp(), p, "dir");
    else { mkdirSync(p); chmodSync(p, 0o755); }
    assert.equal(actorRoot({ env: {}, tmp: other, home }), join(home, ".cache", "journey-qa-actors"), plant);
    if (plant === "open folder") assert.equal(lstatSync(p).mode & 0o777, 0o755, "a planted folder is not repaired");
  }
});

test("unsafe_workdir: a base others can write gets a chmod hint, a folder journey-qa makes gets a removal hint", async (t) => {
  if (process.platform === "win32") return t.skip("no POSIX modes on Windows");
  const { prepareActorWorkdir } = await lib("packet.mjs");
  // ~/.cache made under umask 002: 0775. It is the user's, not journey-qa's, so removing it is the wrong advice.
  const cache = tmp();
  chmodSync(cache, 0o775);
  const shared = sharedTmp();
  mkdirSync(join(shared, `journey-qa-${process.getuid()}`), { mode: 0o700 });
  chmodSync(join(shared, `journey-qa-${process.getuid()}`), 0o755); // the temporary-folder step is refused, so the cache is the base
  const opts = { env: { XDG_CACHE_HOME: cache }, tmp: shared, home: tmp() };
  const run = join(tmp(), "run");
  assert.throws(() => prepareActorWorkdir(run, "ana", opts),
    (e) => e.code === "unsafe_workdir" && e.message.includes(`chmod go-w ${cache}`) && !/Remove it/.test(e.message), "base");
  chmodSync(cache, 0o700);
  const actors = join(cache, "journey-qa-actors");
  mkdirSync(actors, { mode: 0o700 });
  chmodSync(actors, 0o755);
  assert.throws(() => prepareActorWorkdir(run, "ana", opts),
    (e) => e.code === "unsafe_workdir" && /Remove it and run again/.test(e.message) && !/chmod/.test(e.message), "journey-qa-actors");
});

test("actor workdir: the driver accepts a handed-over folder only when it is this actor's and private", async (t) => {
  const { checkActorWorkdir, prepareActorWorkdir } = await lib("packet.mjs");
  const base = tmp();
  const run = join(tmp(), "run");
  const env = { XDG_RUNTIME_DIR: base };
  const wd = prepareActorWorkdir(run, "ana", { env });
  const bruno = prepareActorWorkdir(run, "bruno", { env });
  const otherRun = prepareActorWorkdir(join(tmp(), "run"), "ana", { env });
  assert.equal(checkActorWorkdir(wd, run, "ana"), realpathSync(wd));
  const refused = (dir, label) => assert.throws(() => checkActorWorkdir(dir, run, "ana"), (e) => e.code === "unsafe_workdir", label);
  refused(undefined, "no folder");
  refused(relative(base, wd), "a relative path");
  refused(bruno, "another actor's folder");
  refused(otherRun, "the same actor in another run");
  refused(base, "the base itself");
  const carla = join(dirname(wd), "carla");
  assert.throws(() => checkActorWorkdir(carla, run, "carla"), (e) => e.code === "unsafe_workdir" && /missing/.test(e.message), "a folder that does not exist");
  assert.equal(existsSync(carla), false, "the check creates nothing");
  if (process.platform === "win32") return t.skip("no POSIX modes on Windows: the shape checks above are what applies");
  chmodSync(wd, 0o755);
  refused(wd, "an actor folder others can enter");
  chmodSync(wd, 0o700);
  chmodSync(base, 0o777);
  assert.throws(() => checkActorWorkdir(wd, run, "ana"), (e) => e.code === "unsafe_workdir" && /chmod go-w/.test(e.message), "a base others can write");
});

test("actor spawn writes the actor folder 0700 and its files 0600 (act.mjs 0700), then removes it", async (t) => {
  if (process.platform === "win32") return t.skip("no POSIX modes on Windows");
  const { spawnActor, prepareActorWorkdir } = await lib("packet.mjs");
  const base = tmp();
  const work = tmp();
  const run = join(work, "r");
  assert.equal(jqa(["init", "--product", "p", "--out", run, "--no-current"]).status, 0);
  const card = { ...JSON.parse(readFileSync(join(ROOT, "skills", "journey-qa", "assets", "templates", "actor-card.json"), "utf8")), actor_id: "ana" };
  writeFileSync(join(work, "card.json"), JSON.stringify(card));
  assert.equal(jqa(["actor", "packet", join(work, "card.json"), "--run", run]).status, 0);
  // Files left by an earlier spawn are replaced, not rewritten in place with the mode they had.
  const stale = prepareActorWorkdir(run, "ana", { env: { XDG_RUNTIME_DIR: base } });
  writeFileSync(join(stale, "PACKET.md"), "old", { mode: 0o644 });
  writeFileSync(join(stale, "act.mjs"), "old", { mode: 0o755 });
  const probe = "const f=require('fs');const o={};for(const n of ['.',...f.readdirSync('.')])o[n]=(f.lstatSync(n).mode&511).toString(8);console.log(JSON.stringify(o))";
  const saved = process.env.XDG_RUNTIME_DIR;
  process.env.XDG_RUNTIME_DIR = base;
  let r;
  try {
    r = spawnActor(run, "ana", { host: "custom", custom: `"${process.execPath}" -e "${probe}"`, exec: true, unsandboxed: true, driver: { port: 1, token: "t" } });
  } finally {
    if (saved === undefined) delete process.env.XDG_RUNTIME_DIR; else process.env.XDG_RUNTIME_DIR = saved;
  }
  assert.equal(r.exit_code, 0, readFileSync(join(run, "actors", "ana", "stderr.log"), "utf8"));
  assert.ok(r.cwd.startsWith(join(realpathSync(base), "journey-qa-actors")), r.cwd);
  const modes = JSON.parse(readFileSync(join(run, "actors", "ana", "transcript.md"), "utf8"));
  assert.deepEqual(modes, { ".": "700", "PACKET.md": "600", "act.mjs": "700", ".driver.json": "600" });
  for (const d of [join(realpathSync(base), "journey-qa-actors"), dirname(r.cwd)]) assert.equal(lstatSync(d).mode & 0o777, 0o700, d);
  assert.equal(existsSync(r.cwd), false, "the folder and its token are gone after the actor ends");
});

test("actor spawn and actor wait call the cleanup after recording the transcript", () => {
  // Spawning a host is out of scope here, so pin the wiring in the source: each path cleans up in a finally.
  const packet = readFileSync(join(S, "lib", "packet.mjs"), "utf8");
  const cli = readFileSync(JQA, "utf8");
  // Each removes the folder spawn prepared and recorded, never one derived again from the environment.
  assert.match(packet, /recordTranscript\(runDir, actorId, done\);\s*\} finally \{\s*cleanupActorWorkdir\(runDir, actorId, cwd\)/);
  assert.match(cli, /recordTranscript\(runDir, id, done\); \} finally \{ left = cleanupActorWorkdir\(runDir, id, meta\.cwd\)/);
});

test("actor workdir: the run is named by its real path, so --run through a symlink names the driver's folder", async (t) => {
  const { actorWorkdir, checkActorWorkdir, prepareActorWorkdir } = await lib("packet.mjs");
  const env = { XDG_RUNTIME_DIR: tmp() };
  const run = tmp();
  const link = join(tmp(), "run-link");
  try { symlinkSync(run, link, "dir"); } catch (e) { return t.skip(`symlinks unavailable here (${e.code})`); }
  const wd = prepareActorWorkdir(link, "ana", { env });
  assert.equal(wd, actorWorkdir(run, "ana", { env }), "spawn through the link and the driver on the run name one folder");
  assert.equal(checkActorWorkdir(wd, run, "ana"), wd);
  assert.equal(checkActorWorkdir(wd, link, "ana"), wd);
  // A folder of another run or actor is not unsafe, only not this one: the message names what differs, not a removal.
  const otherRun = prepareActorWorkdir(tmp(), "ana", { env });
  assert.throws(() => checkActorWorkdir(otherRun, run, "ana"),
    (e) => e.code === "unsafe_workdir" && /another run than/.test(e.message) && !/Remove it/.test(e.message), "another run");
  const bruno = prepareActorWorkdir(run, "bruno", { env });
  assert.throws(() => checkActorWorkdir(bruno, run, "ana"),
    (e) => e.code === "unsafe_workdir" && /actor bruno, not of ana/.test(e.message) && !/Remove it/.test(e.message), "another actor");
});

test("actor wait removes the folder spawn recorded, from any environment, and nothing that fails the check", async (t) => {
  if (process.platform === "win32") return t.skip("a custom host goes through cmd.exe there");
  const { prepareActorWorkdir } = await lib("packet.mjs");
  const work = tmp();
  const run = join(work, "r");
  assert.equal(jqa(["init", "--product", "p", "--out", run, "--no-current"]).status, 0);
  const card = { ...JSON.parse(readFileSync(join(ROOT, "skills", "journey-qa", "assets", "templates", "actor-card.json"), "utf8")), actor_id: "ana" };
  writeFileSync(join(work, "card.json"), JSON.stringify(card));
  assert.equal(jqa(["actor", "packet", join(work, "card.json"), "--run", run]).status, 0);
  // Spawn under one XDG_RUNTIME_DIR, wait without it and with another TMPDIR: the folder each would derive differs.
  const runtime = tmp();
  const s = jqa(["actor", "spawn", "ana", "--run", run, "--exec", "--background", "--host", "custom", "--cmd", `"${process.execPath}" -e "console.log(1)"`, "--unsandboxed"],
    { env: { ...process.env, XDG_RUNTIME_DIR: runtime } });
  assert.equal(s.status, 0, s.stdout + s.stderr);
  const spawnFile = join(run, "actors", "ana", "spawn.json");
  const { cwd } = JSON.parse(readFileSync(spawnFile, "utf8"));
  assert.ok(cwd.startsWith(realpathSync(runtime)), cwd);
  assert.ok(existsSync(join(cwd, ".runner.mjs")), "the runner is in the folder spawn recorded");
  const waitEnv = { ...process.env, TMPDIR: tmp() };
  delete waitEnv.XDG_RUNTIME_DIR;
  const wait = () => jqa(["actor", "wait", "ana", "--run", run], { env: waitEnv });
  const w = wait();
  assert.equal(w.status, 0, w.stdout + w.stderr);
  assert.equal(existsSync(cwd), false, "runner, packet and driver file are gone with the folder spawn recorded");
  // A recorded folder that is not ana's (another actor's, or any folder at all) is left as it is, and wait says so.
  for (const victim of [prepareActorWorkdir(run, "bruno", { env: { XDG_RUNTIME_DIR: runtime } }), tmp()]) {
    writeFileSync(join(victim, "keep.txt"), "x");
    writeFileSync(spawnFile, JSON.stringify({ ...JSON.parse(readFileSync(spawnFile, "utf8")), recorded: false, cwd: victim }));
    const again = wait();
    assert.equal(again.status, 0, again.stdout + again.stderr);
    assert.equal(existsSync(join(victim, "keep.txt")), true, victim);
    assert.match(again.stdout + again.stderr, /actor folder not removed/);
  }
});
