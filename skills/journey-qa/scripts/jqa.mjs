#!/usr/bin/env node
// Journey QA command line. Node >= 20, zero dependencies except Playwright for `browser`.
// Run `node jqa.mjs help` for the command list.
import { existsSync, mkdirSync, readFileSync, writeFileSync, appendFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { randomBytes } from "node:crypto";
import { ContractError, readJson } from "./lib/json.mjs";
import { RUN_DOCUMENTS, SKILL_ROOT, loadTemplate, validateDocument } from "./lib/schema.mjs";
import { VALIDATION_LIMIT, checkCapabilities, syncReport, validateReport } from "./lib/report.mjs";
import { appendEvidence, appendTrace, readLedger, verifyLedger } from "./lib/ledger.mjs";
import { renderReport } from "./lib/render.mjs";
import { assertActorId, buildPacket, cleanupActorWorkdir, loadObserverDocs, recordOutcome, recordTranscript, spawnActor } from "./lib/packet.mjs";

export const VERSION = readFileSync(join(SKILL_ROOT, "VERSION"), "utf8").trim();
const BOOL = new Set(["dry-run", "chromium", "unsandboxed", 
  "full", "frames", "headed", "json", "double", "secret", "no-snapshot", "no-shot", "no-auto-shot", "mobile", "restore",
  "exec", "background", "strict", "all", "no-current", "force", "quiet", "quick", "help", "bare",
]);
const REPEAT = new Set(["allow", "observer-doc", "uncertain", "effect", "evidence", "source"]);
export const GUARDRAIL_CODES = new Set(["privileged", "budget_exhausted", "origin_blocked", "no_allowlist", "leak", "no_jail"]);

export function parseArgs(argv) {
  const pos = [];
  const flags = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === "--") { pos.push(...argv.slice(i + 1)); break; }
    if (a.startsWith("--")) {
      let [k, v] = a.slice(2).split(/=(.*)/s, 2);
      if (v === undefined) {
        if (BOOL.has(k)) v = true;
        else if (i + 1 < argv.length) v = argv[++i];
        else throw new ContractError(`--${k} needs a value`, "bad_args");
      }
      if (REPEAT.has(k)) (flags[k] ??= []).push(v);
      else flags[k] = v;
    } else pos.push(a);
  }
  return { pos, flags };
}

function runDirFrom(flags, required = true) {
  const candidate = flags.run ?? process.env.JQA_RUN ?? (existsSync(".jqa-run") ? readFileSync(".jqa-run", "utf8").trim() : null);
  if (!candidate) {
    if (!required) return null;
    throw new ContractError("No run selected: pass --run DIR (or run `jqa init`, which writes .jqa-run).", "no_run");
  }
  const dir = resolve(candidate);
  if (!existsSync(dir)) {
    if (!required) return null;
    throw new ContractError(`Run directory does not exist: ${dir}`, "no_run");
  }
  return dir;
}

function detectKind(doc) {
  if ("qa_verdict" in doc) return "run-report";
  if ("capability_report_version" in doc) return "session-binding";
  if ("profile_version" in doc && "business_rules" in doc) return "product-profile";
  if ("goal" in doc && "actor_id" in doc) return "actor-card";
  if ("journey_id" in doc && "end_conditions" in doc) return "scenario";
  if ("next_allowed_action" in doc) return "resume-checkpoint";
  if ("review_version" in doc) return "architecture-review";
  return null;
}

// HTML label language for `report render` and `arch render`: English unless --lang pt is given.
export function langFrom(flags) {
  if (flags.lang === undefined) return "en";
  const m = /^(pt|en)(?:[-_]|$)/i.exec(String(flags.lang));
  if (!m) throw new ContractError(`--lang accepts pt or en, got ${flags.lang}`, "bad_args");
  return m[1].toLowerCase();
}

const out = (flags, data, text) => {
  if (flags.json || text === undefined) console.log(JSON.stringify(data, null, 2));
  else console.log(text);
};

