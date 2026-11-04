// Strict JSON reader: rejects duplicate keys, NaN/Infinity and trailing garbage.
// Zero dependencies. Used for every contract file the pack reads.
import { readFileSync } from "node:fs";

export class ContractError extends Error {
  constructor(message, code = "contract") {
    super(message);
    this.code = code;
  }
}

export function parseStrictJson(text, label = "JSON") {
  let i = 0;
  const fail = (msg) => {
    throw new ContractError(`${label}: ${msg} (position ${i})`);
  };
  const ws = () => {
    while (i < text.length && " \t\n\r".includes(text[i])) i++;
  };
  const value = () => {
    ws();
    const c = text[i];
    if (c === "{") return object();
    if (c === "[") return array();
    if (c === '"') return string();
    if (c === "t") return literal("true", true);
    if (c === "f") return literal("false", false);
    if (c === "n") return literal("null", null);
    if (c === "-" || (c >= "0" && c <= "9")) return number();
    return fail(`unexpected value ${JSON.stringify(c ?? "EOF")}`);
  };
  const literal = (word, result) => {
    if (text.startsWith(word, i)) {
      i += word.length;
      return result;
    }
    return fail(`invalid literal`);
  };
  const number = () => {
    const m = /^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?/.exec(text.slice(i));
    if (!m) fail("invalid number");
    i += m[0].length;
    const n = Number(m[0]);
    if (!Number.isFinite(n)) fail("non-finite number");
    return n;
  };
  const string = () => {
    const start = i;
    i++;
    while (i < text.length) {
      const c = text[i];
      if (c === '"') {
        i++;
        return JSON.parse(text.slice(start, i));
      }
      if (c === "\\") i += 2;
      else if (c < " ") fail("control character in string");
      else i++;
    }
    return fail("unterminated string");
  };
  const array = () => {
    i++;
    const out = [];
    ws();
    if (text[i] === "]") {
      i++;
      return out;
    }
    for (;;) {
      out.push(value());
      ws();
      if (text[i] === ",") {
        i++;
        continue;
      }
      if (text[i] === "]") {
        i++;
        return out;
      }
      fail("expected ',' or ']'");
    }
  };
  const object = () => {
    i++;
    const out = {};
    ws();
    if (text[i] === "}") {
      i++;
      return out;
    }
    for (;;) {
      ws();
      if (text[i] !== '"') fail("key must be a string");
      const key = string();
      if (Object.prototype.hasOwnProperty.call(out, key)) {
        throw new ContractError(`${label}: duplicate key ${JSON.stringify(key)}`);
      }
      ws();
      if (text[i] !== ":") fail("expected ':'");
      i++;
      out[key] = value();
      ws();
      if (text[i] === ",") {
        i++;
        continue;
      }
      if (text[i] === "}") {
        i++;
        return out;
      }
      fail("expected ',' or '}'");
    }
  };
  if (text.charCodeAt(0) === 0xfeff) i = 1;
  const result = value();
  ws();
  if (i !== text.length) fail("content after the end of the JSON");
  return result;
}

export function readJson(path) {
  let text;
  try {
    text = readFileSync(path, "utf8");
  } catch (err) {
    throw new ContractError(`Could not read ${path}: ${err.message}`, "io");
  }
  return parseStrictJson(text, String(path));
}

export function stableStringify(value) {
  if (Array.isArray(value)) return `[${value.map(stableStringify).join(",")}]`;
  if (value && typeof value === "object") {
    return `{${Object.keys(value)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${stableStringify(value[k])}`)
      .join(",")}}`;
  }
  return JSON.stringify(value);
}
