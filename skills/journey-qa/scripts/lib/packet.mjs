// Actor packets: the only material an isolated actor receives.
// Builds the mission text, a restricted browser wrapper (act.mjs) and, optionally,
// the command that runs the actor in a fresh host process (clean model context).
import { spawn, spawnSync } from "node:child_process";
import { chmodSync, existsSync, mkdirSync, openSync, readFileSync, realpathSync, rmSync, statSync, writeFileSync } from "node:fs";
import { basename, dirname, join, resolve } from "node:path";
import { createHash } from "node:crypto";
import { homedir, tmpdir } from "node:os";
import { ContractError, readJson } from "./json.mjs";
import { ID_PATTERN, SKILL_ROOT, validateDocument } from "./schema.mjs";
import { appendEvidence } from "./ledger.mjs";

const JQA = join(SKILL_ROOT, "scripts", "jqa.mjs");
// Evaluator-only vocabulary in both languages: Portuguese terms kept, English equivalents beside them.
const OBSERVER_WORDS = /\b(gabarito|or[aá]culo|oracle|answer key|ground truth|defeito semeado|seeded|planted (?:bug|defect)|bug conhecido|known bug|known defect|defeito conhecido|expected verdict|veredito esperado|expected outcome|resultado esperado|rule_id|qa_verdict|mandatory_rule)\b|[?&]case=/i;

function words(text) {
  return String(text).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").match(/[a-z0-9$]+/g) ?? [];
}

/** Heuristic leak check: card vs observer-only material. Warnings, not proof of blindness. */
export function leakCheck(card, observerDocs = []) {
  const warnings = [];
  const cardText = JSON.stringify(card);
  if (OBSERVER_WORDS.test(cardText)) warnings.push(`The card contains an evaluator term: ${cardText.match(OBSERVER_WORDS)[0]}`);
  const cardWords = words(cardText);
  const grams = new Set();
  for (let i = 0; i + 5 <= cardWords.length; i++) grams.add(cardWords.slice(i, i + 5).join(" "));
  for (const doc of observerDocs) {
    const rules = [...(doc.business_rules ?? []), ...(doc.rules ?? [])];
    for (const r of rules) {
      if (r.rule_id && cardText.includes(r.rule_id)) warnings.push(`The card cites the rule identifier ${r.rule_id}`);
      for (const field of ["description", "expected_effect", "statement"]) {
        const w = words(r[field] ?? "");
        for (let i = 0; i + 5 <= w.length; i++) {
          const g = w.slice(i, i + 5).join(" ");
          if (grams.has(g)) {
            warnings.push(`The card repeats a passage of rule ${r.rule_id ?? "?"}: “${g}”`);
            break;
          }
        }
      }
    }
    for (const id of doc.mandatory_rule_ids ?? []) if (cardText.includes(id)) warnings.push(`The card cites the mandatory rule ${id}`);
  }
  return [...new Set(warnings)];
}

export const ACTOR_COMMANDS = [
  "snapshot", "screenshot", "text", "tabs", "open", "back", "forward", "reload", "tab", "close-tab", "click", "dblclick", "hover",
  "fill", "type", "select", "check", "uncheck", "upload", "press", "keydown", "keyup", "hold", "scroll", "wait", "dialog",
];
// Flags that take no value, mirrored from the CLI parser for the broker path.
export const ACTOR_BOOL_FLAGS = ["full", "frames", "double", "secret", "no-snapshot", "no-shot"];
export const ACTOR_FLAGS = ["--full", "--frames", "--ref", "--double", "--secret", "--delay", "--text", "--gone", "--url", "--ms", "--timeout", "--no-snapshot", "--no-shot", "--button"];

