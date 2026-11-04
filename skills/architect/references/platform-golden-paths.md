# Platform and golden paths

Apply this module when creating reusable capabilities or preparing infrastructure alongside the application.
Treat the rules below as the Architect's procedure; adapt them to the repository and to the authorised scope.

## Choose the smallest sufficient platform

1. Survey the existing path for build, deploy, identity, observability and operation.
2. Reuse healthy capabilities before proposing a new platform.
3. Identify the wait that blocks delivery: access, contract, environment, review or shared resource.
4. Automate first the recurring wait that most affects the developer flow.
5. Use AWS + Terraform + GitHub Actions as the available profile, when it fits the context.
6. Treat Backstage as an optional interface; allow the path to be consumed through CLI, API or Git.
7. Justify Kubernetes with concrete operational requirements; do not make it a requirement of the skill.

## Define the golden path product

Record for each path:

| Field | Verifiable content |
| --- | --- |
| Identity | Name, version, owner and support channel |
| Outcome | Capability delivered and who consumes it |
| Interface | Typed inputs, defaults, outputs and constraints |
| Compatibility | Supported runtimes, providers, versions and environments |
| Implementation | Template, modules and workflow with pinned revisions |
| Verification | Tests, smoke test and expected evidence |
| Operation | Applicable SLO, logs, alerts, costs and runbook |
| Evolution | Migration, deprecation, exceptions and retirement |

Version the path with its contracts and tests; record the version each consumer adopted.
Test an update on a representative consumer before recommending a mass migration.
Keep an exception with a reason, an owner and a review condition; avoid forcing an unsuitable abstraction.
Do not confuse generating a repository with delivering the operational capability.

## Stabilise the runtime contract early

Define the minimum contract before releasing parallel implementation:

| Aspect | Agreement between application and platform |
| --- | --- |
| Executable | Artifact format, CPU architecture, command and digest at publication |
| Network | Ports, protocols, exposure and discovery of dependencies |
| Health | Liveness, readiness, startup and graceful termination |
| Configuration | Names, types, whether required, and references to secrets |
| Identity | Workload identity, allowed actions and reachable resources |
| Data | Contracts, migrations, persistence and retention |
| Resources | CPU/memory, concurrency, limits and scaling signals |
| Operation | Logs/traces, rollback and evidence of the functional flow |

Allow the application to implement this contract with fakes while the platform creates the resources.
Allow the infrastructure to validate the runtime with a minimal, compatible and identified artifact.
Mark a validation artifact as such; prevent its accidental promotion as the finished application.
Reserve real integration to prove identity, network, secrets, data and end-to-end behaviour.

## Separate authoring, plan and apply

Produce IaC, configuration and tests in parallel with the application code.
Compose small modules that receive dependencies through inputs; publish stable outputs.
Avoid having each module recreate network, identity or data that already have an owner.
Distinguish in the status: written, validated locally, planned in the environment, applied and verified.
Inspect the real plan before the apply; check environment, account, region, replacements and deletions.
Apply within the existing authorisation; ask for a decision only when there is a concrete new limit.
Use one executor per shared Terraform state and respect its locking mechanism.
Parallelise independent states only when their contracts and dependencies allow it.
Do not remove locks, import resources or change state to get around another active executor.

## Keep the catalog and the Platform State Graph minimal

Start with versioned YAML/JSON and observations that record their source; do not require a graph database.
Adapt the example to the existing schema; keep IDs stable across systems:

```yaml
schema_version: "1"
components:
  - id: service.orders
    owner: team.product
    golden_path: api-container@1.0.0
    desired_environment: staging
    depends_on: [data.orders]
    observed_state: unknown
    observed_at: null
    evidence: []
  - id: data.orders
    owner: team.platform
    depends_on: []
    observed_state: unknown
    observed_at: null
    evidence: []
```

Relate service, repository, pipeline, artifact, runtime, data, owner and SLO as needed.
Record the desired state separately from the observed state; attach source, timestamp and commit/digest.
Expose outdated or contradictory information; do not resolve a divergence by inventing a healthy state.
Add specialised storage only when queries, volume or update rate justify it.

## Measure FinOps and experience

Estimate cost with region, currency, date, units, traffic and explicit assumptions.
Separate the monthly floor, the variable cost and the temporary cost of test environments.
Measure cost per transaction or per useful task, time to first deploy and manual intervention.
Define owner, budget and cleanup for ephemeral environments, preserving the data that is needed.
Compare the estimate with observed consumption; turn a relevant deviation into a concrete action.
Prioritise reducing waiting and rework; do not use the number of templates or PRs as success.

## Primary sources

Consult current documentation before generating syntax or choosing versions. References verified on 23/10/2026.
- [CNCF: platforms as products and composable capabilities](https://tag-app-delivery.cncf.io/whitepapers/platforms/).
- [Backstage: Software Templates](https://backstage.io/docs/features/software-templates/) and [limits of the official examples](https://github.com/backstage/software-templates).
- [HashiCorp: module composition, inputs, assumptions and guarantees](https://developer.hashicorp.com/terraform/language/modules/develop/composition).
- [AWS: evaluating architectural trade-offs](https://docs.aws.amazon.com/wellarchitected/latest/framework/welcome.html).
