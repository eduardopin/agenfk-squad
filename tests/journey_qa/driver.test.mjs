// Browser driver tests against a fake Playwright (JQA_PLAYWRIGHT): what a failed action on a secret field leaves in
// the run. No browser and no network beyond the driver's own loopback port.
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtempSync, readdirSync, readFileSync, statSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { send, startDriver } from "../../skills/journey-qa/scripts/lib/driver/client.mjs";

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
