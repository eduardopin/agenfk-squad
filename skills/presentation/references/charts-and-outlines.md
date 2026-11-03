# Charts, outlines and copy formulas

A companion to `deck-language.md`. Its sentence rules win over everything
here: a copy formula never justifies a label title, a number without a source
or a sentence that needs a second reading.

## Outline by kind of deck

Choose the structure before the first slide and say which one you used.

| Deck | Slides | Sequence |
|---|---|---|
| Seed pitch | 10–12 | hook, problem, solution, traction, market, product, business model, team, numbers, the ask |
| Product demo | 5–8 | problem, solution overview, screen or demo, capabilities, benefit and price, next step |
| Sales | 7–10 | the customer's hook, their problem, cost of doing nothing, solution, proof, difference, price or return, objections, next step |
| Problem, solution, benefit | 3–5 | for an audience short on time |
| Epic or quarterly review | 10–15 | what we promised, what shipped (with chips), numbers with sources, what got stuck and on whom, next cycle, the ask |
| Board | 15–20 | a one-page summary first; the rest backs up the questions |
| Case study | 8–12 | context, before, what was done, after (measured), what applies to the audience |

**Alternation (Duarte):** in a persuasion deck, alternate "what is" (the pain,
with a number) and "what could be" (the change), and close on the new state.
A change of rhythm near 1/3 and 2/3 of the deck (a number on its own, a
screen, a quote) holds attention.

## Layout by kind of slide

| Slide | Template layout | Note |
|---|---|---|
| Cover | `.cover` | a claim title, not the project's name |
| Problem | `.split` with `.ledger` | the pain as a number, with a source |
| Solution, capability | `.split` with `.phone` | the product's real screen; preview labelled |
| Metrics | `.cols.c3` with a large number | 3–4 numbers at most, one concept each |
| Comparison | `table` | aligned columns, `td.n` for numbers |
| Process, journey | `.steps` | up to 5 steps; the new one marked |
| Hero number | `h2` + one large `.v` + `.sources` | one number, alone on the slide |
| Quote | a large `.callout` | who said it, with role and permission; never invented |
| Closing | `.status` with chips | the ask, from whom, by when |

Motion: the deck already slides sideways between slides. Do not add per-element
entrance animation; the only extra motion is the `.phone`'s.

## Copy formulas (for the draft, before `writing`)

| Slide | Formula | Mould |
|---|---|---|
| Problem | Problem, agitate, solve | "[Pain]. Every [period], [measured consequence]. [Solution] fixes that." |
| Cost of doing nothing | Cost of inaction | "Without [solution], [loss] every [period]." |
| Capability | Feature, advantage, benefit | "[Feature] enables [advantage], and so [benefit for the listener]." |
| Before and after | Before, after, bridge | "[How it was]. [How it is now]. [What made the bridge]." |
| Closing | Attention, interest, desire, action | "[Claim]. [Benefit]. [Proof]. [The ask]." |

The moulds produce the first draft. The final version is whole sentences, in
the chosen register, and goes through `writing`.

## Chart with Chart.js

Use a chart when the shape of the series is the point (a trend, a comparison
between groups); a single number goes in the `.ledger` or as a hero number,
never in a chart.

```html
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"
  integrity="sha384-9nhczxUqK87bcKHh20fSQcTGD4qq5GhayNYSYWqwBkINBhOfQLg/P5HG5lF1urn4"
  crossorigin="anonymous"></script>
<div class="chart" style="height:clamp(220px,38vh,340px)"><canvas id="c-revenue"
  role="img" aria-label="Monthly revenue from March to June, from 5 to 45 thousand dollars"></canvas></div>
<script>
(function(){
  var css=getComputedStyle(document.documentElement), v=function(n){return css.getPropertyValue(n).trim();};
  var render=document.documentElement.classList.contains("render");
  new Chart(document.getElementById("c-revenue"),{
    type:"line",
    data:{labels:["Mar","Apr","May","Jun"],
      datasets:[{label:"Revenue ($ thousand)",data:[5,12,28,45],borderColor:v("--color-accent"),
        backgroundColor:v("--color-accent"),borderWidth:3,pointRadius:4,tension:0}]},
    options:{animation:render?false:{duration:400},maintainAspectRatio:false,
      plugins:{legend:{display:false}},
      scales:{x:{grid:{display:false},ticks:{color:v("--color-ink-3"),font:{family:v("--font-data")}}},
              y:{grid:{color:v("--color-line")},ticks:{color:v("--color-ink-3"),font:{family:v("--font-data")}}}}}
  });
})();
</script>
```

- **Colour comes from the tokens**, read with `getComputedStyle`; never hex in
  the chart. A single series: `--color-accent`. Up to 4 series: accent, ink,
  and the chip inks (`--chip-now-ink`, `--chip-partner-ink`), whose contrast
  is already measured in both themes. More than 4 series: group them or split
  the slide.
- **Do not depend on colour alone**: a direct label at the end of each line
  (or a legend in the order of the lines), a different marker per series, a
  dashed stroke for a projection, and the projection labelled with a `label`
  "Projection".
- **`animation:false` in render mode**, or the PDF and the PNG come out
  mid-animation. The dark theme swaps colours on load; whoever switches the
  theme live has to recreate the chart.
- A bar chart's axis starts at zero; the number's source goes in the slide's
  `.sources`, like any other number.
- The `canvas` `aria-label` says what the chart shows in one sentence, with
  the numbers at both ends.
- The CDN pins the exact version (`chart.js@4.4.1`) with the file's
  `integrity` (SRI) hash; if you change the version, recompute the hash
  (`curl -sL <url> | openssl dgst -sha384 -binary | base64`). A deck that has
  to open offline embeds the file.

## Origin

Outlines, layouts by kind of slide, copy formulas and the Chart.js pattern
summarised from the claudekit `slides` skill (MIT), rewritten and bound to
this skill's sourcing and register rules.
