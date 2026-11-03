// Journey QA browser daemon. One process per run directory.
// Holds Playwright contexts (one per actor), enforces the origin allowlist and
// action budgets, and writes every observation to the evidence ledger + trace.
// Started by `jqa browser start`; talks HTTP on 127.0.0.1 with a bearer token.
import { randomBytes } from "node:crypto";
import { appendFileSync, chmodSync, closeSync, constants, existsSync, fstatSync, mkdirSync, openSync, readFileSync, readlinkSync, realpathSync, rmSync, statSync, writeFileSync } from "node:fs";
import http from "node:http";
import { basename, dirname, join, sep } from "node:path";
import { fileURLToPath } from "node:url";
import { appendEvidence, appendTrace } from "../ledger.mjs";
import { loadPlaywright } from "./playwright.mjs";
import { ACTOR_COMMANDS, ACTOR_FLAGS, actorWorkdir, recordOutcome } from "../packet.mjs";

const HERE = dirname(fileURLToPath(import.meta.url));
const SNAPSHOT_FN = readFileSync(join(HERE, "snapshot.js"), "utf8").replace(/^\s*\/\/.*$/gm, "").trim();
export const DRIVER_VERSION = "2.1.0";

const runDir = process.argv[2];
const cfg = JSON.parse(readFileSync(process.argv[3], "utf8"));
const jqaDir = join(runDir, ".jqa");
mkdirSync(jqaDir, { recursive: true });
const log = (msg) => appendFileSync(join(jqaDir, "driver.log"), `${new Date().toISOString()} ${msg}\n`);

const INTERACTIONS = new Set([
  "open", "click", "dblclick", "fill", "type", "press", "select", "check", "uncheck", "hover", "upload", "scroll",
  "back", "forward", "reload", "dialog", "keydown", "keyup", "hold", "tab", "close-tab", "drag",
]);
const PRIVILEGED = new Set(["eval", "console", "network", "storage", "cookies"]);
const COORDINATOR = new Set(["actor-add", "actor-save", "budget", "stop", "handshake", "allow"]);

const state = {
  pw: null, browser: null, mode: cfg.cdp ? "driver_cdp" : "driver_launch", browserName: null, browserVersion: null,
  actors: new Map(), allow: new Set(cfg.allow ?? []), traceSeq: 0, lastActivity: Date.now(), queue: new Map(),
  actorTokens: new Map(), // token -> actor id, for spawned actors that talk to the driver directly
};

// ---------- helpers ----------
const nowIso = () => new Date().toISOString();
const SECRET_PARAM = /^(token|access_token|id_token|code|key|api_key|apikey|secret|password|pass|sig|signature|auth|session)$/i;
function maskUrl(u) {
  try {
    const url = new URL(u);
    for (const k of [...url.searchParams.keys()]) if (SECRET_PARAM.test(k)) url.searchParams.set(k, "***");
    if (url.username || url.password) { url.username = "***"; url.password = ""; }
    if (/token|code|key|secret|auth|session/i.test(url.hash)) url.hash = "#***";
    return url.href;
  } catch {
    return String(u).slice(0, 300);
  }
}
function originAllowed(u) {
  if (!u || u === "about:blank" || u.startsWith("data:") || u.startsWith("blob:") || u.startsWith("chrome-error:")) return true;
  try {
    return state.allow.has(new URL(u).origin);
  } catch {
    return false;
  }
}
const JWT = /eyJ[\w-]{8,}\.[\w-]{8,}\.[\w-]{8,}/g;
const maskSecrets = (text) => String(text).replace(JWT, "[jwt hidden]");
function trace(event) {
  return appendTrace(runDir, event);
}
function evidence(item) {
  return appendEvidence(runDir, { captured_by: "driver", origin: "observed", ...item });
}
const err = (code, message) => Object.assign(new Error(message), { code });

// ---------- browser ----------
async function launch() {
  const { pw, from } = await loadPlaywright();
  state.pw = pw;
  log(`playwright ${from}`);
  if (cfg.cdp) {
    state.browser = await pw.chromium.connectOverCDP(cfg.cdp);
    state.browserName = "chrome (cdp)";
  } else {
    const headless = !cfg.headed;
    const order = cfg.browser === "chromium" ? ["chromium"] : cfg.browser === "msedge" ? ["msedge"] : cfg.browser === "chrome" ? ["chrome"] : ["chrome", "chromium"];
    let last;
    for (const b of order) {
      try {
        state.browser = await pw.chromium.launch({ headless, channel: b === "chromium" ? undefined : b, args: cfg.args ?? [] });
        state.browserName = b;
        break;
      } catch (e) {
        last = e;
        log(`launch ${b} failed: ${e.message.split("\n")[0]}`);
      }
    }
    if (!state.browser) throw err("browser_launch_failed", `Could not start the browser (${order.join(", ")}): ${last?.message.split("\n")[0]}. Install Chrome or run \`npx playwright install chromium\`.`);
  }
  state.browserVersion = state.browser.version();
}

function wirePage(actor, page) {
  page.__jqa = { inflight: 0, nav: 0 };
  page.on("request", () => page.__jqa.inflight++);
  const done = () => { page.__jqa.inflight = Math.max(0, page.__jqa.inflight - 1); };
  page.on("requestfinished", async (req) => {
    done();
    let status = null;
    try { status = (await req.response())?.status() ?? null; } catch {}
    actor.net.push({ t: nowIso(), method: req.method(), url: maskUrl(req.url()), status, type: req.resourceType() });
    if (actor.net.length > 800) actor.net.shift();
  });
  page.on("requestfailed", (req) => {
    done();
    actor.net.push({ t: nowIso(), method: req.method(), url: maskUrl(req.url()), status: "failed", type: req.resourceType(), error: req.failure()?.errorText ?? null });
    if (actor.net.length > 800) actor.net.shift();
  });
  page.on("console", (m) => {
    actor.console.push({ t: nowIso(), type: m.type(), text: m.text().slice(0, 600) });
    if (actor.console.length > 600) actor.console.shift();
    if (m.type() === "error") actor.consoleErrors++;
  });
  page.on("pageerror", (e) => {
    actor.console.push({ t: nowIso(), type: "pageerror", text: String(e.message).slice(0, 600) });
    actor.consoleErrors++;
  });
  page.on("framenavigated", (f) => { if (f === page.mainFrame()) page.__jqa.nav++; });
  page.on("dialog", (d) => {
    actor.dialog = { dialog: d, type: d.type(), message: d.message().slice(0, 500), default: d.defaultValue?.() ?? "" };
    actor.notices.push(`${d.type()} dialog open: ${JSON.stringify(d.message().slice(0, 200))} → use \`dialog accept\` or \`dialog dismiss\``);
  });
  page.on("download", async (dl) => {
    try {
      const tmp = join(jqaDir, `download-${Date.now()}-${dl.suggestedFilename().replace(/[^A-Za-z0-9_.-]/g, "_")}`);
      await dl.saveAs(tmp);
      const e = evidence({ kind: "file", actor_id: actor.id, sourceFile: tmp, url: maskUrl(dl.url()), summary: `Download received: ${dl.suggestedFilename()}` });
      rmSync(tmp, { force: true });
      actor.notices.push(`download saved as evidence ${e.evidence_id} (${dl.suggestedFilename()})`);
    } catch (e) {
      actor.notices.push(`download failed: ${e.message.split("\n")[0]}`);
    }
  });
  page.on("close", () => {
    actor.pages = actor.pages.filter((p) => p !== page);
    if (actor.page === page) actor.page = actor.pages.at(-1) ?? null;
  });
}

