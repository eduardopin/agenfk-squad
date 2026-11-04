// CLI side of the browser driver: start/stop the per-run daemon and send commands.
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, openSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { ContractError } from "../json.mjs";
import { isExplicitOrigin } from "../report.mjs";

const DAEMON = join(dirname(fileURLToPath(import.meta.url)), "daemon.mjs");
const infoPath = (runDir) => join(runDir, ".jqa", "driver.json");

export function driverInfo(runDir) {
  const p = infoPath(runDir);
  if (!existsSync(p)) return null;
  const info = JSON.parse(readFileSync(p, "utf8"));
  try {
    process.kill(info.pid, 0);
  } catch {
    rmSync(p, { force: true });
    return null;
  }
  return info;
}

export async function startDriver(runDir, flags) {
  const existing = driverInfo(runDir);
  if (existing) return { already: true, info: existing };
  const allow = [].concat(flags.allow ?? []).flatMap((a) => String(a).split(",")).filter(Boolean).map((o) => o.replace(/\/$/, ""));
  for (const o of allow) if (!isExplicitOrigin(o)) throw new ContractError(`--allow needs an explicit origin (e.g. https://app.example.com), got ${o}`);
  const cfg = {
    allow: allow.map((o) => new URL(o).origin),
    browser: flags.browser ?? "auto",
    cdp: flags.cdp ?? null,
    headed: !!flags.headed,
    maxActions: Number(flags["max-actions"] ?? 60),
    maxMinutes: Number(flags["max-minutes"] ?? 20),
    viewport: flags.viewport ?? "1280x800",
    locale: flags.locale ?? "pt-BR",
    timezone: flags.timezone ?? undefined,
    autoShot: !flags["no-auto-shot"],
    idleMinutes: Number(flags["idle-minutes"] ?? 60),
  };
  const jqa = join(runDir, ".jqa");
  mkdirSync(jqa, { recursive: true });
  rmSync(join(jqa, "driver.error.json"), { force: true });
  const cfgPath = join(jqa, "driver.config.json");
  writeFileSync(cfgPath, JSON.stringify(cfg, null, 2));
  const logFd = openSync(join(jqa, "driver.log"), "a");
  const child = spawn(process.execPath, [DAEMON, runDir, cfgPath], { detached: true, stdio: ["ignore", logFd, logFd], windowsHide: true });
  child.unref();
  const deadline = Date.now() + Number(flags["start-timeout"] ?? 45000);
  while (Date.now() < deadline) {
    const errFile = join(jqa, "driver.error.json");
    if (existsSync(errFile)) {
      const e = JSON.parse(readFileSync(errFile, "utf8"));
      throw new ContractError(e.message, e.code);
    }
    const info = driverInfo(runDir);
    if (info) return { already: false, info, cfg };
    await new Promise((r) => setTimeout(r, 150));
  }
  throw new ContractError(`The driver did not respond in time. See ${join(jqa, "driver.log")}`, "driver_timeout");
}

export async function send(runDir, body, timeoutMs = 120000) {
  const info = driverInfo(runDir);
  if (!info) throw new ContractError("Driver not started for this run. Run `jqa browser start --allow ORIGIN`.", "driver_not_running");
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), timeoutMs);
  try {
    const res = await fetch(`http://127.0.0.1:${info.port}/cmd`, {
      method: "POST",
      headers: { authorization: `Bearer ${info.token}`, "content-type": "application/json" },
      body: JSON.stringify(body),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new ContractError(`Driver refused the call (HTTP ${res.status})`, "driver_http");
    return await res.json();
  } catch (e) {
    if (e.name === "AbortError") throw new ContractError(`Command exceeded ${timeoutMs} ms`, "driver_timeout");
    if (e instanceof ContractError) throw e;
    throw new ContractError(`Could not reach the driver: ${e.message}`, "driver_unreachable");
  } finally {
    clearTimeout(timer);
  }
}