// ---------------- commands ----------------
function cmdInit({ flags }) {
  const product = flags.product;
  if (!product || !/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(product)) throw new ContractError("--product needs an identifier (letters, digits, . _ -)", "bad_args");
  const runId = `run-${new Date().toISOString().replace(/[-:]/g, "").replace(/\.\d+Z$/, "Z")}-${randomBytes(3).toString("hex")}`;
  // Default under the project, never a temp dir: evidence is the deliverable and must outlive the session.
  const dir = resolve(flags.out ?? join("qa-runs", runId));
  if (existsSync(dir)) throw new ContractError(`Target already exists; nothing overwritten: ${dir}`, "exists");
  const docs = Object.fromEntries(RUN_DOCUMENTS.map((n) => [n, loadTemplate(n)]));
  for (const n of ["product-profile", "scenario", "run-report"]) docs[n].product_id = product;
  docs["product-profile"].label = product;
  docs["run-report"].run_id = runId;
  docs["run-report"].skill_version = VERSION;
  docs["run-report"].trace_file = "trace.jsonl";
  if (flags.journey) docs.scenario.journey_id = docs["run-report"].journey_id = flags.journey;
  docs["resume-checkpoint"].run_id = runId;
  docs["resume-checkpoint"].product_ref = "product-profile.json";
  for (const [n, d] of Object.entries(docs)) {
    const errors = validateDocument(d, n);
    if (errors.length) throw new ContractError(`Template ${n} is invalid:\n${errors.join("\n")}`);
  }
  mkdirSync(join(dir, "evidence"), { recursive: true });
  // Screenshots can carry personal data until someone reviews them, so the default qa-runs/ stays out of git.
  if (!flags.out && !existsSync(join("qa-runs", ".gitignore"))) writeFileSync(join("qa-runs", ".gitignore"), "# Journey QA evidence: review screenshots for personal data before committing.\n*\n");
  mkdirSync(join(dir, ".jqa"), { recursive: true });
  for (const [n, d] of Object.entries(docs)) writeFileSync(join(dir, `${n}.json`), `${JSON.stringify(d, null, 2)}\n`, { flag: "wx" });
  writeFileSync(join(dir, "trace.jsonl"), "", { flag: "wx" });
  writeFileSync(join(dir, ".gitignore"), ".jqa/\n", { flag: "wx" });
  if (!flags["no-current"]) writeFileSync(".jqa-run", `${dir}\n`);
  out(flags, { created: dir, run_id: runId, product_actions_executed: false, current: !flags["no-current"] },
    `run prepared: ${dir}\nrun_id ${runId}${flags["no-current"] ? "" : "\n.jqa-run points to it (later commands need no --run)"}\nno product action was executed`);
}

function cmdValidate({ pos, flags }) {
  const file = pos[0];
  if (!file) throw new ContractError("Usage: validate FILE [--kind KIND] [--run DIR]", "bad_args");
  const doc = readJson(file);
  const kind = flags.kind ?? detectKind(doc);
  if (!kind) throw new ContractError("Document kind not recognised; pass --kind", "bad_args");
  let errors = [];
  let warnings = [];
  if (kind === "run-report") {
    const runDir = flags.run ? resolve(flags.run) : existsSync(join(dirname(resolve(file)), "evidence", "ledger.jsonl")) ? dirname(resolve(file)) : null;
    ({ errors, warnings } = validateReport(doc, { runDir }));
  } else if (kind === "session-binding") errors = checkCapabilities(doc);
  else if (kind === "architecture-review") ({ errors, warnings } = await_arch().validateReview(doc));
  else errors = validateDocument(doc, kind);
  if (flags.strict && warnings.length) errors.push(...warnings.map((w) => `(strict) ${w}`));
  const result = { file, kind, valid: errors.length === 0, errors, warnings, limit: VALIDATION_LIMIT };
  if (kind === "run-report") result.qa_verdict = doc.qa_verdict;
  if (kind === "session-binding") result.declared_ready = errors.length === 0;
  out(flags, result, `${result.valid ? "VALID" : "INVALID"} ${kind} ${file}${errors.map((e) => `\n✗ ${e}`).join("")}${warnings.map((w) => `\n! ${w}`).join("")}\n${VALIDATION_LIMIT}`);
  return result.valid ? 0 : 2;
}

