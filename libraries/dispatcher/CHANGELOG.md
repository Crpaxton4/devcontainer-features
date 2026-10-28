# Changelog

## [9.1.0](https://github.com/Crpaxton4/devcontainer-features/compare/dispatcher-v9.0.0...dispatcher-v9.1.0) (2026-10-28)


### Features

* **dispatcher,odoo-sdk:** dispatch submit and dispatch report, and ODOO_JOB_ID in each dispatched session ([0b7a02c](https://github.com/Crpaxton4/devcontainer-features/commit/0b7a02c1f6a2d8ccd79c1697f2327cb78f057fdd)), closes [#1213](https://github.com/Crpaxton4/devcontainer-features/issues/1213)
* **dispatcher,odoo-sdk:** dispatch submit, dispatch report and ODOO_JOB_ID ([#1234](https://github.com/Crpaxton4/devcontainer-features/issues/1234)) ([0b7a02c](https://github.com/Crpaxton4/devcontainer-features/commit/0b7a02c1f6a2d8ccd79c1697f2327cb78f057fdd))
* **dispatcher:** a host-side dispatcher that runs one task per container session and records it ([#1068](https://github.com/Crpaxton4/devcontainer-features/issues/1068)) ([9f43a92](https://github.com/Crpaxton4/devcontainer-features/commit/9f43a927501ba6f6192e1f05898ca3861f78381b))
* **dispatcher:** accept an optional per-job entry so one batch can mix implement and upgrade-implement tasks ([#1265](https://github.com/Crpaxton4/devcontainer-features/issues/1265)) ([8235482](https://github.com/Crpaxton4/devcontainer-features/commit/8235482d2a0bfbcb6e24f77aabcc304d7072f171))
* **dispatcher:** bound the memory wait with DISPATCH_ADMIT_TIMEOUT_S and drop the dead ODOO_ACTIVE_REPOS export ([#1103](https://github.com/Crpaxton4/devcontainer-features/issues/1103)) ([87da355](https://github.com/Crpaxton4/devcontainer-features/commit/87da355dd6c8843ba54716f3ee5da2cf0b1e0bbe)), closes [#1080](https://github.com/Crpaxton4/devcontainer-features/issues/1080)
* **dispatcher:** put the bundle root on the Feature's odoo-dev mount and drop the --mount that a running container ignores ([#1111](https://github.com/Crpaxton4/devcontainer-features/issues/1111)) ([ab6266a](https://github.com/Crpaxton4/devcontainer-features/commit/ab6266a64c1c8c81aded3a06547390a0ad012529))
* **dispatcher:** start the session with /odoo-dev:implement through DISPATCH_ENTRY, and match the real claude binaries in the kill pattern ([#1114](https://github.com/Crpaxton4/devcontainer-features/issues/1114)) ([0d3bcf2](https://github.com/Crpaxton4/devcontainer-features/commit/0d3bcf2ee604f244c847878a7c94adf0a99dca74)), closes [#1077](https://github.com/Crpaxton4/devcontainer-features/issues/1077)
* **dispatcher:** wait for postgres and odoo inside the container before the exec; an expired wait is CONTAINER_START_FAILED ([#1112](https://github.com/Crpaxton4/devcontainer-features/issues/1112)) ([93a8a23](https://github.com/Crpaxton4/devcontainer-features/commit/93a8a236bd7ca8931832cd5e6913108884f8d5a2)), closes [#1084](https://github.com/Crpaxton4/devcontainer-features/issues/1084)
* **usage-log:** log which owned features agents use, with a standalone SQLite writer ([#1239](https://github.com/Crpaxton4/devcontainer-features/issues/1239)) ([6450a38](https://github.com/Crpaxton4/devcontainer-features/commit/6450a38bb795a4a23fd92fcbf418d99849bdd8ba))


### Bug Fixes

* **dispatcher,odoo-dev:** deny the tracking tools and the real config path with one --disallowedTools rule per argument ([5039e81](https://github.com/Crpaxton4/devcontainer-features/commit/5039e81788cda677c57e6a58e2b1fe5b56654b19))
* **dispatcher,odoo-dev:** deny the tracking tools and the real config path with one --disallowedTools rule per argument; gate the start_task call on ODOO_UNATTENDED ([#1107](https://github.com/Crpaxton4/devcontainer-features/issues/1107)) ([5039e81](https://github.com/Crpaxton4/devcontainer-features/commit/5039e81788cda677c57e6a58e2b1fe5b56654b19))
* **dispatcher:** a stop request also kills the container-side session, as the deadline path does ([#1108](https://github.com/Crpaxton4/devcontainer-features/issues/1108)) ([3f61308](https://github.com/Crpaxton4/devcontainer-features/commit/3f61308541969a712839825b0f745d51195de77e))
* **dispatcher:** accept a repo name with a leading dot such as .github, never . or .. ([#1266](https://github.com/Crpaxton4/devcontainer-features/issues/1266)) ([fd63f7c](https://github.com/Crpaxton4/devcontainer-features/commit/fd63f7c3c331daba3f42202f69f3b6254352b838))
* **dispatcher:** default DISPATCH_ADDITIONAL_FEATURES to the personal-features Feature ([b2f0dbe](https://github.com/Crpaxton4/devcontainer-features/commit/b2f0dbefff96cb9a5d4357bc44eb26285dc0bc66))
* **dispatcher:** default DISPATCH_ADDITIONAL_FEATURES to the personal-features Feature so a cold create has claude and the config mounts ([#1092](https://github.com/Crpaxton4/devcontainer-features/issues/1092)) ([b2f0dbe](https://github.com/Crpaxton4/devcontainer-features/commit/b2f0dbefff96cb9a5d4357bc44eb26285dc0bc66))
* **dispatcher:** read the agent's task.json at tasks/&lt;task_id&gt;/task.json where state-dir.sh writes it ([#1099](https://github.com/Crpaxton4/devcontainer-features/issues/1099)) ([a968ff4](https://github.com/Crpaxton4/devcontainer-features/commit/a968ff4db7b5084c8bf30df42d2e271646582e69))
* **dispatcher:** read the invariants file on the host and inline it with --append-system-prompt ([#1098](https://github.com/Crpaxton4/devcontainer-features/issues/1098)) ([1ed4815](https://github.com/Crpaxton4/devcontainer-features/commit/1ed4815ac25041245d48d476c8c0ac5c5cfbac80)), closes [#1083](https://github.com/Crpaxton4/devcontainer-features/issues/1083)
* **dispatcher:** reap the worktree through devcontainer exec, where its .git file resolves ([#1113](https://github.com/Crpaxton4/devcontainer-features/issues/1113)) ([712c674](https://github.com/Crpaxton4/devcontainer-features/commit/712c674223132a020ff24014e8dbdaba6d987475))
* **dispatcher:** refuse a submit whose repo has no checkout or no central config ([44003b5](https://github.com/Crpaxton4/devcontainer-features/commit/44003b50ced5593133315c61fd95d3873560cc56))
* **dispatcher:** refuse a submit whose repo has no checkout or no central config, so nothing dead-letters later ([#1264](https://github.com/Crpaxton4/devcontainer-features/issues/1264)) ([44003b5](https://github.com/Crpaxton4/devcontainer-features/commit/44003b50ced5593133315c61fd95d3873560cc56))
* **dispatcher:** resolve the devcontainer config per repo instead of one DISPATCH_REPO_OWNER per process ([#1261](https://github.com/Crpaxton4/devcontainer-features/issues/1261)) ([60027d1](https://github.com/Crpaxton4/devcontainer-features/commit/60027d11164639446b4284c8e84fdf10933463f8))
* **dispatcher:** seed each job bundle with the shared repo-map.json so task-env preflight resolves the project ([#1267](https://github.com/Crpaxton4/devcontainer-features/issues/1267)) ([cd331f7](https://github.com/Crpaxton4/devcontainer-features/commit/cd331f7161b4e939e5ec80d2d88024c2bdade672))
* **dispatcher:** start the run row's clock at the session spawn so a cold container start is not billed to the task ([#1105](https://github.com/Crpaxton4/devcontainer-features/issues/1105)) ([4900374](https://github.com/Crpaxton4/devcontainer-features/commit/4900374468e9678d2a9b3cc90fe0eefb5e88a146))

## [1.2.0](https://github.com/Crpaxton4/devcontainer-features/compare/dispatcher-v1.1.0...dispatcher-v1.2.0) (2026-10-26)


### Features

* **dispatcher,odoo-sdk:** dispatch submit and dispatch report, and ODOO_JOB_ID in each dispatched session ([0b7a02c](https://github.com/Crpaxton4/devcontainer-features/commit/0b7a02c1f6a2d8ccd79c1697f2327cb78f057fdd)), closes [#1213](https://github.com/Crpaxton4/devcontainer-features/issues/1213)
* **dispatcher,odoo-sdk:** dispatch submit, dispatch report and ODOO_JOB_ID ([#1234](https://github.com/Crpaxton4/devcontainer-features/issues/1234)) ([0b7a02c](https://github.com/Crpaxton4/devcontainer-features/commit/0b7a02c1f6a2d8ccd79c1697f2327cb78f057fdd))
* **dispatcher:** a host-side dispatcher that runs one task per container session and records it ([#1068](https://github.com/Crpaxton4/devcontainer-features/issues/1068)) ([9f43a92](https://github.com/Crpaxton4/devcontainer-features/commit/9f43a927501ba6f6192e1f05898ca3861f78381b))
* **dispatcher:** accept an optional per-job entry so one batch can mix implement and upgrade-implement tasks ([#1265](https://github.com/Crpaxton4/devcontainer-features/issues/1265)) ([8235482](https://github.com/Crpaxton4/devcontainer-features/commit/8235482d2a0bfbcb6e24f77aabcc304d7072f171))
* **dispatcher:** bound the memory wait with DISPATCH_ADMIT_TIMEOUT_S and drop the dead ODOO_ACTIVE_REPOS export ([#1103](https://github.com/Crpaxton4/devcontainer-features/issues/1103)) ([87da355](https://github.com/Crpaxton4/devcontainer-features/commit/87da355dd6c8843ba54716f3ee5da2cf0b1e0bbe)), closes [#1080](https://github.com/Crpaxton4/devcontainer-features/issues/1080)
* **dispatcher:** put the bundle root on the Feature's odoo-dev mount and drop the --mount that a running container ignores ([#1111](https://github.com/Crpaxton4/devcontainer-features/issues/1111)) ([ab6266a](https://github.com/Crpaxton4/devcontainer-features/commit/ab6266a64c1c8c81aded3a06547390a0ad012529))
* **dispatcher:** start the session with /odoo-dev:implement through DISPATCH_ENTRY, and match the real claude binaries in the kill pattern ([#1114](https://github.com/Crpaxton4/devcontainer-features/issues/1114)) ([0d3bcf2](https://github.com/Crpaxton4/devcontainer-features/commit/0d3bcf2ee604f244c847878a7c94adf0a99dca74)), closes [#1077](https://github.com/Crpaxton4/devcontainer-features/issues/1077)
* **dispatcher:** wait for postgres and odoo inside the container before the exec; an expired wait is CONTAINER_START_FAILED ([#1112](https://github.com/Crpaxton4/devcontainer-features/issues/1112)) ([93a8a23](https://github.com/Crpaxton4/devcontainer-features/commit/93a8a236bd7ca8931832cd5e6913108884f8d5a2)), closes [#1084](https://github.com/Crpaxton4/devcontainer-features/issues/1084)
* **usage-log:** log which owned features agents use, with a standalone SQLite writer ([#1239](https://github.com/Crpaxton4/devcontainer-features/issues/1239)) ([6450a38](https://github.com/Crpaxton4/devcontainer-features/commit/6450a38bb795a4a23fd92fcbf418d99849bdd8ba))


### Bug Fixes

* **dispatcher,odoo-dev:** deny the tracking tools and the real config path with one --disallowedTools rule per argument ([5039e81](https://github.com/Crpaxton4/devcontainer-features/commit/5039e81788cda677c57e6a58e2b1fe5b56654b19))
* **dispatcher,odoo-dev:** deny the tracking tools and the real config path with one --disallowedTools rule per argument; gate the start_task call on ODOO_UNATTENDED ([#1107](https://github.com/Crpaxton4/devcontainer-features/issues/1107)) ([5039e81](https://github.com/Crpaxton4/devcontainer-features/commit/5039e81788cda677c57e6a58e2b1fe5b56654b19))
* **dispatcher:** a stop request also kills the container-side session, as the deadline path does ([#1108](https://github.com/Crpaxton4/devcontainer-features/issues/1108)) ([3f61308](https://github.com/Crpaxton4/devcontainer-features/commit/3f61308541969a712839825b0f745d51195de77e))
* **dispatcher:** accept a repo name with a leading dot such as .github, never . or .. ([#1266](https://github.com/Crpaxton4/devcontainer-features/issues/1266)) ([fd63f7c](https://github.com/Crpaxton4/devcontainer-features/commit/fd63f7c3c331daba3f42202f69f3b6254352b838))
* **dispatcher:** default DISPATCH_ADDITIONAL_FEATURES to the personal-features Feature ([b2f0dbe](https://github.com/Crpaxton4/devcontainer-features/commit/b2f0dbefff96cb9a5d4357bc44eb26285dc0bc66))
* **dispatcher:** default DISPATCH_ADDITIONAL_FEATURES to the personal-features Feature so a cold create has claude and the config mounts ([#1092](https://github.com/Crpaxton4/devcontainer-features/issues/1092)) ([b2f0dbe](https://github.com/Crpaxton4/devcontainer-features/commit/b2f0dbefff96cb9a5d4357bc44eb26285dc0bc66))
* **dispatcher:** read the agent's task.json at tasks/&lt;task_id&gt;/task.json where state-dir.sh writes it ([#1099](https://github.com/Crpaxton4/devcontainer-features/issues/1099)) ([a968ff4](https://github.com/Crpaxton4/devcontainer-features/commit/a968ff4db7b5084c8bf30df42d2e271646582e69))
* **dispatcher:** read the invariants file on the host and inline it with --append-system-prompt ([#1098](https://github.com/Crpaxton4/devcontainer-features/issues/1098)) ([1ed4815](https://github.com/Crpaxton4/devcontainer-features/commit/1ed4815ac25041245d48d476c8c0ac5c5cfbac80)), closes [#1083](https://github.com/Crpaxton4/devcontainer-features/issues/1083)
* **dispatcher:** reap the worktree through devcontainer exec, where its .git file resolves ([#1113](https://github.com/Crpaxton4/devcontainer-features/issues/1113)) ([712c674](https://github.com/Crpaxton4/devcontainer-features/commit/712c674223132a020ff24014e8dbdaba6d987475))
* **dispatcher:** refuse a submit whose repo has no checkout or no central config ([44003b5](https://github.com/Crpaxton4/devcontainer-features/commit/44003b50ced5593133315c61fd95d3873560cc56))
* **dispatcher:** refuse a submit whose repo has no checkout or no central config, so nothing dead-letters later ([#1264](https://github.com/Crpaxton4/devcontainer-features/issues/1264)) ([44003b5](https://github.com/Crpaxton4/devcontainer-features/commit/44003b50ced5593133315c61fd95d3873560cc56))
* **dispatcher:** resolve the devcontainer config per repo instead of one DISPATCH_REPO_OWNER per process ([#1261](https://github.com/Crpaxton4/devcontainer-features/issues/1261)) ([60027d1](https://github.com/Crpaxton4/devcontainer-features/commit/60027d11164639446b4284c8e84fdf10933463f8))
* **dispatcher:** seed each job bundle with the shared repo-map.json so task-env preflight resolves the project ([#1267](https://github.com/Crpaxton4/devcontainer-features/issues/1267)) ([cd331f7](https://github.com/Crpaxton4/devcontainer-features/commit/cd331f7161b4e939e5ec80d2d88024c2bdade672))
* **dispatcher:** start the run row's clock at the session spawn so a cold container start is not billed to the task ([#1105](https://github.com/Crpaxton4/devcontainer-features/issues/1105)) ([4900374](https://github.com/Crpaxton4/devcontainer-features/commit/4900374468e9678d2a9b3cc90fe0eefb5e88a146))

## [1.1.0](https://github.com/Crpaxton4/devcontainer-features/compare/dispatcher-v1.0.0...dispatcher-v1.1.0) (2026-10-21)


### Features

* **dispatcher,odoo-sdk:** dispatch submit and dispatch report, and ODOO_JOB_ID in each dispatched session ([0b7a02c](https://github.com/Crpaxton4/devcontainer-features/commit/0b7a02c1f6a2d8ccd79c1697f2327cb78f057fdd)), closes [#1213](https://github.com/Crpaxton4/devcontainer-features/issues/1213)
* **dispatcher,odoo-sdk:** dispatch submit, dispatch report and ODOO_JOB_ID ([#1234](https://github.com/Crpaxton4/devcontainer-features/issues/1234)) ([0b7a02c](https://github.com/Crpaxton4/devcontainer-features/commit/0b7a02c1f6a2d8ccd79c1697f2327cb78f057fdd))
* **dispatcher:** bound the memory wait with DISPATCH_ADMIT_TIMEOUT_S and drop the dead ODOO_ACTIVE_REPOS export ([#1103](https://github.com/Crpaxton4/devcontainer-features/issues/1103)) ([87da355](https://github.com/Crpaxton4/devcontainer-features/commit/87da355dd6c8843ba54716f3ee5da2cf0b1e0bbe)), closes [#1080](https://github.com/Crpaxton4/devcontainer-features/issues/1080)
* **dispatcher:** put the bundle root on the Feature's odoo-dev mount and drop the --mount that a running container ignores ([#1111](https://github.com/Crpaxton4/devcontainer-features/issues/1111)) ([ab6266a](https://github.com/Crpaxton4/devcontainer-features/commit/ab6266a64c1c8c81aded3a06547390a0ad012529))
* **dispatcher:** start the session with /odoo-dev:implement through DISPATCH_ENTRY, and match the real claude binaries in the kill pattern ([#1114](https://github.com/Crpaxton4/devcontainer-features/issues/1114)) ([0d3bcf2](https://github.com/Crpaxton4/devcontainer-features/commit/0d3bcf2ee604f244c847878a7c94adf0a99dca74)), closes [#1077](https://github.com/Crpaxton4/devcontainer-features/issues/1077)
* **dispatcher:** wait for postgres and odoo inside the container before the exec; an expired wait is CONTAINER_START_FAILED ([#1112](https://github.com/Crpaxton4/devcontainer-features/issues/1112)) ([93a8a23](https://github.com/Crpaxton4/devcontainer-features/commit/93a8a236bd7ca8931832cd5e6913108884f8d5a2)), closes [#1084](https://github.com/Crpaxton4/devcontainer-features/issues/1084)
* **usage-log:** log which owned features agents use, with a standalone SQLite writer ([#1239](https://github.com/Crpaxton4/devcontainer-features/issues/1239)) ([6450a38](https://github.com/Crpaxton4/devcontainer-features/commit/6450a38bb795a4a23fd92fcbf418d99849bdd8ba))


### Bug Fixes

* **dispatcher,odoo-dev:** deny the tracking tools and the real config path with one --disallowedTools rule per argument ([5039e81](https://github.com/Crpaxton4/devcontainer-features/commit/5039e81788cda677c57e6a58e2b1fe5b56654b19))
* **dispatcher,odoo-dev:** deny the tracking tools and the real config path with one --disallowedTools rule per argument; gate the start_task call on ODOO_UNATTENDED ([#1107](https://github.com/Crpaxton4/devcontainer-features/issues/1107)) ([5039e81](https://github.com/Crpaxton4/devcontainer-features/commit/5039e81788cda677c57e6a58e2b1fe5b56654b19))
* **dispatcher:** a stop request also kills the container-side session, as the deadline path does ([#1108](https://github.com/Crpaxton4/devcontainer-features/issues/1108)) ([3f61308](https://github.com/Crpaxton4/devcontainer-features/commit/3f61308541969a712839825b0f745d51195de77e))
* **dispatcher:** default DISPATCH_ADDITIONAL_FEATURES to the personal-features Feature ([b2f0dbe](https://github.com/Crpaxton4/devcontainer-features/commit/b2f0dbefff96cb9a5d4357bc44eb26285dc0bc66))
* **dispatcher:** default DISPATCH_ADDITIONAL_FEATURES to the personal-features Feature so a cold create has claude and the config mounts ([#1092](https://github.com/Crpaxton4/devcontainer-features/issues/1092)) ([b2f0dbe](https://github.com/Crpaxton4/devcontainer-features/commit/b2f0dbefff96cb9a5d4357bc44eb26285dc0bc66))
* **dispatcher:** read the agent's task.json at tasks/&lt;task_id&gt;/task.json where state-dir.sh writes it ([#1099](https://github.com/Crpaxton4/devcontainer-features/issues/1099)) ([a968ff4](https://github.com/Crpaxton4/devcontainer-features/commit/a968ff4db7b5084c8bf30df42d2e271646582e69))
* **dispatcher:** read the invariants file on the host and inline it with --append-system-prompt ([#1098](https://github.com/Crpaxton4/devcontainer-features/issues/1098)) ([1ed4815](https://github.com/Crpaxton4/devcontainer-features/commit/1ed4815ac25041245d48d476c8c0ac5c5cfbac80)), closes [#1083](https://github.com/Crpaxton4/devcontainer-features/issues/1083)
* **dispatcher:** reap the worktree through devcontainer exec, where its .git file resolves ([#1113](https://github.com/Crpaxton4/devcontainer-features/issues/1113)) ([712c674](https://github.com/Crpaxton4/devcontainer-features/commit/712c674223132a020ff24014e8dbdaba6d987475))
* **dispatcher:** start the run row's clock at the session spawn so a cold container start is not billed to the task ([#1105](https://github.com/Crpaxton4/devcontainer-features/issues/1105)) ([4900374](https://github.com/Crpaxton4/devcontainer-features/commit/4900374468e9678d2a9b3cc90fe0eefb5e88a146))

## 1.0.0 (2026-10-16)


### Features

* **dispatcher:** a host-side dispatcher that runs one task per container session and records it ([#1068](https://github.com/Crpaxton4/devcontainer-features/issues/1068)) ([9f43a92](https://github.com/Crpaxton4/devcontainer-features/commit/9f43a927501ba6f6192e1f05898ca3861f78381b))
* **personal-features:** extended tooling ([#12](https://github.com/Crpaxton4/devcontainer-features/issues/12)) ([1b571d8](https://github.com/Crpaxton4/devcontainer-features/commit/1b571d89f6474afb552846cb061f983ed1c56824))


### Bug Fixes

* **odoo-sdk:** return list[str] from prompt functions for FastMCP compatibility ([#72](https://github.com/Crpaxton4/devcontainer-features/issues/72)) ([#74](https://github.com/Crpaxton4/devcontainer-features/issues/74)) ([c60d925](https://github.com/Crpaxton4/devcontainer-features/commit/c60d925c13b41f55bba99fae6965695fd9feab0d))
