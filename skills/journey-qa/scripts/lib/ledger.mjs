// Append-only, hash-chained evidence ledger (evidence/ledger.jsonl).
// The chain makes later edits detectable; it does not prove the run happened
// on an honest machine. Every driver observation is written here automatically.
import { createHash } from "node:crypto";
import {
  appendFileSync, copyFileSync, existsSync, mkdirSync, readFileSync, rmSync, statSync, writeFileSync,
} from "node:fs";
import { basename, extname, join, relative, sep } from "node:path";
import { ContractError, parseStrictJson, stableStringify } from "./json.mjs";

export const GENESIS = "0".repeat(64);
const KINDS = new Set(["screenshot", "dom", "tool_result", "network", "log", "file", "video", "transcript", "source", "fixture", "note"]);
const ORIGINS = new Set(["observed", "provided", "synthetic"]);
const CAPTURED_BY = new Set(["driver", "tool", "agent", "user", "fixture"]);

export const sha256 = (data) => createHash("sha256").update(data).digest("hex");
export const ledgerFile = (runDir) => join(runDir, "evidence", "ledger.jsonl");
const posix = (p) => p.split(sep).join("/");

function sleep(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function withLock(runDir, fn) {
  const lock = join(runDir, ".jqa", "ledger.lock");
  mkdirSync(join(runDir, ".jqa"), { recursive: true });
  const deadline = Date.now() + 10000;
  for (;;) {
    try {
      mkdirSync(lock);
      break;
    } catch (err) {
      if (err.code !== "EEXIST") throw err;
      try {
        if (Date.now() - statSync(lock).mtimeMs > 30000) rmSync(lock, { recursive: true, force: true });
      } catch {}
      if (Date.now() > deadline) throw new ContractError("Ledger locked by another process for more than 10 s", "ledger_locked");
      sleep(25);
    }
  }
  try {
    return fn();
  } finally {
    rmSync(lock, { recursive: true, force: true });
  }
}

export function entryHash(entry) {
  const { hash, ...rest } = entry;
  return sha256(`${rest.prev}\n${stableStringify(rest)}`);
}

export function readLedger(runDir) {
  const file = ledgerFile(runDir);
  if (!existsSync(file)) return [];
  return readFileSync(file, "utf8")
    .split("\n")
    .filter((line) => line.trim())
    .map((line, i) => parseStrictJson(line, `ledger line ${i + 1}`));
}

export function verifyLedger(runDir) {
  const errors = [];
  let entries = [];
  try {
    entries = readLedger(runDir);
  } catch (err) {
    return { ok: false, errors: [err.message], entries: [], head: null };
  }
  let prev = GENESIS;
  const ids = new Set();
  entries.forEach((e, i) => {
    const where = `ledger[${i + 1}] ${e.evidence_id ?? "?"}`;
    if (e.seq !== i + 1) errors.push(`${where}: sequence ${e.seq} out of order`);
    if (e.prev !== prev) errors.push(`${where}: previous link does not match (entry removed or reordered)`);
    if (entryHash(e) !== e.hash) errors.push(`${where}: entry hash does not match (content altered)`);
    if (ids.has(e.evidence_id)) errors.push(`${where}: duplicate evidence_id`);
    ids.add(e.evidence_id);
    if (e.file) {
      const path = join(runDir, e.file);
      if (!existsSync(path)) errors.push(`${where}: missing file ${e.file}`);
      else if (sha256(readFileSync(path)) !== e.sha256) errors.push(`${where}: file ${e.file} altered after it was recorded`);
    }
    prev = e.hash;
  });
  return { ok: errors.length === 0, errors, entries, head: entries.length ? prev : null };
}

/**
 * Append one evidence item. Provide `content` (Buffer|string) to create a new file,
 * `sourceFile` to copy an existing artifact, or neither for a locator-only entry.
 */
export function appendEvidence(runDir, item) {
  const kind = item.kind;
  if (!KINDS.has(kind)) throw new ContractError(`invalid kind: ${kind}`);
  const origin = item.origin ?? "observed";
  if (!ORIGINS.has(origin)) throw new ContractError(`invalid origin: ${origin}`);
  const capturedBy = item.captured_by ?? "agent";
  if (!CAPTURED_BY.has(capturedBy)) throw new ContractError(`invalid captured_by: ${capturedBy}`);
  if (!item.summary || !String(item.summary).trim()) throw new ContractError("summary is required");
  if (item.redaction !== undefined && item.redaction !== null && !["none_needed", "redacted", "pending"].includes(item.redaction)) {
    throw new ContractError(`invalid redaction: ${item.redaction} (none_needed|redacted|pending)`);
  }
  if (item.sourceFile && !existsSync(item.sourceFile)) throw new ContractError(`File not found: ${item.sourceFile}`);
  mkdirSync(join(runDir, "evidence"), { recursive: true });
  return withLock(runDir, () => {
    const entries = readLedger(runDir);
    const last = entries.at(-1);
    const seq = (last?.seq ?? 0) + 1;
    const evidenceId = `EV-${String(seq).padStart(4, "0")}`;
    let file = null;
    let digest = null;
    let bytes = null;
    if (item.content !== undefined || item.sourceFile) {
      const ext = item.ext ?? (item.sourceFile ? extname(item.sourceFile).slice(1) : "txt") ?? "txt";
      const suffix = item.sourceFile ? basename(item.sourceFile).replace(/[^A-Za-z0-9_.-]/g, "_").slice(0, 60) : `${kind}.${ext}`;
      const name = `${evidenceId}-${suffix}`;
      const target = join(runDir, "evidence", name);
      if (item.sourceFile) copyFileSync(item.sourceFile, target, 1 /* COPYFILE_EXCL */);
      else writeFileSync(target, item.content, { flag: "wx" });
      const data = readFileSync(target);
      digest = sha256(data);
      bytes = data.length;
      file = posix(relative(runDir, target));
    }
    const entry = {
      seq,
      evidence_id: evidenceId,
      captured_at: new Date().toISOString(),
      kind,
      origin,
      captured_by: capturedBy,
      actor_id: item.actor_id ?? null,
      url: item.url ?? null,
      summary: String(item.summary).slice(0, 500),
      file,
      locator: item.locator ?? file ?? "inline",
      sha256: digest,
      bytes,
      redaction: item.redaction ?? "none_needed",
      prev: last?.hash ?? GENESIS,
    };
    entry.hash = entryHash(entry);
    appendFileSync(ledgerFile(runDir), `${JSON.stringify(entry)}\n`);
    return entry;
  });
}

/** Append one trace event with a run-wide monotonic sequence (shared lock with the ledger). */
export function appendTrace(runDir, event) {
  return withLock(runDir, () => {
    const file = join(runDir, "trace.jsonl");
    let seq = 1;
    if (existsSync(file)) {
      const text = readFileSync(file, "utf8").trimEnd();
      const last = text.slice(text.lastIndexOf("\n") + 1);
      if (last) seq = (JSON.parse(last).seq ?? 0) + 1;
    }
    appendFileSync(file, `${JSON.stringify({ seq, ts: new Date().toISOString(), ...event })}\n`);
    return seq;
  });
}
