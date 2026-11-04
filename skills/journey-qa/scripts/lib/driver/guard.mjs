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

// URLs with no origin of their own to check: a frame not yet navigated, about:blank and about:srcdoc (they inherit
// the parent's origin), data:/blob: documents the product builds, and the error page left by an aborted navigation.
const NO_ORIGIN = /^(about:(blank|srcdoc)([?#]|$)|data:|blob:|chrome-error:)/;

/** True when a frame at `url` may be shown and driven: its origin is in `allow`, or it carries no origin to check. */
export function originAllowed(allow, url) {
  if (!url || NO_ORIGIN.test(url)) return true;
  try {
    return allow.has(new URL(url).origin);
  } catch {
    return false;
  }
}

// An origin the hint may quote as a shell command: http(s), letters, digits, dots and hyphens in the host, an optional
// port. A hostile page picks its frame's URL, and URL.origin keeps characters such as ` $ ( ) in a host, so anything
// else gets no command to copy.
const QUOTABLE_ORIGIN = /^https?:\/\/[A-Za-z0-9.-]+(:[0-9]{1,5})?$/;

/** How the coordinator lets a blocked origin in, for the notices about frames the product embeds; "" when the origin
 * of `url` is missing, opaque or not safe to quote as a command. */
export function allowHint(url) {
  let origin = null;
  try { origin = new URL(url).origin; } catch {}
  return origin && QUOTABLE_ORIGIN.test(origin)
    ? `; if the product needs it, the coordinator allows it with \`jqa browser allow ${origin}\``
    : "";
}

/**
 * Frames of `pages` that sit on an origin outside `allow`. A blocked main frame stands for its whole page (leaving it
 * removes the subframes); otherwise each blocked subframe is listed. Pages and frames are Playwright's, or any object
 * with the same isClosed/mainFrame/frames/url methods.
 */
export function blockedFrames(pages, allow) {
  const out = [];
  for (const page of pages) {
    if (!page || page.isClosed()) continue;
    const main = page.mainFrame();
    if (!originAllowed(allow, main.url())) {
      out.push({ page, frame: main, url: main.url(), main: true });
      continue;
    }
    for (const frame of page.frames()) {
      if (frame !== main && !originAllowed(allow, frame.url())) out.push({ page, frame, url: frame.url(), main: false });
    }
  }
  return out;
}
