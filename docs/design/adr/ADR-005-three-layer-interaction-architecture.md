# ADR-005 - Three-Layer Interaction Architecture

Status: Superseded by ADR-006

Date: 2026-09-10

Reconciled: 2026-10

## Reconciliation note (2026-10)

ADR-006 replaces this ADR. The layer idea survives and now spans packages. The
rest is withdrawn.

- **The layer model survives.** Surface, core and data are still the three
  layers, defined by the direction of interaction. In ADR-006 they are the
  `apps/`, the logic packages (`odoo_dev_core`, `dispatcher`) and the data
  packages (`odoo_client`, `odoo_dev_data`, `usage_log`). Root import-linter
  contracts in `pyproject.toml` enforce them across the nine packages.
- **The frozen-test-suite rule is withdrawn.** This ADR kept 124 deep-importing
  test files unchanged as a regression oracle. That rule led to shim modules,
  pinned lazy fallbacks and adapter bodies that could not move. Tests now move
  with the module they test.
- **The shims, the baseline-debt register and the amendment policy are gone.**
  There is no `utilities/`, `tracking/`, `services/` or `state/` package in
  `odoo_client`. That package holds the Odoo RPC client and nothing else.
- **The `bootstrap` composition root stayed.** Each app has one `bootstrap`
  module, and it is the only module of the app that names data constructors.
- **The ports stayed.** `odoo_dev_core.ports` holds the structural protocols and
  the frozen `Services` bundle. The contracts forbid core from naming a data
  constructor.

The text below is the 2026-09 record of the original decision, shortened to the
parts that explain the current design.

## Context

The SDK grew phase by phase without a stated layering model. An audit found a
structure that was close to a clean three-layer shape: no core-to-surface edge,
no data-to-surface edge, three mutually independent frontends. It also found a
bounded set of deviations: surface-to-data type imports, resync logic copied
into the CLI, two data-to-core imports, and internal root-imports that formed an
import cycle. Nothing enforced the model, so any new module could break it.

## Decision

### Layer model

Three layers and a small shared kernel, defined by the direction of interaction:

- **Surface**: frontends that parse input, format output and dispatch into
  core. They own no business logic and no persistence.
- **Core**: the application layer. The command registry and its builtin
  commands, billing and reporting workflows, sessionization transforms.
- **Data**: everything that talks to an external system or owns persistence.
  The RPC transport stack, local SQLite state and configuration, and the
  external-source adapters.
- **Shared kernel**: small dependency-free helpers and cross-layer vocabulary
  that any layer may import.

### The rules

1. Surfaces are mutually independent. CLI, MCP and TUI never import each other.
2. Nothing below a surface imports a surface.
3. Data does not import core, with two permanent exceptions for the event
   vocabulary.
4. Surfaces reach data only through core. The composition roots are the permanent
   exceptions, because someone must construct the object graph.
5. No internal root-imports. Inside the package, `from odoo_client import ...` is
   banned. It re-enters the partly initialized package and caused the cycle.

ADR-006 keeps rules 1 to 4 in the form of the four root contracts. Rule 5 stayed
a local convention of `odoo_client`.

### Enforcement

The rules were import-linter contracts in `pyproject.toml`. Every
`ignore_imports` entry was either marked `permanent` or named the issue that
removes it. `unmatched_ignore_imports_alerting = "error"` made a stale entry fail
the build, so the exception list could only shrink. Adding an exception needed an
amendment of this ADR in the same pull request. ADR-006 keeps the first two
mechanisms and drops the amendment policy.

### Amendments to the original decision

Four later amendments (2026-09) made these changes. They are the origin of the
current design.

- A single composition root, `bootstrap`, assembled the default graph. Config was
  injected, not loaded inside a command.
- Connection settings moved into a shared-kernel module, `settings.py`, so the
  transport stopped importing the state layer. This module is still the home of
  `OdooConnectionSettings` in `odoo_client`.
- A complete port set gave core one structural protocol for each external system:
  the RPC client, the state store, the git and GitHub gateways, and a read-only
  settings view.
- The recordset and the domain types were promoted from data to core. The public
  API is recordset-first, so a port that returns a recordset returns a core type.
- The packaged consulting skills were removed (2026-09-25). The `odoo-dev` plugin
  owns that text. The SDK owns tools only.

## Rejected alternatives

- **A big-bang move into `surface/`, `core/` and `data/` directories.** Rejected
  then because of the deep-importing tests. ADR-006 achieved the split by
  packages instead.
- **Shim packages over the old layout.** Rejected: two names for every module and
  no enforcement value.
- **Enforcement only, with no ADR.** Rejected: the ignore list means nothing
  without the layer definitions.
- **A fourth domain-models layer.** Rejected: the vocabulary is small and lives
  where it is used.
- **The registry as the only gateway from surface to core.** Kept as a
  convention, because a module contract cannot express call-through-registry.

## Deliberately not adopted

- **Unit of Work.** No operation spans several aggregates.
- **Message bus.** The command registry already is the dispatcher.
- **DI container.** The object graph is a few constructors, and a container would
  hide the wiring from the import linter.
- **A repository over the RPC port.** It would re-invent the recordset.
- **Full DDD vocabulary.** The domain is transformational, not stateful.
