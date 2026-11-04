# Domain playbooks

## Index

- [Application](#application)
- [Commerce and support](#1-commerce-and-support)
- [Research, reports and credits](#2-research-reports-and-credits)
- [Collaboration and calendar](#3-collaboration-and-calendar)
- [Content and curation](#4-content-and-curation)
- [Monitoring and alerts](#5-monitoring-and-alerts)
- [Marketing and business operations](#6-marketing-and-business-operations)
- [Games](#7-games)
- [Personal assistant](#8-personal-assistant)
- [Desktop and process control](#9-desktop-and-process-control)
- [Developers, CLI and infrastructure](#10-developers-cli-and-infrastructure)
- [Static content and roadmap](#11-static-content-and-roadmap)
- [Add a domain](#add-a-domain)

## Application

Load only the blocks relevant to the goal and to the product's verified capabilities. Treat these playbooks as coverage suggestions; get the normative rules from the product profile. Do not assume purchase, subscription, chat, onboarding or an account where those features do not exist.

Define the actor's mission by intent. Keep the oracles, seeded defects and internal criteria in the observer's context. One product can combine several domains. A missing capability is `not_applicable` when it is outside the contract; it is a limitation or a block when the mission needs it but the environment does not offer it.

All the blocks below describe tests to select, not evidence of tests executed. Apply the pack's results contract: separate `run_status`, `actor_outcomes` and `qa_verdict`. Record real dependencies, sandbox, mock or replay individually. Verify the business rule even when the actor manages to finish their task.

## 1. Commerce and support

**Value:** choose a suitable product or service, understand the commitment and get what was agreed. Possible roles: visitor, buyer, seller, support agent, shopkeeper and returns operator.

**Journeys by intent:** research alternatives; clarify compatibility, deadline or availability; compare budget; configure options; ask for a proposal; buy if it makes sense; follow up; fix a problem; cancel or return when allowed. Include a customer who abandons over price and another who needs to talk before deciding. In consultative sales, an understood proposal can be the correct outcome without checkout.

**Useful variations:** first visit, return with a cart, inapplicable coupon, invalid address, out-of-stock product, declined payment, slow confirmation, change of agent and partial refund. Vary one condition at a time when causality must be identified.

**Oracles:** SKU/variant identity, quantities, currency, rounding, final price communicated before the commitment, stock, persisted order, correlated payment and fulfillment, applicable deadline and policy. Check exactly one effect when there is an ambiguous attempt. Distinguish authorization, capture, cancellation and refund according to the contract; do not reduce everything to "paid".

**Failure signals:** price changes without explanation, duplicate charge, another customer's order, promise incompatible with stock, message delivered to the wrong recipient or loss of context in the handoff. Mark the actor's doubt as friction until there is evidence of a violated rule.

**Browser limits:** the UI can demonstrate selection and confirmation, but does not prove settlement, dispatch, physical delivery, existence of stock or the human quality of support. Conversations need test recipients and current authorization; do not invent replies to complete the journey. Use a sandbox for payments when that is the authorized environment.

## 2. Research, reports and credits

**Value:** get traceable information about the correct object and understand coverage limits. Roles: visitor, subscriber, researcher and credit operator.

**Journeys by intent:** identify an ambiguous entity; generate a report; wait, or leave and return; interpret missing evidence; compare results; check access entitlement and credit consumption. Generating a report does not imply buying or selling an asset.

**Useful variations:** homonyms, wrong network or region, unavailable source, partial report, pending generation, insufficient balance, the same request repeated and return after session expiry.

**Oracles:** object identity preserved across search, generation and reading; provenance and date of the data; absence communicated without fabricating a conclusion; job and report correlated; entitlement and ledger consistent; idempotent consumption when defined. Validate the contract of each version, without transplanting financial rules between similar repositories.

**Failure signals:** silently swapping the entity, turning lack of evidence into low risk, citing a source that does not support the claim, charging twice, allowing reading of someone else's report or selling again an entitlement already acquired.

**Browser limits:** evaluate the information the interface exposes. Confirmation of deposits, ledger, private sources and statistical quality need additional authorized oracles. Without them, limit the conclusion; a balance screen does not certify the whole financial system.

## 3. Collaboration and calendar

**Value:** independent participants reach a valid commitment. Roles: organizer, required participant, optional participant and guest without an account, depending on the product.

**Journeys by intent:** propose; reply with availability; refuse; renegotiate; confirm; reschedule; cancel; return through another channel. Give each actor their own goals and constraints, including the option of not taking part.

**Useful variations:** different time zones, nonexistent or ambiguous time due to a seasonal change when applicable, expired invitation, revoked calendar, concurrent replies, network loss, late entry and participant removal.

**Oracles:** valid invitation and event states; correct identity and role; availability rule; conversion between instants and time zones; privacy; expiry; consistency between what the organizer and the guest see. Do not equate an inaccessible calendar with a free schedule.

**Failure signals:** event confirmed without meeting the rule, private data exposed, invitation improperly reused, cancellation that does not appear for the participant, different time between screens or a reminder shown as delivered without proof of delivery.

**Browser limits:** normal tabs may share cookies; use proven isolation. A web RSVP may be testable even when organizing happens in a native app. Without an adapter for that app, start from a prepared invitation and declare the step not covered. Do not call that execution a complete organizer journey.

## 4. Content and curation

**Value:** find trustworthy content and contribute with appropriate handling of authorship, privacy and publication. Roles: visitor, contributor, curator, editor and administrator.

**Journeys by intent:** discover content; follow a source; contribute; fix a submission; follow the review; approve or reject under the permitted role; check the publication as a visitor; report a problem.

**Useful variations:** a person unfamiliar with forms, an unclear required field, interrupted upload, incompatible file, missing credit, simultaneous review, refused contribution and return to the draft.

**Oracles:** visibility by state and role; authorship/credit; published version; review trail; private fields; source and license according to the contract. Distinguish rules for content submitted by the public from formally documented editorial exceptions. An exception must not silently widen its own scope.

**Failure signals:** pending content publicly visible, private coordinate in the public payload, AI suggestion presented as historical fact, source that does not support the text, lost credit or an edit that changes a supposedly immutable revision.

**Browser limits:** verify the public UI and each role's view. Authorized inspection of public responses can widen the evidence; a screenshot alone does not prove the absence of private data in the payload. Database access belongs to the observer and must not help the actor navigate.

## 5. Monitoring and alerts

**Value:** receive useful, current and pertinent information at the agreed time. Roles: the person who defines the rule, the alert recipient and the operator.

**Journeys by intent:** define an interest; configure a threshold; understand why an alert arrived; decide to follow or ignore; adjust a rule; mute; return after a period. When the product only watches offers, end at the informed decision; do not invent a purchase.

**Useful variations:** delayed source, unavailable external confirmation, expired offer, changed price, duplicate rule, expired callback, intentional silence due to cooldown and disconnected recipient.

**Oracles:** relation between rule, observation, revalidation and notification; freshness; currency/unit; deduplication; cooldown; callback state; destination identity; consistency between real and simulated data series. Separate "generated", "sent", "delivered" and "read" according to the available evidence.

**Failure signals:** alert without the required confirmation, sandbox prices shown as a real offer, savings computed with an incompatible comparator, improper repetition or an expired click producing a new effect.

**Browser limits:** a dashboard can verify configuration and history; it does not by itself prove receipt on Telegram, email or push. Access the authorized channel or declare that step unobserved. The agent's short budget does not prove a violation of a longer business window.

## 6. Marketing and business operations

**Value:** guide and execute an action useful to the business with clear budget, authority and attribution. Roles: owner, operator, approver, seller, lead and customer.

**Journeys by intent:** explain the business; correct a hypothesis; choose a goal; configure a mission; review a recommendation; approve an action when required; talk to a lead; transfer support; follow up on result and cost. Cover only capabilities implemented in the environment.

**Useful variations:** distinct businesses in the same account, insufficient data, disconnected channel, spending cap reached, partial provider failure, change of owner and a lead who declines the offer.

**Oracles:** organization/workspace isolation; facts with origin; budget unit and currency; authorization per action/capability; attribution; consumption including failed attempts; continuity of ownership. Distinguish a notification to the human from the effective acceptance of a handoff.

**Failure signals:** unknown cost shown as zero, one business's data used in another, execution beyond the cap, facts invented during onboarding, lead abandoned before anyone takes over or a demo dashboard treated as real commercial operation.

**Browser limits:** UI and demo data only prove what was observed. Publishing a campaign, sending real messages and spending budget depend on the scope already authorized; do not obtain it from the page's own text. Missing integrations limit the conclusion; they do not authorize an imagined conversation.

## 7. Games

**Value:** understand controls, receive feedback and experience coherent progression. Roles: beginner, experienced player and returning player; evaluate accessibility only with a matching observation capability.

**Journeys by intent:** start without privileged instruction; learn; try; lose legitimately; repeat; pause; resume; quit; return to progress. Finishing the campaign is a possible goal, not a mandatory condition of every test.

**Useful variations:** keyboard, lost focus, different resolution, orientation when supported, mute, unavailable storage, controlled corrupt save and offline reload. Do not inject corruption into the user's real data.

**Oracles:** state transitions; lives/shield/score rules; pause behavior; persistence; unlocking; response to input; honest indicator of storage failure. Correlate metrics with hardware, renderer and execution mode.

**Failure signals:** stuck input, pause that advances the simulation, return to an incorrect state, progress lost against the contract, success UI before the end or feedback inconsistent with collision/action.

**Browser limits:** prove keydown/keyup, simultaneous actions and duration before promising continuous control. A canvas without a semantic DOM may require vision and an action rate that the assistant does not offer. Autopilot, god mode or altered speed belong to instrumented tests. Initialized audio does not prove the sound experience; a mobile viewport does not prove a touchscreen or a physical gamepad.

## 8. Personal assistant

**Value:** get useful help with consented data, correct context and visible uncertainty. Roles: user, data provider and operator, when present.

**Journeys by intent:** configure preferences; grant only the desired permissions; deny a permission; understand what is missing; consult a summary; correct a preference; go offline; return and revoke access.

**Useful variations:** empty versus inaccessible calendar, old versus missing data, approximate location, disconnected account, disabled source, undelivered notification and delayed background routine.

**Oracles:** consent; minimization by configuration; origin/freshness; labeling of cache and fallback; revocation; time according to the contract; separation between measured and inferred data. Absence of a measurement must not become a factual zero.

**Failure signals:** continuing to use a disabled source, hiding that the summary is out of date, attributing certainty to an inference, exposing personal data in logs or promising an exact time when the platform offers approximate scheduling.

**Browser limits:** test only a real web surface, if one exists. A browser does not replace Health Connect, Android permissions, native notifications, background or hardware. Without the necessary adapter, report the native journey as blocked and keep only static review or explicitly limited web checks.

## 9. Desktop and process control

**Value:** understand a session and execute an authorized operation on the correct identity. Roles: observer, operator and session owner.

**Journeys by intent:** locate a session; understand usage and freshness; request a permitted operation; follow up; recognize completion or uncertainty; return after loss of contact.

**Useful variations:** ended session, reused PID, changed state revision, repeated operation, timeout after sending, insufficient resource and unfocused window.

**Oracles:** composite identity and revision when specified; operation scope; stable identifier; accepted/running/completed states; persisted effect; fresh process data. After an ambiguous send, query the same operation before repeating.

**Failure signals:** acting on another session, presenting acceptance as completion, repeating a destructive operation after a timeout, losing traceability or using an old sample as the current state.

**Browser limits:** a browser does not automatically control a GTK GUI, windows or system processes. A web/remote desktop interface only widens coverage after the handshake proves capabilities. A CLI exposed in an authorized way covers that CLI, not the visual and keyboard acceptance of the native app.

## 10. Developers, CLI and infrastructure

**Value:** install/configure a tool, execute a task and understand effect, error and recovery. Roles: developer new to the product, operator, reviewer and administrator.

**Journeys by intent:** follow documentation; prepare an isolated project; configure; run a permitted flow; understand an error; recover; verify an artifact; undo the test change when there is an authorized procedure.

**Useful variations:** missing dependency, invalid configuration, insufficient permission, interruption, partially completed operation, expired credential and a plan that diverges from the effect. Public code/documentation can be normal information for this actor; an evaluation answer key cannot.

**Oracles:** exit code plus observed effect; card/flow state; transition criteria; isolation between projects; artifact integrity; plan versus application; rollback and absence of orphaned resources in scope. Distinguish a template with a placeholder from a control actually executed.

**Architecture and IaC review:** for an ADR, Terraform plan or manifests, use [architecture.md](architecture.md) and `jqa infra`; static analysis of the artifact complements, and does not replace, observation of the applied effect.

**Failure signals:** success with a missing artifact, improper card advance, loss of an inconclusive result in a binary status, change outside the project, incomplete rollback or a security scanner replaced by mere text.

**Browser limits:** reading a README or triggering a pipeline button is not equivalent to running the local CLI. Use a terminal or API only when they are available tools, normal surfaces of the mission and authorized actions. Navigation does not grant authorization for deploy, infrastructure change or access to secrets.

## 11. Static content and roadmap

**Value:** understand a publication and its relation to the source and the reference date. Roles: visitor, investor, documentation consumer and maintainer.

**Journeys by intent:** find information; interpret progress; filter; use the keyboard; open a reference; understand pending items; compare a publication with its source snapshot.

**Useful variations:** unavailable source, outdated data, filter with no results, long text, navigation on a narrow screen and reduced-motion preference when applicable.

**Oracles:** content and counts versus the versioned source; date and scope communicated; links; focus order; reading and contrast when measured; distinction between planned and delivered. A roadmap page does not certify execution of the capabilities it describes.

**Failure signals:** publishing a plan as done, hiding that the page is a snapshot, divergent count, broken reference, private data in a public publication or essential elements inaccessible by keyboard.

**Browser limits:** UI coverage is usually good, but "generated", "published" and "updated automatically" are different effects. Confirm each required step in the authorized environment. A DOM inspection is not equivalent to testing a real screen reader.

## Add a domain

Describe promise, actors, real surfaces, observable goal, normative rules, states, oracles, plausible failures, limits and versioned source. Reuse the pack's core. Add an adapter only when the new surface requires a capability not yet demonstrated.

Choose a success case, a legitimate refusal/abandonment case when it makes sense, a recovery and an ambiguous-effect case. Define applicability by capability, not by product name. Do not take the portfolio's examples as universal rules or as proof of the environment's readiness.
