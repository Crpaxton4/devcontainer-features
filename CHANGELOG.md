# Changelog

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
