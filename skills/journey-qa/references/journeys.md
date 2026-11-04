# Catalog of journeys and variations

Select intents by the product's value and by the risks. Do not run the whole table by default and do not require capabilities the product does not promise.

## Index

1. [Assemble the journey](#1-assemble-the-journey)
2. [Common journeys](#2-common-journeys)
3. [Product families](#3-product-families)
4. [Risk variations](#4-risk-variations)
5. [Test campaigns](#5-test-campaigns)
6. [Closing and regression](#6-closing-and-regression)

## 1. Assemble the journey

Fill in the run's `scenario.json` (created by `jqa init`). Define:

- Value goal and participating roles.
- Initial state, data and permissions needed.
- Facts known by each actor and termination conditions.
- Surface and capability required of the assistant.
- Mandatory rules, oracles and expected evidence.
- Chosen variation, deadline, budget and authorized actions.
- Limits that will prevent broader conclusions.

Give the actor only intent, context and constraints. Keep criteria and verification paths in the observer's context. Describe the task without revealing the buttons or the correct sequence.

Example of an intent: "You want to organize a one-hour meeting next week and need two people to attend." The analyst separately defines the rules for invitation, time zone, availability and confirmation.

## 2. Common journeys

| Journey | Actor's intent | Possible checks |
| --- | --- | --- |
| Discover | Understand what the product offers and whether it fits the need. | Clarity of the offer, limitations, identity, trust and next steps. |
| Compare | Choose a suitable option. | Consistent criteria, total price, real differences and missing information. |
| Sign in | Create an account or access an existing one. | Validation, authentication, recovery, understandable errors and session state. |
| Configure | Prepare preferences, business, team or integration. | Field values, saving, permissions, tenant separation and resume. |
| Get value | Perform the product's main task. | Correct result, quality, ownership, persistence and final effect. |
| Converse | Clarify a doubt or resolve a need with another actor. | Delivery, context, understanding, authority, handoff and closing. |
| Collaborate | Coordinate decisions with other people. | Visibility, independent replies, conflict, deadline and shared state. |
| Recover | Continue after an error, network loss or ambiguous attempt. | Preserved state, correction, dedupe, resend and understanding of the problem. |
| Return | Come back after leaving, closing or waiting. | Persistence, validity, updated information and continuity. |
| End | Cancel, refuse, leave, return or delete, when it exists. | Correct effect, clarity of consequences and remaining obligations. |

A complete journey can cross several of these moments. The test must last long enough to observe the defined termination or to record clearly the pending part.

## 3. Product families

Use the families below as a starting point. Their names do not restrict new products and do not attest that features are implemented in the user's repositories.

### Commerce, stores, subscriptions and credits

- **Intent:** find a suitable option, clarify terms, choose, pay in an authorized environment and use what was acquired. **Roles:** buyer, support agent, administrator; include others only when needed.
- **Variations:** insufficient budget, invalid coupon, changed stock, corrected address, ambiguous payment, canceled subscription, expired entitlement, permitted return or the choice not to buy.
- **Oracles:** correct item and variant; total price and currency; quantity; order identity; payment confirmation; balance or entitlement; absence of duplication; applicable policy; persistence after return.

Do not use a "success" screen as the only proof of charge, settlement, delivery or access. Do not make a real purchase without authorization that covers the case.

### SaaS and business onboarding

- **Intent:** configure the minimum necessary and get the first useful result. **Roles:** new user, business owner, administrator, member and another test tenant.
- **Variations:** partial data, pending invitation, changed preference, denied integration, unsuitable plan, limited budget, interrupted session and return.
- **Oracles:** onboarding produces a usable state; configuration does not leak between organizations; correct permissions; plan and budget rules; clear indication of a missing integration; saving and continuity.

Separate demo screens, planned capabilities and operational actions. Completing a form does not prove business activation.

### Scheduling and collaboration

- **Intent:** reach a valid shared decision, including refusal or counterproposal. **Roles:** organizer, guests with independent constraints and a test observer when pertinent.
- **Variations:** different time zone, infeasible time, unknown calendar, revoked permission, absent required participant, expired invitation and changed proposal.
- **Oracles:** appropriate visibility; private data preserved; ordering of events; quorum or confirmation rules; invitation validity; consistency between screens and notifications.

Do not interpret an unknown calendar as free. Do not confuse sending an invitation with a confirmed meeting.

### Content, public research and curation

- **Intent:** find trustworthy content, contribute and follow how the contribution is handled. **Roles:** visitor, contributor, curator and editor, depending on the product.
- **Variations:** missing source, incomplete submission, requested correction, refused approval, private content, sensitive location, search with no result and difficult language.
- **Oracles:** match with sources; visibility by state and role; publication according to the rule; editorial trail; preservation of private data; understanding of the experience by people with little digital familiarity.

Apply editorial exceptions only to the scope in which they were defined. Do not extend them automatically to contributions from the public.

### Alerts, monitoring and opportunities

- **Intent:** define an interest, receive valid information at a useful moment and decide the next step. **Roles:** a person following a condition and the observed issuing service.
- **Variations:** no opportunity, old data, almost-met condition, edited rule, repetition, unavailable provider, expired window and cancellation of the follow.
- **Oracles:** correct condition; source and freshness; revalidation when required; dedupe; cooldown; destination; callback signature or validity when applicable; continuity of history.

Do not add a purchase to a product whose value ends at following or alerting. Sandbox data does not demonstrate a real market opportunity.

### Analysis, reports and AI assistance

- **Intent:** get information suited to the question and recognize what it allows one to conclude. **Roles:** the user who decides, the data source and the observed producing system.
- **Variations:** ambiguous entity, lack of data, contradictory source, partial report, delay, out-of-scope question, malicious instruction in external content and return to an existing report.
- **Oracles:** entity identity; coverage and provenance; supported facts; calculations; transparency about absence; proportional confidence; consistency between summary and detail; useful conclusion without extrapolating the evidence.

Separate textual quality from factual correctness and from usefulness. Do not require an assertive answer when the correct conclusion is insufficient data.

### Communication, leads and support

- **Intent:** clarify a need and get an appropriate referral. **Roles:** customer or lead, support agent, person responsible for the business and handoff recipient.
- **Variations:** objection, changed preference, ambiguous language, late reply, unavailability, lack of authority, transfer, refusal and return on another supported channel.
- **Oracles:** message actually received; maintained context; no repetition of questions already answered; authorized promises; responsibility after handoff; continuity and closing.

A queued message is not a read message; a transfer notice is not acceptance by the new owner. Do not invent an absent counterpart.

### Games and interactive experiences

- **Intent:** understand controls, play, deal with failure, pause and return. **Roles:** beginner or experienced player depending on the goal.
- **Variations:** legitimate loss, focus change, restart, remapped controls, no audio, short session, progress continuity and offline mode when it exists.
- **Oracles:** response to controls; feedback; progression; pause; persistence; win and lose states; errors and performance measured with a suitable instrument.

Validate real support for held and simultaneous keys. Autopilot, invincibility, acceleration and hooks do not prove human learning or skill. Do not declare a sound test without observing the audio.

### Native assistants, desktop and operations

- **Intent:** get a result, observe a session or perform a permitted operation. **Roles:** user, administrator or operator with appropriate knowledge.
- **Variations:** denied permission, old data, offline, wrong session, accepted request still running, timeout, cancellation and resume.
- **Oracles:** consent; identity; freshness; completion by identifier; persistence; correct effect; rollback when defined and authorized.

The browser covers only the available web surfaces. If the goal depends on native Android, Linux windows, CLI, hardware or the operating system, check for an additional adapter or declare the part blocked. Do not simulate in text the execution of these resources.

### Developer tools, API, infrastructure and publications

- **Intent:** understand, configure, consume or verify a resource according to the role's normal surface. **Roles:** developer, operator, reader or maintainer.
- **Variations:** incomplete documentation, missing dependency, outdated example, insufficient permission, failing command, old snapshot and data diverging from the source.
- **Oracles:** instructions executable within the permitted scope; contract and result; error codes; environment identity; limits; match between publication and versioned source.

Reading documentation in the browser is not equivalent to running a CLI or applying infrastructure. A template, a placeholder and a reference fork must not receive a certificate of real operation.

## 4. Risk variations

Combine selectively, avoiding a Cartesian product:

| Axis | Useful variations |
| --- | --- |
| Knowledge | First visit, returning user, specialist, product change. |
| Data | Valid, incomplete, ambiguous, old, duplicate, permitted boundary. |
| Time | Before/after the deadline, expired session, asynchronous reply, different time zone. |
| Identity | Permitted role, role without permission, another account or test tenant. |
| Continuity | Refresh, back, close/reopen, session loss, ambiguous retry. |
| Dependency | Available, slow, unavailable or simulated in a declared way. |
| Decision | Accept, ask, refuse, suspend, abandon and return. |
| Access | Keyboard, focus, zoom, form error and observed viewport. |

Inject failures only through authorized mechanisms. If the condition cannot be produced, keep the scenario as not executed; do not narrate an imagined result.

## 5. Test campaigns

- **Initial exploration:** discover value and run a short journey to a verifiable result.
- **Complete journey:** cross discovery, sign-in, value, interaction and return/closing when applicable.
- **Specific risk:** concentrate on one high-impact rule, permission, value or recovery.
- **Multi-actor:** observe coordination between identities and contexts that are effectively separate.
- **Regression:** repeat the precise conditions of a confirmed defect, preferably with deterministic evidence.
- **Host compatibility:** verify the capabilities that a new assistant or connector actually offers.

A short campaign does not allow saying that the whole product was validated. Report the selected scenarios, the relevant exclusions and the coverage of the mandatory rules.

## 6. Closing and regression

End each journey at the defined termination or when a dependency, limit or cancellation prevents continuing. Preserve `run_status`, `actor_outcomes` and `qa_verdict` separately.

Convert confirmed defects into reproducible cases with environment, data, expectation, observation and proof. Send them to the project's flow; do not silently change its gates or approval authority.

To validate the pack's universality, select different families, including one not used to build the core. A missing capability must produce a correct limit, never a fictitious success.
