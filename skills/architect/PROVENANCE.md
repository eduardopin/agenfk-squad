# Provenance

Architect plans a new app, stack or multi-front feature before anything is built:
architecture, contracts, a resource -> PR table, and optimistic/likely/conservative
forecasts computed from a task DAG.

| | |
|---|---|
| Origin | the repository owner's own work; first generated with ChatGPT on 2026-10-23, reworked since |
| Version | 1.6.0 |
| Licence | the repository's licence; no third-party code |
| Language | defined in English, with pt-BR trigger phrases; the rendered map is English only |
| Scripts | `plan.py`, `delivery_map.py`, `handoff.py`, `fleet.py`, `fanout.py`: Python standard library only |

## What the public port changed

- The skill is named `architect` (it was `arquiteto` in the private copy); planning
  files go to `docs/architect/`.
- `references/host-integration.md` replaces the private host file. It describes the
  same routing for any host that has an infra skill, the `product` agent and a card
  board.
- The private change log, the trial table that named private cards and the install
  notes for one workstation were dropped.
- The script tests moved to `tests/test_architect.py`, with package-layout and
  public-hygiene checks added.
