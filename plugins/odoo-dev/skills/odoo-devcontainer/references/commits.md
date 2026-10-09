# Commits

[Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/). Scope is **always the module name**.

## Format

```
<type>(<module>): <description>

[optional body]

[optional footer(s)]
```

## Types

| Type | Use for |
|------|---------|
| `feat` | New feature, field, view, or behaviour |
| `fix` | Bug fix |
| `refactor` | Code change with no behaviour change |
| `perf` | Performance improvement |
| `test` | Test changes only |
| `docs` | Documentation only |
| `style` | Formatting only, no code change |
| `build` | Build system or dependency change |
| `ci` | CI configuration or pipeline change |
| `chore` | Tooling and other non-functional changes |
| `revert` | Reverting an earlier commit |

## Examples

```
feat(sale_custom): add margin field to sale order line
fix(account_extend): correct tax computation rounding
refactor(stock_custom): extract picking logic to helper
chore(base_setup): update pre-commit hooks
revert(sale_custom): revert the margin field on sale order line
```

## Breaking Changes

Append `!` and add a `BREAKING CHANGE:` footer. The `!` goes after the scope and before the colon — `feat(product_extend)!:` — which is the only form the installed `commit-msg` hook accepts (`^(type)(\(scope\))?!?: `), so `feat!(product_extend):` is rejected:

```
feat(product_extend)!: rename price_unit to unit_price

BREAKING CHANGE: price_unit renamed to unit_price across all views and reports
```

## Manifest Version

Run before committing — the script reads your commits and applies the correct bump:

```bash
python <base directory>/scripts/bump_manifest_version.py [MODULE_PATH]
```
