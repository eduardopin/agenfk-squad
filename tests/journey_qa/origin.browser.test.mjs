// The origin allowlist in a real browser: two loopback servers on different ports are two origins, one allowed.
// Opt-in (JQA_BROWSER_TESTS=1, with Playwright reachable as `jqa setup` or JQA_PLAYWRIGHT leaves it): the default suite
// and CI have no browser, and origin.test.mjs covers the same driver paths there with a fake Playwright.
import { test, after } from "node:test";
import assert from "node:assert/strict";
import http from "node:http";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { send, startDriver } from "../../skills/journey-qa/scripts/lib/driver/client.mjs";

const ON = process.env.JQA_BROWSER_TESTS === "1";
const skip = ON ? false : "real browser: set JQA_BROWSER_TESTS=1 to run";

const page = (body) => `<!doctype html><html lang="en"><head><meta charset="utf-8"><title>t</title></head><body>${body}</body></html>`;
const listen = (handler) => new Promise((resolve) => {
  const server = http.createServer(handler);
  server.listen(0, "127.0.0.1", () => resolve(server));
});

let RUN = null;
let app = null;
let other = null;
const otherHits = [];
if (ON) {
  other = await listen((req, res) => {
    otherHits.push(req.url);
    if (req.url === "/clicked") return res.writeHead(204).end();
    if (req.url === "/alert") return res.writeHead(200, { "content-type": "text/html" }).end(page(`<h1>Other</h1><script>alert("hello from the other origin")</script>`));
    res.writeHead(200, { "content-type": "text/html" }).end(page(`<h1>Other</h1><button type="button" onclick="fetch('/clicked')">Press</button>`));
  });
  const OTHER = `http://127.0.0.1:${other.address().port}`;
  app = await listen((req, res) => {
    const to = { "/redirect": `${OTHER}/landing`, "/to-other": `${OTHER}/widget`, "/to-alert": `${OTHER}/alert` }[req.url];
    if (to) return res.writeHead(302, { location: to }).end();
    const body = {
      "/home": `<a href="/redirect">Go</a>`,
      "/alert-home": `<a href="/to-alert">Go</a>`,
      "/framed": `<iframe src="${OTHER}/widget" title="widget"></iframe>`,
      "/framed-redirect": `<iframe src="/to-other" title="widget"></iframe>`,
    }[req.url] ?? "<h1>App</h1>";
    res.writeHead(200, { "content-type": "text/html" }).end(page(body));
  });
  RUN = mkdtempSync(join(tmpdir(), "jqa-driver-browser-"));
  await startDriver(RUN, { "start-timeout": 60000, allow: `http://127.0.0.1:${app.address().port}` });
  assert.equal((await send(RUN, { cmd: "actor-add", args: ["ana"], flags: {} })).ok, true);
}
after(async () => {
  if (RUN) await send(RUN, { cmd: "stop" }, 10000).catch(() => {});
  app?.close();
  other?.close();
});

const APP = () => `http://127.0.0.1:${app.address().port}`;
const call = (cmd, args = [], flags = {}) => send(RUN, { cmd, actor: "ana", args, flags: { "no-shot": true, ...flags } }, 60000);
const tabUrl = async () => JSON.parse((await send(RUN, { cmd: "status" })).text).actors[0].url;

test("real browser: a 302 to another origin ends origin_blocked and the tab is taken to about:blank", { skip }, async () => {
  const res = await call("open", [`${APP()}/redirect`]);
  assert.equal(res.code, "origin_blocked", res.text);
  assert.equal(await tabUrl(), "about:blank");
});

test("real browser: a link whose target redirects to another origin ends origin_blocked", { skip }, async () => {
  assert.equal((await call("open", [`${APP()}/home`])).ok, true);
  const snap = await call("snapshot");
  const ref = /link "Go" \[(e\d+)\]/.exec(snap.text)?.[1];
  assert.ok(ref, snap.text);
  const res = await call("click", [ref]);
  assert.equal(res.code, "origin_blocked", res.text);
  assert.equal(await tabUrl(), "about:blank");
});

test("real browser: an iframe to another origin is not loaded and gives no refs", { skip }, async () => {
  const before = otherHits.length;
  const res = await call("open", [`${APP()}/framed`]);
  assert.equal(res.ok, true, res.text);
  const snap = await call("snapshot", [], { frames: true });
  assert.doesNotMatch(snap.text, /button "Press"/, snap.text);
  assert.deepEqual(otherHits.slice(before), [], "the frame request never reached the other origin");
});

test("real browser: an iframe redirected to another origin is taken off it and its button is never pressed", { skip }, async () => {
  const res = await call("open", [`${APP()}/framed-redirect`]);
  assert.equal(res.ok, true, res.text);
  const snap = await call("snapshot", [], { frames: true });
  assert.doesNotMatch(snap.text, /button "Press"/, snap.text);
  assert.notEqual((await call("click", ["f1e1"])).ok, true);
  assert.ok(!otherHits.includes("/clicked"));
});

test("real browser: a redirected page that raises an alert as it loads is still left, and the actor stays usable", { skip }, async () => {
  assert.equal((await call("open", [`${APP()}/alert-home`])).ok, true);
  const snap = await call("snapshot");
  const ref = /link "Go" \[(e\d+)\]/.exec(snap.text)?.[1];
  assert.ok(ref, snap.text);
  const res = await call("click", [ref]);
  assert.equal(res.code, "origin_blocked", res.text);
  assert.equal(await tabUrl(), "about:blank", res.text);
  assert.match(res.text, /The tab was taken to about:blank\./);
  assert.equal(JSON.parse((await send(RUN, { cmd: "status" })).text).actors[0].dialog, null, "no dialog left for the actor");
  const tabs = await send(RUN, { cmd: "tabs", actor: "ana", args: [], flags: {} }, 5000);
  assert.equal(tabs.ok, true, tabs.text);
  assert.doesNotMatch(tabs.text, /127\.0\.0\.1:\d+\/alert/);
  const back = await send(RUN, { cmd: "open", actor: "ana", args: [`${APP()}/home`], flags: { "no-shot": true } }, 15000);
  assert.equal(back.ok, true, back.text);
});
