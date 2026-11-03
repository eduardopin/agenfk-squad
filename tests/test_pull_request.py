#!/usr/bin/env python3
"""O gate de PR pega o titulo e o corpo ruins, em pt e en, e deixa passar os bons.

Auto-verificavel: o codigo de saida e o veredito. Repositorios git
descartaveis, HOME descartavel e um `gh` de mentira na frente do PATH: nada sai
para a rede e nada toca em ~/.claude. Nao depende da skill `escrita`.

    python3 tests/test_pull_request.py
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True  # a skill mora num repo versionado: nada de __pycache__
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "skills", "pull-request")
HOOK = os.path.join(SKILL, "hooks", "pr_gate.py")
INVENTORY = os.path.join(SKILL, "scripts", "inventory.py")
CHECK = os.path.join(SKILL, "scripts", "pr_check.py")
sys.path.insert(0, os.path.join(SKILL, "scripts"))
import pr_check  # noqa: E402
import review_comments  # noqa: E402

# Um `gh` de mentira: imprime o arquivo de FAKE_GH_OUT, ou falha como sem login.
FAKE_BIN = tempfile.mkdtemp(prefix="pr-fakebin-")
with open(os.path.join(FAKE_BIN, "gh"), "w") as fh:
    fh.write('#!/bin/sh\n[ -n "$FAKE_GH_OUT" ] && exec cat "$FAKE_GH_OUT"\necho "gh: sem login" >&2\nexit 1\n')
os.chmod(os.path.join(FAKE_BIN, "gh"), 0o755)
os.environ["PATH"] = FAKE_BIN + os.pathsep + os.environ.get("PATH", "")
os.environ.pop("FAKE_GH_OUT", None)
GIT_ENV = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.invalid",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.invalid"}

BOM_PT = """O aviso do tour guiado aparecia a cada visita, e quem ja tinha visto o tour precisava fecha-lo de novo. A mudanca grava que o aviso foi visto e nao mexe no conteudo do tour.

## Antes e depois

| Comportamento | Antes | Depois |
|---|---|---|
| Aviso do tour | Aparece em toda visita | Aparece uma vez por navegador |

## Recursos criados

Nenhum. A mudanca usa o armazenamento local que ja existe.

## Recursos removidos

Nenhum. Nada sai.

## Estado e fluxo

### Estado

```mermaid
stateDiagram-v2
    [*] --> NaoVisto
    NaoVisto --> Visto
```

### Fluxo

Nenhum. Nao ha fluxo novo entre componentes.

## Funções criadas e removidas

| Função | Arquivo | Mudança | Para quê |
|---|---|---|---|
| `marcarVisto` | `src/tour.ts` | Criada | Grava que o aviso foi visto |

## Custo

Nenhum. Nao ha recurso cobrado.

## Análise de risco

| | |
|---|---|
| Risco de aplicar | Baixo: quem limpa o navegador ve o aviso de novo |
| Risco de não aplicar | O aviso continua atrapalhando quem volta |
| Raio de impacto | So a pagina do tour |
| Reversão | Reverter o commit; nenhum dado se perde |

## Práticas aplicadas

| Prática | Fonte | Onde |
|---|---|---|
| Teste do comportamento novo | `CLAUDE.md` do projeto | `src/tour.test.ts` |

## Varreduras

| Ferramenta | Comando | Resultado |
|---|---|---|
| Lint | `npm run lint` | Passou, 0 avisos |
| Semgrep | Não rodou: nao instalado nesta maquina | - |
| Trivy | `trivy fs .` | Passou |
| Segredos (gitleaks) | `gitleaks detect` | Passou |

## Testes

| Suíte | Comando | Resultado |
|---|---|---|
| unidade | `npm test` | 42 passaram, 0 falharam |
"""

BOM_EN = """The guided tour notice showed on every visit, so people who had already seen the tour had to close it again. The change records that the notice was seen and leaves the tour content alone.

## Before and after

| Behaviour | Before | After |
|---|---|---|
| Tour notice | Shows on every visit | Shows once per browser |

## Resources created

None. The change uses the existing local storage.

## Resources deleted

None. Nothing goes away.

## State and flow

### State

```mermaid
stateDiagram-v2
    [*] --> Unseen
    Unseen --> Seen
```

### Flow

None. No new flow between components.

## Functions created and deleted

| Function | File | Change | Purpose |
|---|---|---|---|
| `markSeen` | `src/tour.ts` | Created | Records that the notice was seen |

## Cost

None. Nothing billable.

## Risk analysis

| | |
|---|---|
| Risk of applying | Low: clearing the browser shows the notice again |
| Risk of not applying | The notice keeps getting in the way |
| Blast radius | The tour page only |
| Rollback | Revert the commit; no data is lost |

## Practices applied

| Practice | Source | Where |
|---|---|---|
| Test the new behaviour | Project `CLAUDE.md` | `src/tour.test.ts` |

## Scans

| Tool | Command | Result |
|---|---|---|
| Lint | `npm run lint` | Pass, 0 warnings |
| Semgrep | Not run: not installed here | - |
| Trivy | `trivy fs .` | Pass |
| Secrets (gitleaks) | `gitleaks detect` | Pass |

## Tests