let archModule = null;
function await_arch() {
  if (!archModule) throw new ContractError("architecture module not loaded", "internal");
  return archModule;
}

async function cmdReport({ pos, flags }) {
  const [sub, file] = pos;
  if (!file) throw new ContractError("Usage: report sync|validate|render REPORT [--run DIR] [--out HTML] [--lang pt|en]", "bad_args");
  const report = readJson(file);
  const runDir = flags.run ? resolve(flags.run) : dirname(resolve(file));
  if (sub === "sync") {
    const { missing, ledgerErrors } = syncReport(report, runDir);
    writeFileSync(file, `${JSON.stringify(report, null, 2)}\n`);
    const v = validateReport(report, { runDir });
    out(flags, { synced: file, evidence: report.evidence.length, missing, ledger_errors: ledgerErrors, ...v },
      `synced: ${report.evidence.length} evidence items from the ledger${missing.length ? `\n✗ references without evidence: ${missing.join(", ")}` : ""}${ledgerErrors.map((e) => `\n✗ ${e}`).join("")}\n${v.errors.length ? `INVALID${v.errors.map((e) => `\n✗ ${e}`).join("")}` : "VALID"}${v.warnings.map((w) => `\n! ${w}`).join("")}`);
    return v.errors.length || missing.length ? 2 : 0;
  }
  if (sub === "validate") return cmdValidate({ pos: [file], flags: { ...flags, kind: "run-report", run: runDir } });
  if (sub === "render") {
    const v = validateReport(report, { runDir: existsSync(join(runDir, "evidence", "ledger.jsonl")) ? runDir : null });
    if (v.errors.length && !flags.force) throw new ContractError(`Invalid report; fix it or pass --force to render with the errors shown:\n${v.errors.join("\n")}`);
    const target = flags.out ?? (file.endsWith(".json") ? file.replace(/\.json$/, ".html") : `${file}.html`);
    if (resolve(target) === resolve(file)) throw new ContractError("The HTML cannot overwrite the input report.", "bad_args");
    mkdirSync(dirname(resolve(target)), { recursive: true });
    writeFileSync(target, renderReport(report, { runDir, validation: v, lang: langFrom(flags) }));
    out(flags, { created: target, valid: !v.errors.length }, `HTML report: ${resolve(target)}`);
    return 0;
  }
  throw new ContractError(`Unknown subcommand: report ${sub}`, "bad_args");
}

function cmdEvidence({ pos, flags }) {
  const sub = pos[0];
  const runDir = runDirFrom(flags);
  if (sub === "add") {
    if (flags.file && !existsSync(flags.file)) throw new ContractError(`File not found: ${flags.file}`, "bad_args");
    const e = appendEvidence(runDir, {
      kind: flags.kind ?? "tool_result", origin: flags.origin ?? "observed", captured_by: flags.by ?? "agent", actor_id: flags.actor ?? null,
      summary: flags.summary, url: flags.url ?? null, sourceFile: flags.file, locator: flags.locator, redaction: flags.redaction,
      content: flags.text,
    });
    out(flags, e, `recorded ${e.evidence_id} (${e.kind}, ${e.captured_by})${e.file ? ` → ${e.file}` : ""}`);
    return 0;
  }
  if (sub === "verify") {
    const v = verifyLedger(runDir);
    out(flags, { ok: v.ok, entries: v.entries.length, head: v.head, errors: v.errors }, `${v.ok ? "ledger intact" : "LEDGER BROKEN"} · ${v.entries.length} entries${v.errors.map((e) => `\n✗ ${e}`).join("")}`);
    return v.ok ? 0 : 2;
  }
  if (sub === "list") {
    const rows = readLedger(runDir).filter((e) => !flags.actor || e.actor_id === flags.actor).filter((e) => !flags.kind || e.kind === flags.kind);
    out(flags, rows, rows.map((e) => `${e.evidence_id} ${e.captured_at} ${e.kind} ${e.captured_by}${e.actor_id ? ` ${e.actor_id}` : ""} — ${e.summary}`).join("\n") || "(ledger empty)");
    return 0;
  }
  throw new ContractError("Usage: evidence add|list|verify", "bad_args");
}

