#!/usr/bin/env python3
"""UserPromptSubmit: points to the squad agent when the request matches a role.

Meant to sit next to a design skill router, if you run one. Prints at most 2
route hints and stays silent when nothing matches. When the owner states a
standing rule ("always...", "from now on...", "nunca..."), it adds one
`[squad decision]` line asking to record it with `squad-decisions add`; it
never blocks the prompt and exits 0 on any input. `developer` has no trigger on
purpose: it would match almost every request; the table in the global
CLAUDE.md covers that case.

The patterns are bilingual on purpose (Portuguese and English): owners type in
any language. The prompt is lowercased and stripped of accents before matching.
"""
import json
import re
import sys
import unicodedata

def norm(s: str) -> str:
    s = unicodedata.normalize("NFD", s.lower())
    return "".join(c for c in s if unicodedata.category(c) != "Mn")

# Order matters: the specific comes before the generic.
RULES = [
    (r"\b(terraform|tfstate|iam\b|vpc\b|pipeline|ci/cd|github actions|workflow do ci|ci workflow|dockerfile|docker-compose|deploy|provisionar|provision|infraestrutura|infrastructure)",
     "infra -> `infra` agent (writes and validates, stops at plan; never apply/deploy)."),

    (r"\b(quanto custa|custo mensal|custo de|cotar|cotacao|fatura|billing|finops|gasto de tokens|custo de tokens|quanto gastamos|onde cortar"
     r"|how much does|monthly cost|cost of|cost estimate|price quote|invoice|token spend|token cost|how much (did|have) we spen|where to cut)",
     "cost -> `finops` agent (cloud with source and date; tokens through `agenfk tokens`)."),

    (r"\b(precific|preco do plano|pagina de preco|margem|economia unitaria|unit economics|ponto de equilibrio|break-?even|runway|receita|investidor"
     r"|pricing|plan price|profit margin|gross margin|margin per|revenue|investor)",
     "finance -> `finance` agent (numbers with a source; without data, returns what is missing)."),

    (r"\b(concorrente|posicionamento|go-to-market|gtm\b|pitch|primeiros clientes|cliente ideal|icp\b|como vender|abordagem comercial|prospec"
     r"|competitor|positioning|first customers|ideal customer|how to sell|sales approach|outreach|prospect)",
     "go-to-market -> `gtm` agent (drafts only; never sends or publishes)."),

    (r"\b(quanto falta|proximo bloco|proximo passo do projeto|priorizar|prioridade|replanejar|roadmap|decompor|epico|epic\b|planejar a semana|em qual projeto"
     r"|how much is left|next block|next step (of|for) the project|prioriti[sz]e|priority|replan|break down into cards|decompose|plan the week|which project)",
     "product -> `product` agent (real state of the repo, smallest increment, cards in AgenFK)."),

    (r"\b(revisao independente|verificar a branch|verificador|mutacao|mutation|antes do pr|antes de abrir o pr|pode mergear|esta pronto para merge"
     r"|independent review|verify the branch|verifier|before the pr|before opening the pr|can i merge|ready to merge)",
     "verification -> `verifier` agent (clean context, mutation, SHIP/FIX_FIRST/REJECT)."),

    (r"\b(escrever testes|escreva testes|cobertura|teste de regressao|testes do card|falta teste"
     r"|write tests|coverage|regression test|tests for the card|card'?s tests|missing tests?)",
     "tests -> `tester` agent (Sonnet; proof by mutation; records with `agenfk log-test`)."),

    (r"\b(worktree|pilha de pr|stack de pr|mergear|merge\b|rebase|branch(es)? (aberta|morta|orfa)|paraleliz|em paralelo|varias sessoes|quem esta onde|o que esta aberto"
     r"|pr stack|stacked prs?|(open|dead|stale|orphan(ed)?) branch(es)?|paralleli[sz]|in parallel|several sessions|multiple sessions|who is where|what is open|what's open)",
     "git/parallelism -> `github` agent (map session->worktree->branch->card->PR; disjoint fronts; merge train as a proposal; never merges)."),

    (r"\b(handoff|hand-off|contexto cheio|compact|/clear|de onde parei|retomar|em loop|loop\b|gastando muito|burn|releitura|trocar de card|fechar o dia|encerrar por hoje"
     r"|full context|context is full|where did i stop|where was i|resume|pick up where|in a loop|spending too much|reread|switch(ing)? cards?|close the day|done for today|call it a day)",
     "context -> `context` agent (Sonnet; measures ctx/burn/reread/loop with session_context.py; decides compact|handoff|cut the loop; switches between cards)."),

    (r"\b(tela|pagina|landing|dashboard|componente|interface|ui\b|ux\b|design|figma|design tokens?|tokens? de design|dark mode|animacao|motion|poster|banner|logo|icone|arte|canvas|visual"
     r"|screen|page\b|component|animation|icons?\b|art\b|artwork)",
     "interface/visual -> `frontend` agent (loads `design-reference` + at most 1 design skill; direction before code; measured quality floor)."),
]

MAX_HINTS = 2

