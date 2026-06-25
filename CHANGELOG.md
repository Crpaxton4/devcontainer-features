# Changelog

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
