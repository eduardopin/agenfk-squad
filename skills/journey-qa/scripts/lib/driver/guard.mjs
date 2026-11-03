// Role and allowlist guards of the browser daemon, kept apart from it so they can be tested without a browser.

// Commands that change the run itself, not the product under test: an actor never issues them.
export const COORDINATOR = new Set(["actor-add", "actor-save", "budget", "stop", "handshake", "allow"]);

const fail = (code, message) => Object.assign(new Error(message), { code });

/** Refuse a coordination command that claims to come from an actor. An absent --as is the CLI's own call. */
export function assertRole(cmd, as) {
  if (COORDINATOR.has(cmd) && as === "actor") {
    throw fail("privileged", `\`${cmd}\` is a coordinator command. The actor uses only the interface; run it as the coordinator (omit --as actor).`);
  }
}