# Owner directives: a standing rule ("always...", "from now on...") becomes a
# recorded decision, so the next session does not ask again. Precision over
# recall: a missed rule costs one line the owner can still ask for, a false one
# nags on every ordinary request. So the marker has to open a sentence that is
# not a question; only the unmistakable phrases count in mid-sentence; and a
# marker followed by a report of what happened ("never worked", "sempre que
# rodo") is a symptom, not a rule. Runs on the normalised prompt.
DECISION = ("[squad decision] The owner stated a standing rule: record it with "
            "`squad-decisions add --title \"<the rule>\" --source owner` (What / Why on stdin), "
            "acknowledge it in one line, and if it changes how the repository behaves open a card "
            "for the change (a decision records context; it is not the implementation).")

FILLER = (r"^(?:(?:please|pls|also|and|so|ok|okay|now|note|rule|reminder|important|remember to"
          r"|por favor|pf|tambem|e|entao|ok|agora|nota|regra|importante|lembre(?:-se)? de|lembrando)[\s,:-]+)*")

QUESTION = re.compile(
    r"(?:should|shall|can|could|would|will|is|are|was|were|why|how|what|when|where|which|who|whose|any idea"
    r"|(?:do|does|did|have|has) (?:we|you|i|they|it|the|this|that)"
    r"|por que|porque|pq|como(?! regra)|o que|qual|quais|quando|onde|quem|sera que|e se|devemos|deveriamos"
    r"|podemos|da pra|e possivel)\b")

# A marker that needs the word after it checked: "never <verb>" is a rule,
# "never worked" is a report.
OPENERS = re.compile(
    r"(?:always|never(?: ever)?|we (?:always|never|don't|do not|no longer)|(?:we|you) (?:must|should) (?:always|never)"
    r"|i want (?:you|us|everyone) to (?:always|never)"
    r"|sempre|nunca(?: mais)?|jamais|(?:a gente|nos) (?:sempre|nunca|nao|jamais)"
    r"|quero que (?:voce|voces|vc|vcs|a gente|todos) (?:sempre|nunca|jamais))\b ?")
REPORT = re.compile(
    r"(?:mind|seen|been|had|got|gotten|did|was|were|have|has|know|knew|understand|see|saw|get|seem"
    r"|(?!(?:need|embed|feed|proceed|seed|succeed|exceed)\b)\w+ed|\w*[^s\W]s"
    r"|que (?:eu )?\w+o|\w+(?:ou|eu|iu)|vi|vimos|tive|tivemos|fiz|fizemos|consegui|conseguimos|foi|era|tem(?! que)|temos"
    r"|sabe|sabemos|da|funciona|falha|quebra|trava|passa|roda|retorna|aparece|acontece|termina|carrega|responde"
    r"|sobe|cai|mostra"
    r"|the|a|an|this|that|it|my|our|o|os|as|um|uma|esse|essa|este|esta|isso|isto|ele|ela|meu|minha|nosso|nossa)\b")

# A limit to one run makes it an instruction for now, not a standing rule.
ONE_OFF = re.compile(
    r"\b(?:this time|for now|for this (?:run|test|one|card|pr)|just this once"
    r"|desta vez|dessa vez|so desta vez|por enquanto|por ora|nesse teste|neste teste|nessa rodada|nesta rodada)\b")

# A marker that is a rule by itself, when it opens the sentence.
STANDALONE = re.compile(
    r"(?:don't ever|dont ever|do not ever|stop (?!(?:every|some|no|any)thing\b)\w+ing\b"
    r"|prefer \S.{0,60}? (?:over|to|instead of|rather than) \S|moving forward\b"
    r"|nao (?:use|usem)\b|parem? de\b|prefiram?\b|de hoje em diante|a partir de hoje)")

# Unmistakable anywhere in a sentence: "PRs target beta from now on".
ANYWHERE = re.compile(
    r"\b(?:from now on|going forward|from here on(?: out)?|henceforth|as a (?:standing )?rule"
    r"|daqui (?:em|pra|para) (?:diante|frente)|a partir de agora|de agora em diante|como regra)\b")

SENTENCE = re.compile(r"(?<=[.!?;:])\s+|\n+")
FENCE = re.compile(r"```.*?(?:```|\Z)", re.S)


def directive(prompt: str) -> bool:
    """True when the (normalised) prompt states a standing rule, not a question or a task."""
    if prompt.lstrip().startswith(("[squad", "<")):     # the squad's own injected prompts, tool notifications
        return False
    text = FENCE.sub("\n", prompt.replace("\u2019", "'"))
    for line in text.split("\n"):
        if line.lstrip().startswith(">"):               # quoted text is someone else's
            continue
        for raw in SENTENCE.split(line):
            s = re.sub(FILLER, "", raw.strip())
            if not s or s.endswith("?") or QUESTION.match(s) or ONE_OFF.search(s):
                continue
            if ANYWHERE.search(s) or STANDALONE.match(s):
                return True
            m = OPENERS.match(s)
            if m and not REPORT.match(s[m.end():]):
                return True
    return False


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if not isinstance(payload, dict):
        return 0
    prompt = norm(str(payload.get("prompt") or ""))
    if not prompt:
        return 0
    hints = [hint for pattern, hint in RULES if re.search(pattern, prompt)]
    for hint in hints[:MAX_HINTS]:
        print("[squad route] " + hint)
    if hints:
        print("A project-specific agent for the same role takes priority; pass the AgenFK card id.")
    if directive(prompt):
        print(DECISION)
    return 0

if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)
