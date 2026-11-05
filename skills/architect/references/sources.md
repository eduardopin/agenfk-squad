# Sources and adoption limits

Documentation consulted on **23/10/2026**. Use the sources below as design references; check versions and documentation before running commands. The contributions are this skill's own choices. No external project was installed or fully audited in this research.

## Architecture, Staff and specification

1. [GitHub Spec Kit](https://github.com/github/spec-kit): reuse the link between intent, spec, plan, tasks, implementation and convergence.
   Limit: templates and the community catalog do not prove the independence, quality or security of the extensions.

2. [OpenSpec](https://github.com/Fission-AI/OpenSpec): organise incremental changes with a persisted proposal, contracts, decisions and tasks.
   Limit: change folders do not prevent collisions; choose one canonical format per project.

3. [Superpowers: parallel agents](https://github.com/obra/superpowers/blob/main/skills/dispatching-parallel-agents/SKILL.md): reuse briefs and independent scopes.
   Limit: [subagent-driven-development](https://github.com/obra/superpowers/blob/main/skills/subagent-driven-development/SKILL.md) currently forbids simultaneous implementers; do not import its policy wholesale.

4. [BMAD: Architecture Spine](https://github.com/bmad-code-org/BMAD-METHOD/blob/main/docs/plan/design-ux-and-architecture.md): record early the shared decisions that could diverge between executors.
   Limit: fit the process to the size of the change; keep decisions aligned with the real code.

5. [DORA: Loosely Coupled Teams](https://dora.dev/capabilities/loosely-coupled-teams/): use independence to change, test and deploy as the decoupling criterion.
   Limit: organisational evidence does not guarantee individual speed; the number of services or agents does not measure autonomy.

6. [C4 Model](https://c4model.com/diagrams): communicate context, containers and other levels only when needed.
   Limit: diagrams explain structure; they do not demonstrate cost, performance or security.

7. [arc42](https://arc42.org/overview/) and [architecture decisions](https://docs.arc42.org/section-9/): structure constraints, quality, risks and the decisions that shape the architecture.
   Limit: adapt the documentation and avoid repetition; do not require twelve documents per project.

8. [Will Larson: Staff Archetypes](https://staffeng.com/guides/staff-archetypes/): guide technical direction, delegation, unblocking and the connection to the business.
   Limit: a human taxonomy does not certify a model's seniority or reliability.

## Platform and delivery

9. [CNCF Platforms White Paper](https://tag-app-delivery.cncf.io/whitepapers/platforms/): treat the platform as a product and offer composable capabilities with a low operational load.
   Limit: principles are not a ready runtime; a portal and Kubernetes are not universal requirements.

10. [Backstage Software Templates](https://backstage.io/docs/features/software-templates/): use scaffolding as an optional interface for versioned golden paths.
    Limit: generating a repository does not prove deployment or later maintenance; review the examples and dependencies.

11. [Terraform Module Composition](https://developer.hashicorp.com/terraform/language/modules/develop/composition): define dependencies through inputs and guarantees through clear interfaces.
    Limit: composition does not eliminate provisioning dependencies, state locks or migrations.

12. [GitHub Actions: OIDC in AWS](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws): use federated identity with restricted trust policies.
    Limit: verify the current issuer, audience and subject; OIDC does not fix excessive permissions.

13. [AWS Well-Architected](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html): evaluate trade-offs across operations, security, reliability, efficiency and cost.
    Limit: adapt to the provider and workload; the framework does not prove an optimal architecture nor provide a universal budget.

14. [OpenTelemetry: Context Propagation](https://opentelemetry.io/docs/concepts/context-propagation/): correlate activity across components and executions.
    Limit: define the backend, retention and trust boundaries; avoid sensitive content in baggage and logs.

## AI engineering and portability

15. [Anthropic: Building Effective Agents](https://www.anthropic.com/research/building-effective-agents): choose a simple flow and delegate independent subtasks with a defined scope.
    Limit: autonomy adds cost and latency; the 2024 article does not replace checking the current SDKs.

16. [LangGraph Functional API](https://docs.langchain.com/oss/python/langgraph/functional-api): use persistence, resumption and explicit boundaries for external effects.
    Limit: checkpoints do not guarantee exactly-once; idempotency and durable storage depend on the implementation.

17. [Anthropic: Evals for AI Agents](https://www.anthropic.com/research/demystifying-evals-for-ai-agents): evaluate task, attempt, trajectory and the outcome observable in the environment.
    Limit: a small suite or LLM-judge agreement does not show general competence; record the conditions and gaps.

18. [Promptfoo: Model-Graded Metrics](https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/): version rubrics and separate retrieval and generation evaluations.
    Limit: semantic metrics depend on the evaluator and the data; calibrate against the product's criteria.

19. [Agent Skills Specification](https://agentskills.io/specification): use `SKILL.md`, metadata and references loaded according to the task.
    Limit: a portable format does not provide tools, credentials, parallelism or identical behaviour across hosts.

## Candidate for later evaluation

20. [spec-kit-schedule](https://github.com/jfranc38/spec-kit-schedule): examine the DAG, file/resource constraints and CP-SAT optimisation for larger scenarios.
    Limit: project consulted, not run or audited; the Architect uses its own heuristic planner in Python stdlib, without this dependency. A solver's optimality depends on the model and the input estimates.
