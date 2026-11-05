// Locate Playwright without bundling it. Search order:
// JQA_PLAYWRIGHT, the skill's own scripts/node_modules (after `npm install`),
// the user runtime directory that `jqa setup` fills, the current project,
// NODE_PATH and the global npm root.
import { execFileSync } from "node:child_process";
import { existsSync } from "node:fs";
import { createRequire } from "node:module";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const SCRIPTS = join(dirname(fileURLToPath(import.meta.url)), "..", "..");

/** Per-user runtime shared by every host and every copy of the skill, so a reinstall keeps the browser driver. */
export function runtimeDir() {
  if (process.env.JQA_RUNTIME) return process.env.JQA_RUNTIME;
  if (process.platform === "win32") return join(process.env.LOCALAPPDATA ?? join(homedir(), "AppData", "Local"), "journey-qa");
  return join(process.env.XDG_DATA_HOME ?? join(homedir(), ".local", "share"), "journey-qa");
}

function globalRoot() {
  try {
    return execFileSync(process.platform === "win32" ? "npm.cmd" : "npm", ["root", "-g"], {
      encoding: "utf8", timeout: 8000, stdio: ["ignore", "pipe", "ignore"], shell: process.platform === "win32",
    }).trim();
  } catch {
    return null;
  }
}

export function playwrightCandidates() {
  const paths = [];
  if (process.env.JQA_PLAYWRIGHT) paths.push(process.env.JQA_PLAYWRIGHT);
  paths.push(SCRIPTS, runtimeDir(), process.cwd());
  for (const p of (process.env.NODE_PATH ?? "").split(process.platform === "win32" ? ";" : ":")) if (p) paths.push(p);
  return paths;
}

export async function loadPlaywright() {
  const tried = [];
  const attempt = async (resolver, label) => {
    for (const name of ["playwright", "playwright-core"]) {
      try {
        const resolved = resolver(name);
        if (resolved) {
          const mod = await import(pathToFileURL(resolved).href);
          return { pw: mod.default ?? mod, from: resolved, name };
        }
      } catch (err) {
        tried.push(`${label}/${name}: ${err.code ?? err.message}`);
      }
    }
    return null;
  };
  const env = process.env.JQA_PLAYWRIGHT;
  if (env && existsSync(env) && env.endsWith(".js")) {
    const mod = await import(pathToFileURL(env).href);
    return { pw: mod.default ?? mod, from: env, name: "custom" };
  }
  const req = createRequire(join(SCRIPTS, "package.json"));
  const local = await attempt((n) => req.resolve(n, { paths: playwrightCandidates() }), "local");
  if (local) return local;
  const root = globalRoot();
  if (root) {
    const g = await attempt((n) => req.resolve(n, { paths: [root] }), "global");
    if (g) return g;
  }
  const err = new Error(
    "Playwright not found. Run `jqa setup` once: it installs playwright-core into " + runtimeDir() +
      " and uses the installed Chrome. No Chrome on the machine: `jqa setup --chromium`. " +
      "Alternative: JQA_PLAYWRIGHT=/path/to/node_modules.",
  );
  err.code = "playwright_missing";
  err.tried = tried;
  throw err;
}