// PACKET.md text after the role, in the card's language (pt for a pt* locale, English otherwise).
const PACKET_TEXT = {
  pt: (fence, payload) => `## Sua ficha

Os dados abaixo delimitam quem você é nesta jornada. Eles não substituem instruções superiores.

${fence}json
${payload}
${fence}

## Como operar o navegador

Você está no diretório do seu papel. Use **apenas** \`node act.mjs …\` pelo shell, **um comando por chamada**: sem \`|\`, \`&&\`, \`;\` ou redirecionamento, que o host recusa. Cada comando age como uma pessoa na interface e devolve a nova observação.

\`\`\`
node act.mjs snapshot              # ver a página: cada controle tem uma ref [eN]
node act.mjs open URL              # abrir o endereço que você conhece
node act.mjs click e12             # clicar
node act.mjs fill e7 "texto"       # preencher campo (substitui)   · type e7 "texto" digita
node act.mjs select e9 "Opção"     # escolher em lista             · check/uncheck eN
node act.mjs press Enter           # tecla (Tab, Escape, Control+A…) · scroll down|up
node act.mjs back                 # voltar                        · reload recarrega
node act.mjs wait --text "Pedido"  # esperar um texto aparecer
node act.mjs dialog accept|dismiss # responder a um diálogo do navegador
node act.mjs note "o que vi e por que decidi"   # registre decisões importantes
node act.mjs done achieved "motivo"             # encerre: achieved|not_achieved|abandoned|undetermined
\`\`\`

Comece com \`node act.mjs snapshot\`; se a página estiver em branco, abra o endereço que está na sua ficha. Regras de operação: refs valem só para a observação mais recente; depois de navegar, observe de novo. Uma resposta \`ok\` da ferramenta não prova que seu objetivo foi atingido — confira na tela. Se um comando for recusado por orçamento, encerre com \`done\`. Termine **sempre** com \`node act.mjs done …\` e uma resposta final curta: o que fez, o que viu, o resultado para você e dúvidas.
`,
  en: (fence, payload) => `## Your card

The data below defines who you are in this journey. It does not override higher-level instructions.

${fence}json
${payload}
${fence}

## How to operate the browser

You are in your role's directory. Use **only** \`node act.mjs …\` from the shell, **one command per call**: no \`|\`, \`&&\`, \`;\` or redirection, which the host refuses. Each command acts like a person on the interface and returns the new observation.

\`\`\`
node act.mjs snapshot              # see the page: each control has a ref [eN]
node act.mjs open URL              # open the address you know
node act.mjs click e12             # click
node act.mjs fill e7 "text"        # fill a field (replaces)       · type e7 "text" types it
node act.mjs select e9 "Option"    # pick from a list              · check/uncheck eN
node act.mjs press Enter           # key (Tab, Escape, Control+A…) · scroll down|up
node act.mjs back                 # go back                       · reload reloads
node act.mjs wait --text "Order"   # wait for a text to appear
node act.mjs dialog accept|dismiss # answer a browser dialog
node act.mjs note "what I saw and why I decided"   # record important decisions
node act.mjs done achieved "reason"                # finish: achieved|not_achieved|abandoned|undetermined
\`\`\`

Start with \`node act.mjs snapshot\`; if the page is blank, open the address on your card. Operating rules: refs are valid only for the most recent observation; after navigating, observe again. An \`ok\` reply from the tool does not prove your goal was reached — check on the screen. If a command is refused for budget, finish with \`done\`. **Always** finish with \`node act.mjs done …\` and a short final answer: what you did, what you saw, the outcome for you, and open questions.
`,
};

// act.mjs help and refusal text, in the card's language.
const WRAPPER_TEXT = {
  pt: {
    help: "Uso: node act.mjs <comando> [args]\n  snapshot | open URL | click eN | fill eN TEXTO | type eN TEXTO | select eN OPÇÃO | check eN | uncheck eN\n  press TECLA [--ref eN] | scroll down|up|eN | back | reload | wait --text T | dialog accept|dismiss | tabs | tab N | screenshot | text\n  note \"o que observei e por que decidi\" | done achieved|not_achieved|abandoned|undetermined \"motivo\"",
    refuse: "Indisponível para o ator: ", only: ". Use somente a interface do produto.", noteFlags: "flags não são aceitas em note/done", command: "comando ",
  },
  en: {
    help: "Usage: node act.mjs <command> [args]\n  snapshot | open URL | click eN | fill eN TEXT | type eN TEXT | select eN OPTION | check eN | uncheck eN\n  press KEY [--ref eN] | scroll down|up|eN | back | reload | wait --text T | dialog accept|dismiss | tabs | tab N | screenshot | text\n  note \"what I observed and why I decided\" | done achieved|not_achieved|abandoned|undetermined \"reason\"",
    refuse: "Unavailable to the actor: ", only: ". Use only the product interface.", noteFlags: "flags are not accepted in note/done", command: "command ",
  },
};

