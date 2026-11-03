# AI engineering

Apply this module when the product uses models, retrieval, tools or agentic execution.
Treat the model's ability, the skill's instructions and the host's capabilities as distinct components.

## Choose the degree of autonomy

1. Define the business outcome and the acceptable cost of error, waiting and intervention.
2. Implement deterministic rules for validation, authorisation and known transitions.
3. Try a simple call with structured output and sufficient context.
4. Adopt an explicit workflow when the steps, branches and criteria are known.
5. Adopt an agent when decisions that depend on observations require real flexibility.
6. Adopt multiple agents only for independent subtasks with defined integration.
7. Compare the new alternative with the baseline using the same cases and budget.

Do not promise that a skill adds workers, APIs, credentials or tools that do not exist.
Define a fallback for a missing tool and record what could not be verified.

## Settle contracts before implementation

Define input/output schemas, required fields, errors, timeout and the authorised identity.
Separate the interfaces of model, retriever, tools, state storage and telemetry.
Version contracts with valid examples and rejection cases.
Provide deterministic fakes that reproduce success, timeout, rate limit and invalid response.
Release application, provider adapter, infrastructure and evaluation from these contracts.
Test the real adapter before claiming compatibility with the provider.
Validate structured output in code; do not delegate parsing or authorisation to trust in the prompt.

## Build evaluations that verify the outcome

Define what counts as success before optimising prompt, model or architecture.
Create cases from real user flows, including failures already observed and priority requirements.
Keep development cases separate from the cases reserved for evaluation.
Define a verifiable reference: final state, grounded answer, calculation or expected behaviour.
Use deterministic checks for invariants and rubrics for open-ended qualities.
Calibrate an LLM judge with independent evaluations suited to the domain.
Repeat attempts when measuring consistency; record the sample size and the observed variation.
Measure success rate, failures by category, latency, cost and need for intervention.
Separate regression of known behaviour from exploration of new capabilities.
Do not declare a universal benchmark based on a few examples produced by the author.

| Case type | Required verification |
| --- | --- |
| Factual answer | Relevant evidence and coverage of the question |
| Tool use | Valid arguments, authorised action and observed result |
| Code or infrastructure | Executable artifact and a test that matches the requirement |
| Missing information | Explicit uncertainty or a question that closes the gap |
| Provider failure | Timeout, retry and fallback within the budget |
| Insufficient permission | No access, and no action outside the scope |

## Design RAG with authorisation

Authenticate the caller and resolve their tenant and permissions before the query.
Apply the access restrictions during retrieval, before passing candidates to the pipeline.
Do not send forbidden documents to a reranker, LLM, shared cache or logs.
Keep the ACL, source, version, dates and IDs of the documents in their chunks too.
Define refresh and invalidation for permission changes, corrections and deletion at the source.
Test isolation between users/tenants, revocation and caches after an ACL change.
Evaluate retrieval and generation separately to locate the cause of failures.
Compare lexical, vector and hybrid search with real cases; justify a reranker by the measured gain.
Define the behaviour with no evidence, with conflicting sources or with expired information.
Treat retrieved text as data; prevent instructions in it from widening permissions or changing the mission.

## Persist execution and control effects

Persist run_id, task_id, contract version, attempt, status and the evidence needed to resume.
Choose durable storage when the run must survive a restart.
Wrap external effects in identified, idempotent operations.
Check the result of an ambiguous operation before repeating a payment, send, creation or deploy.
Define limits on attempts, backoff, timeout and the stop condition per task.
Prevent repetition without new information from being counted as progress.
Define compensation when there is no rollback; explain the partially completed state.
Do not mistake a checkpoint for an exactly-once guarantee of external effects.
Use LangGraph, a workflow engine or an existing orchestrator according to the needs shown.

## Version model profiles

Record provider, model/revision, required capabilities and the evidence for the choice.
Fix limits on context, output, calls, concurrency, timeout and cost per task.
Separate profiles for classification, generation, judging and tasks that use tools.
Verify prices, limits and availability in the current documentation when configuring the profile.
Define a fallback that preserves the privacy, region, tool and minimum quality requirements.
Avoid a silent fallback that changes contract, retention or semantics that matter to the product.
Record in each execution the profile actually used, without exposing credentials or sensitive data.

## Size inference and data

Separate the requirements for training, fine-tuning, indexing and inference; do not propose training without a measured gap and suitable data.
For local inference, size weights, KV cache, context, batch, concurrency and RAM/VRAM; verify the licence and runtime support.
Evaluate quantisation with the same set of cases before accepting memory savings as a gain without loss.
Record data lineage, artifact versions and data removal when required; do not mix secrets with the model registry.
Measure throughput and latency on the real hardware/workload; do not turn a GPU's commercial spec into a performance promise.

## Deliver and monitor

Version prompt, code, tools, dataset, retrieval configuration and model together in the release record.
Promote an identifiable, reversible configuration; test shadow/canary only when it yields useful evidence.
Monitor errors, cost, latency, sampled quality and changes in the data profile.
Define per-tenant quotas and queue containment to prevent uncontrolled consumption.
Run the relevant regressions on changes to model, prompt, contract or data source.
Deliver evidence, limitations and the next experiment; do not mask gaps with a high aggregate score.

## Primary sources

Consult current API versions before implementing. References verified on 23/10/2026.
- [Anthropic: workflows, agents and proportional complexity](https://www.anthropic.com/research/building-effective-agents).
- [Anthropic: evaluating tasks, trajectories and outcomes](https://www.anthropic.com/research/demystifying-evals-for-ai-agents).
- [LangGraph: replay, tasks and idempotency](https://docs.langchain.com/oss/python/langgraph/functional-api) and [persistence](https://docs.langchain.com/oss/python/langgraph/persistence).
- [Promptfoo: rubrics and metrics for RAG](https://www.promptfoo.dev/docs/configuration/expected-outputs/model-graded/).
- [Microsoft: document-level authorisation at ingestion and query](https://learn.microsoft.com/en-us/azure/search/search-document-level-access-overview).