function cmdTrace({ pos, flags }) {
  const runDir = runDirFrom(flags);
  const file = join(runDir, "trace.jsonl");
  if (pos[0] === "note") {
    const text = pos.slice(1).join(" ").trim();
    if (!text) throw new ContractError("Usage: trace note \"text\" --actor ID", "bad_args");
    const seq = appendTrace(runDir, { actor_id: flags.actor ?? null, as: flags.as ?? "actor", cmd: "note", note: text.slice(0, 2000), ok: true });
    out(flags, { noted: true, seq }, `note recorded in the trace (#${seq})`);
    return 0;
  }
  if (pos[0] === "show") {
    const rows = (existsSync(file) ? readFileSync(file, "utf8").trim().split("\n").filter(Boolean) : []).map((l) => JSON.parse(l))
      .filter((e) => !flags.actor || e.actor_id === flags.actor).slice(-Number(flags.last ?? 50));
    out(flags, rows, rows.map((e) => `#${e.seq} ${e.ts.slice(11, 19)} ${e.actor_id ?? "-"} ${e.as ?? ""} ${e.cmd}${e.target ? ` ${e.target}` : ""}${e.note ? ` “${e.note}”` : ""}${e.ok === false ? ` ✗ ${e.error?.code ?? ""}` : ""}${e.evidence_ids?.length ? ` [${e.evidence_ids.join(",")}]` : ""}`).join("\n") || "(trace empty)");
    return 0;
  }
  throw new ContractError("Usage: trace note|show", "bad_args");
}

async function revokeActorToken(runDir, id) {
  try {
    const { driverInfo, send } = await import("./lib/driver/client.mjs");
    if (driverInfo(runDir)) await send(runDir, { cmd: "actor-token-revoke", actor: id, args: [], flags: { as: "coordinator" } });
  } catch {}
}