async function addActor(id, flags = {}) {
  if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/.test(id)) throw err("bad_actor", "invalid actor_id");
  if (state.actors.has(id)) throw err("actor_exists", `Actor ${id} already exists`);
  const opts = {
    acceptDownloads: true,
    locale: flags.locale ?? cfg.locale ?? "pt-BR",
    timezoneId: flags.timezone ?? cfg.timezone ?? undefined,
    viewport: parseViewport(flags.viewport ?? cfg.viewport ?? "1280x800"),
    serviceWorkers: "block",
  };
  if (flags.mobile) Object.assign(opts, state.pw.devices["Pixel 7"] ?? {}, { locale: opts.locale });
  const restore = join(jqaDir, "state", `${id}.json`);
  if (flags.restore && existsSync(restore)) opts.storageState = restore;
  const context = await state.browser.newContext(opts);
  const actor = {
    id, role: flags.role === "observer" ? "observer" : "actor", context, pages: [], page: null, net: [], console: [],
    consoleErrors: 0, notices: [], dialog: null, refs: null, actions: 0, startedAt: null,
    maxActions: Number(flags["max-actions"] ?? cfg.maxActions ?? 60), maxMinutes: Number(flags["max-minutes"] ?? cfg.maxMinutes ?? 20),
    mobile: !!flags.mobile, contextId: randomBytes(4).toString("hex"),
  };
  await context.route("**/*", (route) => {
    const req = route.request();
    if (req.isNavigationRequest() && req.frame().parentFrame() === null && !originAllowed(req.url())) {
      actor.notices.push(`navigation blocked (outside the allowlist): ${maskUrl(req.url())}`);
      trace({ actor_id: id, as: "driver", cmd: "blocked-navigation", url: maskUrl(req.url()), ok: false });
      return route.abort("blockedbyclient");
    }
    return route.fallback();
  });
  context.on("page", (p) => {
    wirePage(actor, p);
    actor.pages.push(p);
    if (actor.page && p !== actor.page) actor.notices.push(`new tab opened [${actor.pages.length - 1}] and selected`);
    actor.page = p;
  });
  state.actors.set(id, actor);
  const page = await context.newPage();
  actor.page = page;
  trace({ actor_id: id, as: "coordinator", cmd: "actor-add", args: { role: actor.role, mobile: actor.mobile, restore: !!opts.storageState }, ok: true });
  const others = [...state.actors.values()].filter((a) => a !== actor);
  if (others.length) {
    const e = evidence({
      kind: "tool_result", actor_id: id,
      summary: `Isolated browser context created for ${id} (ctx ${actor.contextId}); other contexts: ${others.map((o) => `${o.id}/${o.contextId}`).join(", ")}. Playwright BrowserContexts share no cookies, storage or cache.`,
      locator: `jqa-driver:context:${actor.contextId}`,
    });
    state.isolationEvidence = [...(state.isolationEvidence ?? []), e.evidence_id];
  }
  return actor;
}

function parseViewport(v) {
  const m = /^(\d{3,4})x(\d{3,4})$/.exec(String(v));
  return m ? { width: Number(m[1]), height: Number(m[2]) } : { width: 1280, height: 800 };
}

function getActor(id) {
  const a = state.actors.get(id);
  if (!a) throw err("unknown_actor", `Actor ${id} does not exist. Create it with \`jqa browser actor add ${id}\`.`);
  return a;
}

async function ensurePage(actor) {
  if (!actor.page || actor.page.isClosed()) {
    actor.page = await actor.context.newPage();
  }
  return actor.page;
}

async function settle(page, maxMs = 2500) {
  try { await page.waitForLoadState("domcontentloaded", { timeout: Math.min(maxMs, 8000) }); } catch {}
  const end = Date.now() + maxMs;
  let quietSince = Date.now();
  while (Date.now() < end) {
    if (page.isClosed()) return;
    if (page.__jqa.inflight > 0) quietSince = Date.now();
    else if (Date.now() - quietSince > 300) return;
    await new Promise((r) => setTimeout(r, 60));
  }
}

// ---------- observation ----------
const isObserver = (actor) => (actor.currentAs ?? "actor") !== "actor";
const refKey = (actor) => (isObserver(actor) ? "journey-qa.refs.observer" : "journey-qa.refs");

