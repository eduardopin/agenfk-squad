# Security and delivery

Apply controls according to the risk and the resources changed; preserve the delivery flow.
Reuse the existing authorisation and the repository's rules; do not invent ceremonies for trivial changes.

## Identify the relevant boundaries

Map the assets, identities, data, trust boundaries and external effects of the change.
Describe concrete threats that influence the implementation, and their verifiable mitigation.
Distinguish local development, staging, production and shared resources.
Identify destructive changes, relevant new spending and the impact on data or third parties.
Prepare the diff, plan, cost and recovery before asking for a decision that is still needed.
Continue independent tasks when a specific action is blocked.

## Build CI with least privilege

Define the token's permissions explicitly; widen them only in the job that needs them.
Separate build/test from publish/deploy; do not give production credentials to the first group.
Treat branch names, PR titles and external content as untrusted inputs.
Pass data through arguments or safe variables; avoid interpolation into shell code.
Do not run untrusted contributed code in a privileged context.
Verify the origin of actions and pin immutable revisions by full SHA.
Accept a native lockfile only when it is supported, enabled and verified in the real environment.
Keep the existing dependency update policy; pinning does not replace maintenance.
Remove secrets from artifacts, published plans and logs; treat redaction as an extra defence.

## Configure the deploy identity

Prefer short-lived federation when the provider and the runner support it.
Check the issuer, audience, subject and real workflow context before writing a trust policy.
Restrict the identity to the repository and the branch/environment needed.
Verify the current subject formats, including immutable IDs and environment conditions.
Separate the permissions to read/plan from those to apply changes when that reduces exposure.
Check the observed identity and target before applying; do not assume the account or region from the local name.
Reuse approvals already satisfied; keep only the gates required by the risk or the applicable policy.

## Promote identifiable artifacts

Tie a release to the commit, contract, dependencies and digest of the artifact produced.
Build once and promote the same artifact across environments whenever the format allows.
Inject the environment's configuration and secrets at deployment; prevent their inclusion in the image.
Check that the deployed digest matches the tested digest.
Generate an inventory/SBOM and provenance when the risk, distribution or policy requires it.
Keep the scanner evidence with version and scope; do not treat the absence of findings as proof of security.
Distinguish an exploitable vulnerability, an accepted risk and a false positive with a traceable justification.

## Verify proportionally

Choose tests based on the behaviour changed and the boundaries affected.

| Change | Proportional evidence |
| --- | --- |
| Text or isolated adjustment | Inspection of the result and the relevant local check |
| Business rule | Positive case, failure case and affected regression |
| Shared contract | Producer and consumer compatibility |
| IAM, tenancy or secrets | Allowed and denied permissions, with no leak |
| Infrastructure | Validation, real plan and verification after apply |
| Data migration | Compatibility, integrity and viable recovery |
| Critical user flow | Functional integration and the matching operational signals |

Do not create a test that only repeats constants or the structure of the implementation.
Widen verification only to resolve a concrete risk or to meet an existing gate.
Record tests not run and the reason; do not replace them with presumed success.

## Enforce tenancy and access limits

Get the tenant and identity from a trusted context; do not accept an arbitrary tenant from the request body.
Apply isolation in queries, objects, queues, caches, jobs and tools that touch data.
Test cross access with two distinct identities and resources.
Check that asynchronous jobs keep the authorised context without inheriting undue permissions.
Treat authentication as a requirement distinct from effective tenant isolation.
Apply the same discipline to document retrieval and to caches of AI responses.

## Evolve the schema with compatibility

Assign one owner to order the migrations of the same database or schema.
Use expand-contract when old and new versions must coexist.
Add compatible structure before changing consumers; preserve the semantics of the data.
Design an idempotent, bounded and observable backfill, with checkpoints if the volume justifies it.
Define consistency during the transition; do not use naive dual-write between independent systems.
Verify integrity and performance before moving reads to the new structure.
Remove old structures only after confirming the migrated consumers and the recovery window.
Assess locks, duration, backup/restore and the point at which rollback is no longer safe.
Prefer a forward fix when reverting would mean data loss; record that limit.

## Roll out and prove the result

Define the success signal, the failure threshold and the recovery action before starting the deployment.
Choose a simple, gradual or blue-green rollout according to impact, compatibility and cost.
Verify the relevant readiness, main user flow, authorisation, persistence and telemetry.
Record environment, time, commit/digest, checks run and accessible evidence.
Stop the expansion when the criteria fail; recover according to the authorised plan.
Distinguish application rollback, infrastructure rollback and data recovery.
Close with the observed state and concrete open items; do not declare production healthy with only a green CI.

## Primary sources

Verify that features are available before generating configuration. References verified on 23/10/2026.
- [GitHub: security in workflows](https://docs.github.com/en/actions/reference/security/secure-use) and [actions lockfiles, currently in preview](https://docs.github.com/en/actions/concepts/security/workflow-dependency-lockfiles).
- [GitHub: OIDC in AWS and subject formats](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-aws).
- [AWS: tenant isolation](https://docs.aws.amazon.com/whitepapers/latest/saas-architecture-fundamentals/tenant-isolation.html).
- [Prisma: schema expansion, transition and retirement](https://www.prisma.io/dataguide/types/relational/expand-and-contract-pattern).
- [OpenTelemetry: context propagation and data protection](https://opentelemetry.io/docs/concepts/context-propagation/).
