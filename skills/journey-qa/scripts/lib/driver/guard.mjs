// Role and allowlist guards of the browser daemon, kept apart from it so they can be tested without a browser.
import { isExplicitOrigin } from "../report.mjs";

// Commands that change the run itself, not the product under test: an actor never issues them.
export const COORDINATOR = new Set(["actor-add", "actor-save", "budget", "stop", "handshake", "allow"]);

const fail = (code, message) => Object.assign(new Error(message), { code });

/** Refuse a coordination command that claims to come from an actor. An absent --as is the CLI's own call. */
export function assertRole(cmd, as) {
  if (COORDINATOR.has(cmd) && as === "actor") {
    throw fail("privileged", `\`${cmd}\` is a coordinator command. The actor uses only the interface; run it as the coordinator (omit --as actor).`);
  }
}

/** Add the origin of each URL to the allowlist, only when it is an explicit origin (same rule as `browser start`). */
export function addOrigins(allow, urls) {
  const origins = urls.map((o) => String(o).replace(/\/$/, ""));
  for (const o of origins) {
    if (!isExplicitOrigin(o)) throw fail("bad_args", `--allow needs an explicit origin (e.g. https://app.example.com), got ${o}`);
  }
  for (const o of origins) allow.add(new URL(o).origin);
  return allow;
}