async function cmdActor({ pos, flags }) {
  const sub = pos[0];
  if (sub === "packet") {
    const file = pos[1];
    if (!file) throw new ContractError("Usage: actor packet CARD.json [--run DIR] [--observer-doc profile.json ...] [--strict]", "bad_args");
    const card = readJson(file);
    const runDir = runDirFrom(flags, false);
    const docs = loadObserverDocs(flags["observer-doc"] ?? [
      ...(runDir && existsSync(join(runDir, "product-profile.json")) ? [join(runDir, "product-profile.json")] : []),
      ...(runDir && existsSync(join(runDir, "scenario.json")) ? [join(runDir, "scenario.json")] : []),
    ]);
    const p = buildPacket(card, { runDir, observerDocs: docs, strict: !!flags.strict, out: flags.out ?? null, force: !!flags.force });
    out(flags, { actor_id: card.actor_id, dir: p.dir, out: flags.out ?? null, warnings: p.warnings, context_isolation_created: false },
      `actor packet ${card.actor_id}${p.dir ? ` in ${p.dir} (PACKET.md + act.mjs)` : ""}${flags.out ? ` · ${flags.out}` : ""}${p.warnings.map((w) => `\n! ${w}`).join("")}\nReview the content: the check is heuristic and creates no isolation.`);
    return 0;
  }
  if (sub === "spawn") {
    const id = pos[1];
    const runDir = runDirFrom(flags);
    // A spawned actor gets its own driver token: it can drive only its own context through the interface.
    let driver = null;
    if (flags.exec) {
      const { driverInfo, send } = await import("./lib/driver/client.mjs");
      const info = driverInfo(runDir);
      if (info) {
        const t = await send(runDir, { cmd: "actor-token", actor: id, args: [], flags: { as: "coordinator" } });
        if (!t.ok) throw new ContractError(t.text, t.code ?? "driver_error");
        driver = { port: info.port, token: t.token };
      }
    }
    const r = spawnActor(runDir, id, {
      driver, host: flags.host, model: flags.model, custom: flags.cmd, bare: !!flags.bare, exec: !!flags.exec, background: !!flags.background,
      unsandboxed: !!flags.unsandboxed, timeoutMinutes: Number(flags["timeout-minutes"] ?? 30),
    });
    if (driver && !r.background) await revokeActorToken(runDir, id);
    out(flags, r, r.planned ? `command (not executed; pass --exec):\n  cd ${r.cwd}\n  ${r.command}` : r.background ? `actor ${id} started in the background (pid ${r.pid}); follow it with \`jqa actor wait ${id}\`` : `actor ${id} finished (exit ${r.exit_code})${r.error ? ` error: ${r.error}` : ""} · transcript ${r.evidence ?? "missing"}${r.session ? ` · ${r.session.total} commands, ${r.session.outside_interface} outside act.mjs, ${r.session.denied.length} denied${r.session.cost_usd != null ? `, USD ${r.session.cost_usd.toFixed(2)}` : ""}` : ""}`);
    return r.planned || r.background || r.exit_code === 0 ? 0 : 1;
  }
  if (sub === "wait") {
    const id = assertActorId(pos[1]);
    const runDir = runDirFrom(flags);
    const spawnFile = join(runDir, "actors", id, "spawn.json");
    if (!existsSync(spawnFile)) throw new ContractError(`No spawn recorded for ${id}`, "bad_args");
    const meta = readJson(spawnFile);
    if (!meta.background || meta.recorded) { out(flags, meta, `actor ${id} is not in the background or was already recorded (exit ${meta.exit_code ?? "?"})`); return 0; }
    const deadline = Date.now() + Number(flags["timeout-minutes"] ?? 30) * 60000;
    const alive = () => { try { process.kill(meta.pid, 0); return true; } catch { return false; } };
    while (alive() && Date.now() < deadline) Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, 1000);
    if (alive()) { out(flags, { running: true }, `actor ${id} still running`); return 1; }
    const exitFile = join(runDir, "actors", id, "exit.json");
    const exit = existsSync(exitFile) ? readJson(exitFile) : { exit_code: null, error: "process ended without recording an exit" };
    const done = { ...meta, ...exit, ended_at: exit.ended_at ?? new Date().toISOString(), recorded: true };
    writeFileSync(spawnFile, JSON.stringify(done, null, 2));
    let ev;
    try { ev = recordTranscript(runDir, id, done); } finally { cleanupActorWorkdir(runDir, id); }
    out(flags, { finished: true, exit_code: done.exit_code, evidence: ev }, `actor ${id} finished (exit ${done.exit_code ?? "unknown"}) · transcript ${ev ?? "missing"}`);
    await revokeActorToken(runDir, id);
    return done.exit_code === 0 ? 0 : 1;
  }
  if (sub === "outcome") {
    const runDir = runDirFrom(flags);
    const r = recordOutcome(runDir, pos[1], pos[2], pos.slice(3).join(" "));
    out(flags, r, `actor outcome ${r.actor_id}: ${r.outcome} (${r.evidence_id})`);
    return 0;
  }
  throw new ContractError("Usage: actor packet|spawn|wait|outcome", "bad_args");
}

