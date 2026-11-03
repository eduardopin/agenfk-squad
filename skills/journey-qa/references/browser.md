# Browser: the pack's driver and the host's tools

The method is the same with any adapter. What changes is how automatic the evidence is and how much the guardrails are enforced by code.

| Adapter | When to use | Evidence | Guardrails |
| --- | --- | --- | --- |
| **`jqa_driver`** (preferred) | Host with a shell: Claude Code, Codex, pi, Claude with code execution, any agent that runs commands | Automatic: screenshot + DOM in the ledger on every action | Origin allowlist, context per actor, budget, oracle commands only for the observer, masked secrets |
| **`host_tools`** | The host already controls the user's Chrome (Claude in Chrome, browser MCP) and the driver is unavailable or the user wants their own session | Manual: `jqa evidence add` for each proof | By instruction; declare what was not enforced |
| **`none`** | Text only, no browser | Only `provided`/`static_review` | Journey execution blocked; analysis and architecture continue |

Record the adapter in `run-report.json → host.adapter`.

## 1. Prepare the driver

```bash
jqa doctor                      # Node, Playwright, browser
```

If Playwright is missing, install it once per user, not in the project and not in the skill folder:

```bash
jqa setup                       # playwright-core into ~/.local/share/journey-qa (Windows: %LOCALAPPDATA%\journey-qa), uses the installed Chrome
jqa setup --chromium            # no Chrome on the machine: also downloads Playwright's Chromium
jqa setup --dry-run             # prints the npm commands without running them
```

The runtime is shared by every host and every copy of the skill, so reinstalling or updating the skill keeps the driver. `JQA_RUNTIME` moves it. A `node_modules` in `<skill>/scripts` still wins if present.

`JQA_PLAYWRIGHT=/path/node_modules` points to an existing installation. The driver tries `chrome` and then `chromium` (`--browser chrome|chromium|msedge` forces one). Without a browser, continue with `host_tools` or declare the execution blocked; architecture review and evidence analysis remain possible.

Installing a package needs network access and changes the disk: do it only if the user authorized preparing the environment; otherwise, report the command.

## 2. Start, create actors, prove capabilities

```bash
jqa browser start --allow https://staging.example.com [--allow https://auth.example.com] [--headed]
jqa browser actor add comprador [--mobile] [--max-actions 60] [--max-minutes 20]
jqa browser actor add atendente
jqa browser actor add qa --role observer        # optional: the observer's own account (e.g. admin)
jqa browser handshake --actor comprador --url https://staging.example.com/
```

- **Allowlist:** main-page navigation outside the authorized origins is aborted and recorded. Include the journey's legitimate origins (external login, hosted checkout). Third-party subresources and iframes are not blocked.
- **Context per actor:** each `actor add` creates its own BrowserContext (separate cookies, storage and cache). The second actor generates isolation evidence automatically.
- **Handshake:** exercises observation (DOM and screenshot), click, typing, keyboard, wait, held key and evidence capture on a self-test page of the driver itself; with `--url`, it opens the target. Writes `session-binding.json` with `status: verified` and `proof_ref` for each proven capability. `jqa validate session-binding.json` checks the contract.
- **User's Chrome:** `--cdp http://127.0.0.1:9222` connects to a Chrome that the user started with remote debugging. Never enable debugging, install an extension or open a port on your own. New contexts via CDP remain isolated from the personal profile.
- The default budget comes from `scenario.json` (or `--max-actions`/`--max-minutes`). A second `start` does not change the allowlist: use `jqa browser allow ORIGIN`.
- The driver stops by itself after 60 min of no use (`--idle-minutes`). `jqa browser stop` stops it now; `jqa browser status` shows actors, budget and tabs.
- Observer observations (`--as observer`) use their own refs and consume neither the actor's warnings nor its budget.

## 3. Actor commands (interface)

All accept `--actor ID`. Each action returns a status line, evidence IDs and the new observation (use `--no-snapshot` to save context when you do not need it).

| Intent | Command |
| --- | --- |
| Observe | `snapshot [--full] [--frames]` · `screenshot [--full] [--ref eN]` · `text` · `tabs` |
| Navigate | `open URL` · `back` · `forward` · `reload` · `tab N` · `close-tab` |
| Act | `click eN [--double]` · `hover eN` · `fill eN "text" [--secret]` · `type eN "text" [--delay ms]` · `select eN "Option"` · `check eN` · `uncheck eN` · `upload eN file` |
| Keyboard | `press Enter [--ref eN]` · `keydown K` · `keyup K` · `hold K --ms 800` |
| Scroll | `scroll down|up|top|bottom|eN` |
| Wait | `wait --text "Order" | --gone "Loading" | --url /pedido | --ms 2000 [--timeout ms]` |
| Dialog | `dialog accept [--text answer]` · `dialog dismiss` |