async function snapshot(actor, { full = false, frames = false, record = true, reason = "snapshot" } = {}) {
  const page = await ensurePage(actor);
  const observer = isObserver(actor);
  if (actor.dialog) return { text: `${actor.dialog.type} dialog pending: ${JSON.stringify(actor.dialog.message)} — answer with \`dialog accept|dismiss\` before observing.`, evidence: [] };
  const targets = [page.mainFrame()];
  if (frames) for (const f of page.frames()) if (f !== page.mainFrame()) targets.push(f);
  const sections = [];
  const refFrames = [];
  let total = 0;
  for (let i = 0; i < targets.length; i++) {
    const f = targets[i];
    const prefix = i === 0 ? "" : `f${i}`;
    try {
      const r = await f.evaluate(`(${SNAPSHOT_FN})(${JSON.stringify({ full, prefix, key: refKey(actor) })})`);
      refFrames.push({ frame: f, meta: r.meta, prefix });
      total += r.count;
      if (i === 0) sections.push(`# ${r.title || "(untitled)"} — ${maskUrl(r.url)}\n# actor=${actor.id} tab=${actor.pages.indexOf(page)} scroll=${r.scroll.y}/${r.scroll.max}px${r.truncated ? " (truncated: use --full)" : ""}`);
      else sections.push(`# frame f${i}: ${maskUrl(r.url)}`);
      sections.push(r.lines.join("\n") || "(no visible elements)");
    } catch (e) {
      sections.push(`# frame ${i}: inaccessible (${e.message.split("\n")[0]})`);
    }
  }
  const refs = { frames: refFrames, nav: page.__jqa.nav, page };
  // The observer keeps its own refs and never consumes the actor's notices.
  if (observer) actor.observerRefs = refs;
  else actor.refs = refs;
  const notices = observer ? [] : actor.notices.splice(0);
  if (!observer && actor.consoleErrors) notices.push(`${actor.consoleErrors} console error(s) since the last observation (visible to the observer)`), (actor.consoleErrors = 0);
  let text = sections.join("\n");
  if (notices.length) text += `\n# notices:\n${notices.map((n) => `- ${n}`).join("\n")}`;
  const out = { text, evidence: [], refs: total };
  if (record) {
    const e = evidence({ kind: "dom", actor_id: actor.id, url: maskUrl(page.url()), content: text, ext: "txt", summary: `${reason}: ${page.url() ? maskUrl(page.url()).slice(0, 160) : ""} (${total} refs)` });
    out.evidence.push(e.evidence_id);
    out.text = `${text}\n# evidence ${e.evidence_id}`;
  }
  return out;
}

async function screenshot(actor, { full = false, ref = null, reason = "screenshot" } = {}) {
  const page = await ensurePage(actor);
  if (actor.dialog) return null;
  let buf;
  if (ref) buf = await (await resolveRef(actor, ref)).handle.screenshot({ type: "jpeg", quality: 70, timeout: 8000 });
  else buf = await page.screenshot({ type: "jpeg", quality: 65, fullPage: !!full, timeout: 15000 });
  const e = evidence({ kind: "screenshot", actor_id: actor.id, url: maskUrl(page.url()), content: buf, ext: "jpg", summary: `${reason}${ref ? ` ${ref}` : ""}: ${maskUrl(page.url()).slice(0, 160)}` });
  actor.lastShotFile = e.file;
  return e.evidence_id;
}

async function resolveRef(actor, ref) {
  const m = /^(?:f(\d+))?e(\d+)$/.exec(String(ref ?? ""));
  if (!m) throw err("bad_ref", `Invalid reference ${JSON.stringify(ref)}. Use the refs from the last snapshot (e.g. e12).`);
  const refs = isObserver(actor) ? actor.observerRefs : actor.refs;
  if (!refs) throw err("no_snapshot", "Run `snapshot` before acting: refs come from the current observation.");
  const page = actor.page;
  if (refs.page !== page || refs.nav !== page.__jqa.nav) throw err("stale_ref", "The page changed since the last snapshot. Observe again before acting.");
  const frameIdx = m[1] ? Number(m[1]) : 0;
  const idx = Number(m[2]) - 1;
  const fr = refs.frames[frameIdx];
  if (!fr || !fr.meta[idx]) throw err("bad_ref", `Ref ${ref} does not exist in the last snapshot.`);
  const handle = (await fr.frame.evaluateHandle(([i, k]) => window[Symbol.for(k)]?.refs?.[i] ?? null, [idx, refKey(actor)])).asElement();
  if (!handle) throw err("stale_ref", `Ref ${ref} expired. Observe again.`);
  const connected = await handle.evaluate((el) => el.isConnected);
  if (!connected) throw err("stale_ref", `Element ${ref} left the page. Observe again.`);
  return { handle, meta: fr.meta[idx], frame: fr.frame };
}

// ---------- commands ----------
function checkBudget(actor) {
  if (actor.startedAt && (Date.now() - actor.startedAt) / 60000 > actor.maxMinutes) {
    throw err("budget_exhausted", `Time budget of actor ${actor.id} exhausted (${actor.maxMinutes} min). Stop, or ask the coordinator for \`budget\`.`);
  }
  if (actor.actions >= actor.maxActions) {
    throw err("budget_exhausted", `Action budget of actor ${actor.id} exhausted (${actor.maxActions}). End the journey, or ask the coordinator for \`budget\`.`);
  }
}

