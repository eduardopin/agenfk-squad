# Entry prompts

## New project
Use $architect. Turn the idea below into a first working slice.
Read the repository and the available context; record constraints and assumptions.
Choose a proportionate architecture and golden path. Define minimal contracts,
create a backlog with dependencies, owners, paths and locks. Before creating resources,
present the complete table resource → planned PR → owner → dependencies
→ duration range. Show PR ready, resource verified and product milestone
separately; identify what can start in parallel. In this request,
produce the design and the reviewable artifacts.
Idea: [goal/user flow]. Repo: [local/link]. Constraints: [deadline/cost/stack].

pt-BR: Use $architect. Transforme a ideia a seguir em uma primeira fatia funcional. Leia o repositório e o contexto disponível; registre restrições e hipóteses. Escolha arquitetura e golden path proporcionais. Defina contratos mínimos, crie backlog com dependências, owners, paths e locks. Antes de criar recursos, apresente a tabela completa recurso → PR planejada → responsável → dependências → faixa de prazo. Mostre separadamente PR pronta, recurso verificado e marco de produto; identifique o que pode começar em paralelo. Neste pedido, produza o design e os artefatos revisáveis. Ideia: [objetivo/jornada]. Repo: [local/link]. Restrições: [prazo/custo/stack].

## Authorized implementation in a development environment
Use $architect in execute mode in this repository and development scope.
First present the baseline of resources, PRs and delivery forecast.
Implement the slice [description], prepare infrastructure and CI/CD in parallel
where contracts and permissions allow. Preserve existing instructions and
changes. Reuse authorizations already established; ask only for what
is not authorized. Deliver the diff, evidence and real state.

pt-BR: Use $architect no modo execute neste repositório e escopo de desenvolvimento. Apresente primeiro a baseline de recursos, PRs e previsão de entrega. Implemente a fatia [descrição], prepare infraestrutura e CI/CD em paralelo onde contratos e permissões permitirem. Preserve instruções e mudanças existentes. Reutilize autorizações já estabelecidas; peça somente pelo que de fato não estiver autorizado. Entregue diff, evidências e estado real.

## Predictability for the manager
Use $architect to plan the delivery of [app, stack or resource].
Record new, changed and reused resources; group PRs by capability.
Show the owner, the reviewer, the gates, the available capacity and the milestones.
Compute optimistic, likely and conservative scenarios with explicit assumptions;
do not turn a scenario into a promise or add up durations of shared tasks.
If a resource outside the inventory appears, update the table before creating it.

pt-BR: Use $architect para planejar a entrega de [app, stack ou recurso]. Registre recursos novos, alterados e reutilizados; agrupe PRs por capacidade. Mostre o responsável, o revisor, os gates, a capacidade disponível e os marcos. Calcule cenários otimista, provável e conservador com premissas explícitas; não transforme cenário em promessa nem some durações de tarefas compartilhadas. Se surgir um recurso fora do inventário, atualize a tabela antes de criá-lo.

## Break a loop
Use $architect to analyze these attempts and the current state.
Identify the proven blocker, consolidate related changes and produce
the smallest useful verifiable delivery. Do not reopen decisions without new facts.
Files/logs: [references]. Desired result: [behavior].

pt-BR: Use $architect para analisar estas tentativas e o estado atual. Identifique o bloqueio comprovado, consolide mudanças relacionadas e produza a menor entrega útil verificável. Não reabra decisões sem fatos novos. Arquivos/logs: [referências]. Resultado desejado: [comportamento].

## Staff review
Use $architect in review mode. Assess the requirements, domain, data, security,
operability and cost of this plan. Point out artificial and real dependencies,
missing contracts, collisions, migration risks and a simpler alternative.
Do not mark a proposal as validated without evidence.

pt-BR: Use $architect no modo review. Avalie requisitos, domínio, dados, segurança, operabilidade e custo deste plano. Aponte dependências artificiais e reais, contratos ausentes, colisões, riscos de migração e alternativa mais simples. Não marque uma proposta como validada sem evidências.

## Portability
In assistants that do not discover Agent Skills, provide the whole folder as
context/project and ask it to read SKILL.md, then only the modules needed.
Access to files, terminal, cloud, permissions and multi-agent work depends on the assistant.
Do not paste only the skill name expecting it to be installed.
