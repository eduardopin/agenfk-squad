// Origin allowlist beyond the first request: server redirects, pages restored from history and subframes. The guard
// logic runs as plain functions; the driver runs against a fake Playwright (JQA_PLAYWRIGHT) that routes only the first
// request of a navigation, as Playwright does. No browser and no network beyond the driver's own loopback port.
import { test, after } from "node:test";
import assert from "node:assert/strict";
import { existsSync, mkdtempSync, readFileSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { send, startDriver } from "../../skills/journey-qa/scripts/lib/driver/client.mjs";
import { blockedFrames, originAllowed } from "../../skills/journey-qa/scripts/lib/driver/guard.mjs";

const APP = "https://app.example.test";
const allow = new Set([APP]);

test("originAllowed takes the allowlisted origin and URLs with no origin of their own, nothing else", () => {
  for (const u of [`${APP}/`, `${APP}/a?b=1#c`, "", "about:blank", "about:srcdoc", "about:blank#top", "data:text/html,x", "blob:https://x.test/1", "chrome-error://chromewebdata/"]) {
    assert.equal(originAllowed(allow, u), true, u);
  }
  for (const u of ["https://evil.example.net/", "http://app.example.test/", "https://app.example.test:8443/", "https://sub.app.example.test/", "about:settings", "not a url"]) {
    assert.equal(originAllowed(allow, u), false, u);
  }
});

test("blockedFrames reports a blocked main frame for the whole tab, and blocked subframes one by one", () => {
  const frame = (url) => ({ url: () => url });
  const page = (main, subs, closed = false) => ({ isClosed: () => closed, mainFrame: () => main, frames: () => [main, ...subs] });
  const okMain = frame(`${APP}/`);
  const badSub = frame("https://evil.example.net/widget");
  const blankSub = frame("about:srcdoc");
  const badMain = frame("https://evil.example.net/landing");
  const p1 = page(okMain, [blankSub, badSub]);
  const p2 = page(badMain, [frame("https://other.example.net/x")]);
  const p3 = page(frame("https://evil.example.net/closed"), [], true);
  const hits = blockedFrames([p1, p2, p3], allow);
  assert.deepEqual(hits.map((h) => [h.page === p1 ? "p1" : "p2", h.url, h.main]), [
    ["p1", "https://evil.example.net/widget", false],
    ["p2", "https://evil.example.net/landing", true],
  ]);
  assert.equal(hits[0].frame, badSub);
  assert.deepEqual(blockedFrames([page(okMain, [blankSub])], allow), []);
});

// ---------- driver against a fake Playwright ----------
// The fake site: `redirect` is a 3xx the route never sees, `frame` an iframe the page embeds, `go` where the page's
// one button navigates its own frame. Every click, key and element screenshot is appended to HITS, so a test can
// prove that nothing reached a blocked origin. A frame on STICKY cannot be navigated away (its goto times out).
const fakeDir = mkdtempSync(join(tmpdir(), "jqa-fake-pw-origin-"));
const HITS = join(fakeDir, "hits.log");
const FAKE_PLAYWRIGHT = `"use strict";
const { appendFileSync } = require("node:fs");
const HITS = ${JSON.stringify(HITS)};
const APP = "https://app.example.test", EVIL = "https://evil.example.net", STICKY = "https://sticky.example.net";
const SITE = {
  [APP + "/home"]: { title: "Home", go: APP + "/redirect" },
  [APP + "/direct"]: { title: "Direct", go: EVIL + "/landing" },
  [APP + "/redirect"]: { redirect: EVIL + "/landing" },
  [EVIL + "/landing"]: { title: "Evil landing", go: EVIL + "/next" },
  [APP + "/framed"]: { title: "Framed", frame: EVIL + "/widget" },
  [APP + "/framed-redirect"]: { title: "Framed redirect", frame: APP + "/to-evil" },
  [APP + "/to-evil"]: { redirect: EVIL + "/widget" },
  [EVIL + "/widget"]: { title: "Evil widget", go: EVIL + "/next" },
  [APP + "/framed-inner"]: { title: "Framed inner", frame: APP + "/inner" },
  [APP + "/inner"]: { title: "Inner", go: APP + "/to-sticky" },
  [APP + "/to-sticky"]: { redirect: STICKY + "/widget" },
  [STICKY + "/widget"]: { title: "Sticky widget", go: STICKY + "/next" },
};
const hit = (line) => appendFileSync(HITS, line + "\\n");
function makePage(ctx) {
  const listeners = {};
  const page = { children: [], history: ["about:blank"], idx: 0 };
  const emit = (ev, arg) => { for (const fn of listeners[ev] ?? []) fn(arg); };
  const makeFrame = (parent) => {
    const f = {
      _url: "about:blank",
      url: () => f._url,
      parentFrame: () => parent,
      goto: async (u) => { if (f._url.startsWith(STICKY)) throw new Error("frame.goto: Timeout 10000ms exceeded."); return navigate(f, u); },
      evaluate: async (src) => {
        const doc = SITE[f._url];
        const prefix = (/"prefix":"(f\\d*)"/.exec(String(src)) ?? [])[1] ?? "";
        const go = doc?.go ? 1 : 0;
        return { title: doc?.title ?? "", url: f._url, scroll: { y: 0, max: 0 }, truncated: false, count: go,
          meta: go ? [{ role: "button", name: "Go" }] : [], lines: go ? ['button "Go" [' + prefix + 'e1]'] : [] };
      },
      evaluateHandle: async (_fn, [i]) => {
        const doc = SITE[f._url];
        const el = { isConnected: true, type: "button", autocomplete: "" };
        const handle = {
          evaluate: async (fn, arg) => { globalThis.window ??= {}; return fn(el, arg); },
          click: async () => { hit("click " + f._url); if (doc.go) await navigate(f, doc.go); },
          screenshot: async () => { hit("shot " + f._url); return Buffer.from("fake jpeg"); },
        };
        return { asElement: () => (doc?.go && i === 0 ? handle : null) };
      },
    };
    return f;
  };
  const main = makeFrame(null);
  function commit(frame, url, fromHistory = false) {
    frame._url = url;
    if (frame === main) {
      page.children = [];
      if (!fromHistory) { page.history = page.history.slice(0, page.idx + 1); page.history.push(url); page.idx++; }
    }
    emit("framenavigated", frame);
  }
  // The route sees the first request of a navigation only; a redirect is followed without it.
  async function navigate(frame, url) {
    if (ctx.handler && !url.startsWith("about:")) {
      let aborted = false;
      const req = { url: () => url, isNavigationRequest: () => true, frame: () => frame };
      await ctx.handler({ request: () => req, abort: async () => { aborted = true; }, fallback: async () => {} });
      if (aborted) { commit(frame, "chrome-error://chromewebdata/"); return null; }
    }
    let final = url;
    while (SITE[final]?.redirect) final = SITE[final].redirect;
    commit(frame, final);
    const doc = SITE[final];
    if (frame === main && doc?.frame) {
      const child = makeFrame(main);
      page.children = [child];
      await navigate(child, doc.frame);
    }
    return { status: () => 200 };
  }
  const travel = async (step) => {
    const i = page.idx + step;
    if (i < 0 || i >= page.history.length) return null;
    page.idx = i;
    commit(main, page.history[i], true); // restored from history: no request, so no route
    return null;
  };
  Object.assign(page, {
    on: (ev, fn) => { (listeners[ev] ??= []).push(fn); },
    url: () => main._url, mainFrame: () => main, frames: () => [main, ...page.children], isClosed: () => false,
    goto: (u) => navigate(main, u), goBack: () => travel(-1), goForward: () => travel(1), reload: () => navigate(main, main._url),
    waitForLoadState: async () => {}, screenshot: async () => Buffer.from("fake jpeg"), viewportSize: () => ({ width: 1280, height: 800 }),
    close: async () => {}, title: async () => SITE[main._url]?.title ?? "",
    keyboard: { press: async (k) => hit("key " + k + " " + main._url), type: async () => {}, down: async () => {}, up: async () => {} },
    mouse: { wheel: async () => {} },
  });
  return page;
}
function context() {
  const onPage = [];
  const ctx = {
    handler: null,
    route: async (_pattern, fn) => { ctx.handler = fn; },
    on: (ev, fn) => { if (ev === "page") onPage.push(fn); }, close: async () => {},
    newPage: async () => { const p = makePage(ctx); for (const fn of onPage) fn(p); return p; },
  };
  return ctx;
}
const browser = { newContext: async () => context(), version: () => "fake-1", close: async () => {} };
module.exports = { chromium: { launch: async () => browser }, devices: {} };
`;

const FAKE = join(fakeDir, "fake-playwright.js");
writeFileSync(FAKE, FAKE_PLAYWRIGHT);
const RUN = mkdtempSync(join(tmpdir(), "jqa-driver-origin-"));
process.env.JQA_PLAYWRIGHT = FAKE;
try {
  await startDriver(RUN, { "start-timeout": 30000, allow: APP });
} finally {
  delete process.env.JQA_PLAYWRIGHT;
}
after(async () => { await send(RUN, { cmd: "stop" }, 10000).catch(() => {}); });

const call = (cmd, args = [], flags = {}) => send(RUN, { cmd, actor: "ana", args, flags: { settle: "1", "no-shot": true, ...flags } }, 30000);
assert.equal((await send(RUN, { cmd: "actor-add", args: ["ana"], flags: {} })).ok, true);

const hits = () => (existsSync(HITS) ? readFileSync(HITS, "utf8").trim().split("\n").filter(Boolean) : []);
const blockedHits = () => hits().filter((h) => !h.includes(APP));
const tabUrl = async () => JSON.parse((await send(RUN, { cmd: "status" })).text).actors[0].url;
const traceEntries = () => readFileSync(join(RUN, "trace.jsonl"), "utf8").trim().split("\n").map((l) => JSON.parse(l));

test("open of an allowed URL that redirects to another origin ends origin_blocked, with the tab taken off it", async () => {
  const res = await call("open", [`${APP}/redirect`]);
  assert.equal(res.code, "origin_blocked", res.text);
  assert.equal(await tabUrl(), "about:blank");
  assert.ok(traceEntries().some((e) => e.cmd === "blocked-navigation" && e.left && e.url === "https://evil.example.net/landing"));
  assert.match(res.text, /evil\.example\.net\/landing/);
});

test("a page restored from history onto a blocked origin is left again (back sends no routed request)", async () => {
  const res = await call("back");
  assert.equal(res.code, "origin_blocked", res.text);
  assert.equal(await tabUrl(), "about:blank");
});

test("a click whose navigation redirects to another origin ends origin_blocked, and nothing there is clicked", async () => {
  assert.equal((await call("open", [`${APP}/home`])).ok, true);
  assert.equal((await call("snapshot")).ok, true);
  const res = await call("click", ["e1"]);
  assert.equal(res.code, "origin_blocked", res.text);
  assert.equal(await tabUrl(), "about:blank");
  assert.deepEqual(blockedHits(), []);
});

test("a direct link to another origin is still aborted by the route before it loads", async () => {
  assert.equal((await call("open", [`${APP}/direct`])).ok, true);
  assert.equal((await call("snapshot")).ok, true);
  const res = await call("click", ["e1"]);
  assert.equal(res.ok, true, res.text);
  assert.equal(await tabUrl(), "chrome-error://chromewebdata/");
  assert.match(res.text, /navigation blocked \(outside the allowlist\): https:\/\/evil\.example\.net\/landing/);
  assert.equal((await call("open", ["https://evil.example.net/landing"])).code, "origin_blocked");
});

test("an iframe pointing to another origin is not loaded: no refs come from it and its button is never clicked", async () => {
  const res = await call("open", [`${APP}/framed`]);
  assert.equal(res.ok, true, res.text);
  assert.match(res.text, /frame navigation blocked \(outside the allowlist\): https:\/\/evil\.example\.net\/widget/);
  const snap = await call("snapshot", [], { frames: true });
  assert.doesNotMatch(snap.text, /f1e1/);
  assert.notEqual((await call("click", ["f1e1"])).ok, true);
  assert.deepEqual(blockedHits(), []);
});

test("an iframe that redirects to another origin is taken off it: its refs are gone and nothing in it is clicked", async () => {
  const res = await call("open", [`${APP}/framed-redirect`]);
  assert.equal(res.ok, true, "a subframe does not fail the action; it is reported and left");
  assert.match(res.text, /frame reached an origin outside the allowlist \(https:\/\/evil\.example\.net\/widget\)/);
  const snap = await call("snapshot", [], { frames: true });
  assert.doesNotMatch(snap.text, /f1e1/);
  assert.notEqual((await call("click", ["f1e1"])).ok, true);
  assert.deepEqual(blockedHits(), []);
});

test("a frame stuck on a blocked origin refuses its refs, element screenshots and keyboard input", async () => {
  assert.equal((await call("open", [`${APP}/framed-inner`])).ok, true);
  const snap = await call("snapshot", [], { frames: true });
  assert.match(snap.text, /button "Go" \[f1e1\]/, "the inner frame is on the allowed origin at first");
  // The inner button sends its own frame to an allowed URL that redirects to STICKY, where goto cannot leave.
  const moved = await call("click", ["f1e1"], { "no-snapshot": true });
  assert.equal(moved.ok, true, moved.text);
  const before = blockedHits();
  assert.deepEqual(before, []);
  const actions = traceEntries().filter((e) => e.cmd === "click").at(-1).actions_used;
  // --no-snapshot keeps the refs of the snapshot above, so the element screenshot below resolves the same f1e1.
  const again = await call("click", ["f1e1"], { "no-snapshot": true });
  assert.equal(again.code, "origin_blocked", again.text);
  const shot = await send(RUN, { cmd: "screenshot", actor: "ana", args: [], flags: { ref: "f1e1" } });
  assert.equal(shot.code, "origin_blocked", shot.text);
  const key = await call("press", ["Enter"]);
  assert.equal(key.code, "origin_blocked", key.text);
  assert.deepEqual(blockedHits(), [], "no click, key or screenshot reached the blocked frame");
  assert.ok(hits().every((h) => !h.startsWith("key ")));
  assert.equal(traceEntries().filter((e) => e.cmd === "press").at(-1).actions_used, actions, "a refused action costs no budget");
  const snap2 = await call("snapshot", [], { frames: true });
  assert.match(snap2.text, /# frame f1: blocked, outside the allowlist \(https:\/\/sticky\.example\.net\/widget\); no refs/);
  assert.equal((await call("open", [`${APP}/home`])).ok, true, "open still takes the tab elsewhere");
});