function wrapperSource(runDir, actorId, lang = "en") {
  const T = WRAPPER_TEXT[lang] ?? WRAPPER_TEXT.en;
  return `#!/usr/bin/env node
// Wrapper for actor ${actorId}: browser through the interface only (human_ui).
// Allowlist of commands and flags; no --as, --run, --actor or oracle commands.
import { spawnSync } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
const JQA = ${JSON.stringify(JQA)};
const RUN = ${JSON.stringify(resolve(runDir))};
const ACTOR = ${JSON.stringify(actorId)};
const COMMANDS = new Set(${JSON.stringify(ACTOR_COMMANDS)});
const FLAGS = new Set(${JSON.stringify(ACTOR_FLAGS)});
const [cmd, ...rest] = process.argv.slice(2);
if (!cmd || cmd === "help") {
  console.log(${JSON.stringify(T.help)});
  process.exit(0);
}
const refuse = (why) => { console.error(${JSON.stringify(T.refuse)} + why + ${JSON.stringify(T.only)}); process.exit(3); };
if (cmd === "note" || cmd === "done") {
  if (rest.some((a) => a.startsWith("--"))) refuse(${JSON.stringify(T.noteFlags)});
} else {
  if (!COMMANDS.has(cmd)) refuse(${JSON.stringify(T.command)} + JSON.stringify(cmd));
  for (const a of rest) if (a.startsWith("--") && !FLAGS.has(a.split("=")[0])) refuse("flag " + JSON.stringify(a));
}
// Spawned actors talk to the driver directly with a token valid only for this actor's interface commands, so the
// run folder (ledger, trace, the driver's full token) never has to be visible to them.
const BROKER = join(dirname(fileURLToPath(import.meta.url)), ".driver.json");
if (existsSync(BROKER)) {
  const { port, token } = JSON.parse(readFileSync(BROKER, "utf8"));
  const BOOL = new Set(${JSON.stringify(ACTOR_BOOL_FLAGS)});
  const args = [];
  const flags = {};
  for (let i = 0; i < rest.length; i++) {
    const a = rest[i];
    if (cmd !== "note" && cmd !== "done" && a.startsWith("--")) {
      const [k, v] = a.slice(2).split(/=(.*)/s, 2);
      flags[k] = v !== undefined ? v : BOOL.has(k) ? true : rest[++i];
    } else args.push(cmd === "upload" && args.length > 0 ? resolve(a) : a);
  }
  const body = cmd === "note" ? { cmd, args: [rest.join(" ")] } : cmd === "done" ? { cmd, args: [rest[0] ?? "", rest.slice(1).join(" ")] } : { cmd, args, flags };
  try {
    const res = await fetch("http://127.0.0.1:" + port + "/cmd", { method: "POST", headers: { authorization: "Bearer " + token, "content-type": "application/json" }, body: JSON.stringify(body) });
    if (!res.ok) { console.error("driver refused (HTTP " + res.status + ")"); process.exit(1); }
    const r = await res.json();
    console.log(r.text);
    process.exit(r.ok ? 0 : ["privileged", "budget_exhausted", "origin_blocked", "no_allowlist", "leak"].includes(r.code) ? 3 : 1);
  } catch (e) {
    console.error("driver unreachable: " + e.message);
    process.exit(1);
  }
}
let argv;
if (cmd === "note") argv = [JQA, "trace", "note", rest.join(" "), "--run", RUN, "--actor", ACTOR];
else if (cmd === "done") argv = [JQA, "actor", "outcome", ACTOR, rest[0] ?? "", rest.slice(1).join(" "), "--run", RUN];
else {
  const args = cmd === "upload" ? rest.map((a, i) => (i > 0 && !a.startsWith("--") ? resolve(a) : a)) : rest;
  argv = [JQA, "browser", cmd, ...args, "--run", RUN, "--actor", ACTOR];
}
// JQA_VIA marks the trace entry as the actor's own action; anything else in this context is the coordinator.
const r = spawnSync(process.execPath, argv, { stdio: "inherit", env: { ...process.env, JQA_VIA: "act" } });
process.exit(r.status ?? 1);
`;
}