async function cmdBrowser({ pos, flags }) {
  const { startDriver, send, driverInfo } = await import("./lib/driver/client.mjs");
  const sub = pos[0];
  const runDir = runDirFrom(flags);
  if (sub === "start") {
    const scenarioFile = join(runDir, "scenario.json");
    if (existsSync(scenarioFile)) {
      const budget = readJson(scenarioFile).budget;
      if (budget) {
        flags["max-actions"] ??= budget.max_actions;
        flags["max-minutes"] ??= budget.max_minutes;
      }
    }
    const r = await startDriver(runDir, flags);
    const wanted = [].concat(flags.allow ?? []).flatMap((a) => String(a).split(",")).filter(Boolean).map((o) => new URL(o).origin);
    const missing = r.already ? wanted.filter((o) => !r.info.allow.includes(o)) : [];
    out(flags, r, r.already ? `driver already running (pid ${r.info.pid}, ${r.info.browser}) · allowlist ${r.info.allow.join(", ")}${missing.length ? `\n! new origins NOT added: ${missing.join(", ")} — use \`jqa browser allow ORIGIN\`` : ""}` : `driver running: ${r.info.browser} ${r.info.version} (${r.info.mode}) · allowlist ${r.info.allow.join(", ") || "EMPTY"}\nnext: jqa browser actor add ID && jqa browser handshake --actor ID --url URL`);
    return 0;
  }
  if (sub === "status" && !driverInfo(runDir)) { out(flags, { running: false }, "driver stopped"); return 0; }
  let cmd = sub;
  let args = pos.slice(1);
  if (sub === "actor") {
    cmd = `actor-${pos[1]}`;
    args = pos.slice(2);
  }
  if (!cmd) throw new ContractError("Usage: browser <command>. See `jqa help browser`.", "bad_args");
  if (cmd === "upload") args = args.map((a, i) => (i > 0 ? resolve(a) : a));
  const timeout = Number(flags["cmd-timeout"] ?? 180000);
  const r = await send(runDir, { cmd, actor: flags.actor, args, flags: { ...flags, via: process.env.JQA_VIA === "act" ? "act" : "direct" } }, timeout);
  if (flags.json) console.log(JSON.stringify(r, null, 2));
  else console.log(r.text);
  return r.ok ? 0 : GUARDRAIL_CODES.has(r.code) ? 3 : 1;
}

async function cmdCheckpoint({ flags }) {
  const runDir = runDirFrom(flags);
  const actorId = flags.actor;
  if (!actorId) throw new ContractError("Usage: checkpoint --actor ID --next \"next action\" [--effect TEXT] [--uncertain 'op|action|status|how to verify']", "bad_args");
  const trace = existsSync(join(runDir, "trace.jsonl")) ? readFileSync(join(runDir, "trace.jsonl"), "utf8").trim().split("\n").filter(Boolean).map((l) => JSON.parse(l)) : [];
  const mine = trace.filter((e) => e.actor_id === actorId);
  const last = mine.at(-1);
  const evidence = readLedger(runDir).filter((e) => e.actor_id === actorId).map((e) => e.evidence_id);
  const scenario = existsSync(join(runDir, "scenario.json")) ? readJson(join(runDir, "scenario.json")) : null;
  let used = mine.filter((e) => e.actions_used).at(-1)?.actions_used ?? 0;
  const budget = { ...(scenario?.budget ?? { max_actions: 60, max_minutes: 20, max_retries: 2 }) };
  try {
    const { driverInfo, send } = await import("./lib/driver/client.mjs");
    if (driverInfo(runDir)) {
      const live = JSON.parse((await send(runDir, { cmd: "status", flags: {} })).text).actors.find((a) => a.id === actorId);
      if (live) {
        used = live.actions;
        budget.max_actions = live.max_actions;
        budget.max_minutes = Math.max(0.1, live.max_minutes - live.minutes_used);
      }
    }
  } catch {}
  const cp = {
    schema_version: "1.0", run_id: existsSync(join(runDir, "run-report.json")) ? readJson(join(runDir, "run-report.json")).run_id : "run-unknown",
    saved_at: new Date().toISOString(), product_ref: "product-profile.json", session_alias: actorId, actor_id: actorId,
    current_url: last?.url_after ?? null, last_observation: last ? `${last.cmd} ${last.target ?? ""} ${last.ok === false ? "(failed)" : ""}`.trim() : "",
    completed_effects: [].concat(flags.effect ?? []),
    uncertain_operations: [].concat(flags.uncertain ?? []).map((u) => {
      const [operation_id, action, status, verification_next] = u.split("|");
      return { operation_id, action, status, verification_next };
    }),
    evidence_ids: evidence.slice(-20),
    remaining_budget: { max_actions: Math.max(1, budget.max_actions - used), max_minutes: budget.max_minutes, max_retries: budget.max_retries },
    next_allowed_action: flags.next ?? "Observe the current state and confirm identity before acting.",
    limitations: ["A text checkpoint does not keep the browser session; use `browser actor save` for the state and confirm it when resuming."],
  };
  const errors = validateDocument(cp, "resume-checkpoint");
  if (errors.length) throw new ContractError(errors.join("\n"));
  writeFileSync(join(runDir, "resume-checkpoint.json"), `${JSON.stringify(cp, null, 2)}\n`);
  out(flags, cp, `checkpoint saved: ${join(runDir, "resume-checkpoint.json")}`);
  return 0;
}

// The only command that reaches the network: npm installs playwright-core (and, with --chromium, the
// Playwright Chromium build) into the per-user runtime, never into the skill folder or the project.
async function cmdSetup({ flags }) {
  const { runtimeDir } = await import("./lib/driver/playwright.mjs");
  const dir = runtimeDir();
  const pkg = flags.chromium ? "playwright" : "playwright-core";
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  const steps = [[npm, ["install", "--no-audit", "--no-fund", "--prefix", dir, `${pkg}@1`]]];
  if (flags.chromium) steps.push([process.platform === "win32" ? "npx.cmd" : "npx", ["--prefix", dir, "playwright", "install", "chromium"]]);
  if (flags["dry-run"]) {
    out(flags, { runtime: dir, steps: steps.map(([b, a]) => [b, ...a].join(" ")) }, steps.map(([b, a]) => [b, ...a].join(" ")).join("\n"));
    return 0;
  }
  mkdirSync(dir, { recursive: true });
  if (!existsSync(join(dir, "package.json"))) writeFileSync(join(dir, "package.json"), '{ "name": "journey-qa-runtime", "private": true }\n');
  const { spawnSync } = await import("node:child_process");
  for (const [bin, args] of steps) {
    const r = spawnSync(bin, args, { stdio: flags.json ? "ignore" : "inherit", shell: process.platform === "win32" });
    if (r.status !== 0) {
      out(flags, { ok: false, runtime: dir, failed: [bin, ...args].join(" ") }, `setup failed: ${[bin, ...args].join(" ")}`);
      return 1;
    }
  }
  return cmdDoctor({ flags });
}

async function cmdDoctor({ flags }) {
  const checks = [];
  const major = Number(process.versions.node.split(".")[0]);
  checks.push({ check: "node>=20", ok: major >= 20, detail: process.versions.node });
  try {
    const { loadPlaywright } = await import("./lib/driver/playwright.mjs");
    const { pw, from } = await loadPlaywright();
    checks.push({ check: "playwright", ok: true, detail: from });
    if (!flags.quick) {
      let launched = null;
      for (const channel of flags.browser === "chromium" ? [undefined] : ["chrome", undefined]) {
        try {
          const b = await pw.chromium.launch({ headless: true, channel });
          launched = `${channel ?? "chromium"} ${b.version()}`;
          await b.close();
          break;
        } catch (e) {
          checks.push({ check: `launch ${channel ?? "chromium"}`, ok: false, detail: e.message.split("\n")[0] });
        }
      }
      checks.push({ check: "browser", ok: !!launched, detail: launched ?? "no browser started" });
    }
  } catch (e) {
    checks.push({ check: "playwright", ok: false, detail: e.message });
  }
  for (const f of ["SKILL.md", "roles/actor.md", "assets/schemas/run-report.schema.json"]) checks.push({ check: f, ok: existsSync(join(SKILL_ROOT, f)) });
  const ready = checks.filter((c) => !c.check.startsWith("launch ")).every((c) => c.ok);
  out(flags, { version: VERSION, ready, browser_ready: checks.some((c) => c.check === "browser" && c.ok), checks },
    `Journey QA ${VERSION} — ${ready ? "ready" : "NOT READY"}\n${checks.map((c) => `${c.ok ? "✓" : "✗"} ${c.check}${c.detail ? ` — ${c.detail}` : ""}`).join("\n")}${ready ? "" : "\nWithout a browser the method still works with the host's own tools (see references/browser.md)."}`);
  return ready ? 0 : 1;
}

const HELP = {
  _: `Journey QA ${VERSION} — jqa <command>

Run
  init --product ID [--out DIR] [--journey ID]     prepares the directory (templates, ledger, trace)
  doctor [--quick]                                 checks Node, Playwright and the browser
  setup [--chromium] [--dry-run]                   installs playwright-core in the per-user runtime (the only network call)
  browser start --allow ORIGIN [--headed] [--browser chrome|chromium|msedge] [--cdp URL]
  browser actor add ID [--role observer] [--mobile] [--max-actions N]
  browser handshake --actor ID [--url URL]         checks capabilities and writes session-binding.json
  browser <action> --actor ID ...                  snapshot | open | click | fill | type | select | check | press | scroll | wait | dialog | screenshot | text | tabs
  browser <oracle> --actor ID --as observer        eval | console | network | storage | cookies
  browser budget --actor ID --add N --reason T     recorded intervention
  browser allow ORIGIN · browser actor save ID · browser actor add ID --restore · browser status · browser stop
Actors
  actor packet CARD.json [--strict]                PACKET.md + restricted act.mjs in actors/ID/
  actor spawn ID --host claude|codex|pi|custom [--exec] [--background] [--model M] [--bare] [--cmd "…{PROMPT_FILE}"]
  actor wait ID · actor outcome ID OUTCOME "reason"
Evidence and report
  evidence add --kind K --summary T [--file F] · evidence list · evidence verify
  trace note "text" --actor ID · trace show
  report sync|validate|render run-report.json      syncs with the ledger, validates, renders HTML [--lang pt|en]
  validate FILE [--strict]                         any contract of the pack (detects the kind) · check-capabilities FILE
  checkpoint --actor ID --next "..."
Architecture and infrastructure
  arch init --title T [--source ADR.md]            review of an ADR/RFC/proposal
  arch scan [DIR]                                  finds ADRs/RFCs and lists decisions
  arch validate|render review.json [--lang pt|en] · arch questions review.json [--format md|slack] [--out F]
  arch rules review.json [--merge product-profile.json]
  infra terraform plan.json · infra k8s objects.json [--fail-on critical|high|medium|low] [--run DIR]
  infra checklist [--topic resilience|data|security|observability|delivery|cost|governance]

Use --json for structured output. Exit codes: 0 ok, 1 action failed, 2 invalid contract, 3 guardrail refusal.`,
};

export async function main(argv = process.argv.slice(2)) {
  const [command, ...rest] = argv;
  let parsed;
  try {
    parsed = parseArgs(rest);
  } catch (e) {
    console.error(`ERROR: ${e.message}`);
    return 2;
  }
  try {
    if (["arch", "infra", "validate"].includes(command)) archModule = await import("./lib/arch.mjs");
    switch (command) {
      case undefined: case "help": case "--help": case "-h": console.log(HELP._); return 0;
      case "version": case "--version": console.log(VERSION); return 0;
      case "init": cmdInit(parsed); return 0;
      case "validate": return cmdValidate(parsed);
      case "check-capabilities": return cmdValidate({ ...parsed, flags: { ...parsed.flags, kind: "session-binding" } });
      case "report": return await cmdReport(parsed);
      case "evidence": return cmdEvidence(parsed);
      case "trace": return cmdTrace(parsed);
      case "actor": return await cmdActor(parsed);
      case "browser": return await cmdBrowser(parsed);
      case "checkpoint": return await cmdCheckpoint(parsed);
      case "doctor": return await cmdDoctor(parsed);
      case "setup": return await cmdSetup(parsed);
      case "arch": return await archModule.cmdArch(parsed, { out, runDirFrom, langFrom });
      case "infra": return await archModule.cmdInfra(parsed, { out, runDirFrom, langFrom });
      default: console.error(`Unknown command: ${command}\n\n${HELP._}`); return 2;
    }
  } catch (e) {
    if (e instanceof ContractError) {
      console.error(`ERROR${e.code && e.code !== "contract" ? ` [${e.code}]` : ""}: ${e.message}`);
      return GUARDRAIL_CODES.has(e.code) ? 3 : 2;
    }
    if (["ENOENT", "EACCES", "EEXIST", "EISDIR", "ENOTDIR", "EPERM"].includes(e.code)) {
      console.error(`ERROR [io]: ${e.message}`);
      return 2;
    }
    console.error(`UNEXPECTED ERROR: ${e.stack ?? e.message}`);
    return 1;
  }
}

if (resolve(process.argv[1] ?? "") === fileURLToPath(import.meta.url)) {
  main().then((code) => process.exit(code ?? 0));
}