async function interact(actor, cmd, args, flags, fn) {
  if ((flags.as ?? "actor") === "actor") checkBudget(actor);
  actor.startedAt ??= Date.now();
  const page = await ensurePage(actor);
  const urlBefore = maskUrl(page.url());
  const started = Date.now();
  let target = null;
  let error = null;
  let detail = null;
  let secret = !!flags.secret;
  try {
    const r = await fn(page);
    target = r?.target ?? null;
    detail = r?.detail ?? null;
    secret ||= !!r?.secret;
  } catch (e) {
    error = e;
  }
  const notCounted = error && ["bad_ref", "stale_ref", "no_snapshot", "bad_args", "no_dialog"].includes(error.code);
  // Observer/coordinator interactions are interventions recorded in the trace, not the actor's budget.
  if (!notCounted && (flags.as ?? "actor") === "actor") actor.actions++;
  const active = actor.page ?? page;
  if (!active.isClosed()) await settle(active, flags.settle ? Number(flags.settle) : 2500);
  const evidenceIds = [];
  let snap = null;
  if (!error || !["bad_ref", "stale_ref", "no_snapshot", "bad_args"].includes(error.code)) {
    if (cfg.autoShot !== false && !flags["no-shot"] && !secret) {
      try { const id = await screenshot(actor, { reason: `after ${cmd}` }); if (id) evidenceIds.push(id); } catch {}
    }
    if (!flags["no-snapshot"]) {
      try {
        snap = await snapshot(actor, { reason: `after ${cmd}` });
        evidenceIds.push(...snap.evidence);
      } catch (e) {
        snap = { text: `(observation failed: ${e.message.split("\n")[0]})` };
      }
    }
  }
  const urlAfter = active.isClosed() ? null : maskUrl(active.url());
  trace({
    actor_id: actor.id, as: flags.as ?? "actor", via: flags.via ?? "direct", cmd, args: sanitizeArgs(cmd, args, secret), target,
    ok: !error, error: error ? { code: error.code ?? "action_failed", message: error.message.split("\n")[0].slice(0, 300) } : null,
    url_before: urlBefore, url_after: urlAfter, evidence_ids: evidenceIds, actions_used: actor.actions, duration_ms: Date.now() - started,
  });
  const head = `${error ? "ERROR" : "ok"} ${cmd}${target ? ` ${target}` : ""}${detail ? ` — ${detail}` : ""} · actions ${actor.actions}/${actor.maxActions}${urlAfter && urlAfter !== urlBefore ? ` · url ${urlAfter}` : ""}`;
  const errLine = error ? `\n${error.code ? `[${error.code}] ` : ""}${error.message.split("\n")[0].slice(0, 400)}` : "";
  return { ok: !error, text: `${head}${errLine}${evidenceIds.length ? `\n# evidence ${evidenceIds.join(", ")}` : ""}${snap ? `\n${snap.text}` : ""}`, evidence: evidenceIds, code: error?.code };
}

function sanitizeArgs(cmd, args, secret) {
  if ((cmd === "fill" || cmd === "type") && secret) {
    return [args[0], `[hidden: ${String(args[1] ?? "").length} characters]`];
  }
  if (cmd === "open") return [maskUrl(args[0])];
  if (cmd === "upload") return [args[0], ...args.slice(1).map((f) => f.split(/[\\/]/).pop())];
  return args.map((a) => String(a).slice(0, 200));
}

const describe = (ref, meta) => `${ref} ${meta.role}${meta.name ? ` ${JSON.stringify(meta.name.slice(0, 60))}` : ""}`;

async function withRef(actor, ref, fn) {
  const r = await resolveRef(actor, ref);
  const out = await fn(r.handle, r);
  if (out && typeof out === "object") return { target: describe(ref, r.meta), detail: out.detail ?? null, secret: !!out.secret };
  return { target: describe(ref, r.meta), detail: out ?? null };
}

// Marks a field as sensitive inside the page (WeakSet, no DOM change) so snapshots mask its value.
async function markSecret(h, forced) {
  return h.evaluate((el, f) => {
    const secret = f || el.type === "password" || el.autocomplete === "one-time-code";
    if (secret) (window[Symbol.for("journey-qa.secret")] ??= new WeakSet()).add(el);
    return secret;
  }, !!forced);
}

const commands = {
  async open(actor, [url], flags) {
    if (!url) throw err("bad_args", "Usage: open URL");
    if (!state.allow.size) throw err("no_allowlist", "No origin allowed. Restart with `jqa browser start --allow https://your-target`.");
    if (!originAllowed(url)) throw err("origin_blocked", `Origin outside the allowlist: ${maskUrl(url)}. Allowed: ${[...state.allow].join(", ")}`);
    return interact(actor, "open", [url], flags, async (page) => {
      const resp = await page.goto(url, { waitUntil: "domcontentloaded", timeout: Number(flags.timeout ?? 30000) });
      return { target: maskUrl(url), detail: resp ? `HTTP ${resp.status()}` : null };
    });
  },
  click: (a, [ref], f) => interact(a, f.double ? "dblclick" : "click", [ref], f, () => withRef(a, ref, (h) => (f.double ? h.dblclick({ timeout: 8000 }) : h.click({ timeout: 8000, button: f.button ?? "left" })))),
  dblclick: (a, [ref], f) => interact(a, "dblclick", [ref], f, () => withRef(a, ref, (h) => h.dblclick({ timeout: 8000 }))),
  hover: (a, [ref], f) => interact(a, "hover", [ref], f, () => withRef(a, ref, (h) => h.hover({ timeout: 8000 }))),
  check: (a, [ref], f) => interact(a, "check", [ref], f, () => withRef(a, ref, (h) => h.check({ timeout: 8000 }))),
  uncheck: (a, [ref], f) => interact(a, "uncheck", [ref], f, () => withRef(a, ref, (h) => h.uncheck({ timeout: 8000 }))),
  fill: (a, [ref, ...rest], f) => interact(a, "fill", [ref, rest.join(" ")], f, () => withRef(a, ref, async (h) => {
    const secret = await markSecret(h, f.secret);
    await h.fill(rest.join(" "), { timeout: 8000 });
    return { secret };
  })),
  type: (a, [ref, ...rest], f) => interact(a, "type", [ref, rest.join(" ")], f, () => withRef(a, ref, async (h) => {
    const secret = await markSecret(h, f.secret);
    await h.focus();
    await a.page.keyboard.type(rest.join(" "), { delay: Number(f.delay ?? 25) });
    return { secret };
  })),
  select: (a, [ref, ...values], f) => interact(a, "select", [ref, ...values], f, () => withRef(a, ref, async (h) => {
    const chosen = await h.selectOption(values.map((v) => v), { timeout: 8000 }).catch(async () => h.selectOption(values.map((label) => ({ label })), { timeout: 8000 }));
    return `selected ${chosen.join(", ")}`;
  })),
  upload: (a, [ref, ...files], f) => interact(a, "upload", [ref, ...files], f, () => {
    for (const file of files) if (!existsSync(file)) throw err("bad_args", `File not found: ${file}`);
    return withRef(a, ref, (h) => h.setInputFiles(files, { timeout: 8000 }));
  }),
  press: (a, [key], f) => interact(a, "press", [key, ...(f.ref ? [f.ref] : [])], f, async (page) => {
    if (!key) throw err("bad_args", "Usage: press KEY [--ref eN] (e.g. Enter, Tab, Escape, Control+A)");
    if (f.ref) return withRef(a, f.ref, (h) => h.press(key, { timeout: 8000 }));
    await page.keyboard.press(key);
    return { target: key };
  }),
  keydown: (a, [key], f) => interact(a, "keydown", [key], f, async (page) => { await page.keyboard.down(key); return { target: key }; }),
  keyup: (a, [key], f) => interact(a, "keyup", [key], f, async (page) => { await page.keyboard.up(key); return { target: key }; }),
  hold: (a, [key], f) => interact(a, "hold", [key, f.ms ?? 500], f, async (page) => {
    const ms = Math.min(Number(f.ms ?? 500), 10000);
    await page.keyboard.down(key);
    await new Promise((r) => setTimeout(r, ms));
    await page.keyboard.up(key);
    return { target: key, detail: `${ms} ms` };
  }),
  scroll: (a, [where = "down"], f) => interact(a, "scroll", [where], f, async (page) => {
    if (/^(f\d+)?e\d+$/.test(where)) return withRef(a, where, (h) => h.scrollIntoViewIfNeeded({ timeout: 8000 }));
    const vh = (page.viewportSize()?.height ?? 800) * 0.8;
    await page.mouse.wheel(0, where === "up" ? -vh : where === "top" ? -1e6 : where === "bottom" ? 1e6 : vh);
    return { target: where };
  }),
  back: (a, _x, f) => interact(a, "back", [], f, (page) => page.goBack({ timeout: 15000 })),
  forward: (a, _x, f) => interact(a, "forward", [], f, (page) => page.goForward({ timeout: 15000 })),
  reload: (a, _x, f) => interact(a, "reload", [], f, (page) => page.reload({ waitUntil: "domcontentloaded", timeout: 30000 })),
  dialog: (a, [decision, ...rest], f) => interact(a, "dialog", [decision], f, async () => {
    if (!a.dialog) throw err("no_dialog", "No pending dialog.");
    const d = a.dialog;
    a.dialog = null;
    if (decision === "accept") await d.dialog.accept(f.text ?? rest.join(" ") ?? undefined);
    else if (decision === "dismiss") await d.dialog.dismiss();
    else throw err("bad_args", "Usage: dialog accept|dismiss [--text ANSWER]");
    return { target: `${d.type} ${JSON.stringify(d.message.slice(0, 80))}`, detail: decision };
  }),
  tab: (a, [index], f) => interact(a, "tab", [index], f, async () => {
    const p = a.pages[Number(index)];
    if (!p) throw err("bad_args", `Tab ${index} does not exist (tabs: ${a.pages.length}).`);
    a.page = p;
    await p.bringToFront();
    return { target: `tab ${index}` };
  }),
  "close-tab": (a, _x, f) => interact(a, "close-tab", [], f, async (page) => { await page.close(); return { target: "current tab" }; }),
  async wait(actor, args, flags) {
    const page = await ensurePage(actor);
    const timeout = Math.min(Number(flags.timeout ?? 10000), 60000);
    const started = Date.now();
    let ok = true;
    let msg = "";
    try {
      if (flags.text) await page.getByText(flags.text, { exact: false }).first().waitFor({ state: "visible", timeout });
      else if (flags.gone) await page.getByText(flags.gone, { exact: false }).first().waitFor({ state: "hidden", timeout });
      else if (flags.url) await page.waitForURL((u) => u.href.includes(flags.url), { timeout });
      else await new Promise((r) => setTimeout(r, Math.min(Number(flags.ms ?? args[0] ?? 1000), 30000)));
      msg = `condition met in ${Date.now() - started} ms`;
    } catch (e) {
      ok = false;
      msg = `condition not met in ${timeout} ms`;
    }
    const snap = flags["no-snapshot"] ? { text: "", evidence: [] } : await snapshot(actor, { reason: "after wait" });
    trace({ actor_id: actor.id, as: flags.as ?? "actor", cmd: "wait", args: [flags.text ?? flags.gone ?? flags.url ?? flags.ms ?? args[0] ?? "1000"], ok, evidence_ids: snap.evidence, duration_ms: Date.now() - started });
    return { ok, text: `${ok ? "ok" : "TIMEOUT"} wait — ${msg}\n${snap.text}`, evidence: snap.evidence, code: ok ? undefined : "wait_timeout" };
  },
  async snapshot(actor, _a, flags) {
    const s = await snapshot(actor, { full: !!flags.full, frames: !!flags.frames });
    trace({ actor_id: actor.id, as: flags.as ?? "actor", cmd: "snapshot", ok: true, evidence_ids: s.evidence });
    return { ok: true, text: s.text, evidence: s.evidence };
  },
  async screenshot(actor, _a, flags) {
    const id = await screenshot(actor, { full: !!flags.full, ref: flags.ref ?? null });
    trace({ actor_id: actor.id, as: flags.as ?? "actor", cmd: "screenshot", ok: !!id, evidence_ids: id ? [id] : [] });
    return { ok: !!id, text: id ? `ok screenshot ${id} → ${join(runDir, actor.lastShotFile)}` : "screenshot unavailable (dialog pending)", evidence: id ? [id] : [] };
  },
  async text(actor, _a, flags) {
    const page = await ensurePage(actor);
    const t = (await page.evaluate(() => document.body?.innerText ?? "")).slice(0, flags.full ? 200000 : 20000);
    const e = evidence({ kind: "dom", actor_id: actor.id, url: maskUrl(page.url()), content: t, ext: "txt", summary: `visible text: ${maskUrl(page.url()).slice(0, 160)}` });
    trace({ actor_id: actor.id, as: flags.as ?? "actor", cmd: "text", ok: true, evidence_ids: [e.evidence_id] });
    return { ok: true, text: `${t}\n# evidence ${e.evidence_id}`, evidence: [e.evidence_id] };
  },
  async tabs(actor) {
    const lines = await Promise.all(actor.pages.map(async (p, i) => `${p === actor.page ? "*" : " "} [${i}] ${await p.title().catch(() => "")} — ${maskUrl(p.url())}`));
    return { ok: true, text: lines.join("\n") || "(no tabs)" };
  },
  // ---- privileged (observer/coordinator) ----
  async eval(actor, args, flags) {
    const page = await ensurePage(actor);
    const expr = args.join(" ");
    let failed = null;
    const value = await page.evaluate(`(async () => (${expr}))()`).catch((e) => { failed = e.message.split("\n")[0]; return { error: failed }; });
    const text = maskSecrets(JSON.stringify(value, null, 2)?.slice(0, 20000) ?? "undefined");
    const e = evidence({ kind: "tool_result", actor_id: actor.id, url: maskUrl(page.url()), content: `// eval by ${flags.as}\n// ${expr.slice(0, 500)}\n${text}`, ext: "txt", summary: `eval (observer) on ${actor.id}: ${expr.slice(0, 120)}` });
    trace({ actor_id: actor.id, as: flags.as, cmd: "eval", args: [expr.slice(0, 300)], ok: !failed, evidence_ids: [e.evidence_id] });
    if (failed) return { ok: false, code: "eval_error", text: `ERROR eval: ${failed}\n# evidence ${e.evidence_id}`, evidence: [e.evidence_id] };
    return { ok: true, text: `${text}\n# evidence ${e.evidence_id}`, evidence: [e.evidence_id] };
  },
  async console(actor, _a, flags) {
    const rows = actor.console.slice(-Number(flags.last ?? 100));
    const text = rows.map((r) => `${r.t} [${r.type}] ${r.text}`).join("\n") || "(no messages)";
    const e = evidence({ kind: "log", actor_id: actor.id, content: text, ext: "txt", summary: `console of ${actor.id} (${rows.length} lines)` });
    trace({ actor_id: actor.id, as: flags.as, cmd: "console", ok: true, evidence_ids: [e.evidence_id] });
    return { ok: true, text: `${text}\n# evidence ${e.evidence_id}`, evidence: [e.evidence_id] };
  },
  async network(actor, _a, flags) {
    let rows = actor.net;
    if (flags.filter) rows = rows.filter((r) => r.url.includes(flags.filter));
    if (!flags.all) rows = rows.filter((r) => !["image", "font", "stylesheet", "media"].includes(r.type));
    rows = rows.slice(-Number(flags.last ?? 100));
    const text = rows.map((r) => `${r.t} ${r.method} ${r.status} ${r.type} ${r.url}${r.error ? ` (${r.error})` : ""}`).join("\n") || "(no requests)";
    const e = evidence({ kind: "network", actor_id: actor.id, content: text, ext: "txt", summary: `network of ${actor.id} (${rows.length} requests${flags.filter ? `, filter ${flags.filter}` : ""})` });
    trace({ actor_id: actor.id, as: flags.as, cmd: "network", ok: true, evidence_ids: [e.evidence_id] });
    return { ok: true, text: `${text}\n# evidence ${e.evidence_id}`, evidence: [e.evidence_id] };
  },
  async storage(actor, _a, flags) {
    const page = await ensurePage(actor);
    const data = await page.evaluate(() => {
      const SECRET = /(token|secret|auth|session|password|pass|jwt|credential|api[_-]?key|refresh)/i;
      const dump = (s) => { const o = {}; for (let i = 0; i < s.length; i++) { const k = s.key(i); const v = s.getItem(k) ?? ""; o[k] = SECRET.test(k) ? `[hidden: ${v.length} characters]` : v; } return o; };
      try { return { origin: location.origin, localStorage: dump(localStorage), sessionStorage: dump(sessionStorage) }; } catch (e) { return { error: String(e) }; }
    });
    const text = maskSecrets(JSON.stringify(data, null, 2).slice(0, 50000));
    const e = evidence({ kind: "tool_result", actor_id: actor.id, url: maskUrl(page.url()), content: text, ext: "json", summary: `storage of ${actor.id} on ${data.origin ?? "?"} (observer)` });
    trace({ actor_id: actor.id, as: flags.as, cmd: "storage", ok: true, evidence_ids: [e.evidence_id] });
    return { ok: true, text: `${text}\n# evidence ${e.evidence_id}`, evidence: [e.evidence_id] };
  },
  async cookies(actor, _a, flags) {
    const list = (await actor.context.cookies()).map((c) => ({ name: c.name, domain: c.domain, path: c.path, httpOnly: c.httpOnly, secure: c.secure, sameSite: c.sameSite, value: "[hidden]" }));
    const text = JSON.stringify(list, null, 2);
    const e = evidence({ kind: "tool_result", actor_id: actor.id, content: text, ext: "json", summary: `cookies of ${actor.id} (${list.length}, values hidden)` });
    trace({ actor_id: actor.id, as: flags.as, cmd: "cookies", ok: true, evidence_ids: [e.evidence_id] });
    return { ok: true, text: `${text}\n# evidence ${e.evidence_id}`, evidence: [e.evidence_id] };
  },
};

const SELFTEST_HTML = `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>jqa self-test</title></head><body>
<main><h1>Journey QA self-test</h1><label for="n">Name</label><input id="n"><button id="b" type="button">Confirm</button>
<p id="s" role="status"></p><p id="k" role="status"></p></main>
<script>let down=0;document.getElementById('b').onclick=()=>{document.getElementById('s').textContent='clicked:'+document.getElementById('n').value};
addEventListener('keydown',e=>{if(e.key==='ArrowRight'&&!down)down=performance.now()});addEventListener('keyup',e=>{if(e.key==='ArrowRight'&&down){document.getElementById('k').textContent='held:'+Math.round(performance.now()-down);down=0}});</script></body></html>`;

async function handshake(actor, flags) {
  const results = {};
  const proofs = {};
  const page = await actor.context.newPage();
  const mark = (cap, ok, proof, note = "") => { results[cap] = { ok, note }; if (proof) proofs[cap] = proof; };
  try {
    await page.goto("about:blank");
    await page.setContent(SELFTEST_HTML);
    actor.page = page;
    const s1 = await snapshot(actor, { reason: "handshake: observe_dom" });
    mark("observe_dom", /button "Confirm" \[e\d+\]/.test(s1.text), s1.evidence[0]);
    const shot = await screenshot(actor, { reason: "handshake: observe_screenshot" });
    mark("observe_screenshot", !!shot, shot);
    const input = s1.text.match(/textbox "Name" \[(e\d+)\]/)?.[1];
    const btn = s1.text.match(/button "Confirm" \[(e\d+)\]/)?.[1];
    await (await resolveRef(actor, input)).handle.fill("Ana");
    await (await resolveRef(actor, btn)).handle.click();
    const typed = await page.locator("#s").textContent();
    const s2 = await snapshot(actor, { reason: "handshake: click + type_text" });
    mark("click", typed === "clicked:Ana", s2.evidence[0]);
    mark("type_text", typed === "clicked:Ana", s2.evidence[0]);
    await page.focus("#n");
    await page.keyboard.press("Tab");
    const focused = await page.evaluate(() => document.activeElement?.id);
    await page.keyboard.down("ArrowRight");
    await new Promise((r) => setTimeout(r, 350));
    await page.keyboard.up("ArrowRight");
    const held = Number(((await page.locator("#k").textContent()) ?? "").split(":")[1] ?? 0);
    const s3 = await snapshot(actor, { reason: "handshake: keyboard + continuous_input" });
    mark("keyboard", focused === "b", s3.evidence[0]);
    mark("continuous_input", held >= 250, s3.evidence[0], `key held ${held} ms`);
    const t0 = Date.now();
    await page.getByText("held:").waitFor({ timeout: 3000 });
    mark("wait", Date.now() - t0 < 3000, s3.evidence[0]);
    mark("capture_evidence", !!(s1.evidence[0] && shot), s1.evidence[0]);
    mark("console", true, null, "passive console capture available to the observer");
    mark("network", true, null, "passive network capture available to the observer");
    mark("upload", true, null, "setInputFiles available; not exercised in the self-test");
    mark("download", true, null, "downloads saved as evidence; not exercised in the self-test");
  } finally {
    await page.close().catch(() => {});
    actor.page = actor.pages.at(-1) ?? null;
    actor.refs = null;
    actor.observerRefs = null;
    actor.notices = actor.notices.filter((n) => !n.startsWith("new tab"));
  }
  let navProof = null;
  if (flags.url) {
    const r = await commands.open(actor, [flags.url], { ...flags, "no-shot": false });
    navProof = r.ok ? r.evidence[0] : null;
    actor.actions = Math.max(0, actor.actions - 1); // handshake does not consume the journey budget
    mark("navigate", r.ok, navProof, r.ok ? `target ${maskUrl(flags.url)}` : r.text.split("\n")[1]);
  } else {
    mark("navigate", true, proofs.observe_dom, "about:blank navigation in the self-test; target not opened");
  }
  const multi = state.actors.size > 1;
  const caps = Object.entries(results).map(([capability, r]) => ({
    capability, status: r.ok ? (proofs[capability] ? "verified" : "declared") : "unavailable",
    tool_name: `jqa browser (${state.mode})`, proof_ref: proofs[capability] ?? null, notes: r.note,
  }));
  caps.push({
    capability: "isolate_browser_sessions", status: multi ? "verified" : "declared", tool_name: "jqa browser actor add",
    proof_ref: multi ? (state.isolationEvidence ?? [])[0] ?? null : null, notes: multi ? "one BrowserContext per actor" : "create a second actor to verify",
  });
  caps.push({ capability: "isolate_model_context", status: "unknown", tool_name: null, proof_ref: null, notes: "depends on the host; the driver does not control the model context" });
  const binding = {
    capability_report_version: "1.1",
    adapter: "jqa_driver",
    host: { name: flags.host ?? "not provided", version: null, model: flags.model ?? null },
    browser: { name: state.browserName, version: state.browserVersion, connection_mode: state.mode, session_alias: actor.id, allowed_origins: [...state.allow] },
    capabilities: caps,
    required_capabilities: ["navigate", "click", "type_text", "keyboard", "wait", "capture_evidence"],
    observation_alternatives: ["observe_dom", "observe_screenshot"],
    isolation: { model_context: "unknown", browser_sessions: multi ? "isolated" : "single_actor", proof_refs: multi ? state.isolationEvidence ?? [] : [] },
    limitations: [
      "Self-test run on the driver's own local page; actions on the target depend on the product.",
      ...(flags.url ? [] : ["Target not opened in the handshake (use --url)."]),
    ],
  };
  writeFileSync(join(runDir, "session-binding.json"), `${JSON.stringify(binding, null, 2)}\n`);
  trace({ actor_id: actor.id, as: "coordinator", cmd: "handshake", ok: Object.values(results).every((r) => r.ok), evidence_ids: Object.values(proofs) });
  const lines = caps.map((c) => `${c.status === "verified" ? "✓" : c.status === "declared" ? "·" : c.status === "unknown" ? "?" : "✗"} ${c.capability}${c.proof_ref ? ` (${c.proof_ref})` : ""}${c.notes ? ` — ${c.notes}` : ""}`);
  return { ok: true, text: `handshake ${state.browserName} ${state.browserVersion} (${state.mode})\n${lines.join("\n")}\n→ ${join(runDir, "session-binding.json")}`, evidence: Object.values(proofs) };
}

async function dispatch(body) {
  const { cmd, actor: actorId, args = [], flags = {} } = body;
  const as = flags.as ?? "actor";
  if (!["actor", "observer", "coordinator"].includes(as)) throw err("bad_args", `invalid --as: ${as}`);
  if (PRIVILEGED.has(cmd) && !["observer", "coordinator"].includes(as)) {
    throw err("privileged", `\`${cmd}\` is an observer command. In a human_ui journey the actor uses only the interface. Use --as observer in the observer's context.`);
  }
  if (cmd === "status") return status();
  if (cmd === "stop") {
    setTimeout(shutdown, 50);
    return { ok: true, text: "driver stopping" };
  }
  if (cmd === "allow") {
    for (const o of args) state.allow.add(new URL(o).origin);
    trace({ as: "coordinator", cmd: "allow", args, ok: true });
    return { ok: true, text: `allowlist: ${[...state.allow].join(", ")}` };
  }
  if (cmd === "actor-add") {
    const a = await addActor(args[0], flags);
    return { ok: true, text: `actor ${a.id} created (${a.role}, context ${a.contextId}${a.mobile ? ", mobile" : ""}) · budget ${a.maxActions} actions/${a.maxMinutes} min` };
  }
  const actor = getActor((cmd === "actor-save" ? args[0] : null) ?? actorId ?? (state.actors.size === 1 ? [...state.actors.keys()][0] : undefined));
  actor.currentAs = as;
  if (cmd === "actor-save") {
    mkdirSync(join(jqaDir, "state"), { recursive: true });
    const path = join(jqaDir, "state", `${actor.id}.json`);
    await actor.context.storageState({ path });
    try { chmodSync(path, 0o600); } catch {}
    trace({ actor_id: actor.id, as: "coordinator", cmd: "actor-save", ok: true });
    return { ok: true, text: `session state saved to ${path} (contains cookies: do not attach to reports)` };
  }
  if (cmd === "actor-token") {
    const t = randomBytes(24).toString("hex");
    state.actorTokens.set(t, actor.id);
    trace({ actor_id: actor.id, as: "coordinator", cmd: "actor-token", ok: true });
    return { ok: true, text: t, token: t };
  }
  if (cmd === "actor-token-revoke") {
    for (const [t, id] of state.actorTokens) if (id === actor.id) state.actorTokens.delete(t);
    return { ok: true, text: `tokens of ${actor.id} revoked` };
  }
  if (cmd === "budget") {
    actor.maxActions += Number(flags.add ?? 0);
    if (flags.minutes) actor.maxMinutes += Number(flags.minutes);
    trace({ actor_id: actor.id, as: "coordinator", cmd: "budget", args: { add: flags.add, minutes: flags.minutes, reason: flags.reason ?? null }, ok: true, intervention: true });
    return { ok: true, text: `budget of ${actor.id}: ${actor.maxActions} actions, ${actor.maxMinutes} min (intervention recorded)` };
  }
  if (cmd === "handshake") return handshake(actor, flags);
  const fn = commands[cmd];
  if (!fn) throw err("unknown_command", `Unknown command: ${cmd}`);
  return fn(actor, args, { ...flags, as });
}

function status() {
  const actors = [...state.actors.values()].map((a) => ({
    id: a.id, role: a.role, context: a.contextId, actions: a.actions, max_actions: a.maxActions, max_minutes: a.maxMinutes,
    minutes_used: a.startedAt ? Math.round(((Date.now() - a.startedAt) / 60000) * 10) / 10 : 0, tabs: a.pages.length, url: a.page ? maskUrl(a.page.url()) : null,
    dialog: a.dialog ? a.dialog.type : null,
  }));
  return { ok: true, text: JSON.stringify({ driver: DRIVER_VERSION, browser: state.browserName, version: state.browserVersion, mode: state.mode, allow: [...state.allow], actors }, null, 2) };
}

async function shutdown() {
  try { await state.browser?.close(); } catch {}
  try { rmSync(join(jqaDir, "driver.json"), { force: true }); } catch {}
  log("driver stopped");
  process.exit(0);
}

// ---------- server ----------
const token = randomBytes(24).toString("hex");
try {
  await launch();
} catch (e) {
  writeFileSync(join(jqaDir, "driver.error.json"), JSON.stringify({ code: e.code ?? "launch_failed", message: e.message }));
  log(`failure: ${e.message}`);
  process.exit(3);
}
// An actor token can only drive its own actor through the interface, note a decision or declare its outcome.
const ACTOR_CMDS = new Set(ACTOR_COMMANDS);
const ACTOR_FLAG_NAMES = new Set(ACTOR_FLAGS.map((f) => f.slice(2)));
function actorRequest(actorId, body) {
  if (body.cmd === "note") {
    trace({ actor_id: actorId, as: "actor", via: "act", cmd: "note", note: String(body.args?.[0] ?? "").slice(0, 2000), ok: true });
    return { ok: true, text: "note recorded" };
  }
  if (body.cmd === "done") {
    const r = recordOutcome(runDir, actorId, String(body.args?.[0] ?? ""), String(body.args?.[1] ?? ""));
    return { ok: true, text: `outcome ${r.outcome} recorded (${r.evidence_id})` };
  }
  if (!ACTOR_CMDS.has(body.cmd)) return { ok: false, code: "privileged", text: `\`${body.cmd}\` is not available to the actor` };
  const flags = { as: "actor", via: "act" };
  for (const [k, v] of Object.entries(body.flags ?? {})) {
    if (!ACTOR_FLAG_NAMES.has(k)) return { ok: false, code: "privileged", text: `flag --${k} is not available to the actor` };
    flags[k] = v;
  }
  const args = (body.args ?? []).map(String);
  if (body.cmd === "upload") {
    // The driver reads the file, not the actor: with an actor token it may only hand over files from the actor's own
    // folder, or a jailed actor could make the browser upload any host file to the product. The bytes are copied at
    // check time (no symlink followed, same inode as the checked path) into a driver-only folder, and that copy is what
    // the browser uploads, so swapping the path after the check changes nothing.
    let root = null;
    try { root = realpathSync(actorWorkdir(runDir, actorId)); } catch {}
    const refuse = { ok: false, code: "privileged", text: "upload: only files in the actor's own folder can be uploaded" };
    const staged = [args[0]];
    for (const f of args.slice(1)) {
      let data = null;
      try {
        const real = realpathSync(f);
        if (!root || !real.startsWith(root + sep)) return refuse;
        const fd = openSync(real, constants.O_RDONLY | constants.O_NOFOLLOW);
        try {
          const st = fstatSync(fd);
          if (!st.isFile() || st.size > 50 * 1024 * 1024) return refuse;
          // Judge the file actually opened, not a name: on Linux the kernel reports the open descriptor's path, so a
          // directory swapped between the check and the open is caught. Elsewhere (no jail exists there) compare inodes.
          if (existsSync("/proc/self/fd")) {
            if (!readlinkSync(`/proc/self/fd/${fd}`).startsWith(root + sep)) return refuse;
          } else {
            const now = statSync(real);
            if (st.ino !== now.ino || st.dev !== now.dev) return refuse;
          }
          data = readFileSync(fd);
        } finally { closeSync(fd); }
        const dir = join(jqaDir, "uploads", randomBytes(8).toString("hex"));
        mkdirSync(dir, { recursive: true, mode: 0o700 });
        const copy = join(dir, basename(real));
        writeFileSync(copy, data, { mode: 0o600 });
        staged.push(copy);
      } catch {
        return refuse;
      }
    }
    args.splice(0, args.length, ...staged);
  }
  return { cmd: body.cmd, actor: actorId, args, flags };
}

const server = http.createServer((req, res) => {
  const auth = String(req.headers.authorization ?? "").replace(/^Bearer /, "");
  const actorOnly = auth !== token ? state.actorTokens.get(auth) : null;
  if (req.method !== "POST" || req.url !== "/cmd" || (auth !== token && !actorOnly)) {
    res.writeHead(403).end();
    return;
  }
  let raw = "";
  req.on("data", (c) => { raw += c; if (raw.length > 2_000_000) req.destroy(); });
  req.on("end", async () => {
    state.lastActivity = Date.now();
    let body;
    try { body = JSON.parse(raw); } catch { res.writeHead(400).end(); return; }
    if (actorOnly) {
      let r;
      try { r = actorRequest(actorOnly, body); } catch (e) { r = { ok: false, code: e.code ?? "bad_args", text: `ERROR ${body.cmd}: ${e.message}` }; }
      if (!r.cmd) { res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(r)); return; }
      body = r;
    }
    const key = body.actor ?? "_";
    const prev = state.queue.get(key) ?? Promise.resolve();
    const run = prev.then(async () => {
      try {
        return await dispatch(body);
      } catch (e) {
        log(`error ${body.cmd}: ${e.stack ?? e.message}`);
        return { ok: false, code: e.code ?? "driver_error", text: `ERROR ${body.cmd}: ${e.code ? `[${e.code}] ` : ""}${String(e.message).split("\n")[0]}` };
      }
    });
    state.queue.set(key, run.catch(() => {}));
    const result = await run;
    res.writeHead(200, { "content-type": "application/json" }).end(JSON.stringify(result));
  });
});
server.listen(0, "127.0.0.1", () => {
  const info = { pid: process.pid, port: server.address().port, token, started_at: nowIso(), driver: DRIVER_VERSION, browser: state.browserName, version: state.browserVersion, mode: state.mode, allow: [...state.allow] };
  const path = join(jqaDir, "driver.json");
  writeFileSync(path, JSON.stringify(info, null, 2), { mode: 0o600 });
  try { chmodSync(path, 0o600); } catch {}
  log(`driver ready on 127.0.0.1:${info.port} (${state.browserName} ${state.browserVersion})`);
});
setInterval(() => {
  if (Date.now() - state.lastActivity > (cfg.idleMinutes ?? 60) * 60000) shutdown();
}, 30000).unref();
process.on("SIGTERM", shutdown);
process.on("SIGINT", shutdown);
