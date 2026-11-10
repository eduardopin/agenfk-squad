// cloud-diagram probe. render.py and render.sh inject it into the page they render; it measures
// what a reader's eye misses and writes the result as JSON into a hidden pre element with the id
// probe-out, for --dump-dom to read. Inside an iframe (widths under 500 px) it also posts the
// result to the parent frame. The rules are in references/probe-rules.md.
//
// A diagram is any svg[data-diagram], .dia svg or svg#dia. Inside it, the probe reads:
//   <text>                    labels (those in a badge and in <defs> are skipped)
//   <use x y width height>    icons
//   .ar or .edge              edges: path (M/L/H/V/Z, absolute or relative), line, polyline or
//                             polygon
//   .jn or .junction          the dot that marks a fork, where one edge leaves another
//   g.st[data-s] or           numbered badges, matched against the drawing's ol.steps: the one
//   g.step[data-step]         whose data-diagram names the svg id, else the first ol.steps after
//                             the drawing and before the next one
//   rect.grp, .g-sub, .g-priv, .g-svc, .g-aux   group boundaries
// Every shape is mapped through its transform into the svg's user space, so a node grouped under
// <g transform> is measured where it is drawn. The page's stamp must carry a Status cell: CURRENT
// with a YYYY-MM-DD date, or PROPOSED.
(function () {
  "use strict";
  var GROUPS = "rect.grp, rect.g-sub, rect.g-priv, rect.g-svc, rect.g-aux";
  var EDGES = ".ar, .edge", JUNCTIONS = ".jn, .junction", BADGES = ".st, .step";
  // Two parallel edges closer than this read as one line on screen.
  var CORRIDOR = 3;

  function box(x, y, w, h) { return { x: x, y: y, w: w, h: h }; }
  function shrink(b, d) { return box(b.x + d, b.y + d, b.w - 2 * d, b.h - 2 * d); }
  function hit(a, b) { return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h; }
  function inside(a, b) { return a.x >= b.x && a.y >= b.y && a.x + a.w <= b.x + b.w && a.y + a.h <= b.y + b.h; }
  function num(el, a) { return parseFloat(el.getAttribute(a) || "0"); }
  function name(el) { return (el.textContent || el.getAttribute("href") || el.tagName).trim().replace(/\s+/g, " ").slice(0, 40); }
  function round(v) { return Math.round(v * 100) / 100; }

  // The matrix from an element's own coordinates to the svg's user space.
  function toSvg(svg, el) {
    var root = svg.getScreenCTM(), own = el.getScreenCTM();
    return root && own ? root.inverse().multiply(own) : { a: 1, b: 0, c: 0, d: 1, e: 0, f: 0 };
  }
  function point(m, x, y) { return { x: round(m.a * x + m.c * y + m.e), y: round(m.b * x + m.d * y + m.f) }; }
  function mapBox(m, b) {
    var ps = [point(m, b.x, b.y), point(m, b.x + b.w, b.y), point(m, b.x, b.y + b.h), point(m, b.x + b.w, b.y + b.h)];
    var xs = ps.map(function (p) { return p.x; }), ys = ps.map(function (p) { return p.y; });
    var x0 = Math.min.apply(null, xs), y0 = Math.min.apply(null, ys);
    return box(x0, y0, Math.max.apply(null, xs) - x0, Math.max.apply(null, ys) - y0);
  }

  // Parse a path into straight segments. Any command other than M/L/H/V/Z draws
  // a curve, which an orthogonal diagram does not use.
  function segments(d) {
    var out = [], curved = false, x = 0, y = 0, sx = 0, sy = 0, cmd = null;
    var tokens = d.match(/[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[MmLlHhVvZzCcSsQqTtAa]/g) || [];
    var i = 0;
    function next() { return parseFloat(tokens[i++]); }
    while (i < tokens.length) {
      if (/[a-zA-Z]/.test(tokens[i]) && !/^[-+.\d]/.test(tokens[i])) cmd = tokens[i++];
      if (cmd === null) break;
      var rel = cmd === cmd.toLowerCase(), c = cmd.toUpperCase(), nx = x, ny = y;
      if (c === "Z") { nx = sx; ny = sy; cmd = null; }
      else if (c === "M") { nx = next() + (rel ? x : 0); ny = next() + (rel ? y : 0); x = sx = nx; y = sy = ny; cmd = rel ? "l" : "L"; continue; }
      else if (c === "L") { nx = next() + (rel ? x : 0); ny = next() + (rel ? y : 0); }
      else if (c === "H") { nx = next() + (rel ? x : 0); }
      else if (c === "V") { ny = next() + (rel ? y : 0); }
      else { curved = true; break; }
      if (isNaN(nx) || isNaN(ny)) break;
      if (nx !== x || ny !== y) out.push({ x1: x, y1: y, x2: nx, y2: ny });
      x = nx; y = ny;
    }
    return { segs: out, curved: curved };
  }
  // The same segments for a line, polyline or polygon, read as a path.
  function pathOf(el) {
    var tag = el.tagName.toLowerCase();
    if (tag === "path") return el.getAttribute("d") || "";
    if (tag === "line") return "M" + num(el, "x1") + " " + num(el, "y1") + "L" + num(el, "x2") + " " + num(el, "y2");
    if (tag === "polyline" || tag === "polygon") {
      var pts = (el.getAttribute("points") || "").trim().split(/[\s,]+/);
      var d = "";
      for (var k = 0; k + 1 < pts.length; k += 2) d += (k ? "L" : "M") + pts[k] + " " + pts[k + 1];
      return tag === "polygon" ? d + "Z" : d;
    }
    return null;
  }
  function segBox(s) { return box(Math.min(s.x1, s.x2) - .5, Math.min(s.y1, s.y2) - .5, Math.abs(s.x2 - s.x1) + 1, Math.abs(s.y2 - s.y1) + 1); }
  function horizontal(s) { return s.y1 === s.y2; }
  function vertical(s) { return s.x1 === s.x2; }
  function badgeNumber(g) { return g.getAttribute("data-s") || g.getAttribute("data-step"); }
  function band(g) { return /\bg-(svc|aux)\b/.test(g.el.getAttribute("class")); }

  function stepsList(svg, svgs) {
    var lists = [].slice.call(document.querySelectorAll("ol.steps"));
    if (svg.id) {
      var named = lists.filter(function (l) { return l.getAttribute("data-diagram") === svg.id; });
      if (named.length) return named[0];
    }
    var nextSvg = svgs[svgs.indexOf(svg) + 1];
    return lists.filter(function (l) {
      var after = svg.compareDocumentPosition(l) & Node.DOCUMENT_POSITION_FOLLOWING;
      var beforeNext = !nextSvg || (l.compareDocumentPosition(nextSvg) & Node.DOCUMENT_POSITION_FOLLOWING);
      return after && beforeNext && !l.hasAttribute("data-diagram");
    })[0] || null;
  }

  function probeSvg(svg, svgs) {
    var problems = [];
    function report(rule, a, b) { problems.push([rule, a, b]); }
    var texts = [].slice.call(svg.querySelectorAll("text")).filter(function (t) { return !t.closest(BADGES) && !t.closest("defs"); })
      .map(function (t) {
        var b = t.getBBox(), bb = mapBox(toSvg(svg, t), box(b.x, b.y, b.width, b.height));
        return { el: t, b: bb, core: shrink(bb, 1) };
      })
      .filter(function (t) { return t.b.w > 0; });
    var icons = [].slice.call(svg.querySelectorAll("use")).filter(function (u) { return !u.closest("defs"); })
      .map(function (u) {
        var b = mapBox(toSvg(svg, u), box(num(u, "x"), num(u, "y"), num(u, "width"), num(u, "height")));
        return { el: u, b: b, core: shrink(b, 1) };
      });
    var badges = [].slice.call(svg.querySelectorAll(".st circle, .step circle")).map(function (c) {
      var r = num(c, "r");
      return { n: badgeNumber(c.closest(BADGES)), b: mapBox(toSvg(svg, c), box(num(c, "cx") - r, num(c, "cy") - r, 2 * r, 2 * r)) };
    });
    var groups = [].slice.call(svg.querySelectorAll(GROUPS)).map(function (g) {
      return { el: g, b: mapBox(toSvg(svg, g), box(num(g, "x"), num(g, "y"), num(g, "width"), num(g, "height"))) };
    });
    var joins = [].slice.call(svg.querySelectorAll(JUNCTIONS)).map(function (c) { return point(toSvg(svg, c), num(c, "cx"), num(c, "cy")); });
    var edges = [].slice.call(svg.querySelectorAll(EDGES)).filter(function (el) { return !el.closest("defs"); }).map(function (el) {
      var d = pathOf(el);
      if (d === null) { report("edge-not-orthogonal", el.tagName.toLowerCase() + ".edge", "the edge class goes on the path, line or polyline itself"); return { d: "", segs: [] }; }
      var parsed = segments(d), m = toSvg(svg, el);
      var segs = parsed.segs.map(function (s) { var a = point(m, s.x1, s.y1), b = point(m, s.x2, s.y2); return { x1: a.x, y1: a.y, x2: b.x, y2: b.y }; });
      if (parsed.curved) report("edge-not-orthogonal", d, "curve command");
      segs.forEach(function (s) { if (!horizontal(s) && !vertical(s)) report("edge-not-orthogonal", d, "diagonal segment"); });
      return { d: d, segs: segs.filter(function (s) { return horizontal(s) || vertical(s); }) };
    });

    var i, j;
    for (i = 0; i < texts.length; i++) for (j = i + 1; j < texts.length; j++)
      if (hit(texts[i].core, texts[j].core)) report("label-overlap", name(texts[i].el), name(texts[j].el));
    texts.forEach(function (t) {
      icons.forEach(function (u) { if (hit(t.core, u.core)) report("label-on-icon", name(t.el), name(u.el)); });
      // A label sits wholly inside a group or wholly outside it, never across its line.
      // A label inside a band that spans zones (g-svc, g-aux) may cross the lines of
      // the groups that band itself crosses, and only those.
      var bands = groups.filter(function (g) { return band(g) && inside(t.core, g.b); });
      groups.forEach(function (g) {
        if (!hit(t.core, g.b) || inside(t.core, g.b)) return;
        if (bands.some(function (b) { return hit(b.b, g.b) && !inside(b.b, g.b) && !inside(g.b, b.b); })) return;
        report("label-on-border", name(t.el), g.el.getAttribute("class"));
      });
    });
    badges.forEach(function (s) {
      texts.forEach(function (t) { if (hit(t.core, s.b)) report("step-on-label", "step " + s.n, name(t.el)); });
      icons.forEach(function (u) { if (hit(u.core, s.b)) report("step-on-icon", "step " + s.n, name(u.el)); });
    });
    edges.forEach(function (e) {
      e.segs.forEach(function (s) {
        var sb = segBox(s);
        texts.forEach(function (t) { if (hit(sb, t.core)) report("edge-through-label", e.d, name(t.el)); });
        // Touching an icon's edge is arriving at it; going 3 px past the edge is crossing it.
        icons.forEach(function (u) { if (hit(sb, shrink(u.b, 3))) report("edge-through-icon", e.d, name(u.el)); });
      });
    });
    var all = [];
    edges.forEach(function (e) { e.segs.forEach(function (s) { all.push({ e: e, s: s }); }); });
    function joined(x, y) { return joins.some(function (p) { return Math.abs(p.x - x) < 1 && Math.abs(p.y - y) < 1; }); }
    for (i = 0; i < all.length; i++) for (j = i + 1; j < all.length; j++) {
      if (all[i].e === all[j].e) continue;
      var p = all[i].s, q = all[j].s;
      if (horizontal(p) !== horizontal(q)) {
        var h = horizontal(p) ? p : q, v = horizontal(p) ? q : p, x = v.x1, y = h.y1;
        var inH = x > Math.min(h.x1, h.x2) && x < Math.max(h.x1, h.x2);
        var inV = y > Math.min(v.y1, v.y2) && y < Math.max(v.y1, v.y2);
        if (inH && inV && !joined(x, y)) report("edge-crossing", all[i].e.d, all[j].e.d);
        // One edge ending on the middle of another is a fork, and a fork carries a junction dot.
        var onH = inH && (y === v.y1 || y === v.y2), onV = inV && (x === h.x1 || x === h.x2);
        if ((onH || onV) && !joined(x, y)) report("edge-fork-unmarked", all[i].e.d, all[j].e.d);
      } else {
        // Two edges on (almost) the same line, overlapping by more than 2 px, share a corridor.
        var axis = horizontal(p) ? "x" : "y", line = horizontal(p) ? "y1" : "x1";
        if (Math.abs(p[line] - q[line]) >= CORRIDOR) continue;
        var lo = Math.max(Math.min(p[axis + "1"], p[axis + "2"]), Math.min(q[axis + "1"], q[axis + "2"]));
        var hi = Math.min(Math.max(p[axis + "1"], p[axis + "2"]), Math.max(q[axis + "1"], q[axis + "2"]));
        if (hi - lo > 2) report("edge-corridor", all[i].e.d, all[j].e.d);
      }
    }
    if (badges.length) {
      var list = stepsList(svg, svgs), count = list ? list.querySelectorAll(":scope > li").length : 0;
      var nums = badges.map(function (s) { return +s.n; }).sort(function (a, b) { return a - b; });
      var want = []; for (i = 1; i <= count; i++) want.push(i);
      if (nums.join(",") !== want.join(","))
        report("steps-mismatch", "drawing has " + nums.join(","), list ? "list has " + count + " items" : "no ol.steps for this drawing");
    }
    return { id: svg.id || "(no id)", texts: texts.length, icons: icons.length, edges: edges.length, problems: problems };
  }

  // The drawing's status in the stamp: CURRENT with a date (YYYY-MM-DD) or PROPOSED. Without it the
  // reader takes the proposal for the system that is running. The words are matched in English and
  // Portuguese, the two languages the stamp has been written in.
  function status() {
    var flat = function (t) { return t.normalize("NFD").replace(/[\u0300-\u036f]/g, "").trim().toLowerCase(); };
    var dts = [].slice.call(document.querySelectorAll(".stamp dt")).filter(function (dt) { return /^(status|situacao)$/.test(flat(dt.textContent)); });
    if (!dts.length) return { ok: false, text: "", reason: "stamp has no Status cell" };
    var dd = dts[0].nextElementSibling, text = dd ? dd.textContent.trim() : "", t = flat(text);
    if (/\bproposed\b|\bpropost[oa]\b/.test(t)) return { ok: true, text: text };
    if (/\b(current|atual)\b/.test(t)) {
      return /\b\d{4}-\d{2}-\d{2}\b/.test(t) ? { ok: true, text: text }
        : { ok: false, text: text, reason: "Status says CURRENT with no YYYY-MM-DD date" };
    }
    return { ok: false, text: text, reason: "Status says neither CURRENT as of YYYY-MM-DD nor PROPOSED" };
  }

  function run() {
    var vw = document.documentElement.clientWidth;
    var past = [].slice.call(document.body.querySelectorAll("*")).filter(function (e) {
      if (e.closest("svg") && e.tagName.toLowerCase() !== "svg") return false;
      for (var p = e.parentElement; p && p !== document.body; p = p.parentElement) {
        var o = getComputedStyle(p).overflowX;
        if (o === "auto" || o === "scroll" || o === "hidden") return false;
      }
      var r = e.getBoundingClientRect();
      return r.width > 0 && r.right > vw + 1;
    }).slice(0, 8).map(function (e) {
      var c = typeof e.className === "string" && e.className ? "." + e.className.split(" ")[0] : "";
      return e.tagName.toLowerCase() + c;
    });
    var svgs = [].slice.call(document.querySelectorAll("svg[data-diagram], .dia svg, svg#dia"))
      .filter(function (s, k, a) { return a.indexOf(s) === k; });
    var fonts = [];
    if (document.fonts) document.fonts.forEach(function (f) { if (f.status === "loaded" && fonts.indexOf(f.family) < 0) fonts.push(f.family.replace(/"/g, "")); });
    var out = {
      viewport: vw,
      lang: document.documentElement.lang,
      page_scrolls_sideways: document.documentElement.scrollWidth > vw + 1,
      past_viewport: past,
      fonts: fonts,
      diagrams: svgs.map(function (svg) { return probeSvg(svg, svgs); })
    };
    out.status = out.diagrams.length ? status() : { ok: true, text: "", reason: "no diagram on the page" };
    var pre = document.getElementById("probe-out");
    if (!pre) { pre = document.createElement("pre"); pre.id = "probe-out"; pre.hidden = true; document.body.appendChild(pre); }
    pre.textContent = JSON.stringify(out);
    if (window.parent !== window) window.parent.postMessage({ probe: pre.textContent }, "*");
  }
  function safe() {
    try { run(); } catch (err) {
      var pre = document.createElement("pre"); pre.id = "probe-out"; pre.hidden = true;
      pre.textContent = JSON.stringify({ error: String(err && err.stack || err) });
      document.body.appendChild(pre);
      if (window.parent !== window) window.parent.postMessage({ probe: pre.textContent }, "*");
    }
  }
  // fonts.ready settles at once when the stylesheet that declares the face has not
  // arrived yet, so wait for the load event first; labels measured in a fallback
  // face are measured wrong.
  function measure() {
    (document.fonts && document.fonts.ready ? document.fonts.ready : Promise.resolve()).then(function () { setTimeout(safe, 300); });
  }
  if (document.readyState === "complete") measure(); else window.addEventListener("load", measure);
})();