export function assertActorId(id) {
  if (!ID_PATTERN.test(String(id ?? ""))) throw new ContractError(`invalid actor_id: ${JSON.stringify(id)} (letters, digits, . _ -)`, "bad_args");
  return id;
}

export function buildPacket(card, { runDir, observerDocs = [], language = "pt-BR", strict = false, out = null, force = false } = {}) {
  const errors = validateDocument(card, "actor-card");
  if (errors.length) throw new ContractError(errors.join("\n"));
  assertActorId(card.actor_id);
  const warnings = leakCheck(card, observerDocs);
  if (strict && warnings.length) throw new ContractError(`Possible leak to the actor (nothing was written):\n${warnings.join("\n")}`, "leak");
  const lang = packetLang(card.locale);
  const T = PACKET_TEXT[lang];
  const role = readFileSync(join(SKILL_ROOT, "roles", "actor.md"), "utf8").trim();
  const safe = Object.fromEntries(Object.keys(card).map((k) => [k, card[k]]));
  const payload = JSON.stringify(safe, null, 2);
  const fence = "`".repeat(Math.max(3, (payload.match(/`+/g) ?? [""]).reduce((m, s) => Math.max(m, s.length), 0) + 1));
  const text = `${role}

${T(fence, payload)}`;
  let dir = null;
  if (out) {
    mkdirSync(dirname(resolve(out)), { recursive: true });
    writeFileSync(out, text, { flag: force ? "w" : "wx" });
  }
  if (runDir) {
    dir = join(runDir, "actors", card.actor_id);
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "PACKET.md"), text);
    writeFileSync(join(dir, "act.mjs"), wrapperSource(runDir, card.actor_id, lang));
    try { chmodSync(join(dir, "act.mjs"), 0o755); } catch {}
  }
  return { text, warnings, dir, language, lang };
}

// Start prompt for the spawned actor, in the card's language.
const PROMPTS = {
  pt: "Comece agora a sua jornada, seguindo exatamente as instruções do seu papel (PACKET.md).",
  en: "Start your journey now, following exactly the instructions of your role (PACKET.md).",
};

/** Packet language from the card's locale: Portuguese for pt*, English for anything else or none. */
export function packetLang(locale) {
  return /^pt/i.test(String(locale ?? "")) ? "pt" : "en";
}

// The packet does not record its language elsewhere; the locale sits in the card JSON inside PACKET.md.
function langOfPacket(text) {
  return packetLang(text.match(/"locale":\s*"([^"]*)"/)?.[1]);
}

export function spawnCommand(host, { model = null, custom = null, bare = false, lang = "en" } = {}) {
  const PROMPT = PROMPTS[lang] ?? PROMPTS.en;
  switch (host) {
    case "claude":
      // No --bare by default: --bare needs ANTHROPIC_API_KEY (it does not read the OAuth login). Isolation comes from the
      // folder outside the repository, --disable-slash-commands (no skills), --strict-mcp-config (no MCP) and --tools Bash.
      // --setting-sources project: in the actor's empty folder that loads no settings at all, so the user's hooks
      // (session receipts, routers, style injectors) neither run for the actor nor add text to its context.
      return { bin: "claude", args: ["-p", ...(bare ? ["--bare"] : []), "--setting-sources", "project", "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config", "--tools", "Bash", "--allowedTools", "Bash(node act.mjs *)", "--permission-mode", "dontAsk", "--append-system-prompt-file", "PACKET.md", "--output-format", "stream-json", "--verbose", ...(model ? ["--model", model] : []), PROMPT], stream: true };
    case "codex":
      return { bin: "codex", args: ["exec", "--ephemeral", "--skip-git-repo-check", "--sandbox", "workspace-write", "-c", "sandbox_workspace_write.network_access=true", ...(model ? ["-m", model] : []), "-"], stdin: true };
    case "pi":
      return { bin: "pi", args: ["-p", "--no-session", "--no-context-files", "--no-skills", "--no-extensions", "--no-prompt-templates", "--tools", "bash", "--append-system-prompt", "PACKET.md", ...(model ? ["--model", model] : []), PROMPT] };
    case "custom": {
      if (!custom) throw new ContractError("--host custom needs --cmd 'program args {PROMPT_FILE}'", "bad_args");
      const parts = custom.match(/"[^"]*"|\S+/g).map((p) => p.replace(/^"|"$/g, ""));
      return { bin: parts[0], args: parts.slice(1) };
    }
    default:
      throw new ContractError(`Unknown host: ${host}. Use claude|codex|pi|custom.`, "bad_args");
  }
}

// Windows: .cmd shims need a shell; quote every argument ourselves (no prompt text is ever passed as an argument).
const winQuote = (a) => (/^[A-Za-z0-9_./:=@-]+$/.test(a) ? a : `"${a.replace(/"/g, '""')}"`);

/** Actor working directory outside any repository, so hosts do not discover AGENTS.md/CLAUDE.md/skills of the project. */
export function actorWorkdir(runDir, actorId) {
  const key = createHash("sha1").update(resolve(runDir)).digest("hex").slice(0, 10);
  return join(tmpdir(), "journey-qa-actors", key, actorId);
}

/** Remove the actor's scratch folder, which holds its driver token, once the actor has ended. Only the folder `actorWorkdir` names. */
export function cleanupActorWorkdir(runDir, actorId) {
  rmSync(actorWorkdir(runDir, actorId), { recursive: true, force: true });
}

// Environment the jailed agent keeps; everything else (cloud, forge and agent sockets included) is cleared. A provider
// key passes only when the chosen model names its provider (pi otherwise reads its own ~/.pi/agent/auth.json).
const JAIL_ENV = /^(LANG|LC_[A-Z]+|TERM|TZ|NODE_EXTRA_CA_CERTS|SSL_CERT_FILE|SSL_CERT_DIR|REQUESTS_CA_BUNDLE)$/;
const PROVIDER_KEYS = {
  anthropic: "ANTHROPIC_API_KEY", openai: "OPENAI_API_KEY", openrouter: "OPENROUTER_API_KEY", google: "GEMINI_API_KEY",
  gemini: "GEMINI_API_KEY", deepseek: "DEEPSEEK_API_KEY", groq: "GROQ_API_KEY", mistral: "MISTRAL_API_KEY", xai: "XAI_API_KEY",
};
export function providerKeyFor(model) {
  const provider = String(model ?? "").split("/")[0].toLowerCase();
  return PROVIDER_KEYS[provider] ?? null;
}

/**
 * pi has no command allowlist, so its actor would get the user's whole shell. On Linux the actor runs in a bubblewrap
 * jail built as an allowlist: the system read-only, HOME an empty tmpfs with only the agent's own runtime bound back
 * read-only, /run, /mnt and /tmp empty, its own folder the only writable path, the environment cleared to JAIL_ENV, and
 * IPC, UTS and PID namespaces of its own. The run folder is masked with an empty tmpfs: act.mjs reaches the driver with a token that
 * only drives this actor. The network stays shared, for the model API and the loopback driver.
 */
export function jailArgs(cwd, { platform = process.platform, home = homedir(), runDir = null, bind = [], scratch = [], env = process.env, passEnv = [], has = (b) => spawnSync("sh", ["-c", `command -v ${b}`]).status === 0 } = {}) {
  if (platform !== "linux" || !has("bwrap")) return null;
  const a = ["--ro-bind", "/", "/", "--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", "/run", "--tmpfs", home,
    "--unshare-pid", "--unshare-ipc", "--unshare-uts", "--die-with-parent", "--new-session", "--clearenv"];
  if (existsSync("/mnt")) a.push("--tmpfs", "/mnt");
  // resolv.conf may live under /run (systemd-resolved) or /mnt/wsl (WSL); without it the agent has no DNS.
  try { const r = realpathSync("/etc/resolv.conf"); if (/^\/(run|mnt)\//.test(r)) a.push("--ro-bind", r, r); } catch {}
  for (const d of bind) if (existsSync(d)) a.push("--ro-bind", d, d);
  // The agent's own config folder (pi locks settings.json there) comes back through an overlay whose writes vanish.
  for (const d of scratch) if (existsSync(d)) a.push("--overlay-src", d, "--tmp-overlay", d);
  a.push("--setenv", "HOME", home, "--setenv", "PATH", `${dirname(process.execPath)}:/usr/local/bin:/usr/bin:/bin`);
  for (const [k, v] of Object.entries(env)) if ((JAIL_ENV.test(k) || passEnv.includes(k)) && v !== undefined) a.push("--setenv", k, v);
  // The run holds the ledger and the driver's full token; wherever it lives, the jail shows an empty folder there.
  if (runDir) {
    // Mask by real path, and the whole qa-runs folder when the run sits in one: other runs hold their own driver tokens.
    let real = resolve(runDir);
    try { real = realpathSync(real); } catch {}
    const parent = dirname(real);
    a.push("--tmpfs", basename(parent) === "qa-runs" ? parent : real);
  }
  a.push("--bind", cwd, cwd, "--chdir", cwd);
  return a;
}

/** Folders a jailed host needs from HOME: the node install that runs it (and holds a global npm install of the agent). */
function runtimeBinds(bin) {
  const out = new Set();
  const nodeRoot = dirname(dirname(realpathSync(process.execPath)));
  out.add(nodeRoot);
  try {
    const p = spawnSync("sh", ["-c", `command -v ${bin}`], { encoding: "utf8" }).stdout.trim();
    if (p) { const real = realpathSync(p); if (!real.startsWith(nodeRoot)) out.add(dirname(real)); }
  } catch {}
  // Never HOME or a broad parent of it (~/.local, ~/.config): at least three levels below HOME, or the bind is dropped.
  const deep = (d) => d.startsWith(homedir() + "/") && d.slice(homedir().length + 1).split("/").length >= 3;
  return [...out].filter(deep);
}

export function spawnActor(runDir, actorId, { host, model, custom, bare = false, exec = false, background = false, timeoutMinutes = 30, unsandboxed = false, driver = null }) {
  assertActorId(actorId);
  const src = join(runDir, "actors", actorId);
  if (!existsSync(join(src, "PACKET.md"))) throw new ContractError(`Packet missing: run \`jqa actor packet\` for ${actorId}`, "bad_args");
  const prompt = readFileSync(join(src, "PACKET.md"), "utf8");
  const spec = spawnCommand(host, { model, custom, bare, lang: langOfPacket(prompt) });
  const cwd = actorWorkdir(runDir, actorId);
  let args = spec.args.map((a) => a.replace("{PROMPT_FILE}", () => join(cwd, "PACKET.md")));
  // claude is held to `node act.mjs` by its own allowlist and codex by its sandbox; pi and custom agents get a free shell.
  const needsJail = (host === "pi" || host === "custom") && !unsandboxed;
  let jailed = false;
  if (needsJail) {
    const key = providerKeyFor(model);
    const jail = jailArgs(cwd, { runDir, bind: runtimeBinds(spec.bin), scratch: host === "pi" ? [join(homedir(), ".pi", "agent")] : [], passEnv: key ? [key] : [] });
    if (!jail) throw new ContractError(`--host ${host} gives the actor a free shell and no jail is available here (needs Linux and bwrap). Install bubblewrap, use --host claude, or pass --unsandboxed and declare it in limitations.`, "no_jail");
    args = [...jail, "--", spec.bin, ...args];
    spec.bin = "bwrap";
    jailed = true;
  }
  const shown = `${spec.bin} ${args.map((a) => (/[\s*()"']/.test(a) ? JSON.stringify(a) : a)).join(" ")}${spec.stdin ? " < PACKET.md" : ""}`;
  if (!exec) return { planned: true, cwd, command: shown, jailed };
  mkdirSync(cwd, { recursive: true });
  for (const f of ["PACKET.md", "act.mjs"]) writeFileSync(join(cwd, f), readFileSync(join(src, f)));
  rmSync(join(cwd, ".driver.json"), { force: true });
  if (driver) writeFileSync(join(cwd, ".driver.json"), JSON.stringify(driver), { mode: 0o600 });
  else if (jailed) throw new ContractError("A jailed actor reaches the driver through its own token: start the driver (`jqa browser start`) and add the actor first.", "driver_not_running");
  const win = process.platform === "win32";
  const bin = win ? winQuote(spec.bin) : spec.bin;
  const finalArgs = win ? args.map(winQuote) : args;
  const meta = { actor_id: actorId, host, model: model ?? null, command: shown, cwd, started_at: new Date().toISOString() };
  if (background) {
    // Runner records the exit code so `actor wait` can report it.
    const runner = join(cwd, ".runner.mjs");
    writeFileSync(runner, `import { spawnSync } from "node:child_process";
import { readFileSync, writeFileSync } from "node:fs";
const r = spawnSync(${JSON.stringify(bin)}, ${JSON.stringify(finalArgs)}, { cwd: ${JSON.stringify(cwd)}, input: ${spec.stdin ? "readFileSync(" + JSON.stringify(join(cwd, "PACKET.md")) + ", 'utf8')" : "undefined"}, encoding: "utf8", maxBuffer: 64 * 1024 * 1024, shell: ${win}, timeout: ${timeoutMinutes * 60000} });
writeFileSync(${JSON.stringify(join(src, spec.stream ? "session.jsonl" : "transcript.md"))}, r.stdout ?? "");
writeFileSync(${JSON.stringify(join(src, "stderr.log"))}, (r.stderr ?? "") + (r.error ? "\\n" + r.error.message : ""));
writeFileSync(${JSON.stringify(join(src, "exit.json"))}, JSON.stringify({ exit_code: r.status, signal: r.signal ?? null, error: r.error?.message ?? null, ended_at: new Date().toISOString() }));
`);
    const child = spawn(process.execPath, [runner], { cwd, detached: true, stdio: "ignore", windowsHide: true });
    child.unref();
    writeFileSync(join(src, "spawn.json"), JSON.stringify({ ...meta, pid: child.pid, background: true }, null, 2));
    return { started: true, background: true, pid: child.pid, command: shown, cwd };
  }
  const r = spawnSync(bin, finalArgs, {
    cwd, input: spec.stdin ? prompt : undefined, encoding: "utf8", timeout: timeoutMinutes * 60000, maxBuffer: 64 * 1024 * 1024, shell: win,
  });
  writeFileSync(join(src, spec.stream ? "session.jsonl" : "transcript.md"), r.stdout ?? "");
  writeFileSync(join(src, "stderr.log"), `${r.stderr ?? ""}${r.error ? `\n${r.error.message}` : ""}`);
  const done = { ...meta, ended_at: new Date().toISOString(), exit_code: r.status, signal: r.signal ?? null, error: r.error?.message ?? null };
  writeFileSync(join(src, "spawn.json"), JSON.stringify(done, null, 2));
  let evidence;
  try {
    evidence = recordTranscript(runDir, actorId, done);
  } finally {
    cleanupActorWorkdir(runDir, actorId);
  }
  const cj = join(src, "commands.json");
  const session = existsSync(cj) ? JSON.parse(readFileSync(cj, "utf8")) : null;
  return { ...done, evidence, session: session && { total: session.total, outside_interface: session.outside_interface.length, denied: session.denied, cost_usd: session.cost_usd } };
}

/**
 * Reads a stream-json session (claude -p) into transcript.md and commands.json: the actor's final answer, every
 * command it asked to run, the ones outside `node act.mjs` and the ones the host denied. This is the record that the
 * actor stayed on the interface, which the final answer alone cannot show.
 */
export function readSession(dir) {
  const file = join(dir, "session.jsonl");
  if (!existsSync(file)) return null;
  const commands = [];
  let result = null;
  for (const line of readFileSync(file, "utf8").split("\n")) {
    let ev;
    try { ev = JSON.parse(line); } catch { continue; }
    if (ev.type === "assistant") {
      for (const c of ev.message?.content ?? []) if (c.type === "tool_use") commands.push({ tool: c.name, command: c.input?.command ?? JSON.stringify(c.input) });
    } else if (ev.type === "result") result = ev;
  }
  const denied = (result?.permission_denials ?? []).map((d) => d.tool_input?.command ?? JSON.stringify(d.tool_input ?? d));
  const outside = commands.filter((c) => c.tool !== "Bash" || !/^node act\.mjs(\s|$)/.test(String(c.command).trim()));
  const summary = { total: commands.length, outside_interface: outside, denied, cost_usd: result?.total_cost_usd ?? null, turns: result?.num_turns ?? null, is_error: result?.is_error ?? null };
  writeFileSync(join(dir, "transcript.md"), `${result?.result ?? ""}\n`);
  writeFileSync(join(dir, "commands.json"), `${JSON.stringify({ ...summary, commands }, null, 2)}\n`);
  return summary;
}

export function recordTranscript(runDir, actorId, meta) {
  const dir = join(runDir, "actors", actorId);
  const session = readSession(dir);
  const transcript = join(dir, "transcript.md");
  if (!existsSync(transcript)) return null;
  if (session) {
    appendEvidence(runDir, {
      kind: "tool_result", origin: "observed", captured_by: "tool", actor_id: actorId, sourceFile: join(dir, "commands.json"),
      summary: `Actor ${actorId} ran ${session.total} commands: ${session.outside_interface.length} outside act.mjs, ${session.denied.length} denied by the host`,
    });
  }
  const e = appendEvidence(runDir, {
    kind: "transcript", origin: "observed", captured_by: "tool", actor_id: actorId, sourceFile: transcript,
    summary: `Final answer of actor ${actorId} in a separate process (${meta.host}${meta.model ? `/${meta.model}` : ""}, exit ${meta.exit_code ?? "?"})`,
  });
  return e.evidence_id;
}

export function recordOutcome(runDir, actorId, outcome, reason) {
  assertActorId(actorId);
  const allowed = ["achieved", "not_achieved", "abandoned", "undetermined"];
  if (!allowed.includes(outcome)) throw new ContractError(`Invalid outcome. Use ${allowed.join("|")}`);
  if (!reason?.trim()) throw new ContractError("Give the reason for the outcome.");
  const dir = join(runDir, "actors", actorId);
  mkdirSync(dir, { recursive: true });
  const data = { actor_id: actorId, outcome, reason: reason.trim(), declared_at: new Date().toISOString() };
  const e = appendEvidence(runDir, {
    kind: "transcript", origin: "observed", captured_by: "tool", actor_id: actorId, content: JSON.stringify(data, null, 2), ext: "json",
    summary: `Actor ${actorId} declared ${outcome}: ${reason.trim().slice(0, 200)}`,
  });
  writeFileSync(join(dir, "outcome.json"), JSON.stringify({ ...data, evidence_id: e.evidence_id }, null, 2));
  return { ...data, evidence_id: e.evidence_id };
}

export function loadObserverDocs(paths) {
  return paths.filter(Boolean).map((p) => readJson(p));
}