**Snapshot format:** an outline of the page in reading order: regions (`main`, `form`, `dialog`, named sections), headings, text, `status`/`alert` (live regions) and controls with role, accessible name, ref and state (`checked`, `disabled`, `expanded`, `value=…`, `options=…`, `invalid`, `focused`). The header has title, URL, tab and scroll position; the footer has warnings (open dialog, new tab, download, blocked navigation, counted console errors).

Operating rules:

- Refs belong to the last snapshot; after navigation or a large change, the driver refuses the ref (`stale_ref`): observe again. Ref errors do not consume budget.
- `fill` replaces the value; `type` types key by key (useful for autocomplete and validation on typing).
- `password` and `one-time-code` fields are masked in the trace and the snapshot automatically; for other sensitive data use `--secret` (it also masks the snapshot and suppresses the automatic screenshot of that action).
- Native dialogs (`alert/confirm/prompt`) stay pending until `dialog accept|dismiss`: the decision belongs to the actor, never automatic.
- Downloads become `file` evidence in the ledger.
- Budget exhausted → refusal with code 3. End the journey; raising it is a coordinator intervention: `jqa browser budget --actor ID --add 20 --reason "…"` (recorded in the trace).

## 4. Observer commands (oracles)

They require `--as observer` and are marked in the trace. They are not part of the actor's journey.

| Command | Use |
| --- | --- |
| `network [--filter /api/pedidos] [--all]` | Requests from the actor's context (method, URL with masked secrets, status) |
| `console [--last N]` | Page messages and errors |
| `storage` | localStorage/sessionStorage of the current origin |
| `cookies` | Names and attributes (values hidden) |
| `eval "expression"` | Expression in the actor's page context, e.g. `fetch('/api/agenda').then(r=>r.json())` to check the payload that person's browser receives |

`eval` runs as the actor's page: it reads what that user can read. Use it to prove a leak or a state, never to advance the actor's mission.

## 5. Use host tools (`host_tools`)

When the host already offers a browser (extension, MCP, computer use):

1. List the real tools and map the intents (open, observe, click, type, key, wait, capture). Do not invent names or parameters.
2. Make one observation and one reversible interaction on the right target; fill in `session-binding.json` (template in `assets/templates/`) with `adapter: "host_tools"`, the tool and the proof of each capability.
3. For each proof that supports a conclusion, record it in the ledger: `jqa evidence add --kind screenshot --file captura.png --summary "…" --actor ID` (or `--kind tool_result --text "…" --locator "tool X, call Y"` when there is no file).
4. Apply the guardrails by discipline: only authorized origins, one identity per profile, no privileged shortcuts in the actor, budget counted.

Isolation with host tools: tabs of the same profile share a session. Without proven separate profiles, multi-user journeys are sequential (verified logout/login) and the report declares `browser_sessions: shared`.

## 6. Special surfaces

| Surface | Procedure |
| --- | --- |
| Iframe | `snapshot --frames` lists accessible frames with refs `f1e3`. Inaccessible cross-origin content becomes a limitation, not an assumption. |
| Shadow DOM | The snapshot walks open shadow roots; closed ones are left out. |
| Popup / new tab / external login | The driver selects the new tab and warns; confirm the domain and identity; go back with `tab N`. |
| Upload | Only an authorized test file. Selection does not prove processing: verify the effect. |
| Download | Becomes evidence; confirm the content, not just the click. |
| Canvas, WebGL, games | Use screenshot and real keyboard; `hold`/`keydown`/`keyup` for continuous input. Without a semantic DOM, limit conclusions to discrete observable states. |
| Audio and video | Player state does not prove perception of sound/image. |
| Mobile | `actor add ID --mobile` emulates viewport/touch (Pixel 7). It is not a physical device, nor a native app, nor Android permissions. |
| Permissions, geolocation, time zone | `--timezone` on `start`; record emulation as emulation. |
| Accessibility | Accessible names, focus and keyboard are partial checks. Do not claim a screen reader without one. |

Chrome covers web surfaces. Native Android, GTK/desktop, local CLI, sensors, system notifications and gamepad need another adapter; preserve the method and declare the blocked part. Do not simulate in text the execution of these surfaces.

## 7. End with honest coverage

- Essential capability missing before starting → `run_status: blocked`, `qa_verdict: not_evaluated`.
- Useful progress and then lack of budget/observation → `partial`.
- Driver/tool failure → `error`; it is not a product defect.
- Backend oracle, inbox, external channel or device unavailable → check not concluded (not `passed`).
- A success screen proves that the screen appeared; the effect depends on the rule's oracle.