| Suite | Command | Result |
|---|---|---|
| unit | `npm test` | 42 passed, 0 failed |
"""


def codes(problems):
    return {p.code for p in problems}


def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True,
                   env={**os.environ, **GIT_ENV})


def repo(files=None, commits=()):
    """Repositorio git descartavel com estes arquivos e, depois, estes commits vazios."""
    path = tempfile.mkdtemp(prefix="pr-repo-")
    git(path, "init", "-q", "-b", "main")
    for name, text in (files or {}).items():
        full = os.path.join(path, name)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as fh:
            fh.write(text)
    git(path, "add", "-A")
    git(path, "commit", "-q", "--allow-empty", "-m", "Cria o repositorio de teste")
    for msg in commits:
        git(path, "commit", "-q", "--allow-empty", "-m", msg)
    return path


def without_section(body, heading):
    head, _, rest = body.partition(f"## {heading}\n")
    return head + (rest[rest.index("\n## "):].lstrip("\n") if "\n## " in rest else "")


def hook(payload, env=None):
    e = {k: v for k, v in os.environ.items() if not k.startswith(("SQUAD_", "FAKE_GH"))}
    e["HOME"] = tempfile.mkdtemp(prefix="pr-home-")
    e.update(env or {})
    p = subprocess.run([sys.executable, "-I", HOOK], input=json.dumps(payload),
                       capture_output=True, text=True, env=e, timeout=60)
    assert p.returncode == 0, p.stderr
    return p


def decision(p):
    if not p.stdout.strip():
        return "passagem", ""
    out = json.loads(p.stdout)["hookSpecificOutput"]
    if out.get("permissionDecision") == "deny":
        return "nega", out["permissionDecisionReason"]
    return "passa", out.get("additionalContext", "")


def bash(cwd, command):
    return {"tool_name": "Bash", "tool_input": {"command": command}, "cwd": cwd}


# --- titulo -------------------------------------------------------------------

def test_titulo_pt_ruim_e_nega_com_o_codigo_certo():
    ruins = {
        "Permissões dos seis papéis": "TITLE_NO_VERB",
        "Skill pull-request adaptada ao squad": "TITLE_NO_VERB",
        "Fase 6: cria o bucket de exportação": "TITLE_STEP_LABEL",
        "F6-B4: Move a exportação para um job": "TITLE_STEP_LABEL",
        "ABC-12: Move a exportação para um job": "TITLE_ID_PREFIX",
        "Adicionando cache ao relatório": "TITLE_ACTIVITY",
        "Move dealer exports to a background job": "TITLE_NOT_PORTUGUESE",
        "Ajustes": "TITLE_GENERIC",
        "feature/skill-pull-request": "TITLE_BRANCH_NAME",
        "Move o bucket aws_s3_bucket.exports para outra conta": "TITLE_CODE_IDENTIFIER",
    }
    for title, code in ruins.items():
        got = codes(pr_check.check_title(title, "pt"))
        assert code in got, (title, got)


def test_titulo_pt_bom_passa_com_id_so_como_sufixo():
    for title in ("O aviso do tour guiado passou a aparecer uma vez só",
                  "Move a exportação para um job em segundo plano",
                  "Move a exportação para um job em segundo plano (F6-B4)",
                  "Vigia: limiar invalido no ambiente nao desliga o freio",
                  "Corrige o login quando a senha tem acento"):
        assert pr_check.check_title(title, "pt") == [], (title, pr_check.check_title(title, "pt"))


def test_titulo_en_ruim_e_bom():
    ruins = {
        "permissions boundaries for the six roles": "TITLE_NO_VERB",
        "Adding cache to reports": "TITLE_ACTIVITY",
        "Move a exportação para um job em segundo plano": "TITLE_NOT_ENGLISH",
        "Add the export bucket for phase 6": "TITLE_STEP_LABEL",
        "Updates": "TITLE_GENERIC",
    }
    for title, code in ruins.items():
        got = codes(pr_check.check_title(title, "en"))
        assert code in got, (title, got)
    for title in ("Move dealer exports to a background job",
                  "Deny Terraform apply in the agent (ABC-12)",
                  "feat(export): Move dealer exports to a background job"):
        assert pr_check.check_title(title, "en") == [], (title, pr_check.check_title(title, "en"))


# --- corpo --------------------------------------------------------------------

def test_corpo_bom_passa_e_secoes_valem_nos_dois_idiomas():
    assert pr_check.check_body(BOM_PT, "app", "pt") == []
    assert pr_check.check_body(BOM_EN, "app", "en") == []
    assert pr_check.check_body(BOM_EN, "app", "pt") == []


def test_corpo_sem_secao_e_sem_linha_de_segredos_e_negado():
    got = codes(pr_check.check_body(without_section(BOM_PT, "Varreduras"), "app", "pt"))
    assert "SECTION_MISSING:Scans" in got, got
    got = codes(pr_check.check_body(without_section(BOM_EN, "Risk analysis"), "app", "en"))
    assert "SECTION_MISSING:Risk analysis" in got, got
    sem_segredos = BOM_PT.replace("| Segredos (gitleaks) | `gitleaks detect` | Passou |\n", "")
    assert codes(pr_check.check_body(sem_segredos, "app", "pt")) == {"SCAN_MISSING:secrets"}


def test_template_cru_e_negado_por_placeholder():
    for name, lang in (("body.md", "en"),):
        with open(os.path.join(SKILL, "templates", name), encoding="utf-8") as fh:
            got = codes(pr_check.check_body(fh.read(), "app", lang))
        assert "PLACEHOLDER" in got, (name, got)


def test_pr_de_infra_com_mutacao_em_testes_e_negado():
    linha = "| unidade | `npm test` | 42 passaram, 0 falharam |"
    com_mutacao = BOM_PT.replace(linha, linha + "\n| mutação | `mutmut run` | 12 mutantes mortos |")
    assert "TESTS_FORBIDDEN" in codes(pr_check.check_body(com_mutacao, "infra", "pt"))
    assert "TESTS_FORBIDDEN" not in codes(pr_check.check_body(com_mutacao, "app", "pt"))
    en = BOM_EN.replace("| unit | `npm test` | 42 passed, 0 failed |",
                        "| mutation | `stryker run` | 90% killed |")
    assert "TESTS_FORBIDDEN" in codes(pr_check.check_body(en, "infra", "en"))
    # pela CLI: o tipo sai da lista de arquivos do PR
    d = tempfile.mkdtemp(prefix="pr-cli-")
    corpo, arquivos = os.path.join(d, "corpo.md"), os.path.join(d, "arquivos")
    with open(corpo, "w", encoding="utf-8") as fh:
        fh.write(com_mutacao)
    with open(arquivos, "w") as fh:
        fh.write("stacks/export/main.tf\n")
    p = subprocess.run([sys.executable, "-I", CHECK, corpo, "--lang", "pt", "--files", arquivos,
                        "--title", "Move a exportação para um job em segundo plano"],
                       capture_output=True, text=True)
    assert p.returncode == 1 and "TESTS_FORBIDDEN" in p.stdout and "infra PR" in p.stdout, p.stdout


# --- idioma -------------------------------------------------------------------

def test_idioma_auto_le_claude_md_depois_prs_depois_commits_senao_pt():
    r = repo({"CLAUDE.md": "# Regras\n\nConversa em português; títulos de PR e commits em inglês.\n"},
             commits=["Corrige o login quando a senha tem acento"])
    assert pr_check.detect_lang(r) == ("en", "CLAUDE.md")
    r = repo({"AGENTS.md": "Commits e PRs em português do Brasil.\n"})
    assert pr_check.detect_lang(r) == ("pt", "AGENTS.md")

    r = repo(commits=["Corrige o login quando a senha tem acento", "Move a exportação para um job"])
    out = os.path.join(tempfile.mkdtemp(), "titulos")
    with open(out, "w") as fh:
        fh.write("Move dealer exports to a background job\nAdd a retry to the export\nFix the login for the admin\n")
    os.environ["FAKE_GH_OUT"] = out
    try:
        assert pr_check.detect_lang(r) == ("en", "PR titles")
    finally:
        del os.environ["FAKE_GH_OUT"]
    assert pr_check.detect_lang(r) == ("pt", "commits")  # gh sem login: cai para os commits

    r = repo(commits=["Add the export job", "Move the retry to the worker for the admin"])
    assert pr_check.detect_lang(r) == ("en", "commits")
    assert pr_check.detect_lang(tempfile.mkdtemp()) == ("en", "default")


# --- hook ---------------------------------------------------------------------

def _repo_com_corpos(extra=None):
    files = {"CLAUDE.md": "Títulos de PR em português.\n", "corpo-bom.md": BOM_PT,
             "corpo-ruim.md": without_section(BOM_PT, "Varreduras")}
    files.update(extra or {})
    return repo(files)


def test_hook_nega_titulo_e_corpo_ruins():
    r = _repo_com_corpos()
    d, msg = decision(hook(bash(r, 'gh pr create --title "Ajustes" --body-file corpo-ruim.md')))
    assert d == "nega" and "TITLE_GENERIC" in msg and "SECTION_MISSING:Scans" in msg, (d, msg)
    d, msg = decision(hook(bash(r, 'agenfk pr create abc --title "Ajustes" --body "$(cat corpo-bom.md)"')))
    assert d == "nega" and "TITLE_GENERIC" in msg, (d, msg)
    d, msg = decision(hook(bash(r, 'gh pr create --body-file corpo-bom.md')))
    assert d == "nega" and "no --title" in msg, (d, msg)


def test_hook_deixa_passar_o_pr_bom_e_ignora_o_que_nao_e_pr():
    r = _repo_com_corpos()
    d, msg = decision(hook(bash(r, 'gh pr create --title "O aviso do tour passou a aparecer uma vez só" '
                                   '--body-file corpo-bom.md')))
    assert d == "passa" and "passed (language pt)" in msg, (d, msg)
    assert decision(hook(bash(r, 'git log --oneline | grep "gh pr create"')))[0] == "passagem"
    assert decision(hook(bash(r, 'gh pr edit 12 --add-label infra')))[0] == "passagem"


def test_hook_ferramenta_mcp_nega_e_passa():
    r = _repo_com_corpos()
    tool = "mcp__plugin_github_github__create_pull_request"
    ruim = {"tool_name": tool, "cwd": r, "tool_input": {"owner": "o", "repo": "r", "title": "Ajustes",
                                                         "body": BOM_PT, "head": "x", "base": "main"}}
    d, msg = decision(hook(ruim))
    assert d == "nega" and "TITLE_GENERIC" in msg, (d, msg)
    bom = json.loads(json.dumps(ruim))
    bom["tool_input"]["title"] = "Move a exportação para um job em segundo plano"
    assert decision(hook(bom))[0] == "passa"


def test_hook_da_passagem_quando_o_projeto_tem_gate_proprio():
    ruim = 'gh pr create --title "Ajustes" --body-file corpo-ruim.md'
    com_skill = _repo_com_corpos({".claude/skills/acme-pr-delivery/SKILL.md": "---\nname: acme-pr-delivery\n---\n"})
    assert decision(hook(bash(com_skill, ruim))) == ("passagem", "")
    com_hook = _repo_com_corpos({".claude/hooks/guarda.sh": '#!/bin/sh\ncase "$1" in *"gh pr create"*) exit 2;; esac\n'})
    assert decision(hook(bash(com_hook, ruim))) == ("passagem", "")
    # skill de pilha de PRs nao e gate de formato: o gate do squad continua valendo
    so_pilha = _repo_com_corpos({".claude/skills/prs-stack/SKILL.md": "---\nname: prs-stack\n---\n"})
    assert decision(hook(bash(so_pilha, ruim)))[0] == "nega"
    assert decision(hook(bash(_repo_com_corpos(), ruim), {"SQUAD_PR_GATE": "0"})) == ("passagem", "")


def test_hook_com_erro_interno_avisa_e_nao_quebra():
    r = _repo_com_corpos()
    p = hook({"tool_name": "Bash", "tool_input": {"command": ["gh", "pr", "create"]}, "cwd": r})
    assert p.stdout.strip() == "" and "internal error" in p.stderr, (p.stdout, p.stderr)
    p = subprocess.run([sys.executable, "-I", HOOK], input="isto nao e json", capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout.strip() == ""


# --- inventario e revisao -----------------------------------------------------

def test_inventario_em_repo_git_temporario():
    r = repo({"main.tf": 'resource "aws_s3_bucket" "velho" {\n}\n', "app.py": "def velha():\n    pass\n"})
    git(r, "checkout", "-q", "-b", "feat")
    with open(os.path.join(r, "main.tf"), "w") as fh:
        fh.write('resource "aws_s3_bucket" "novo" {\n}\n')
    with open(os.path.join(r, "app.py"), "w") as fh:
        fh.write("def nova():\n    pass\n")
    git(r, "commit", "-q", "-am", "Troca o bucket e a funcao")
    p = subprocess.run([sys.executable, "-I", INVENTORY, "--repo", r, "--base", "main", "--format", "json"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    inv = json.loads(p.stdout)
    assert [i["name"] for i in inv["resources"]["created"]] == ["aws_s3_bucket.novo"], inv
    assert [i["name"] for i in inv["resources"]["deleted"]] == ["aws_s3_bucket.velho"], inv
    assert [i["name"] for i in inv["functions"]["created"]] == ["nova"], inv
    assert [i["name"] for i in inv["functions"]["deleted"]] == ["velha"], inv
    md = subprocess.run([sys.executable, "-I", INVENTORY, "--repo", r, "--base", "main"],
                        capture_output=True, text=True).stdout
    assert "| `nova` | `app.py` | Created |" in md and "| `velha` | `app.py` | Deleted |" in md, md


def test_revisao_bloqueante_pede_mudanca_e_autorrevisao_so_comenta():
    diff = ("diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1,1 +1,2 @@\n x = 1\n+y = segredo\n")
    achado = {"severity": "Bloqueante", "path": "a.py", "line": 2,
              "problem": "Segredo no codigo.", "fix": "Leia do ambiente."}
    rev = review_comments.build_review(diff, [dict(achado)])
    assert rev["event"] == "REQUEST_CHANGES" and len(rev["comments"]) == 1, rev
    assert "**Bloqueante.**" in rev["comments"][0]["body"]
    menor = dict(achado, severity="Menor", line=40)  # fora do diff: vai para o corpo
    rev = review_comments.build_review(diff, [menor])
    assert rev["event"] == "APPROVE" and rev["comments"] == [] and "Fora do diff" in rev["body"], rev
    assert review_comments.build_review(diff, [dict(achado)], self_review=True)["event"] == "COMMENT"


# --- regras do corpo, uma a uma -----------------------------------------------

BOM = {"pt": BOM_PT, "en": BOM_EN}


def troca(body, antigo, novo):
    assert antigo in body, antigo
    return body.replace(antigo, novo, 1)


def com_abertura(body, texto):
    return texto + "\n\n" + body.split("\n\n", 1)[1]


def titulo_de(n, lang):
    """Titulo bom com exatamente n caracteres."""
    base = "Move a exportação para um job em " if lang == "pt" else "Move dealer exports to a job in "
    return base + "a" * (n - len(base))


def test_titulo_longo_so_acima_de_72_caracteres_nos_dois_idiomas():
    """Trava TITLE_MAX e o `>`: 72 passa, 73 e TITLE_LONG (limite errado ou `>=` mudaria um dos dois)."""
    for lang in ("pt", "en"):
        assert len(titulo_de(72, lang)) == 72
        assert "TITLE_LONG" not in codes(pr_check.check_title(titulo_de(72, lang), lang)), lang
        assert "TITLE_LONG" in codes(pr_check.check_title(titulo_de(73, lang), lang)), lang
        assert "TITLE_LONG" in codes(pr_check.check_title(titulo_de(300, lang), lang)), lang


def test_abertura_curta_longa_e_boilerplate_com_limites_exatos():
    """Trava OPENING_MIN_WORDS (12), OPENING_MAX_WORDS (120) e BOILERPLATE, em pt e en."""
    for lang in ("pt", "en"):
        body = BOM[lang]
        palavras = lambda n: " ".join(["palavra"] * n)  # noqa: E731
        got = codes(pr_check.check_body(com_abertura(body, palavras(11)), "app", lang))
        assert "OPENING_SHORT" in got and "OPENING_LONG" not in got, (lang, got)
        assert codes(pr_check.check_body(com_abertura(body, palavras(12)), "app", lang)) == set(), lang
        assert codes(pr_check.check_body(com_abertura(body, palavras(120)), "app", lang)) == set(), lang
        got = codes(pr_check.check_body(com_abertura(body, palavras(121)), "app", lang))
        assert "OPENING_LONG" in got and "OPENING_SHORT" not in got, (lang, got)
    for lang, abertura in (("pt", "Este PR muda o aviso do tour guiado para aparecer uma vez só por navegador em cada visita."),
                           ("pt", "Neste PR o aviso do tour guiado passa a aparecer uma vez só por navegador em cada visita."),
                           ("en", "This PR makes the guided tour notice show once per browser instead of on every single visit."),
                           ("en", "In this pull request the guided tour notice should show once per browser instead of on every visit.")):
        got = codes(pr_check.check_body(com_abertura(BOM[lang], abertura), "app", lang))
        assert got == {"OPENING_BOILERPLATE"}, (abertura, got)


def test_secoes_fora_de_ordem_sao_negadas_nos_dois_idiomas():
    """Trava SECTION_ORDER: trocar Custo e Analise de risco de lugar nao pode passar."""
    for lang, a, b in (("pt", "Custo", "Análise de risco"), ("en", "Cost", "Risk analysis")):
        body = BOM[lang]
        i, j = body.index(f"## {a}\n"), body.index(f"## {b}\n")
        k = body.index("\n## ", j + 1)
        trocado = body[:i] + body[j:k + 1] + body[i:j] + body[k + 1:]
        assert codes(pr_check.check_body(trocado, "app", lang)) == {"SECTION_ORDER"}, lang
        assert "SECTION_ORDER" not in codes(pr_check.check_body(body, "app", lang))


def test_secao_com_titulo_e_sem_conteudo_e_negada():
    """Trava SECTION_EMPTY: `## Custo` vazio nao vale como `Nenhum.`."""
    for lang, titulo, texto in (("pt", "Custo", "Nenhum. Nao ha recurso cobrado."),
                                ("en", "Cost", "None. Nothing billable.")):
        vazio = troca(BOM[lang], f"## {titulo}\n\n{texto}\n\n", f"## {titulo}\n\n")
        assert codes(pr_check.check_body(vazio, "app", lang)) == {f"SECTION_EMPTY:Cost"}, lang


def test_diagramas_de_estado_e_fluxo_pt_e_en():
    """Trava _check_diagrams: sem subsecao, sem mermaid e tipo errado de diagrama sao negados."""
    for lang in ("pt", "en"):
        body = BOM[lang]
        estado = "stateDiagram-v2"
        errado = troca(body, estado, "flowchart TD")
        assert codes(pr_check.check_body(errado, "app", lang)) == {"DIAGRAM_KIND:State"}, lang
        sem_bloco = troca(body, "```mermaid\nstateDiagram-v2\n    [*] --> " + ("NaoVisto" if lang == "pt" else "Unseen"),
                          "```text\nx\n    [*] --> " + ("NaoVisto" if lang == "pt" else "Unseen"))
        assert codes(pr_check.check_body(sem_bloco, "app", lang)) == {"DIAGRAM_MISSING:State"}, lang
        fluxo = "### Fluxo" if lang == "pt" else "### Flow"
        sem_fluxo = body.replace(fluxo, "### Outra coisa")
        assert codes(pr_check.check_body(sem_fluxo, "app", lang)) == {"DIAGRAM_MISSING:Flow"}, lang
        so_estado = troca(body, "```mermaid\nstateDiagram-v2", "```mermaid\nsequenceDiagram")
        assert codes(pr_check.check_body(so_estado, "app", lang)) == {"DIAGRAM_KIND:State"}, lang
        # fluxo com sequenceDiagram e valido
        fluxo_ok = troca(body, "None. No new flow between components." if lang == "en"
                         else "Nenhum. Nao ha fluxo novo entre componentes.",
                         "```mermaid\nsequenceDiagram\n    A->>B: oi\n```")
        assert codes(pr_check.check_body(fluxo_ok, "app", lang)) == set(), lang


def test_custo_de_infra_exige_valor_em_dolar():
    """Trava COST_AMOUNT_MISSING: so PR de infra, e so sem valor."""
    for lang, nenhum, com_valor in (("pt", "Nenhum. Nao ha recurso cobrado.", "US$ 0.00 por mes."),
                                    ("en", "None. Nothing billable.", "$12.50/month for the new bucket.")):
        assert codes(pr_check.check_body(BOM[lang], "infra", lang)) == {"COST_AMOUNT_MISSING"}, lang
        assert codes(pr_check.check_body(BOM[lang], "app", lang)) == set(), lang
        com = troca(BOM[lang], nenhum, com_valor)
        assert codes(pr_check.check_body(com, "infra", lang)) == set(), lang


def test_secao_sem_tabela_e_negada_e_nenhum_dispensa():
    """Trava TABLE_MISSING: prosa no lugar da tabela e negada; `Nenhum.` dispensa."""
    for lang, titulo, code in (("pt", "Práticas aplicadas", "Practices applied"),
                               ("en", "Practices applied", "Practices applied")):
        body = BOM[lang]
        i = body.index(f"## {titulo}\n")
        k = body.index("\n## ", i + 1)
        prosa = body[:i] + f"## {titulo}\n\nA gente segue as regras do projeto.\n" + body[k:]
        assert codes(pr_check.check_body(prosa, "app", lang)) == {f"TABLE_MISSING:{code}"}, lang
        nenhum = body[:i] + f"## {titulo}\n\nNenhum. None. Nada novo.\n" + body[k:]
        assert codes(pr_check.check_body(nenhum, "app", lang)) == set(), lang


def test_antes_e_depois_exige_colunas_antes_e_depois():
    """Trava BEFORE_AFTER_COLUMNS."""
    ruim = {"pt": ("| Comportamento | Antes | Depois |", "| Comportamento | Valor | Outro |"),
            "en": ("| Behaviour | Before | After |", "| Behaviour | Value | Other |")}
    for lang, (certo, errado) in ruim.items():
        got = codes(pr_check.check_body(troca(BOM[lang], certo, errado), "app", lang))
        assert got == {"BEFORE_AFTER_COLUMNS"}, (lang, got)


def test_analise_de_risco_exige_as_quatro_linhas():
    """Trava RISK_MISSING: tirar cada linha da tabela acusa exatamente aquele termo."""
    linhas = {"pt": (("Risco de aplicar", "risk of applying"), ("Risco de não aplicar", "risk of not applying"),
                     ("Raio de impacto", "blast radius"), ("Reversão", "rollback")),
              "en": (("Risk of applying", "risk of applying"), ("Risk of not applying", "risk of not applying"),
                     ("Blast radius", "blast radius"), ("Rollback", "rollback"))}
    for lang, itens in linhas.items():
        for rotulo, termo in itens:
            sem = "\n".join(l for l in BOM[lang].split("\n") if not l.startswith(f"| {rotulo} |"))
            assert sem != BOM[lang], rotulo
            assert codes(pr_check.check_body(sem, "app", lang)) == {f"RISK_MISSING:{termo}"}, (lang, rotulo)


def test_praticas_exigem_coluna_de_fonte():
    """Trava PRACTICE_SOURCE_MISSING."""
    for lang, certo, errado in (("pt", "| Prática | Fonte | Onde |", "| Prática | Origem | Onde |"),
                                ("en", "| Practice | Source | Where |", "| Practice | Origin | Where |")):
        sem = troca(BOM[lang], certo, errado)
        assert codes(pr_check.check_body(sem, "app", lang)) == {"PRACTICE_SOURCE_MISSING"}, lang


def test_varreduras_exigem_uma_linha_por_ferramenta():
    """Trava SCAN_MISSING por ferramenta (lint, semgrep, trivy, segredos)."""
    for lang in ("pt", "en"):
        for ferramenta, rotulo in (("lint", "| Lint |"), ("semgrep", "| Semgrep |"), ("trivy", "| Trivy |")):
            sem = "\n".join(l for l in BOM[lang].split("\n") if not l.startswith(rotulo))
            assert codes(pr_check.check_body(sem, "app", lang)) == {f"SCAN_MISSING:{ferramenta}"}, (lang, ferramenta)


def test_base_do_pr_so_vira_ref_quando_e_segura():
    """Trava SAFE_REF: uma base que o git leria como opcao nao entra na lista de refs."""
    assert pr_check.base_refs("feat/x")[:2] == ["origin/feat/x", "feat/x"]
    for ruim in ("--output=/tmp/x", "-p", "a b", "x;y", ""):
        refs = pr_check.base_refs(ruim)
        assert refs == list(pr_check.DEFAULT_BASES), (ruim, refs)
    assert pr_check.base_refs(None) == list(pr_check.DEFAULT_BASES)
    # e pelo git de verdade: --output=... nao pode escrever arquivo
    r = repo()
    alvo = os.path.join(tempfile.mkdtemp(), "escrito")
    assert pr_check.changed_files(r, f"--output={alvo}") == []
    assert not os.path.exists(alvo)


# --- parser do hook, comando a comando ----------------------------------------

sys.path.insert(0, os.path.join(SKILL, "hooks"))
import pr_gate  # noqa: E402

TITULO_BOM = "O aviso do tour passou a aparecer uma vez só"
RUIM = 'gh pr create --title "Ajustes" --body-file corpo-bom.md'  # nega por TITLE_GENERIC


def veredito(r, command, env=None):
    return decision(hook(bash(r, command), env))


def test_hook_le_o_corpo_do_heredoc_dentro_de_body_cat():
    """Trava o recorte de heredoc: `--body "$(cat <<'EOF' ... EOF)"` e conferido, nao ignorado."""
    r = _repo_com_corpos()
    cmd = lambda corpo, aspas="'EOF'": (f'gh pr create --title "{TITULO_BOM}" --body "$(cat <<{aspas}\n{corpo}\nEOF\n)"')  # noqa: E731
    d, msg = decision(hook(bash(r, cmd(without_section(BOM_PT, "Varreduras")))))
    assert d == "nega" and "SECTION_MISSING:Scans" in msg, (d, msg)
    d, msg = decision(hook(bash(r, cmd(BOM_PT))))
    assert d == "passa" and "passed" in msg, (d, msg)
    d, msg = decision(hook(bash(r, cmd(without_section(BOM_PT, "Varreduras"), "EOF"))))
    assert d == "nega" and "SECTION_MISSING:Scans" in msg, (d, msg)


def test_hook_nao_le_texto_de_heredoc_como_comando():
    """Trava o heredoc como dado: citar `gh pr create` dentro de um heredoc ou de aspas nao abre PR."""
    r = _repo_com_corpos()
    assert veredito(r, "cat <<EOF\ngh pr create --title Ajustes\nEOF") == ("passagem", "")
    assert veredito(r, "git commit -F - <<'EOF'\nExplica o gh pr create --title Ajustes\nEOF") == ("passagem", "")
    assert veredito(r, 'echo "gh pr create --title Ajustes"') == ("passagem", "")
    # heredoc sem terminador nao esconde o comando que vem antes
    assert veredito(r, RUIM + " <<EOF\nsem fim")[0] == "nega"


def test_hook_segue_eval_e_bash_c_e_bash_com_heredoc():
    """Trava eval, `bash -c` e `bash <<EOF`: o PR dentro deles e conferido."""
    r = _repo_com_corpos()
    for cmd in ("eval 'gh pr create --title \"Ajustes\" --body-file corpo-bom.md'",
                "bash -c 'gh pr create --title \"Ajustes\" --body-file corpo-bom.md'",
                "sh -lc 'gh pr create --title \"Ajustes\" --body-file corpo-bom.md'",
                "bash <<EOF\ngh pr create --title \"Ajustes\" --body-file corpo-bom.md\nEOF"):
        d, msg = veredito(r, cmd)
        assert d == "nega" and "TITLE_GENERIC" in msg, (cmd, d, msg)
    assert veredito(r, "bash -c 'echo gh pr create'") == ("passagem", "")


def test_hook_rastreia_cd_para_achar_o_repo_certo():
    """Trava o rastreio de `cd`/`pushd`: o gate proprio e o idioma sao os do diretorio onde o comando roda."""
    skill = {".claude/skills/acme-pr-delivery/SKILL.md": "x"}
    livre, gate = _repo_com_corpos(), _repo_com_corpos(skill)
    assert veredito(livre, RUIM)[0] == "nega"
    assert veredito(livre, f"cd {gate} && " + RUIM) == ("passagem", "")
    assert veredito(livre, f"cd ../{os.path.basename(gate)} && " + RUIM) == ("passagem", "")
    assert veredito(livre, f"pushd {gate} && " + RUIM) == ("passagem", "")
    assert veredito(gate, RUIM) == ("passagem", "")
    d, msg = veredito(gate, f"cd {livre} && " + RUIM)
    assert d == "nega" and "TITLE_GENERIC" in msg, (d, msg)
    # o cd de um subshell nao sobrevive a ele; o de um grupo `{ }` sim
    assert veredito(livre, f"(cd {gate}; true); " + RUIM)[0] == "nega"
    assert veredito(livre, f"(cd {gate} && {RUIM})") == ("passagem", "")
    assert veredito(livre, f"{{ cd {gate}; }}; " + RUIM) == ("passagem", "")


def test_hook_cd_calculado_nao_adivinha_o_diretorio():
    """Trava o `cd` com `$` ou crase: o diretorio e desconhecido, entao corpo relativo e negado com o motivo certo."""
    r = _repo_com_corpos()
    for alvo in ("$DESTINO", "`pwd`", '"$(mktemp -d)"'):
        d, msg = veredito(r, f"cd {alvo} && " + RUIM)
        assert d == "nega" and "computed at run time" in msg, (alvo, d, msg)
    d, msg = veredito(r, f'cd $DESTINO && gh pr create --title "Ajustes" --body-file {r}/corpo-bom.md')
    assert d == "nega" and "TITLE_GENERIC" in msg and "calculado" not in msg, (d, msg)


def test_hook_ignora_comentario_e_nao_confunde_hash_dentro_de_palavra():
    """Trava a remocao de `#`: aspas abertas num comentario nao tornam o comando ilegivel."""
    r = _repo_com_corpos()
    d, msg = veredito(r, RUIM + ' # nota "sem fechar')
    assert d == "nega" and "TITLE_GENERIC" in msg and "could not be parsed" not in msg, (d, msg)
    d, msg = veredito(r, f'gh pr create --title "{TITULO_BOM}" --body-file corpo-bom.md # ok')
    assert d == "passa", (d, msg)
    # `#` no meio da palavra nao abre comentario: o titulo inteiro e lido
    d, msg = veredito(r, 'gh pr create --title Ajustes#12 --body-file corpo-bom.md')
    assert d == "nega" and "Ajustes#12" in msg, (d, msg)


def test_hook_corpo_que_o_gate_nao_consegue_ler_e_negado_com_o_motivo():
    """Trava $(...), crase, stdin, arquivo inexistente, --web/--editor, comando ilegivel e corpo ausente."""
    r = _repo_com_corpos()
    t = f'gh pr create --title "{TITULO_BOM}" '
    casos = (
        (t + '--body "$(date)"', "gate does not run"),
        (t + '--body "`date`"', "gate does not run"),
        (t + "--body-file -", "stdin"),
        (t + "--body-file nao-existe.md", "does not exist yet"),
        (t + "--body-file corpo-bom.md --web", "the browser or the editor"),
        (t + "-e --body-file corpo-bom.md", "the browser or the editor"),
        (t + "--editor", "the browser or the editor"),
        (t.rstrip() + ' --body-file "corpo-bom.md', "could not be parsed"),
        (t.strip(), "no body"),
        ("gh pr create --body-file corpo-bom.md", "no --title"),
        ("gh pr new --body-file corpo-bom.md", "no --title"),
    )
    for cmd, trecho in casos:
        d, msg = veredito(r, cmd)
        assert d == "nega" and trecho in msg, (cmd, d, msg)
    # com o corpo certo, os mesmos comandos passam
    assert veredito(r, t + "--body-file corpo-bom.md")[0] == "passa"
    assert veredito(r, t + "--body-file=corpo-bom.md")[0] == "passa"


def test_hook_arquivo_de_corpo_grande_ou_nao_utf8_e_negado():
    """Trava os limites do arquivo de corpo: 1 MB e UTF-8."""
    r = _repo_com_corpos()
    with open(os.path.join(r, "grande.md"), "w") as fh:
        fh.write("x" * 1_000_001)
    with open(os.path.join(r, "latin.md"), "wb") as fh:
        fh.write(b"acentua\xe7\xe3o")
    t = f'gh pr create --title "{TITULO_BOM}" '
    assert "is over 1000 kB" in veredito(r, t + "--body-file grande.md")[1]
    assert "UTF-8" in veredito(r, t + "--body-file latin.md")[1]


def test_hook_help_e_ignorado():
    """Trava --help/-h: pedir ajuda do comando nao e abrir PR."""
    r = _repo_com_corpos()
    for cmd in ("gh pr create --help", "gh pr create -h", "gh pr edit --help", "agenfk pr create --help"):
        assert veredito(r, cmd) == ("passagem", ""), cmd
    assert veredito(r, "gh pr create --title Ajustes")[0] == "nega"


def test_hook_edit_e_new_sao_conferidos_e_agenfk_so_cria():
    """Trava GH_ACTIONS: `gh pr edit` com titulo ou corpo e `gh pr new` valem como create; edit de label nao."""
    r = _repo_com_corpos()
    d, msg = veredito(r, 'gh pr edit 12 --title "Ajustes"')
    assert d == "nega" and "gh pr edit" in msg and "TITLE_GENERIC" in msg, (d, msg)
    assert "no body" not in msg and "no --title" not in msg
    d, msg = veredito(r, "gh pr edit 12 --body-file corpo-ruim.md")
    assert d == "nega" and "SECTION_MISSING:Scans" in msg, (d, msg)
    assert veredito(r, f'gh pr edit 12 --title "{TITULO_BOM}" --body-file corpo-bom.md')[0] == "passa"
    d, msg = veredito(r, "gh pr edit 12 --web")
    assert d == "nega" and "browser" in msg, (d, msg)
    d, msg = veredito(r, 'gh pr new --title "Ajustes" --body-file corpo-bom.md')
    assert d == "nega" and "gh pr create" in msg and "TITLE_GENERIC" in msg, (d, msg)
    assert veredito(r, f'gh pr new --title "{TITULO_BOM}" --body-file corpo-bom.md')[0] == "passa"
    assert veredito(r, 'agenfk pr edit abc --title "Ajustes"') == ("passagem", "")
    assert veredito(r, "gh pr edit 12 --add-reviewer fulano") == ("passagem", "")
    assert veredito(r, "gh pr view 12 --json title") == ("passagem", "")
    assert veredito(r, "gh pr -R o/r create --title Ajustes --body-file corpo-bom.md")[0] == "nega"
    assert veredito(r, "gh pr --repo=o/r create --title Ajustes --body-file corpo-bom.md")[0] == "nega"
    # o gh tambem aceita -R/--repo antes de `pr`; sem isso o gate deixava passar
    assert veredito(r, "gh -R o/r pr create --title Ajustes --body-file corpo-bom.md")[0] == "nega"
    assert veredito(r, "gh --repo o/r pr create --title Ajustes --body-file corpo-bom.md")[0] == "nega"
    assert veredito(r, "gh --repo=o/r pr -R o/r create --title Ajustes --body-file corpo-bom.md")[0] == "nega"
    assert veredito(r, f'gh -R o/r pr create --title "{TITULO_BOM}" --body-file corpo-bom.md')[0] == "passa"


def test_hook_prefiltro_nao_parseia_o_que_nao_menciona_pr():
    """Trava MENTIONS_TOOL/MENTIONS_PR: comando sem `gh`/`agenfk` ou sem `pr` nem chega ao parser."""
    chamadas = []
    original = pr_gate.find_pr_commands
    pr_gate.find_pr_commands = lambda *a, **k: chamadas.append(a) or []
    try:
        for cmd in ("ls -la", "git status && echo pr", "gh issue list", "agenfk list", "echo ghx prx"):
            assert pr_gate.evaluate(cmd, ".") == (None, ""), cmd
        assert chamadas == [], chamadas
        pr_gate.evaluate("gh pr list", ".")
        pr_gate.evaluate("agenfk pr create x", ".")
        assert len(chamadas) == 2, chamadas
    finally:
        pr_gate.find_pr_commands = original


# --- passagem para gate proprio -----------------------------------------------

def test_mcp_da_passagem_quando_o_projeto_tem_gate_proprio():
    """Trava o desvio de evaluate_mcp: MCP com titulo ruim em repo com gate proprio nao e negado."""
    tool = "mcp__plugin_github_github__create_pull_request"
    ruim = {"tool_name": tool, "tool_input": {"title": "Ajustes", "body": BOM_PT, "head": "x", "base": "main"}}
    com_gate = _repo_com_corpos({".claude/skills/acme-pr-delivery/SKILL.md": "x"})
    sem_gate = _repo_com_corpos()
    assert decision(hook(dict(ruim, cwd=com_gate))) == ("passagem", "")
    assert decision(hook(dict(ruim, cwd=sem_gate)))[0] == "nega"
    # sem title / sem body
    for campo, trecho in (("title", "no title"), ("body", "no body")):
        entrada = dict(ruim["tool_input"], **{campo: "  "})
        d, msg = decision(hook(dict(ruim, tool_input=entrada, cwd=sem_gate)))
        assert d == "nega" and trecho in msg, (campo, d, msg)


def test_home_do_dono_nunca_conta_como_gate_proprio():
    """Trava a exclusao do $HOME: a skill do squad instalada em ~/.claude nao e gate do projeto."""
    r = _repo_com_corpos({".claude/skills/acme-pr-delivery/SKILL.md": "x"})
    assert decision(hook(bash(r, RUIM), {"HOME": tempfile.mkdtemp()})) == ("passagem", "")
    d, msg = decision(hook(bash(r, RUIM), {"HOME": r}))
    assert d == "nega" and "TITLE_GENERIC" in msg, (d, msg)
    tool = "mcp__plugin_github_github__create_pull_request"
    mcp = {"tool_name": tool, "cwd": r, "tool_input": {"title": "Ajustes", "body": BOM_PT}}
    assert decision(hook(mcp, {"HOME": r}))[0] == "nega"


def test_gate_proprio_em_settings_so_conta_se_for_hook():
    """Trava o 7d78583: `Bash(gh pr create:*)` em permissions libera o comando e NAO e gate; mencao em hooks e."""
    permissao = json.dumps({"permissions": {"allow": ["Bash(gh pr create:*)"], "ask": ["Bash(gh pr new:*)"]}})
    gancho = json.dumps({"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [
        {"type": "command", "command": "~/.claude/hooks/meu-gate.sh gh pr create"}]}]}})
    casos = (
        ({".claude/settings.json": permissao}, "nega"),
        ({".claude/settings.local.json": permissao}, "nega"),
        ({".claude/settings.json": gancho}, "passagem"),
        ({".claude/settings.local.json": gancho}, "passagem"),
        ({".claude/settings.json": json.dumps({"hooks": {}, "permissions": {"allow": ["Bash(gh pr create:*)"]}})}, "nega"),
        ({".claude/settings.json": json.dumps({"hooks": "gh pr create"})}, "passagem"),
        ({".claude/settings.json": "{nao e json gh pr create"}, "nega"),
        ({".claude/settings.json": "[1, 2]"}, "nega"),
        ({".claude/hooks/config.json": '{"cmd": "gh pr create"}'}, "passagem"),
        ({".claude/hooks/nada.sh": "echo oi\n"}, "nega"),
        ({".claude/skills/prs-stack/SKILL.md": "x"}, "nega"),
        ({".claude/skills/pull-request/SKILL.md": "x"}, "passagem"),
    )
    for files, esperado in casos:
        r = _repo_com_corpos(files)
        d, msg = decision(hook(bash(r, RUIM)))
        assert d == esperado, (list(files), d, msg)
    # in-process: o retorno diz o que achou
    r = _repo_com_corpos({".claude/settings.json": gancho})
    assert pr_gate.own_pr_gate(r) == ".claude/settings.json"
    assert pr_gate.own_pr_gate(_repo_com_corpos({".claude/settings.json": permissao})) is None
    assert pr_gate.own_pr_gate(tempfile.mkdtemp()) is None
    assert pr_gate.own_pr_gate(None) is None


def test_gate_proprio_so_olha_a_raiz_do_repositorio():
    """Trava o uso de repo_root: .claude/ numa subpasta nao e gate; a raiz vista de uma subpasta e."""
    r = _repo_com_corpos({".claude/skills/acme-pr-delivery/SKILL.md": "x", "sub/.claude/settings.json": "{}"})
    assert pr_gate.own_pr_gate(os.path.join(r, "sub")) == ".claude/skills/acme-pr-delivery"
    r2 = _repo_com_corpos({"sub/.claude/skills/acme-pr-delivery/SKILL.md": "x"})
    assert pr_gate.own_pr_gate(os.path.join(r2, "sub")) is None


# --- config hostil do repositorio ---------------------------------------------

def _repo_hostil():
    """Repo com fsmonitor, diff.external e textconv apontando para scripts que criam sentinelas."""
    r = repo({"CLAUDE.md": "Títulos de PR em português.\n", "corpo-bom.md": BOM_PT, "app.py": "a\n", "x.dat": "1\n"})
    sent = tempfile.mkdtemp(prefix="pr-sentinela-")
    for nome in ("fsmonitor", "external", "textconv"):
        path = os.path.join(sent, nome + ".sh")
        with open(path, "w") as fh:
            fh.write(f'#!/bin/sh\ntouch "{sent}/{nome}"\ncat "$2" 2>/dev/null\n')
        os.chmod(path, 0o755)
    with open(os.path.join(r, ".gitattributes"), "w") as fh:
        fh.write("*.dat diff=evil\n")
    git(r, "add", "-A")
    git(r, "commit", "-q", "-m", "Atributos")
    git(r, "checkout", "-q", "-b", "feat")
    with open(os.path.join(r, "app.py"), "w") as fh:
        fh.write("b\n")
    with open(os.path.join(r, "x.dat"), "w") as fh:
        fh.write("2\n")
    git(r, "commit", "-q", "-am", "Muda")
    git(r, "config", "core.fsmonitor", os.path.join(sent, "fsmonitor.sh"))
    git(r, "config", "diff.external", os.path.join(sent, "external.sh"))
    git(r, "config", "diff.evil.textconv", os.path.join(sent, "textconv.sh"))
    return r, sent


def _sentinelas(sent):
    return sorted(n for n in os.listdir(sent) if not n.endswith(".sh"))


def test_config_hostil_do_repo_nao_executa_nada_no_gate():
    """Trava o 7d78583: fsmonitor, diff.external e textconv do repo nao rodam no hook nem no pr_check."""
    r, sent = _repo_hostil()
    # controle: com a config hostil o git puro EXECUTA os scripts; sem isso o teste nao provaria nada
    env = {**os.environ, **GIT_ENV}
    subprocess.run(["git", "-C", r, "status"], capture_output=True, env=env)
    subprocess.run(["git", "-C", r, "diff", "main...HEAD"], capture_output=True, env=env)
    subprocess.run(["git", "-C", r, "-c", "diff.external=", "diff", "--no-ext-diff", "main...HEAD"],
                   capture_output=True, env=env)
    assert _sentinelas(sent) == ["external", "fsmonitor", "textconv"], _sentinelas(sent)
    for n in _sentinelas(sent):
        os.unlink(os.path.join(sent, n))

    cmd = f'gh pr create --title "{TITULO_BOM}" --body-file corpo-bom.md'
    assert decision(hook(bash(r, cmd), {"FAKE_GH_OUT": ""}))[0] == "passa"
    mcp = {"tool_name": "mcp__plugin_github_github__create_pull_request", "cwd": r,
           "tool_input": {"title": TITULO_BOM, "body": BOM_PT}}
    assert decision(hook(mcp))[0] == "passa"
    p = subprocess.run([sys.executable, "-I", CHECK, os.path.join(r, "corpo-bom.md"), "--repo", r,
                        "--base", "main", "--title", TITULO_BOM], capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stdout
    assert pr_check.changed_files(r, "main") == ["app.py", "x.dat"]
    assert pr_check.detect_lang(r)[0] == "pt"
    assert _sentinelas(sent) == [], _sentinelas(sent)

    # o inventario le o diff inteiro: aqui diff.external e textconv teriam rodado
    p = subprocess.run([sys.executable, "-I", INVENTORY, "--repo", r, "--base", "main", "--format", "json"],
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    assert _sentinelas(sent) == [], _sentinelas(sent)



def test_todo_git_do_gate_sai_sem_fsmonitor_e_o_diff_sem_driver_externo():
    """Trava o 7d78583 pelo argv: nenhuma chamada de git do pr_check, do hook e do inventario omite
    `-c core.fsmonitor=false`, e todo `diff` leva --no-ext-diff e --no-textconv (o git so executa o
    script de fsmonitor em `status`; o argv e a unica prova de que o flag continua la)."""
    real = shutil.which("git")
    spy = tempfile.mkdtemp(prefix="pr-spy-")
    log = os.path.join(spy, "argv")
    with open(os.path.join(spy, "git"), "w") as fh:
        fh.write(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{log}"\nexec "{real}" "$@"\n')
    os.chmod(os.path.join(spy, "git"), 0o755)
    env = {**os.environ, **GIT_ENV, "PATH": spy + os.pathsep + os.environ["PATH"]}
    r = repo({"CLAUDE.md": "Títulos de PR em português.\n", "corpo-bom.md": BOM_PT, "a.py": "1\n"})
    git(r, "checkout", "-q", "-b", "feat")
    with open(os.path.join(r, "a.py"), "w") as fh:
        fh.write("2\n")
    git(r, "commit", "-q", "-am", "Muda")

    def chamadas():
        with open(log) as fh:
            return [l.strip() for l in fh if l.strip()]

    subprocess.run([sys.executable, "-I", HOOK],
                   input=json.dumps(bash(r, f'gh pr create --title "{TITULO_BOM}" --body-file corpo-bom.md')),
                   capture_output=True, text=True, env={**env, "HOME": tempfile.mkdtemp()})
    subprocess.run([sys.executable, "-I", CHECK, os.path.join(r, "corpo-bom.md"), "--repo", r, "--base", "main"],
                   capture_output=True, text=True, env=env)
    sem_politica = repo(commits=["Corrige o login quando a senha tem acento"])  # idioma sai do git log
    subprocess.run([sys.executable, "-I", CHECK, os.path.join(r, "corpo-bom.md"), "--repo", sem_politica],
                   capture_output=True, text=True, env=env)
    por_hook_e_check = chamadas()
    assert any(" diff " in f" {c} " for c in por_hook_e_check) and any("rev-parse" in c for c in por_hook_e_check) \
        and any(" log " in f" {c} " for c in por_hook_e_check), por_hook_e_check
    subprocess.run([sys.executable, "-I", INVENTORY, "--repo", r, "--base", "main"],
                   capture_output=True, text=True, env=env)
    todas = chamadas()
    assert len(todas) > len(por_hook_e_check), todas
    for c in todas:
        assert "-c core.fsmonitor=false" in c, c
        if " diff " in f" {c} ":
            assert "--no-ext-diff" in c and "--no-textconv" in c, c


if __name__ == "__main__":
    try:
        for name, fn in sorted(globals().items()):
            if name.startswith("test_") and callable(fn):
                fn()
                print("ok ", name)
    finally:
        shutil.rmtree(FAKE_BIN, ignore_errors=True)
