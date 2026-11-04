// Browser driver tests against a fake Playwright (JQA_PLAYWRIGHT): what a failed action on a secret field leaves in
// the run. No browser and no network beyond the driver's own loopback port.
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { chmodSync, mkdtempSync, readdirSync, readFileSync, statSync, symlinkSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { driverInfo, send, startDriver } from "../../skills/journey-qa/scripts/lib/driver/client.mjs";
import { prepareActorWorkdir } from "../../skills/journey-qa/scripts/lib/packet.mjs";

const ROOT = join(fileURLToPath(new URL(".", import.meta.url)), "..", "..");
const jqa = (args, opts = {}) => spawnSync(process.execPath, [join(ROOT, "skills", "journey-qa", "scripts", "jqa.mjs"), ...args], { encoding: "utf8", ...opts });

// One page with the fields below. `fail` makes fill and keyboard.type throw an error that quotes the typed text, the
// worst case for the trace: the value would leak through the arguments and through the error message.
const FAKE_PLAYWRIGHT = `"use strict";
const FIELDS = [
  { name: "Password", type: "password", autocomplete: "current-password", fail: true },
  { name: "Code", type: "text", autocomplete: "one-time-code", fail: true },
  { name: "API key", type: "text", autocomplete: "off", fail: true },
  { name: "Name", type: "text", autocomplete: "name", fail: true },
  { name: "New password", type: "password", autocomplete: "new-password", fail: false },
];
const boom = (what, text) => { throw new Error(what + ": Timeout 8000ms exceeded while typing \\"" + text + "\\"\\nCall log:\\n  - " + text); };
function makePage() {
  let focused = null;
  const handle = (field) => {
    const el = { type: field.type, autocomplete: field.autocomplete, isConnected: true };
    return {
      evaluate: async (fn, arg) => { globalThis.window ??= {}; return fn(el, arg); },
      fill: async (text) => { if (field.fail) boom("elementHandle.fill", text); },
      focus: async () => { focused = field; },
      screenshot: async () => Buffer.from("fake jpeg"),
    };
  };
  const frame = {
    url: () => "https://app.example.test/login",
    evaluate: async () => ({
      title: "Sign in", url: "https://app.example.test/login", scroll: { y: 0, max: 0 }, truncated: false, count: FIELDS.length,
      meta: FIELDS.map((f) => ({ role: "textbox", name: f.name })), lines: FIELDS.map((f, i) => "textbox " + JSON.stringify(f.name) + " [e" + (i + 1) + "]"),
    }),
    evaluateHandle: async (_fn, [i]) => ({ asElement: () => handle(FIELDS[i]) }),
  };
  return {
    on: () => {}, url: () => "https://app.example.test/login", isClosed: () => false, mainFrame: () => frame, frames: () => [frame],
    waitForLoadState: async () => {}, screenshot: async () => Buffer.from("fake jpeg"), viewportSize: () => ({ width: 1280, height: 800 }),
    close: async () => {}, title: async () => "Sign in",
    keyboard: { type: async (text) => { if (focused?.fail) boom("keyboard.type", text); } },
  };
}
const context = () => {
  const onPage = [];
  return {
    route: async () => {}, on: (ev, fn) => { if (ev === "page") onPage.push(fn); }, close: async () => {},
    newPage: async () => { const p = makePage(); for (const fn of onPage) fn(p); return p; },
  };
};
const browser = { newContext: async () => context(), version: () => "fake-1", close: async () => {} };
module.exports = { chromium: { launch: async () => browser }, devices: {} };
`;

const fakeDir = mkdtempSync(join(tmpdir(), "jqa-fake-pw-"));
const FAKE = join(fakeDir, "fake-playwright.js");
writeFileSync(FAKE, FAKE_PLAYWRIGHT);
const RUN = mkdtempSync(join(tmpdir(), "jqa-driver-"));
process.env.JQA_PLAYWRIGHT = FAKE;
try {
  // The fake page claims app.example.test; the driver checks every frame it drives against the allowlist.
  await startDriver(RUN, { "start-timeout": 30000, allow: "https://app.example.test" });
} finally {
  delete process.env.JQA_PLAYWRIGHT;
}
after(async () => { await send(RUN, { cmd: "stop" }, 10000).catch(() => {}); });

const call = (cmd, args = [], flags = {}) => send(RUN, { cmd, actor: "ana", args, flags: { settle: "1", ...flags } }, 30000);
assert.equal((await send(RUN, { cmd: "actor-add", args: ["ana"], flags: {} })).ok, true);

const traceEntries = () => readFileSync(join(RUN, "trace.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));
const screenshots = () => readFileSync(join(RUN, "evidence", "ledger.jsonl"), "utf8").split("\n").filter((l) => l.includes('"kind":"screenshot"')).length;
// Every byte the driver wrote for this run: trace, ledger, evidence files, driver log.
function runFiles(dir = RUN) {
  return readdirSync(dir).flatMap((n) => { const p = join(dir, n); return statSync(p).isDirectory() ? runFiles(p) : [p]; });
}
const filesHolding = (value) => runFiles().filter((p) => readFileSync(p).includes(value)).map((p) => p.slice(RUN.length + 1));

/** Runs one failing action from a fresh snapshot; returns the response, its trace entry and the screenshots it took. */
async function failing(cmd, args, flags = {}) {
  assert.equal((await call("snapshot")).ok, true);
  const shots = screenshots();
  const res = await call(cmd, args, flags);
  const entry = traceEntries().at(-1);
  assert.equal(entry.cmd, cmd);
  assert.equal(entry.ok, false, "the fake makes this action fail");
  return { res, entry, shots: screenshots() - shots };
}

test("a fill that fails on a password field keeps the value out of the run and takes no screenshot", async () => {
  const value = "pw-Hunter2-fails";
  const { res, entry, shots } = await failing("fill", ["e1", value]);
  assert.deepEqual(entry.args, ["e1", `[hidden: ${value.length} characters]`]);
  assert.deepEqual(filesHolding(value), []);
  assert.equal(shots, 0, "no automatic screenshot of a secret field");
  assert.ok(!res.text.includes(value), "the error shown to the caller does not echo the value either");
});

test("a type that fails on a one-time-code field keeps the code out of the run", async () => {
  const value = "otp-482913";
  const { entry, shots } = await failing("type", ["e2", value], { delay: "0" });
  assert.deepEqual(entry.args, ["e2", `[hidden: ${value.length} characters]`]);
  assert.deepEqual(filesHolding(value), []);
  assert.equal(shots, 0);
});

test("--secret on a plain field still masks when the fill fails", async () => {
  const value = "sk-live-plain-field";
  const { entry, shots } = await failing("fill", ["e3", value], { secret: true });
  assert.deepEqual(entry.args, ["e3", `[hidden: ${value.length} characters]`]);
  assert.deepEqual(filesHolding(value), []);
  assert.equal(shots, 0);
});

test("a fill whose ref does not resolve keeps the value hidden: the field was never classified", async () => {
  const value = "pw-typed-into-a-stale-ref";
  assert.equal((await call("snapshot")).ok, true);
  const res = await call("fill", ["e9", value]);
  assert.equal(res.code, "bad_ref");
  assert.deepEqual(traceEntries().at(-1).args, ["e9", `[hidden: ${value.length} characters]`]);
  assert.deepEqual(filesHolding(value), []);
});

test("a fill that fails on a plain field records the value and the screenshot (the checks above can see a leak)", async () => {
  const value = "Ana Example";
  const { entry, shots } = await failing("fill", ["e4", value]);
  assert.deepEqual(entry.args, ["e4", value]);
  assert.deepEqual(filesHolding(value), ["trace.jsonl"]);
  assert.equal(shots, 1);
});

test("a fill that succeeds on a password field is masked as before", async () => {
  const value = "pw-New-Secret-ok";
  assert.equal((await call("snapshot")).ok, true);
  const shots = screenshots();
  const res = await call("fill", ["e5", value]);
  assert.equal(res.ok, true, res.text);
  assert.deepEqual(traceEntries().at(-1).args, ["e5", `[hidden: ${value.length} characters]`]);
  assert.deepEqual(filesHolding(value), []);
  assert.equal(screenshots() - shots, 0);
});

// ---------- the actor folder travels with the actor token ----------
// Each folder below sits under a private base the driver's own environment does not name, the way spawn and driver end
// up when XDG_RUNTIME_DIR, TMPDIR or HOME differ between the two processes.
const privateBase = () => mkdtempSync(join(tmpdir(), "jqa-actor-base-"));
const asActor = async (token, body) => {
  const res = await fetch(`http://127.0.0.1:${driverInfo(RUN).port}/cmd`, { method: "POST", headers: { authorization: `Bearer ${token}`, "content-type": "application/json" }, body: JSON.stringify(body) });
  return res.json();
};
const tokenFor = (flags) => send(RUN, { cmd: "actor-token", actor: "ana", args: [], flags: { as: "coordinator", ...flags } });
const revoke = () => send(RUN, { cmd: "actor-token-revoke", actor: "ana", args: [], flags: { as: "coordinator" } });
const staged = (bytes) => filesHolding(bytes).filter((p) => p.startsWith(join(".jqa", "uploads")));

test("an actor token carries the folder spawn prepared, and uploads are judged against that folder", async () => {
  const wd = prepareActorWorkdir(RUN, "ana", { env: { XDG_RUNTIME_DIR: privateBase() } });
  const t = await tokenFor({ workdir: wd });
  assert.equal(t.ok, true, t.text);
  try {
    writeFileSync(join(wd, "photo.txt"), "bytes-from-the-handed-folder");
    const res = await asActor(t.token, { cmd: "upload", args: ["e1", join(wd, "photo.txt")], flags: {} });
    assert.notEqual(res.code, "privileged", res.text);
    assert.equal(staged("bytes-from-the-handed-folder").length, 1, "the driver staged the file from the folder it was handed");
    // The folder is checked again at upload time: one others can now enter is refused, and named for what it is.
    if (process.platform !== "win32") {
      chmodSync(wd, 0o755);
      const late = await asActor(t.token, { cmd: "upload", args: ["e1", join(wd, "photo.txt")], flags: {} });
      assert.equal(late.code, "unsafe_workdir", late.text);
      chmodSync(wd, 0o700);
    }
  } finally {
    await revoke();
  }
});

test("the driver issues no actor token without a private folder of that actor", async () => {
  const base = privateBase();
  const missing = await tokenFor({});
  assert.equal(missing.ok, false);
  assert.equal(missing.code, "bad_args", missing.text);
  assert.equal(missing.token, undefined);
  const bruno = prepareActorWorkdir(RUN, "bruno", { env: { XDG_RUNTIME_DIR: base } });
  const notAna = await tokenFor({ workdir: bruno });
  assert.equal(notAna.code, "unsafe_workdir", notAna.text);
  assert.equal(notAna.token, undefined);
  if (process.platform === "win32") return;
  const wd = prepareActorWorkdir(RUN, "ana", { env: { XDG_RUNTIME_DIR: base } });
  chmodSync(wd, 0o755);
  const open = await tokenFor({ workdir: wd });
  assert.equal(open.code, "unsafe_workdir", open.text);
  assert.equal(open.token, undefined);
});

test("actor spawn hands its folder to the driver: an upload works when spawn and driver see different environments", async (t) => {
  if (process.platform === "win32") return t.skip("a custom host goes through cmd.exe there; the token and upload checks above cover Windows");
  const card = { ...JSON.parse(readFileSync(join(ROOT, "skills", "journey-qa", "assets", "templates", "actor-card.json"), "utf8")), actor_id: "ana" };
  const cardFile = join(fakeDir, "card.json");
  writeFileSync(cardFile, JSON.stringify(card));
  assert.equal(jqa(["actor", "packet", cardFile, "--run", RUN]).status, 0);
  // The actor writes a file in its own folder and asks the browser to upload it through act.mjs and its token.
  const actor = "const f=require('fs');f.writeFileSync('photo.txt','bytes-from-a-spawned-actor');" +
    "const r=require('child_process').spawnSync(process.execPath,['act.mjs','upload','e1','photo.txt'],{encoding:'utf8'});console.log(r.stdout+r.stderr)";
  const r = jqa(["actor", "spawn", "ana", "--run", RUN, "--exec", "--host", "custom", "--cmd", `"${process.execPath}" -e "${actor}"`, "--unsandboxed"],
    { env: { ...process.env, XDG_RUNTIME_DIR: privateBase() } });
  assert.equal(r.status, 0, r.stdout + r.stderr);
  const said = readFileSync(join(RUN, "actors", "ana", "transcript.md"), "utf8");
  assert.ok(!/only files in the actor's own folder/.test(said), said);
  assert.equal(staged("bytes-from-a-spawned-actor").length, 1, said);
});

test("actor spawn with --run through a symlink to the driver's run hands over a folder the driver accepts", async (t) => {
  if (process.platform === "win32") return t.skip("a custom host goes through cmd.exe there; the token and upload checks above cover Windows");
  const link = join(fakeDir, "run-link");
  symlinkSync(RUN, link, "dir");
  const card = { ...JSON.parse(readFileSync(join(ROOT, "skills", "journey-qa", "assets", "templates", "actor-card.json"), "utf8")), actor_id: "ana" };
  const cardFile = join(fakeDir, "card-link.json");
  writeFileSync(cardFile, JSON.stringify(card));
  assert.equal(jqa(["actor", "packet", cardFile, "--run", link]).status, 0);
  const actor = "const f=require('fs');f.writeFileSync('photo.txt','bytes-through-a-linked-run');" +
    "const r=require('child_process').spawnSync(process.execPath,['act.mjs','upload','e1','photo.txt'],{encoding:'utf8'});console.log(r.stdout+r.stderr)";
  const r = jqa(["actor", "spawn", "ana", "--run", link, "--exec", "--host", "custom", "--cmd", `"${process.execPath}" -e "${actor}"`, "--unsandboxed"],
    { env: { ...process.env, XDG_RUNTIME_DIR: privateBase() } });
  assert.equal(r.status, 0, r.stdout + r.stderr);
  const said = readFileSync(join(RUN, "actors", "ana", "transcript.md"), "utf8");
  assert.equal(staged("bytes-through-a-linked-run").length, 1, said);
});
