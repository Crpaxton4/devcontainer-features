# Changelog

## [4.0.3](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v4.0.2...personal-features-v4.0.3) (2026-07-20)


### Bug Fixes

* **mcp:** make start_task branch setup atomic and attach-safe ([#578](https://github.com/Crpaxton4/devcontainer-features/issues/578)) ([20e9e5a](https://github.com/Crpaxton4/devcontainer-features/commit/20e9e5a67d35debb0d074c446d3d0005796c75c5))
* **mcp:** restore chatter in implement_task and re-sync ported skill bodies ([#567](https://github.com/Crpaxton4/devcontainer-features/issues/567)) ([c9e5694](https://github.com/Crpaxton4/devcontainer-features/commit/c9e569495f5ea6696246e6434682796bfb5f2a35)), closes [#514](https://github.com/Crpaxton4/devcontainer-features/issues/514) [#528](https://github.com/Crpaxton4/devcontainer-features/issues/528) [#543](https://github.com/Crpaxton4/devcontainer-features/issues/543)
* **personal-features:** correct hook MCP exclusion glob and de-vacuum Feature tests ([#570](https://github.com/Crpaxton4/devcontainer-features/issues/570)) ([034a326](https://github.com/Crpaxton4/devcontainer-features/commit/034a326390f82de3f5cec7b86604071bef973f75)), closes [#529](https://github.com/Crpaxton4/devcontainer-features/issues/529) [#551](https://github.com/Crpaxton4/devcontainer-features/issues/551) [#552](https://github.com/Crpaxton4/devcontainer-features/issues/552) [#533](https://github.com/Crpaxton4/devcontainer-features/issues/533) [#554](https://github.com/Crpaxton4/devcontainer-features/issues/554)
* **personal-features:** put odoo-sdk on PATH and wire up mempalace persistence ([#569](https://github.com/Crpaxton4/devcontainer-features/issues/569)) ([da00290](https://github.com/Crpaxton4/devcontainer-features/commit/da00290bb1bd48007f87dcb6d3b52f9432e08ccc))
* **personal-features:** restore pandoc feature and clear config drift ([#583](https://github.com/Crpaxton4/devcontainer-features/issues/583)) ([dc69efa](https://github.com/Crpaxton4/devcontainer-features/commit/dc69efa2d1afd6c406869796f49596f452f2b118)), closes [#521](https://github.com/Crpaxton4/devcontainer-features/issues/521) [#534](https://github.com/Crpaxton4/devcontainer-features/issues/534) [#537](https://github.com/Crpaxton4/devcontainer-features/issues/537) [#539](https://github.com/Crpaxton4/devcontainer-features/issues/539)
* **sdk:** convert positional args to named JSON-2 body fields ([#562](https://github.com/Crpaxton4/devcontainer-features/issues/562)) ([87904df](https://github.com/Crpaxton4/devcontainer-features/commit/87904df1b80c1e4f71268c77527b4f3b528d95e7)), closes [#518](https://github.com/Crpaxton4/devcontainer-features/issues/518)
* **sdk:** drop contentless chatter markers and fix task_status scoping claim ([#572](https://github.com/Crpaxton4/devcontainer-features/issues/572)) ([c94459e](https://github.com/Crpaxton4/devcontainer-features/commit/c94459e9484d05be1cb07824a8275f8d34236416)), closes [#505](https://github.com/Crpaxton4/devcontainer-features/issues/505) [#536](https://github.com/Crpaxton4/devcontainer-features/issues/536)
* **sdk:** make stop_task description optional and fix stale docstrings ([#560](https://github.com/Crpaxton4/devcontainer-features/issues/560)) ([26c0122](https://github.com/Crpaxton4/devcontainer-features/commit/26c0122ef5d17ef160048fd041a503d1983340e7)), closes [#482](https://github.com/Crpaxton4/devcontainer-features/issues/482) [#535](https://github.com/Crpaxton4/devcontainer-features/issues/535) [#549](https://github.com/Crpaxton4/devcontainer-features/issues/549)
* **sdk:** report run elapsed from sessionization and name derived-only rows ([#579](https://github.com/Crpaxton4/devcontainer-features/issues/579)) ([29797ed](https://github.com/Crpaxton4/devcontainer-features/commit/29797ed6f4c452c4a41353f83550f9f4fc26d731)), closes [#506](https://github.com/Crpaxton4/devcontainer-features/issues/506) [#511](https://github.com/Crpaxton4/devcontainer-features/issues/511)
* **sdk:** send flat ids and keyword fields in merge_timesheets read ([#559](https://github.com/Crpaxton4/devcontainer-features/issues/559)) ([133dffe](https://github.com/Crpaxton4/devcontainer-features/commit/133dffee80fb69d0457cd1ca8c2d2b186c4e317c)), closes [#515](https://github.com/Crpaxton4/devcontainer-features/issues/515) [#544](https://github.com/Crpaxton4/devcontainer-features/issues/544) [#556](https://github.com/Crpaxton4/devcontainer-features/issues/556)
* **sessionization:** resolve `comment` source in the Python engine ([#587](https://github.com/Crpaxton4/devcontainer-features/issues/587)) ([6f5ec80](https://github.com/Crpaxton4/devcontainer-features/commit/6f5ec80486b77fdc0dc9b6539cc141c46a627c9a)), closes [#516](https://github.com/Crpaxton4/devcontainer-features/issues/516) [#527](https://github.com/Crpaxton4/devcontainer-features/issues/527) [#546](https://github.com/Crpaxton4/devcontainer-features/issues/546)
* **sessionization:** resolve timesheet employee at export time and drop Odoo-derived CSV columns ([#568](https://github.com/Crpaxton4/devcontainer-features/issues/568)) ([5e7b278](https://github.com/Crpaxton4/devcontainer-features/commit/5e7b27827d24af6aa994e70310e9fd373661cb59)), closes [#497](https://github.com/Crpaxton4/devcontainer-features/issues/497) [#498](https://github.com/Crpaxton4/devcontainer-features/issues/498) [#499](https://github.com/Crpaxton4/devcontainer-features/issues/499)
* **state:** close tracker connections, drop the NUL repo sentinel ([#571](https://github.com/Crpaxton4/devcontainer-features/issues/571)) ([f1004cd](https://github.com/Crpaxton4/devcontainer-features/commit/f1004cdb71cddf82eb84e0da4377346cc80dffd4)), closes [#495](https://github.com/Crpaxton4/devcontainer-features/issues/495) [#508](https://github.com/Crpaxton4/devcontainer-features/issues/508) [#555](https://github.com/Crpaxton4/devcontainer-features/issues/555) [#550](https://github.com/Crpaxton4/devcontainer-features/issues/550)
* **state:** load TOML config on Python 3.10 via tomli fallback ([#566](https://github.com/Crpaxton4/devcontainer-features/issues/566)) ([b008d3a](https://github.com/Crpaxton4/devcontainer-features/commit/b008d3a11b27f3caab6cff7e6c761c4351e883b3))
* **tui:** match triage series regex to the ISO tick-id producer ([#563](https://github.com/Crpaxton4/devcontainer-features/issues/563)) ([5f50aad](https://github.com/Crpaxton4/devcontainer-features/commit/5f50aadcdcd0cf02ae5d13e491ecf90e14ef24d6)), closes [#517](https://github.com/Crpaxton4/devcontainer-features/issues/517) [#526](https://github.com/Crpaxton4/devcontainer-features/issues/526)

## [4.0.2](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v4.0.1...personal-features-v4.0.2) (2026-07-20)


### Bug Fixes

* **deps:** sync uv.lock and keep it in sync across releases ([#502](https://github.com/Crpaxton4/devcontainer-features/issues/502)) ([f29d196](https://github.com/Crpaxton4/devcontainer-features/commit/f29d196c8aeaf4f5819bc5041f48b180fe696ff5))

## [4.0.1](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v4.0.0...personal-features-v4.0.1) (2026-07-20)


### Bug Fixes

* **deps:** batch dependabot updates 2026-07-20 ([#500](https://github.com/Crpaxton4/devcontainer-features/issues/500)) ([e9285a9](https://github.com/Crpaxton4/devcontainer-features/commit/e9285a98a301a4c2305c4fc10159e7422899357a))

## [4.0.0](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v3.0.0...personal-features-v4.0.0) (2026-07-17)


### ⚠ BREAKING CHANGES

* **state:** existing tracker.db files must run the rebuild migration (via setup.sh / init_tracker_db.py) to move to the STRICT schema; a DB containing rows that fail the new validation aborts the migration until those rows are fixed.
* **sdk:** `start_task` writes no timesheet and returns `timesheet_id: null`. A task no longer gets a 0-hour anchor row on start; the first `account.analytic.line` row for a task is created by the upload path when it bills the derived session. Behaviour for reconcile is unchanged for existing anchors (still adopted), but new work never produces an anchor to adopt.

### Features

* **mcp:** add search_count aggregate tool ([#460](https://github.com/Crpaxton4/devcontainer-features/issues/460)) ([a2a0c6f](https://github.com/Crpaxton4/devcontainer-features/commit/a2a0c6ffcda4038334b5ce71c9c87f69581b5b32)), closes [#445](https://github.com/Crpaxton4/devcontainer-features/issues/445)
* **mcp:** decorator registries for prompts and composition tools ([#432](https://github.com/Crpaxton4/devcontainer-features/issues/432)) ([45af6cb](https://github.com/Crpaxton4/devcontainer-features/commit/45af6cb63c186420c1702c0d49f328cb25522aee)), closes [#410](https://github.com/Crpaxton4/devcontainer-features/issues/410)
* **mcp:** expose personal-features skills as MCP prompts ([#464](https://github.com/Crpaxton4/devcontainer-features/issues/464)) ([222ab9d](https://github.com/Crpaxton4/devcontainer-features/commit/222ab9dea27762535eda0a24a979d2634b33cf41)), closes [#455](https://github.com/Crpaxton4/devcontainer-features/issues/455)
* **personal-features:** add fibonacci-estimate skill ([#456](https://github.com/Crpaxton4/devcontainer-features/issues/456)) ([be0fdf0](https://github.com/Crpaxton4/devcontainer-features/commit/be0fdf0e535bf5e2779852187a2da655445267e4)), closes [#450](https://github.com/Crpaxton4/devcontainer-features/issues/450)
* **sdk:** stop start_task writing Odoo timesheets — sessionization is sole timelog source ([#443](https://github.com/Crpaxton4/devcontainer-features/issues/443)) ([2ed3446](https://github.com/Crpaxton4/devcontainer-features/commit/2ed34465aed9f4fe522e911999344b6888fdab52)), closes [#329](https://github.com/Crpaxton4/devcontainer-features/issues/329)


### Bug Fixes

* **cli:** route stop/stop-all through StopTaskCommand ([#423](https://github.com/Crpaxton4/devcontainer-features/issues/423)) ([621d919](https://github.com/Crpaxton4/devcontainer-features/commit/621d91991064208ab49b43040778d0b093929c20)), closes [#402](https://github.com/Crpaxton4/devcontainer-features/issues/402) [#403](https://github.com/Crpaxton4/devcontainer-features/issues/403)
* **personal-features:** conform odoo-quote to in-house estimate standard ([#457](https://github.com/Crpaxton4/devcontainer-features/issues/457)) ([429b1c7](https://github.com/Crpaxton4/devcontainer-features/commit/429b1c7086345aba049d9bd3ac2e588817c42d20))
* **personal-features:** harden uv installs against network timeouts ([#431](https://github.com/Crpaxton4/devcontainer-features/issues/431)) ([91bf6b5](https://github.com/Crpaxton4/devcontainer-features/commit/91bf6b5acd1629a571e7239e4af188e123b7f78b)), closes [#401](https://github.com/Crpaxton4/devcontainer-features/issues/401)
* **sdk:** correctness and consistency fixes from simplification review ([#436](https://github.com/Crpaxton4/devcontainer-features/issues/436)) ([39f879b](https://github.com/Crpaxton4/devcontainer-features/commit/39f879b57e0207a435c72bfd3a54966913e64eeb)), closes [#421](https://github.com/Crpaxton4/devcontainer-features/issues/421)
* **sdk:** fork start_task branch from fetched origin tip, not stale local base ([#461](https://github.com/Crpaxton4/devcontainer-features/issues/461)) ([87a7b36](https://github.com/Crpaxton4/devcontainer-features/commit/87a7b361ecf5ff51185ba17187c14e6f381c4981)), closes [#454](https://github.com/Crpaxton4/devcontainer-features/issues/454)
* **sdk:** pass body_is_html=True so chatter notes render as HTML ([#458](https://github.com/Crpaxton4/devcontainer-features/issues/458)) ([33d1bfb](https://github.com/Crpaxton4/devcontainer-features/commit/33d1bfb4bff13ab31b7cdee0a703ee4cd33e9026)), closes [#453](https://github.com/Crpaxton4/devcontainer-features/issues/453)
* **sdk:** stop knowledge tools probing ir.model, classify errors instead ([#462](https://github.com/Crpaxton4/devcontainer-features/issues/462)) ([435f6ed](https://github.com/Crpaxton4/devcontainer-features/commit/435f6ed80e97fa65ce69026a323459cc50e93470)), closes [#444](https://github.com/Crpaxton4/devcontainer-features/issues/444)
* **state:** STRICT typed tracker schema with write-time validation ([#463](https://github.com/Crpaxton4/devcontainer-features/issues/463)) ([0fee768](https://github.com/Crpaxton4/devcontainer-features/commit/0fee76806b76d4fad0c9abb9aee7e118260248ee)), closes [#452](https://github.com/Crpaxton4/devcontainer-features/issues/452)
* **tui:** translate agentless repo sentinel so odoo-tui stops crashing ([#459](https://github.com/Crpaxton4/devcontainer-features/issues/459)) ([7cd014e](https://github.com/Crpaxton4/devcontainer-features/commit/7cd014ed4734f1fcdaf16f86c10fd9059ebb8943)), closes [#451](https://github.com/Crpaxton4/devcontainer-features/issues/451)

## [3.0.0](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v2.1.0...personal-features-v3.0.0) (2026-07-15)


### ⚠ BREAKING CHANGES

* **sdk:** central host-persisted tracker database ([#388](https://github.com/Crpaxton4/devcontainer-features/issues/388))

### Features

* **mcp:** add read-only get_mail_status tool for outgoing mail verification ([#391](https://github.com/Crpaxton4/devcontainer-features/issues/391)) ([3de95bd](https://github.com/Crpaxton4/devcontainer-features/commit/3de95bd4b052e247e616b92c7c7e0f8036e95860)), closes [#389](https://github.com/Crpaxton4/devcontainer-features/issues/389)
* **mcp:** read-only unlogged-time gap report ([#397](https://github.com/Crpaxton4/devcontainer-features/issues/397)) ([769a4d3](https://github.com/Crpaxton4/devcontainer-features/commit/769a4d36d28465e709bb80b438e883ba85050044))
* **mcp:** require tests and nudge note cadence in implement_task workflow ([#390](https://github.com/Crpaxton4/devcontainer-features/issues/390)) ([57c3f72](https://github.com/Crpaxton4/devcontainer-features/commit/57c3f72d9567e535ffec8219ea2fbb6bf03f1e22)), closes [#386](https://github.com/Crpaxton4/devcontainer-features/issues/386) [#387](https://github.com/Crpaxton4/devcontainer-features/issues/387)
* **sdk:** add headless odoo-sdk upload sharing the TUI reconcile path ([#382](https://github.com/Crpaxton4/devcontainer-features/issues/382)) ([0829097](https://github.com/Crpaxton4/devcontainer-features/commit/0829097623164a6142ce6a6a773dfb06e90963f6)), closes [#354](https://github.com/Crpaxton4/devcontainer-features/issues/354)
* **sdk:** add odoo-sdk prune with an un-uploaded-session guard ([#384](https://github.com/Crpaxton4/devcontainer-features/issues/384)) ([f3f5a2f](https://github.com/Crpaxton4/devcontainer-features/commit/f3f5a2f62293b076c1879849aabddeec4ca1f226)), closes [#363](https://github.com/Crpaxton4/devcontainer-features/issues/363)
* **sdk:** apply configurable per-session minimum and rounding at upload ([#383](https://github.com/Crpaxton4/devcontainer-features/issues/383)) ([0a98e6d](https://github.com/Crpaxton4/devcontainer-features/commit/0a98e6d4f19e446d69c32840abeeea8aa8db2f2b)), closes [#355](https://github.com/Crpaxton4/devcontainer-features/issues/355)
* **sdk:** central host-persisted tracker database ([#388](https://github.com/Crpaxton4/devcontainer-features/issues/388)) ([b8094ec](https://github.com/Crpaxton4/devcontainer-features/commit/b8094ec12e12eed5a437684f03be82e85d2111f4))
* **sdk:** derive review-family events as windowed sessions ([#396](https://github.com/Crpaxton4/devcontainer-features/issues/396)) ([c356390](https://github.com/Crpaxton4/devcontainer-features/commit/c356390a3773990ffcaa170dc1c26b1e37f9d994))
* **sdk:** ingest Google Calendar and sent mail as resync event sources ([#395](https://github.com/Crpaxton4/devcontainer-features/issues/395)) ([cc278d2](https://github.com/Crpaxton4/devcontainer-features/commit/cc278d22e14237b9b63ae7cc676e849f9460c373))
* **sdk:** reap stale runs and stop attaching events to them ([#394](https://github.com/Crpaxton4/devcontainer-features/issues/394)) ([a6ba0dd](https://github.com/Crpaxton4/devcontainer-features/commit/a6ba0ddf3be7ab152ea809c27625755e56ae42c3))
* **sdk:** widen resync capture and fix task-id extraction accuracy ([#399](https://github.com/Crpaxton4/devcontainer-features/issues/399)) ([6c418cc](https://github.com/Crpaxton4/devcontainer-features/commit/6c418cc8ef7e551bf2e9a40b3bbfdca714216f16))
* **tui:** review surface with Odoo-line overlap, cross-task badges, and evidence ([#398](https://github.com/Crpaxton4/devcontainer-features/issues/398)) ([363beab](https://github.com/Crpaxton4/devcontainer-features/commit/363beabc66478f0b8c2957288ee8cfd94ad7a6bc))
* **tui:** triage queue assigning unattributed events to tasks ([#392](https://github.com/Crpaxton4/devcontainer-features/issues/392)) ([0c60bea](https://github.com/Crpaxton4/devcontainer-features/commit/0c60bea89e532696d4406dcea9bfea2fa9670c3a)), closes [#370](https://github.com/Crpaxton4/devcontainer-features/issues/370)


### Bug Fixes

* **mcp:** persist only tool name and task id in dispatch events ([#374](https://github.com/Crpaxton4/devcontainer-features/issues/374)) ([04e8ba1](https://github.com/Crpaxton4/devcontainer-features/commit/04e8ba100292e8903875a3104816d2ae67403f4f)), closes [#365](https://github.com/Crpaxton4/devcontainer-features/issues/365)
* **sdk:** abort_task closes the Odoo anchor and aborted runs never bill ([#385](https://github.com/Crpaxton4/devcontainer-features/issues/385)) ([a41348c](https://github.com/Crpaxton4/devcontainer-features/commit/a41348c8bd82cb31c15f7d218c827af2bf745c49)), closes [#356](https://github.com/Crpaxton4/devcontainer-features/issues/356)
* **sdk:** enable SQLite WAL and busy_timeout to stop silent event drops ([#373](https://github.com/Crpaxton4/devcontainer-features/issues/373)) ([b6b0d7c](https://github.com/Crpaxton4/devcontainer-features/commit/b6b0d7cffc874859961f297828320622276a2818)), closes [#357](https://github.com/Crpaxton4/devcontainer-features/issues/357)
* **sdk:** fan multi-task events into every task's session and stop misflagging releases ([#381](https://github.com/Crpaxton4/devcontainer-features/issues/381)) ([bf0835b](https://github.com/Crpaxton4/devcontainer-features/commit/bf0835bd6930363076474c30ac57885c41c1c071)), closes [#362](https://github.com/Crpaxton4/devcontainer-features/issues/362)
* **sdk:** make start_task chatter post best-effort so failures cannot wedge a run ([#375](https://github.com/Crpaxton4/devcontainer-features/issues/375)) ([1a51545](https://github.com/Crpaxton4/devcontainer-features/commit/1a515458753e86b5d5f407285c857135691d9601)), closes [#361](https://github.com/Crpaxton4/devcontainer-features/issues/361)
* **sdk:** normalize query window bounds to stored +00:00 timestamp form ([#376](https://github.com/Crpaxton4/devcontainer-features/issues/376)) ([7d47527](https://github.com/Crpaxton4/devcontainer-features/commit/7d475272b6b96dd580d489f0a30009101eca2229)), closes [#360](https://github.com/Crpaxton4/devcontainer-features/issues/360)
* **sdk:** partition sessions by task only, sweep orphaned uploads, prefilter derivation window ([#380](https://github.com/Crpaxton4/devcontainer-features/issues/380)) ([87fbbaa](https://github.com/Crpaxton4/devcontainer-features/commit/87fbbaac6aeb4cb0650104183b527da63a9eddc4)), closes [#352](https://github.com/Crpaxton4/devcontainer-features/issues/352) [#353](https://github.com/Crpaxton4/devcontainer-features/issues/353) [#359](https://github.com/Crpaxton4/devcontainer-features/issues/359)
* **test:** align odoo scenario tracker checks with the host-provisioned central DB ([#393](https://github.com/Crpaxton4/devcontainer-features/issues/393)) ([f563b0d](https://github.com/Crpaxton4/devcontainer-features/commit/f563b0d298d68e6084c74f9ea524c20b01e1445f))


### Performance Improvements

* **personal-features:** background claude-event-hook logging so tool calls never wait ([#372](https://github.com/Crpaxton4/devcontainer-features/issues/372)) ([333c5dd](https://github.com/Crpaxton4/devcontainer-features/commit/333c5dd8c7518b8bf25cf76401516d3e4ecd4c0a)), closes [#358](https://github.com/Crpaxton4/devcontainer-features/issues/358)

## [2.1.0](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v2.0.0...personal-features-v2.1.0) (2026-07-14)


### Features

* **mcp:** emit an agent event from every tool dispatch via a generic wrapper ([#340](https://github.com/Crpaxton4/devcontainer-features/issues/340)) ([2280468](https://github.com/Crpaxton4/devcontainer-features/commit/2280468f1a4e0605276e81b35af8a0e3ca9b9451)), closes [#326](https://github.com/Crpaxton4/devcontainer-features/issues/326)
* **personal-features:** Claude Code lifecycle hooks into odoo-sdk state ([#327](https://github.com/Crpaxton4/devcontainer-features/issues/327)) ([#344](https://github.com/Crpaxton4/devcontainer-features/issues/344)) ([4c28118](https://github.com/Crpaxton4/devcontainer-features/commit/4c28118f6c630ac1aba94e85bc592c843394d5b1))
* **personal-features:** install the qsv CSV toolkit ([#341](https://github.com/Crpaxton4/devcontainer-features/issues/341)) ([52f7034](https://github.com/Crpaxton4/devcontainer-features/commit/52f70346ffe5e4376afa47ebca0d5ca9ac84ee90)), closes [#285](https://github.com/Crpaxton4/devcontainer-features/issues/285)
* **personal-features:** provision Claude Code lifecycle hooks into odoo-sdk state ([4c28118](https://github.com/Crpaxton4/devcontainer-features/commit/4c28118f6c630ac1aba94e85bc592c843394d5b1)), closes [#327](https://github.com/Crpaxton4/devcontainer-features/issues/327)
* **sdk:** add manual resync utility reconciling events with git/GitHub/Odoo ([5751644](https://github.com/Crpaxton4/devcontainer-features/commit/5751644275282ac4d22e870b338ea5264e728039)), closes [#328](https://github.com/Crpaxton4/devcontainer-features/issues/328)
* **sdk:** add odoo-sdk log-event CLI and strict event-source mapping ([#339](https://github.com/Crpaxton4/devcontainer-features/issues/339)) ([fe88e28](https://github.com/Crpaxton4/devcontainer-features/commit/fe88e2872ffb3b52b47c410acbbd7951d1f3bfa2)), closes [#327](https://github.com/Crpaxton4/devcontainer-features/issues/327)
* **sdk:** derive sessions from events in SQL (read path) ([f00687b](https://github.com/Crpaxton4/devcontainer-features/commit/f00687bc3a983a9928a952ec10e0e92d251b8a67)), closes [#330](https://github.com/Crpaxton4/devcontainer-features/issues/330)
* **sdk:** manual resync utility reconciling events with git/GitHub/Odoo ([#328](https://github.com/Crpaxton4/devcontainer-features/issues/328)) ([#349](https://github.com/Crpaxton4/devcontainer-features/issues/349)) ([5751644](https://github.com/Crpaxton4/devcontainer-features/commit/5751644275282ac4d22e870b338ea5264e728039))
* **sdk:** record repo identity in tracker DBs + cross-DB run discovery/abort ([2863b1f](https://github.com/Crpaxton4/devcontainer-features/commit/2863b1f64824ab83959708f76c2d2a6a17f42a39)), closes [#331](https://github.com/Crpaxton4/devcontainer-features/issues/331)
* **sdk:** render chatter notes from Markdown to HTML ([#338](https://github.com/Crpaxton4/devcontainer-features/issues/338)) ([7220472](https://github.com/Crpaxton4/devcontainer-features/commit/7220472d92e79e719e686e3b81cd9ae907476232)), closes [#324](https://github.com/Crpaxton4/devcontainer-features/issues/324)
* **sdk:** repo-identity in tracker DBs + cross-DB run discovery/abort ([#331](https://github.com/Crpaxton4/devcontainer-features/issues/331)) ([#345](https://github.com/Crpaxton4/devcontainer-features/issues/345)) ([2863b1f](https://github.com/Crpaxton4/devcontainer-features/commit/2863b1f64824ab83959708f76c2d2a6a17f42a39))
* **sdk:** SQL-derived sessions read path ([#330](https://github.com/Crpaxton4/devcontainer-features/issues/330) part 1) ([#346](https://github.com/Crpaxton4/devcontainer-features/issues/346)) ([f00687b](https://github.com/Crpaxton4/devcontainer-features/commit/f00687bc3a983a9928a952ec10e0e92d251b8a67))
* **tui:** show diagnostic counts in the empty-session state ([#347](https://github.com/Crpaxton4/devcontainer-features/issues/347)) ([e5001b2](https://github.com/Crpaxton4/devcontainer-features/commit/e5001b270cc202d0ce29368bdb48e892b3ff72f0)), closes [#332](https://github.com/Crpaxton4/devcontainer-features/issues/332)


### Bug Fixes

* **personal-features:** make shell-history dir world-writable so history writes ([#336](https://github.com/Crpaxton4/devcontainer-features/issues/336)) ([f24505d](https://github.com/Crpaxton4/devcontainer-features/commit/f24505d3c65fc2e51e5b8094468d5dc7f6b09ca7)), closes [#323](https://github.com/Crpaxton4/devcontainer-features/issues/323)
* **tui:** render offset-aware session timestamps without crashing ([#337](https://github.com/Crpaxton4/devcontainer-features/issues/337)) ([e5fbbef](https://github.com/Crpaxton4/devcontainer-features/commit/e5fbbefcb6c15dacbca24926d02e45c80f6dd774)), closes [#333](https://github.com/Crpaxton4/devcontainer-features/issues/333)

## [2.0.0](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.18.1...personal-features-v2.0.0) (2026-07-13)


### ⚠ BREAKING CHANGES

* **sdk:** targeted seams + config consolidation (Epic G) ([#308](https://github.com/Crpaxton4/devcontainer-features/issues/308))
* **sdk:** 

### Features

* **feature:** add delta and lazygit (Epic F) ([#306](https://github.com/Crpaxton4/devcontainer-features/issues/306)) ([5fb251d](https://github.com/Crpaxton4/devcontainer-features/commit/5fb251d857b5f25c89d74a8b25f07c5c87505fd1))
* **feature:** add delta and lazygit (pinned) ([#305](https://github.com/Crpaxton4/devcontainer-features/issues/305)) ([5fb251d](https://github.com/Crpaxton4/devcontainer-features/commit/5fb251d857b5f25c89d74a8b25f07c5c87505fd1))
* **feature:** baked-in Claude consulting skills (Epic I) ([#320](https://github.com/Crpaxton4/devcontainer-features/issues/320)) ([cb4da25](https://github.com/Crpaxton4/devcontainer-features/commit/cb4da25659f45a2b09545e8b99b739a5fca9ed7d))
* **personal-features:** add delta and lazygit (pinned) ([5fb251d](https://github.com/Crpaxton4/devcontainer-features/commit/5fb251d857b5f25c89d74a8b25f07c5c87505fd1))
* **personal-features:** author consulting skills (quote, design-doc, discovery, review, status) ([cb4da25](https://github.com/Crpaxton4/devcontainer-features/commit/cb4da25659f45a2b09545e8b99b739a5fca9ed7d))
* **personal-features:** persisted-paths manifest as single source of truth ([abfaa53](https://github.com/Crpaxton4/devcontainer-features/commit/abfaa53c12c354e46abfe632f763852f575491d8))
* **personal-features:** skill delivery via sync-claude-skills and postCreateCommand ([cb4da25](https://github.com/Crpaxton4/devcontainer-features/commit/cb4da25659f45a2b09545e8b99b739a5fca9ed7d))
* **sdk:** configurable RPC timeout via ODOO_TIMEOUT ([8ca1db7](https://github.com/Crpaxton4/devcontainer-features/commit/8ca1db7821e5fde5018b68c34fb381a0d9f4d57b))
* **sdk:** read-only reporting/billing and knowledge/discovery MCP tools (Epic H) ([#316](https://github.com/Crpaxton4/devcontainer-features/issues/316)) ([e5a2244](https://github.com/Crpaxton4/devcontainer-features/commit/e5a2244fb8caff2d5ce91ee9db2e4884cdb6b57b))


### Bug Fixes

* **devcontainer:** stop installing two versions of personal-features ([#256](https://github.com/Crpaxton4/devcontainer-features/issues/256)) ([5eaff6c](https://github.com/Crpaxton4/devcontainer-features/commit/5eaff6c41b783e121e5ec787a8ba5cfeeb60c705))
* **feature:** install.sh hardening + data-driven persistence manifest (Epic E) ([#304](https://github.com/Crpaxton4/devcontainer-features/issues/304)) ([abfaa53](https://github.com/Crpaxton4/devcontainer-features/commit/abfaa53c12c354e46abfe632f763852f575491d8))
* **personal-features:** chmod credential config dirs to 0700 ([abfaa53](https://github.com/Crpaxton4/devcontainer-features/commit/abfaa53c12c354e46abfe632f763852f575491d8))
* **personal-features:** hoist uv venv creation out of the wheel loop ([abfaa53](https://github.com/Crpaxton4/devcontainer-features/commit/abfaa53c12c354e46abfe632f763852f575491d8))
* **personal-features:** invert claude-wrapper to inject --ide only for bare interactive sessions ([abfaa53](https://github.com/Crpaxton4/devcontainer-features/commit/abfaa53c12c354e46abfe632f763852f575491d8))
* **personal-features:** pin tool versions and drop releases/latest API calls ([abfaa53](https://github.com/Crpaxton4/devcontainer-features/commit/abfaa53c12c354e46abfe632f763852f575491d8))
* **personal-features:** stop masking download failures in install.sh ([abfaa53](https://github.com/Crpaxton4/devcontainer-features/commit/abfaa53c12c354e46abfe632f763852f575491d8))
* **sdk:** auth, error mapping, credential repr, and transport hardening (Epic B) ([#286](https://github.com/Crpaxton4/devcontainer-features/issues/286)) ([8ca1db7](https://github.com/Crpaxton4/devcontainer-features/commit/8ca1db7821e5fde5018b68c34fb381a0d9f4d57b))
* **sdk:** bound MCP profiling artifacts under a pruned subdirectory ([8ca1db7](https://github.com/Crpaxton4/devcontainer-features/commit/8ca1db7821e5fde5018b68c34fb381a0d9f4d57b))
* **sdk:** hide password from OdooConnectionSettings repr ([8ca1db7](https://github.com/Crpaxton4/devcontainer-features/commit/8ca1db7821e5fde5018b68c34fb381a0d9f4d57b))
* **sdk:** map XML-RPC faults to the OdooError taxonomy ([8ca1db7](https://github.com/Crpaxton4/devcontainer-features/commit/8ca1db7821e5fde5018b68c34fb381a0d9f4d57b))
* **sdk:** raise OdooAuthenticationError on failed authentication ([8ca1db7](https://github.com/Crpaxton4/devcontainer-features/commit/8ca1db7821e5fde5018b68c34fb381a0d9f4d57b))
* **sdk:** raise typed exceptions from start_task and abort_task ([050cd64](https://github.com/Crpaxton4/devcontainer-features/commit/050cd6412ea9918d7f3b3ddfa321841182afb173))
* **sdk:** send Authorization Bearer with standard casing ([8ca1db7](https://github.com/Crpaxton4/devcontainer-features/commit/8ca1db7821e5fde5018b68c34fb381a0d9f4d57b))
* **sdk:** uniform command error contract — raise typed, format at MCP boundary (Epic C) ([#300](https://github.com/Crpaxton4/devcontainer-features/issues/300)) ([050cd64](https://github.com/Crpaxton4/devcontainer-features/commit/050cd6412ea9918d7f3b3ddfa321841182afb173))


### Code Refactoring

* **sdk:** decorator-based command and tool registration ([0f58def](https://github.com/Crpaxton4/devcontainer-features/commit/0f58def43889503e0bec0adc2ac081cd76100232))
* **sdk:** targeted seams + config consolidation (Epic G) ([#308](https://github.com/Crpaxton4/devcontainer-features/issues/308)) ([0f58def](https://github.com/Crpaxton4/devcontainer-features/commit/0f58def43889503e0bec0adc2ac081cd76100232))

## [1.18.1](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.18.0...personal-features-v1.18.1) (2026-07-12)


### Bug Fixes

* **personal-features:** make bind mounts work on Windows hosts ([#253](https://github.com/Crpaxton4/devcontainer-features/issues/253)) ([6e18a70](https://github.com/Crpaxton4/devcontainer-features/commit/6e18a7063ceaf1d2a82237e9b83811bb34d49836)), closes [#198](https://github.com/Crpaxton4/devcontainer-features/issues/198)

## [1.18.0](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.17.0...personal-features-v1.18.0) (2026-07-11)


### Features

* **odoo-sdk:** add get_task_attachments MCP tool ([#196](https://github.com/Crpaxton4/devcontainer-features/issues/196)) ([6e940e5](https://github.com/Crpaxton4/devcontainer-features/commit/6e940e535de19c66d04ffb5891ec6bd2e3ca4b45)), closes [#191](https://github.com/Crpaxton4/devcontainer-features/issues/191)


### Bug Fixes

* **odoo-sdk:** flatten timesheet write ids to unwedge stop_task ([#195](https://github.com/Crpaxton4/devcontainer-features/issues/195)) ([3dc89f6](https://github.com/Crpaxton4/devcontainer-features/commit/3dc89f654e7e8ab0528bfd89f4bad639ff5b094e)), closes [#193](https://github.com/Crpaxton4/devcontainer-features/issues/193)

## [1.17.0](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.16.1...personal-features-v1.17.0) (2026-07-10)


### Features

* **odoo-sdk:** add abort_task tool to force-close wedged sessions ([#183](https://github.com/Crpaxton4/devcontainer-features/issues/183)) ([2454a1a](https://github.com/Crpaxton4/devcontainer-features/commit/2454a1a2b15b4a44f4e5e507c41d808647c74ed4))
* **odoo-sdk:** add optional description arg to report_incident prompt ([#182](https://github.com/Crpaxton4/devcontainer-features/issues/182)) ([8eeed8d](https://github.com/Crpaxton4/devcontainer-features/commit/8eeed8d60a24ee548895804acb49f75f001b3e8f))


### Bug Fixes

* **ci:** run mutation-testing SHA resolve step before checkout in workspace root ([#192](https://github.com/Crpaxton4/devcontainer-features/issues/192)) ([41b8c10](https://github.com/Crpaxton4/devcontainer-features/commit/41b8c109569576efe8cde6b39fe668f6e9e3624d))
* **odoo-sdk:** zero merged timesheet rows instead of deleting ([#185](https://github.com/Crpaxton4/devcontainer-features/issues/185)) ([#187](https://github.com/Crpaxton4/devcontainer-features/issues/187)) ([4badba1](https://github.com/Crpaxton4/devcontainer-features/commit/4badba1cc4b2fbc45d4f4e316f2857ed07fe9e23))

## [1.16.1](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.16.0...personal-features-v1.16.1) (2026-07-10)


### Bug Fixes

* **odoo-sdk:** create_timesheet issues single create for scalar id ([#170](https://github.com/Crpaxton4/devcontainer-features/issues/170)) ([22f8b6a](https://github.com/Crpaxton4/devcontainer-features/commit/22f8b6ab915579dcd7fea73720b061c9b267c66f)), closes [#167](https://github.com/Crpaxton4/devcontainer-features/issues/167)
* **odoo-sdk:** do not orphan Odoo records when the local insert fails ([#171](https://github.com/Crpaxton4/devcontainer-features/issues/171)) ([c08436b](https://github.com/Crpaxton4/devcontainer-features/commit/c08436b723b1475410f6d1689b48a1314a0feb51)), closes [#168](https://github.com/Crpaxton4/devcontainer-features/issues/168)
* **odoo-sdk:** pass read fields as keyword in task include helpers ([#169](https://github.com/Crpaxton4/devcontainer-features/issues/169)) ([bdb9444](https://github.com/Crpaxton4/devcontainer-features/commit/bdb9444b9bfd941e1d12672b2ce1db2f1319db8b)), closes [#166](https://github.com/Crpaxton4/devcontainer-features/issues/166)
* **odoo-sdk:** roll back created git branch when start_task fails ([#174](https://github.com/Crpaxton4/devcontainer-features/issues/174)) ([96f24cb](https://github.com/Crpaxton4/devcontainer-features/commit/96f24cb59cc7511588c145cc43aba67d5930c7ca)), closes [#164](https://github.com/Crpaxton4/devcontainer-features/issues/164)

## [1.16.0](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.15.1...personal-features-v1.16.0) (2026-07-10)


### Features

* **ci:** gate release PR on manual required mutation-testing check (&gt;=90% kill rate) ([#130](https://github.com/Crpaxton4/devcontainer-features/issues/130)) ([b9644a4](https://github.com/Crpaxton4/devcontainer-features/commit/b9644a4054af1ad8e020cb8a132f73916bbefedd))


### Bug Fixes

* **create-pr:** send "PR already exists" notice to stderr ([#153](https://github.com/Crpaxton4/devcontainer-features/issues/153)) ([adfbdc9](https://github.com/Crpaxton4/devcontainer-features/commit/adfbdc99239f88459f3b0c059b886eb28c05ae9e))
* **odoo-sdk:** add bounded request timeout to both transports ([#162](https://github.com/Crpaxton4/devcontainer-features/issues/162)) ([3554226](https://github.com/Crpaxton4/devcontainer-features/commit/3554226f847efbfb681e6232889424e61d08a6c6)), closes [#134](https://github.com/Crpaxton4/devcontainer-features/issues/134)
* **odoo-sdk:** make start_task git branch setup idempotent and stash-safe ([#159](https://github.com/Crpaxton4/devcontainer-features/issues/159)) ([e066de9](https://github.com/Crpaxton4/devcontainer-features/commit/e066de9df7e3cd6360ec420a43ea738bae8e469b))
* **odoo-sdk:** post chatter notes with keyword message_post options ([#132](https://github.com/Crpaxton4/devcontainer-features/issues/132)) ([88ee86d](https://github.com/Crpaxton4/devcontainer-features/commit/88ee86dbea78d21c05b4ede3e5b4f7ef014fa02c)), closes [#131](https://github.com/Crpaxton4/devcontainer-features/issues/131)

## [1.15.1](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.15.0...personal-features-v1.15.1) (2026-07-09)


### Bug Fixes

* **odoo-sdk:** degrade start_task branch-name gen when sampling unavailable ([#126](https://github.com/Crpaxton4/devcontainer-features/issues/126)) ([2b22b39](https://github.com/Crpaxton4/devcontainer-features/commit/2b22b39cea7d5597ef50a805451418d15694a158)), closes [#122](https://github.com/Crpaxton4/devcontainer-features/issues/122)
* **odoo-sdk:** exit odoo-tui cleanly on Ctrl+C ([#128](https://github.com/Crpaxton4/devcontainer-features/issues/128)) ([73044ad](https://github.com/Crpaxton4/devcontainer-features/commit/73044ada8d8d94cddd322839e627602fd9a2734d)), closes [#125](https://github.com/Crpaxton4/devcontainer-features/issues/125)
* **odoo-sdk:** make start_task confirmation a single accept/decline gate ([#124](https://github.com/Crpaxton4/devcontainer-features/issues/124)) ([768fd71](https://github.com/Crpaxton4/devcontainer-features/commit/768fd71d116e9a6ef860511d1742d4b9ceced2c6))
* **personal-features:** expose odoo-tui console script on PATH ([#123](https://github.com/Crpaxton4/devcontainer-features/issues/123)) ([63be023](https://github.com/Crpaxton4/devcontainer-features/commit/63be023c1c0a1c63ba9d40448594c74589635872)), closes [#120](https://github.com/Crpaxton4/devcontainer-features/issues/120)

## [1.15.0](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.14.0...personal-features-v1.15.0) (2026-07-09)


### Features

* **odoo-sdk:** btop-style curses TUI for session exploration ([#119](https://github.com/Crpaxton4/devcontainer-features/issues/119)) ([ad26a65](https://github.com/Crpaxton4/devcontainer-features/commit/ad26a65160c169f4701f13065de7b91c10fa6c9c)), closes [#109](https://github.com/Crpaxton4/devcontainer-features/issues/109)
* **odoo-sdk:** global incremental cross-day sessionization ([#118](https://github.com/Crpaxton4/devcontainer-features/issues/118)) ([a8c2e5b](https://github.com/Crpaxton4/devcontainer-features/commit/a8c2e5be7de1dcecfb5ef5ec8830dd6425899b3a)), closes [#108](https://github.com/Crpaxton4/devcontainer-features/issues/108)
* **odoo-sdk:** integrate timelog.py as a pure sessionization ETL module ([#113](https://github.com/Crpaxton4/devcontainer-features/issues/113)) ([065aeb8](https://github.com/Crpaxton4/devcontainer-features/commit/065aeb881ab89bddd614d400ca8e605fbd070089)), closes [#105](https://github.com/Crpaxton4/devcontainer-features/issues/105)
* **odoo-sdk:** make get_task detail selectable via include ([#112](https://github.com/Crpaxton4/devcontainer-features/issues/112)) ([1a42659](https://github.com/Crpaxton4/devcontainer-features/commit/1a426598d103be75b5d1ce1b510b345559a3316a)), closes [#100](https://github.com/Crpaxton4/devcontainer-features/issues/100)


### Bug Fixes

* **odoo-sdk:** annotate injected ctx as Context so it is not in tool schema ([#110](https://github.com/Crpaxton4/devcontainer-features/issues/110)) ([c69aa9f](https://github.com/Crpaxton4/devcontainer-features/commit/c69aa9f8cc21b1f6d014ea2dcd5742bc28ea3896)), closes [#107](https://github.com/Crpaxton4/devcontainer-features/issues/107)
* **odoo-sdk:** resolve tracker state dir to user-writable path ([#111](https://github.com/Crpaxton4/devcontainer-features/issues/111)) ([f552024](https://github.com/Crpaxton4/devcontainer-features/commit/f552024fa6399c2a4e1f9c316ce2198b340e4330)), closes [#106](https://github.com/Crpaxton4/devcontainer-features/issues/106)
* **personal-features:** stop forcing task-tracker state onto unwritable root path ([#117](https://github.com/Crpaxton4/devcontainer-features/issues/117)) ([c4baac3](https://github.com/Crpaxton4/devcontainer-features/commit/c4baac33182e1e87e88b6e5676c8d852ff6ff014)), closes [#115](https://github.com/Crpaxton4/devcontainer-features/issues/115)

## [1.14.0](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.13.2...personal-features-v1.14.0) (2026-07-09)


### Features

* **odoo-sdk:** add optional TOON tool output ([#90](https://github.com/Crpaxton4/devcontainer-features/issues/90)) ([#97](https://github.com/Crpaxton4/devcontainer-features/issues/97)) ([dd95bce](https://github.com/Crpaxton4/devcontainer-features/commit/dd95bcedac614caef34d99e7b1cfa59ff89cc8ae))
* **odoo-sdk:** add per-call MCP profiling option via cProfile ([#98](https://github.com/Crpaxton4/devcontainer-features/issues/98)) ([9187b35](https://github.com/Crpaxton4/devcontainer-features/commit/9187b35dbd80acf4cc307cc061fed46c1ab2a2e0)), closes [#86](https://github.com/Crpaxton4/devcontainer-features/issues/86)
* **personal-features:** add config-driven create-pr command ([#96](https://github.com/Crpaxton4/devcontainer-features/issues/96)) ([e44f277](https://github.com/Crpaxton4/devcontainer-features/commit/e44f277cd5c1cd56f27c66b032cad827defab5d5))
* **personal-features:** integrate CodeRabbit CLI with Claude Code plugin ([#99](https://github.com/Crpaxton4/devcontainer-features/issues/99)) ([6bf9d56](https://github.com/Crpaxton4/devcontainer-features/commit/6bf9d56a6b24329f418a49cdb48f254b8b86b677))
* **personal-features:** integrate mempalace with auto-mining and global config ([#94](https://github.com/Crpaxton4/devcontainer-features/issues/94)) ([dfbafa8](https://github.com/Crpaxton4/devcontainer-features/commit/dfbafa873144ee6f7fc4ba1ef6d4042be685fcf2)), closes [#88](https://github.com/Crpaxton4/devcontainer-features/issues/88)
* **personal-features:** register odoo-sdk MCP server at user scope ([#92](https://github.com/Crpaxton4/devcontainer-features/issues/92)) ([4124218](https://github.com/Crpaxton4/devcontainer-features/commit/41242181f0d966d50fc15628665871afc76a743c)), closes [#87](https://github.com/Crpaxton4/devcontainer-features/issues/87)
* **timelog:** add time-log aggregation ETL script ([#95](https://github.com/Crpaxton4/devcontainer-features/issues/95)) ([a341fe2](https://github.com/Crpaxton4/devcontainer-features/commit/a341fe20fcb07a6f182eb8adc71c0913ae5635ff)), closes [#89](https://github.com/Crpaxton4/devcontainer-features/issues/89)


### Bug Fixes

* **odoo-sdk:** coerce int task_id and list members in implement_task prompt ([f1113a6](https://github.com/Crpaxton4/devcontainer-features/commit/f1113a692d7a6c472322509410f675221ca13e09))
* **odoo-sdk:** coerce int task_id in implement_task prompt ([#91](https://github.com/Crpaxton4/devcontainer-features/issues/91)) ([f1113a6](https://github.com/Crpaxton4/devcontainer-features/commit/f1113a692d7a6c472322509410f675221ca13e09))
* **odoo-sdk:** make tests unittest-discoverable for cosmic-ray baseline ([#93](https://github.com/Crpaxton4/devcontainer-features/issues/93)) ([08f4080](https://github.com/Crpaxton4/devcontainer-features/commit/08f40808cd089ff572d08096c042d57df3e27d18))

## [1.13.2](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.13.1...personal-features-v1.13.2) (2026-06-30)


### Bug Fixes

* **odoo-sdk:** use mail.message.model instead of res_model in chatter query ([#78](https://github.com/Crpaxton4/devcontainer-features/issues/78)) ([ef5458a](https://github.com/Crpaxton4/devcontainer-features/commit/ef5458a5dbf81a852ff6b42dbf1635bc719ed77a))

## [1.13.1](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.13.0...personal-features-v1.13.1) (2026-06-30)


### Bug Fixes

* **odoo-sdk:** remove double-wrapped domain in search_read calls ([#71](https://github.com/Crpaxton4/devcontainer-features/issues/71), [#67](https://github.com/Crpaxton4/devcontainer-features/issues/67)) ([#75](https://github.com/Crpaxton4/devcontainer-features/issues/75)) ([975b6d0](https://github.com/Crpaxton4/devcontainer-features/commit/975b6d0f062ac6a83eb77166348e0bffb02cad0f))
* **odoo-sdk:** return list[str] from prompt functions for FastMCP compatibility ([#72](https://github.com/Crpaxton4/devcontainer-features/issues/72)) ([#74](https://github.com/Crpaxton4/devcontainer-features/issues/74)) ([c60d925](https://github.com/Crpaxton4/devcontainer-features/commit/c60d925c13b41f55bba99fae6965695fd9feab0d))

## [1.13.0](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.12.0...personal-features-v1.13.0) (2026-06-29)


### Features

* **odoo-sdk:** auto-create task branch on start_task ([#70](https://github.com/Crpaxton4/devcontainer-features/issues/70)) ([8c64568](https://github.com/Crpaxton4/devcontainer-features/commit/8c64568eda3afefbced69e1700cc519168b06e08))

## [1.12.0](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.11.1...personal-features-v1.12.0) (2026-06-29)


### Features

* **odoo-sdk:** add report_incident MCP prompt for live incident GitHub issues ([#68](https://github.com/Crpaxton4/devcontainer-features/issues/68)) ([1f2d857](https://github.com/Crpaxton4/devcontainer-features/commit/1f2d85722096548afe0bbfa569104adc8d2a92e8))

## [1.11.1](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.11.0...personal-features-v1.11.1) (2026-06-29)


### Bug Fixes

* **odoo-sdk:** replace Command Protocol with ABC to fix Python 3.10 startup crash ([#65](https://github.com/Crpaxton4/devcontainer-features/issues/65)) ([9fe2f9a](https://github.com/Crpaxton4/devcontainer-features/commit/9fe2f9a4c84423a3c9a284881c4e36720b2c26f4))

## [1.11.0](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.10.5...personal-features-v1.11.0) (2026-06-29)


### Features

* **odoo-sdk:** add get_task and get_task_chatter commands with HTML-to-Markdown conversion ([#61](https://github.com/Crpaxton4/devcontainer-features/issues/61)) ([06b647d](https://github.com/Crpaxton4/devcontainer-features/commit/06b647db14f4de3f4f4d8f0cb27ace370e2bd403))
* **odoo-sdk:** add implement_task MCP prompt and start_task task_id lookup ([#63](https://github.com/Crpaxton4/devcontainer-features/issues/63)) ([9e1af4d](https://github.com/Crpaxton4/devcontainer-features/commit/9e1af4d975c1cff70027f3df2a7c61959ffb2833))

## [1.10.5](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.10.4...personal-features-v1.10.5) (2026-06-25)


### Bug Fixes

* **personal-features:** isolate odoo_sdk install to avoid system cryptography conflict ([#58](https://github.com/Crpaxton4/devcontainer-features/issues/58)) ([0492778](https://github.com/Crpaxton4/devcontainer-features/commit/0492778d59262dca951003e83cc40ca363c24859))

## [1.10.4](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.10.3...personal-features-v1.10.4) (2026-06-25)


### Bug Fixes

* **personal-features:** guard --break-system-packages on pip capability check ([#56](https://github.com/Crpaxton4/devcontainer-features/issues/56)) ([3577088](https://github.com/Crpaxton4/devcontainer-features/commit/3577088bd03047512455c5e5768b83bd0586144f))

## [1.10.3](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.10.2...personal-features-v1.10.3) (2026-06-25)


### Bug Fixes

* **libraries:** constrain cryptography&lt;43 to fix pyOpenSSL compat on odoo:19 ([#54](https://github.com/Crpaxton4/devcontainer-features/issues/54)) ([1690c84](https://github.com/Crpaxton4/devcontainer-features/commit/1690c84809e6de362cbc431b5b07d44de6685805))

## [1.10.2](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.10.1...personal-features-v1.10.2) (2026-06-24)


### Bug Fixes

* **personal-features:** replace odoo_sdk symlink with env var, pre-create task tracker dir ([#52](https://github.com/Crpaxton4/devcontainer-features/issues/52)) ([43556fd](https://github.com/Crpaxton4/devcontainer-features/commit/43556fd4d3083ff8f69cb852f501c886cb1dddbc))

## [1.10.1](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.10.0...personal-features-v1.10.1) (2026-06-24)


### Bug Fixes

* **personal-features:** handle Debian-managed packages blocking pip on odoo:19 ([#50](https://github.com/Crpaxton4/devcontainer-features/issues/50)) ([9a9e986](https://github.com/Crpaxton4/devcontainer-features/commit/9a9e986a73f3d7db57b16e73973acace4dcd111b))

## [1.10.0](https://github.com/Crpaxton4/devcontainer-features/compare/personal-features-v1.9.0...personal-features-v1.10.0) (2026-06-23)


### Features

* **docs:** jupyter documentation in sphinx ([#42](https://github.com/Crpaxton4/devcontainer-features/issues/42)) ([023a571](https://github.com/Crpaxton4/devcontainer-features/commit/023a571b9b34f248b19bd83bbe51514e62e9ede1))
* **docs:** jupyter documentation in sphinx ([#43](https://github.com/Crpaxton4/devcontainer-features/issues/43)) ([8821ae3](https://github.com/Crpaxton4/devcontainer-features/commit/8821ae305c261ac755f2f6b35f63cf6bb3768fba))
* **personal-features:** mount odoo_sdk config from host via bind mount and symlink ([#49](https://github.com/Crpaxton4/devcontainer-features/issues/49)) ([b8652f7](https://github.com/Crpaxton4/devcontainer-features/commit/b8652f7a0bfbcc0bccb92a5c250de09aa1392be5))
* **release:** consolidate into single release bundling odoo_sdk into devcontainer feature ([#45](https://github.com/Crpaxton4/devcontainer-features/issues/45)) ([d6aca1b](https://github.com/Crpaxton4/devcontainer-features/commit/d6aca1bfe8c4fec852645113494f728a24c76b04))


### Bug Fixes

* **devcontainer:** update devcontainer image to compatible distro version (bookworm, not trixie) ([#41](https://github.com/Crpaxton4/devcontainer-features/issues/41)) ([874147f](https://github.com/Crpaxton4/devcontainer-features/commit/874147f4e876c9862dda064b7d1194182d26c784))
