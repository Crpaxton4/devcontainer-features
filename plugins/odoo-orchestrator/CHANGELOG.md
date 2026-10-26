# Changelog

## [1.2.0](https://github.com/Crpaxton4/devcontainer-features/compare/odoo-orchestrator-plugin-v1.1.0...odoo-orchestrator-plugin-v1.2.0) (2026-10-26)


### Features

* **dispatcher:** accept an optional per-job entry so one batch can mix implement and upgrade-implement tasks ([#1265](https://github.com/Crpaxton4/devcontainer-features/issues/1265)) ([8235482](https://github.com/Crpaxton4/devcontainer-features/commit/8235482d2a0bfbcb6e24f77aabcc304d7072f171))


### Bug Fixes

* **dispatcher:** accept a repo name with a leading dot such as .github, never . or .. ([#1266](https://github.com/Crpaxton4/devcontainer-features/issues/1266)) ([fd63f7c](https://github.com/Crpaxton4/devcontainer-features/commit/fd63f7c3c331daba3f42202f69f3b6254352b838))
* **dispatcher:** refuse a submit whose repo has no checkout or no central config ([44003b5](https://github.com/Crpaxton4/devcontainer-features/commit/44003b50ced5593133315c61fd95d3873560cc56))
* **dispatcher:** refuse a submit whose repo has no checkout or no central config, so nothing dead-letters later ([#1264](https://github.com/Crpaxton4/devcontainer-features/issues/1264)) ([44003b5](https://github.com/Crpaxton4/devcontainer-features/commit/44003b50ced5593133315c61fd95d3873560cc56))
* **odoo-orchestrator:** create the state and orchestrator-home bind sources before compose ([a03219d](https://github.com/Crpaxton4/devcontainer-features/commit/a03219d8fd607983a0797bc2861fbf091fdc12bd))
* **odoo-orchestrator:** create the state and orchestrator-home bind sources before compose so docker does not make them root-owned ([#1257](https://github.com/Crpaxton4/devcontainer-features/issues/1257)) ([a03219d](https://github.com/Crpaxton4/devcontainer-features/commit/a03219d8fd607983a0797bc2861fbf091fdc12bd))

## [1.1.0](https://github.com/Crpaxton4/devcontainer-features/compare/odoo-orchestrator-plugin-v1.0.0...odoo-orchestrator-plugin-v1.1.0) (2026-10-21)


### Features

* **dispatcher,odoo-orchestrator:** run the dispatcher as a compose service behind dispatch.sh ([#1236](https://github.com/Crpaxton4/devcontainer-features/issues/1236)) ([5981850](https://github.com/Crpaxton4/devcontainer-features/commit/5981850a4de7ae5ecd5792cbdfd7e220d2010b05))
* **odoo-orchestrator:** queue jobs without blocking and read finished batches on demand ([#1241](https://github.com/Crpaxton4/devcontainer-features/issues/1241)) ([2229abf](https://github.com/Crpaxton4/devcontainer-features/commit/2229abf44b89fc70aa57ce647b60f988a84e1c14))
* **webui:** a read-only web interface for jobs, the queue and the friction log ([#1237](https://github.com/Crpaxton4/devcontainer-features/issues/1237)) ([816f6de](https://github.com/Crpaxton4/devcontainer-features/commit/816f6de5562b6c5578cd9319dea8e3812661ee6b))

## 1.0.0 (2026-10-16)


### Features

* **odoo-orchestrator:** a host plugin that hardens tasks into briefs and queues the approved ones ([#1070](https://github.com/Crpaxton4/devcontainer-features/issues/1070)) ([19be4c6](https://github.com/Crpaxton4/devcontainer-features/commit/19be4c67d94f0748b588145b5bfdcca7b6e94371))
* **personal-features:** extended tooling ([#12](https://github.com/Crpaxton4/devcontainer-features/issues/12)) ([1b571d8](https://github.com/Crpaxton4/devcontainer-features/commit/1b571d89f6474afb552846cb061f983ed1c56824))


### Bug Fixes

* **odoo-sdk:** return list[str] from prompt functions for FastMCP compatibility ([#72](https://github.com/Crpaxton4/devcontainer-features/issues/72)) ([#74](https://github.com/Crpaxton4/devcontainer-features/issues/74)) ([c60d925](https://github.com/Crpaxton4/devcontainer-features/commit/c60d925c13b41f55bba99fae6965695fd9feab0d))
