// In-page function (serialized by the driver; must stay self-contained).
// Produces a compact, model-friendly outline of the visible page and stores
// element references in a symbol-keyed slot so the driver can act on them.
// Does not add attributes or nodes to the page DOM.
(function jqaSnapshot(opts) {
  const MAX_LINES = opts.full ? 4000 : 450;
  const TEXT_CAP = opts.full ? 1200 : 220;
  const KEY = Symbol.for(opts.key || "journey-qa.refs");
  const SECRET_FIELDS = window[Symbol.for("journey-qa.secret")];
  const SECRET_PARAM = /^(token|access_token|id_token|code|key|api_key|apikey|secret|password|pass|sig|signature|auth|session|otp)$/i;
  const refs = [];
  const lines = [];
  const meta = [];
  let truncated = false;

  const clean = (s) => (s || "").replace(/\s+/g, " ").trim();
  const cap = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
  const q = (s) => JSON.stringify(s);

  const IMPLICIT = {
    A: (el) => (el.hasAttribute("href") ? "link" : null),
    BUTTON: () => "button",
    SELECT: (el) => (el.multiple || el.size > 1 ? "listbox" : "combobox"),
    TEXTAREA: () => "textbox",
    SUMMARY: () => "button",
    DETAILS: () => "group",
    DIALOG: () => "dialog",
    IMG: (el) => (el.getAttribute("alt") === "" ? null : "img"),
    OPTION: () => "option",
    PROGRESS: () => "progressbar",
    METER: () => "meter",
    OUTPUT: () => "status",
    INPUT: (el) => {
      const t = (el.getAttribute("type") || "text").toLowerCase();
      return ({
        button: "button", submit: "button", reset: "button", image: "button", checkbox: "checkbox", radio: "radio",
        range: "slider", number: "spinbutton", search: "searchbox", email: "textbox", tel: "textbox", url: "textbox",
        text: "textbox", password: "textbox", date: "textbox", "datetime-local": "textbox", time: "textbox",
        month: "textbox", week: "textbox", color: "button", file: "button", hidden: null,
      })[t] ?? "textbox";
    },
  };
  const INTERACTIVE_ROLES = new Set([
    "button", "link", "checkbox", "radio", "switch", "tab", "menuitem", "menuitemcheckbox", "menuitemradio",
    "option", "combobox", "listbox", "textbox", "searchbox", "spinbutton", "slider", "treeitem", "gridcell",
  ]);
  const HEADINGS = { H1: 1, H2: 2, H3: 3, H4: 4, H5: 5, H6: 6 };
  const REGION_TAGS = new Set(["MAIN", "NAV", "HEADER", "FOOTER", "ASIDE", "FORM", "SECTION", "DIALOG", "FIELDSET", "TABLE"]);
  const TEXT_BLOCKS = new Set(["P", "LI", "TD", "TH", "DT", "DD", "LABEL", "LEGEND", "CAPTION", "FIGCAPTION", "BLOCKQUOTE", "PRE", "SPAN", "DIV", "STRONG", "EM", "SMALL", "B", "I", "CODE", "TIME", "ARTICLE"]);

  const roleOf = (el) => {
    const explicit = clean(el.getAttribute("role")).split(" ")[0];
    if (explicit) return explicit;
    const fn = IMPLICIT[el.tagName];
    return fn ? fn(el) : null;
  };

  const visible = (el) => {
    if (el.hidden || el.getAttribute("aria-hidden") === "true" || el.inert) return false;
    const s = getComputedStyle(el);
    if (s.display === "none" || s.visibility === "hidden" || s.visibility === "collapse") return false;
    if (parseFloat(s.opacity) === 0 && !el.matches("input,select,textarea")) return false;
    const r = el.getBoundingClientRect();
    return r.width > 0 || r.height > 0 || el.getClientRects().length > 0 || s.display === "contents";
  };

  const textOf = (node) => clean(node.innerText ?? node.textContent ?? "");
  const byIds = (ids) =>
    clean(ids.split(/\s+/).map((id) => {
      const n = document.getElementById(id);
      return n ? n.textContent : "";
    }).join(" "));

  const nameOf = (el, role) => {
    const labelledby = el.getAttribute("aria-labelledby");
    if (labelledby) {
      const v = byIds(labelledby);
      if (v) return v;
    }
    const aria = clean(el.getAttribute("aria-label"));
    if (aria) return aria;
    if (el.matches("input,select,textarea,meter,progress,output")) {
      if (el.id) {
        const l = [...document.querySelectorAll("label")].find((x) => x.htmlFor === el.id);
        if (l) return textOf(l);
      }
      const wrap = el.closest("label");
      if (wrap) return textOf(wrap);
      if (["submit", "button", "reset"].includes(el.type) && el.value) return clean(el.value);
      const ph = clean(el.getAttribute("placeholder"));
      if (ph) return ph;
    }
    if (el.tagName === "IMG") return clean(el.getAttribute("alt"));
    if (["FIELDSET"].includes(el.tagName)) {
      const lg = el.querySelector(":scope > legend");
      if (lg) return textOf(lg);
    }
    if (el.tagName === "TABLE") {
      const c = el.querySelector(":scope > caption");
      if (c) return textOf(c);
    }
    if (["button", "link", "tab", "menuitem", "option", "checkbox", "radio", "switch", "treeitem", "heading", "gridcell", "cell"].includes(role) || el.tagName === "SUMMARY") {
      const t = textOf(el);
      if (t) return t;
    }
    return clean(el.getAttribute("title"));
  };

  const states = (el, role) => {
    const out = [];
    const a = (n) => el.getAttribute(n);
    if (el.disabled || a("aria-disabled") === "true") out.push("disabled");
    if (el.matches?.("input[type=checkbox],input[type=radio]")) out.push(el.checked ? "checked" : "unchecked");
    else if (a("aria-checked")) out.push(a("aria-checked") === "true" ? "checked" : a("aria-checked") === "mixed" ? "mixed" : "unchecked");
    if (a("aria-pressed")) out.push(a("aria-pressed") === "true" ? "pressed" : "not-pressed");
    if (a("aria-expanded")) out.push(a("aria-expanded") === "true" ? "expanded" : "collapsed");
    if (el.tagName === "SUMMARY" && el.parentElement?.tagName === "DETAILS") out.push(el.parentElement.open ? "expanded" : "collapsed");
    if (a("aria-selected") === "true" || (el.tagName === "OPTION" && el.selected)) out.push("selected");
    if (a("aria-current") && a("aria-current") !== "false") out.push(`current=${a("aria-current")}`);
    if (el.required || a("aria-required") === "true") out.push("required");
    if (el.readOnly || a("aria-readonly") === "true") out.push("readonly");
    if (a("aria-invalid") === "true" || (el.validity && !el.validity.valid && (el.value || el.dataset?.touched))) out.push("invalid");
    if (document.activeElement === el) out.push("focused");
    if (el.tagName === "SELECT") {
      const sel = [...el.selectedOptions].map((o) => clean(o.textContent));
      out.push(`value=${q(cap(sel.join(", "), 120))}`);
      const opts = [...el.options].map((o) => clean(o.textContent));
      out.push(`options=${q(cap(opts.join(" | "), opts.length > 12 ? 300 : 600))}`);
    } else if (el.matches?.("input,textarea") && !["checkbox", "radio", "button", "submit", "reset", "file", "image"].includes(el.type)) {
      const hidden = el.type === "password" || el.autocomplete === "one-time-code" || (SECRET_FIELDS && SECRET_FIELDS.has(el));
      const v = hidden ? (el.value ? "•".repeat(Math.min(el.value.length, 12)) : "") : el.value;
      out.push(`value=${q(cap(v ?? "", 160))}`);
      if (el.type && el.type !== "text" && el.type !== "textarea") out.push(`type=${el.type}`);
    } else if (el.isContentEditable) {
      out.push(`value=${q(cap(textOf(el), 160))}`);
    } else if (el.type === "file") {
      out.push(`files=${el.files?.length ?? 0}`);
    }
    if (role === "slider" || role === "spinbutton" || role === "progressbar" || role === "meter") {
      const now = a("aria-valuenow") ?? el.value;
      if (now !== null && now !== undefined && now !== "") out.push(`now=${now}`);
    }
    if (el.tagName === "A" && el.href) {
      try {
        const u = new URL(el.href, location.href);
        for (const k of [...u.searchParams.keys()]) if (SECRET_PARAM.test(k)) u.searchParams.set(k, "***");
        if (/token|code|key|secret|auth|session/i.test(u.hash)) u.hash = "#***";
        out.push(`href=${u.origin === location.origin ? u.pathname + u.search + u.hash : u.href}`);
      } catch {}
    }
    return out;
  };

  const isInteractive = (el, role) => {
    if (role && INTERACTIVE_ROLES.has(role)) return true;
    if (el.isContentEditable && el.getAttribute("contenteditable") !== "false" && !el.parentElement?.isContentEditable) return true;
    if (el.tagName === "SUMMARY") return true;
    const ti = el.getAttribute("tabindex");
    if (ti !== null && Number(ti) >= 0) return true;
    if (el.onclick || (getComputedStyle(el).cursor === "pointer" && !el.parentElement?.closest("a,button,[role=button],[onclick]") && el.children.length < 4 && textOf(el).length < 80 && textOf(el).length > 0)) return "heuristic";
    return false;
  };

  const push = (depth, text) => {
    if (lines.length >= MAX_LINES) {
      truncated = true;
      return false;
    }
    lines.push(`${"  ".repeat(Math.min(depth, 8))}${text}`);
    return true;
  };

  const directText = (el) => {
    let s = "";
    for (const n of el.childNodes) if (n.nodeType === 3) s += n.textContent;
    return clean(s);
  };

  const walk = (root, depth) => {
    for (const el of root.children ?? []) {
      if (truncated) return;
      if (["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "HEAD", "META", "LINK"].includes(el.tagName)) continue;
      if (!visible(el)) continue;
      if (el.tagName === "DETAILS" && !el.open) {
        const summary = [...el.children].find((c) => c.tagName === "SUMMARY");
        if (summary) walk({ children: [summary] }, depth);
        continue;
      }
      const role = roleOf(el);
      if (role === "presentation" || role === "none") {
        walk(el, depth);
        continue;
      }
      if (el.tagName === "IFRAME" || el.tagName === "FRAME") {
        push(depth, `- iframe ${q(clean(el.title || el.name || el.src || ""))} (use snapshot --frames)`);
        continue;
      }
      const interactive = isInteractive(el, role);
      if (interactive) {
        const idx = refs.length;
        refs.push(el);
        const r = role || (interactive === "heuristic" ? "clickable" : "generic");
        const name = cap(nameOf(el, r), 140);
        meta.push({ role: r, name });
        const st = states(el, r);
        push(depth, `- ${r}${name ? ` ${q(name)}` : ""} [${opts.prefix}e${idx + 1}]${st.length ? ` ${st.join(" ")}` : ""}`);
        if (el.tagName === "SELECT" || el.tagName === "TEXTAREA" || el.matches("input")) continue;
        if (["button", "link", "option", "tab", "menuitem", "checkbox", "radio", "switch"].includes(r) && !el.querySelector("input,select,textarea,button,a[href]")) continue;
        if (el.shadowRoot) walk(el.shadowRoot, depth + 1);
        walk(el, depth + 1);
        continue;
      }
      if (HEADINGS[el.tagName] || role === "heading") {
        const level = HEADINGS[el.tagName] ?? el.getAttribute("aria-level") ?? "?";
        if (!textOf(el)) continue;
        push(depth, `- heading ${q(cap(textOf(el), TEXT_CAP))} [h${level}]`);
        continue;
      }
      if (role === "alert" || role === "status" || el.getAttribute("aria-live")) {
        const t = cap(textOf(el), TEXT_CAP);
        push(depth, `- ${role === "alert" ? "alert" : "status"}: ${t ? q(t) : "(empty)"}`);
        continue;
      }
      if (el.tagName === "IMG" || role === "img") {
        const n = nameOf(el, "img");
        if (n) push(depth, `- img ${q(cap(n, 120))}`);
        continue;
      }
      let nextDepth = depth;
      if (REGION_TAGS.has(el.tagName) || ["dialog", "alertdialog", "region", "navigation", "main", "form", "group", "tabpanel", "table", "list", "banner", "contentinfo"].includes(role)) {
        const n = nameOf(el, role || "");
        const label = role || el.tagName.toLowerCase();
        const meaningful = n || ["dialog", "alertdialog", "main", "navigation", "form", "table"].includes(label) || ["DIALOG", "MAIN", "NAV", "FORM", "TABLE"].includes(el.tagName);
        if (meaningful) {
          push(depth, `- ${label}${n ? ` ${q(cap(n, 100))}` : ""}${el.open && el.tagName === "DIALOG" ? " open" : ""}${el.getAttribute("aria-modal") === "true" ? " modal" : ""}:`);
          nextDepth = depth + 1;
        }
      }
      if (el.tagName === "LABEL" && ((el.htmlFor && document.getElementById(el.htmlFor)) || el.querySelector("input,select,textarea"))) {
        walk(el, nextDepth);
        continue;
      }
      if (el.tagName === "LEGEND" && el.parentElement?.tagName === "FIELDSET") continue;
      const INLINE = ["SPAN", "B", "STRONG", "EM", "I", "SMALL", "CODE", "TIME", "MARK", "ABBR", "DATA", "OUTPUT", "SUP", "SUB"];
      if (["DIV", "P", "LI", "DD", "DT", "TD"].includes(el.tagName) && el.children.length >= 2 && el.children.length <= 6 && !directText(el) &&
        [...el.children].every((c) => INLINE.includes(c.tagName) && !c.querySelector("*:not(b,strong,em,i,small,code,time,mark,abbr,sup,sub,br)") && !c.getAttribute("role")) &&
        !el.querySelector("a[href],button,input,select,textarea,[tabindex]")) {
        const joined = [...el.children].filter(visible).map((c) => textOf(c)).filter(Boolean).join(" · ");
        if (joined) push(nextDepth, `- text: ${q(cap(joined, TEXT_CAP))}`);
        continue;
      }
      if (TEXT_BLOCKS.has(el.tagName)) {
        const own = directText(el);
        if (own) {
          const hasBlockChildren = [...el.children].some((c) => !["B", "STRONG", "EM", "I", "SMALL", "CODE", "SPAN", "TIME", "BR", "SUP", "SUB", "ABBR", "MARK"].includes(c.tagName));
          if (!hasBlockChildren && !el.querySelector("a[href],button,input,select,textarea,[role],[tabindex]")) {
            if (textOf(el)) push(nextDepth, `- text: ${q(cap(textOf(el), TEXT_CAP))}`);
            continue;
          }
          push(nextDepth, `- text: ${q(cap(own, TEXT_CAP))}`);
        }
      }
      if (el.shadowRoot) walk(el.shadowRoot, nextDepth);
      walk(el, nextDepth);
    }
  };

  walk(document.body ?? document.documentElement, 0);
  window[KEY] = { refs, meta, at: Date.now() };
  const scroll = {
    y: Math.round(window.scrollY),
    max: Math.max(0, Math.round(document.documentElement.scrollHeight - window.innerHeight)),
  };
  return { lines, count: refs.length, meta, truncated, scroll, title: document.title, url: location.href };
})
