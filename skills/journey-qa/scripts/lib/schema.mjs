// Validator for the JSON Schema subset used by the pack's own schemas.
// Unknown keywords are a hard error, so a schema cannot silently lose a constraint.
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { ContractError, readJson } from "./json.mjs";

export const SKILL_ROOT = join(dirname(fileURLToPath(import.meta.url)), "..", "..");
export const SCHEMA_DIR = join(SKILL_ROOT, "assets", "schemas");
export const TEMPLATE_DIR = join(SKILL_ROOT, "assets", "templates");
export const ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$/;

const KEYWORDS = new Set([
  "$schema", "$id", "title", "description", "default", "examples", "type", "const", "enum",
  "required", "properties", "additionalProperties", "items", "minItems", "maxItems",
  "minLength", "maxLength", "pattern", "format", "minimum", "maximum", "exclusiveMinimum",
]);

const TYPE_TESTS = {
  object: (v) => v !== null && typeof v === "object" && !Array.isArray(v),
  array: (v) => Array.isArray(v),
  string: (v) => typeof v === "string",
  boolean: (v) => typeof v === "boolean",
  integer: (v) => Number.isInteger(v),
  number: (v) => typeof v === "number" && Number.isFinite(v),
  null: (v) => v === null,
};

const DATE_TIME = /^\d{4}-\d{2}-\d{2}[Tt]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:[Zz]|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/;

function deepEqual(a, b) {
  return JSON.stringify(a) === JSON.stringify(b);
}

export function validateSchema(value, schema, path = "$", errors = []) {
  if (!TYPE_TESTS.object(schema)) throw new ContractError(`Invalid schema at ${path}`);
  const unknown = Object.keys(schema).filter((k) => !KEYWORDS.has(k));
  if (unknown.length) throw new ContractError(`Unsupported keywords at ${path}: ${unknown.join(", ")}`);
  if (schema.type !== undefined) {
    const allowed = Array.isArray(schema.type) ? schema.type : [schema.type];
    for (const t of allowed) if (!TYPE_TESTS[t]) throw new ContractError(`Unsupported type: ${t}`);
    if (!allowed.some((t) => TYPE_TESTS[t](value))) {
      errors.push(`${path}: invalid type; expected ${allowed.join("|")}`);
      return errors;
    }
  }
  if ("const" in schema && !deepEqual(value, schema.const)) errors.push(`${path}: must be ${JSON.stringify(schema.const)}`);
  if (schema.enum && !schema.enum.some((e) => deepEqual(e, value))) {
    errors.push(`${path}: value ${JSON.stringify(value)} not in ${schema.enum.join("|")}`);
  }
  if (TYPE_TESTS.object(value)) {
    const props = schema.properties ?? {};
    for (const key of schema.required ?? []) if (!(key in value)) errors.push(`${path}.${key}: required field missing`);
    if (schema.additionalProperties === false) {
      for (const key of Object.keys(value).sort()) if (!(key in props)) errors.push(`${path}.${key}: field not allowed`);
    }
    for (const [key, item] of Object.entries(value)) if (key in props) validateSchema(item, props[key], `${path}.${key}`, errors);
  }
  if (Array.isArray(value)) {
    if (value.length < (schema.minItems ?? 0)) errors.push(`${path}: at least ${schema.minItems} items`);
    if (schema.maxItems !== undefined && value.length > schema.maxItems) errors.push(`${path}: at most ${schema.maxItems} items`);
    if (schema.items) value.forEach((item, i) => validateSchema(item, schema.items, `${path}[${i}]`, errors));
  }
  if (typeof value === "string") {
    if (value.length < (schema.minLength ?? 0)) errors.push(`${path}: text too short`);
    if (schema.maxLength !== undefined && value.length > schema.maxLength) errors.push(`${path}: text too long`);
    if (schema.pattern && !new RegExp(schema.pattern, "u").test(value)) errors.push(`${path}: does not match ${schema.pattern}`);
    if (schema.format) {
      if (schema.format !== "date-time") throw new ContractError(`Unsupported format: ${schema.format}`);
      if (!DATE_TIME.test(value) || Number.isNaN(Date.parse(value))) errors.push(`${path}: invalid date-time or missing time zone`);
    }
  }
  if (typeof value === "number") {
    if (schema.minimum !== undefined && value < schema.minimum) errors.push(`${path}: below ${schema.minimum}`);
    if (schema.maximum !== undefined && value > schema.maximum) errors.push(`${path}: above ${schema.maximum}`);
    if (schema.exclusiveMinimum !== undefined && value <= schema.exclusiveMinimum) errors.push(`${path}: must be > ${schema.exclusiveMinimum}`);
  }
  return errors;
}

export const RUN_DOCUMENTS = [
  "product-profile", "scenario", "actor-card", "session-binding", "run-report", "resume-checkpoint",
];
export const DOCUMENTS = [...RUN_DOCUMENTS, "architecture-review"];

const cache = new Map();
export function loadSchema(name) {
  if (!cache.has(name)) cache.set(name, readJson(join(SCHEMA_DIR, `${name}.schema.json`)));
  return cache.get(name);
}

export function validateDocument(value, name) {
  if (!DOCUMENTS.includes(name)) throw new ContractError(`Unknown schema: ${name}`);
  return validateSchema(value, loadSchema(name));
}

export function loadTemplate(name) {
  return readJson(join(TEMPLATE_DIR, `${name}.json`));
}
