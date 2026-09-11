# Testing

## Base Classes

| Class | Use |
|-------|-----|
| `TransactionCase` | Each test runs in a transaction, rolled back after. Fastest. |
| `SavepointCase` | Each test uses a savepoint; class setup runs once. For read-heavy tests. |
| `HttpCase` | Full HTTP test with a real browser-like client. Slower. |
| `BaseCase` | Pure Python, no DB. For utility/helper tests. |

## TransactionCase Example

```python
from odoo.tests.common import TransactionCase
from odoo.exceptions import ValidationError

class TestMyModel(TransactionCase):

    def setUp(self):
        super().setUp()
        self.partner = self.env['res.partner'].create({'name': 'Test Partner'})

    def test_create_record(self):
        record = self.env['my.model'].create({
            'name': 'Test',
            'partner_id': self.partner.id,
        })
        self.assertEqual(record.state, 'draft')
        self.assertEqual(record.partner_id, self.partner)

    def test_validation_error(self):
        with self.assertRaises(ValidationError):
            self.env['my.model'].create({
                'name': 'Bad',
                'start_date': '2025-12-31',
                'end_date': '2025-01-01',  # end < start
            })
```

## @tagged

Controls which tests run with the `--test-tags` CLI flag.

```python
from odoo.tests.common import TransactionCase, tagged

@tagged('my_addon', 'my_feature', '-standard')  # exclude from standard suite
class TestMyFeature(TransactionCase):
    ...

@tagged('post_install', '-at_install')  # run after all modules installed
class TestInstall(TransactionCase):
    ...
```

### Built-in Tags

| Tag | When runs |
|-----|-----------|
| `at_install` (default) | After module install during `--test-enable` |
| `post_install` | After all modules installed |
| `standard` (default) | Normal test run |
| `-standard` | Excluded from normal run (must be explicitly requested) |

## HttpCase / Tours

```python
from odoo.tests.common import HttpCase, tagged

@tagged('post_install', '-at_install')
class TestMyTour(HttpCase):

    def test_my_tour(self):
        self.start_tour('/web', 'my_addon.my_tour', login='admin')
```

```javascript
/** @odoo-module */
import { registry } from "@web/core/registry";

registry.category("web_tour.tours").add("my_addon.my_tour", {
    steps: () => [
        { trigger: '.o_menu_brand', content: "Open menu", run: "click" },
        { trigger: '.o_form_view', content: "Form opened" },
    ],
});
```

### Running tours here — the silent-skip trap

**A tour with no browser does not fail. It skips, and the skip counts as a
pass.** `odoo/tests/common.py` raises `unittest.SkipTest` on every one of these:
no Chrome executable, Chrome that never opens its devtools port within 10s, and
`websocket-client` not importable. The skip is logged at INFO. A suite full of
tours on a machine with no browser is permanently, invisibly green.

There is no Chrome on `PATH` in this devcontainer, so `_find_executable()` finds
nothing and every tour skips by default. Point it at the Playwright build:

```bash
export ODOO_BROWSER_BIN=$(ls -d ~/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell | sort -r | head -1)
mkdir -p /tmp/odoo-test-run/screenshots /tmp/odoo-test-run/screencasts

odoo -d <throwaway_db> -i <module> --test-enable \
     --test-tags /<module>:TestMyTour \
     --http-port=18999 --max-cron-threads=0 --stop-after-init \
     --screenshots=/tmp/odoo-test-run/screenshots \
     --screencasts=/tmp/odoo-test-run/screencasts
```

The screenshot flags are not optional. `/etc/odoo/odoo.conf` points
`screenshots`/`screencasts` at `/mnt/recordings`, a host bind mount that does not
exist inside the container — and Odoo saves a screenshot on **any** failing
`HttpCase`, so without the override a real assertion failure is replaced by
`PermissionError: [Errno 13] Permission denied: '/mnt/recordings'`.

Confirm from the log that a tour actually ran. These lines are on the
`<test>.browser` logger:

```
... .browser: [1/31] Tour my_tour → Step check we are logged in (trigger: .x)
... .browser: tour succeeded
... odoo.tests.result: 0 failed, 0 error(s) of 1 tests when loading database 'db'
```

Match `tour succeeded` **anchored to that logger** — a failing tour test prints
its own source in the traceback, and that source line contains the literal
`success_signal="tour succeeded"`.

The `odoo-dev:odoo-test-run` skill does all of the above and refuses to report a pass when
a module declares tours that did not run. Prefer it over running this by hand.

## CLI Test Runner

```bash
# Run all tests for a module
odoo -u my_addon --test-enable --stop-after-init

# Run specific tags
odoo -u my_addon --test-enable --test-tags my_feature --stop-after-init

# Run a specific test class/method
odoo -u my_addon --test-enable --test-tags /my_addon:TestMyModel --stop-after-init
odoo -u my_addon --test-enable --test-tags /my_addon:TestMyModel.test_create_record --stop-after-init

# Run post_install tests
odoo -u my_addon --test-enable --test-tags post_install --stop-after-init
```

`--test-tags` grammar: `[-][tag][/module][:class][.method]`, comma-separated,
additive; `-` deselects even when something else selected it. Default is
`+standard`. Tests only run in **installed** modules — start from a clean
database with `-i`, or `-u` an installed one.

## Evidence, not vibes

The log lines that carry the counts:

| Line | Meaning |
|---|---|
| `odoo.tests.result: X failed, Y error(s) of N tests when loading database 'db'` | One per phase — at_install and post_install each emit one, so **sum them** |
| `odoo.tests.stats: <module>: N tests 8.48s 293 queries` | Per module (needs `--log-handler odoo.tests.stats:INFO` on some setups) |
| `Module <m>: X failures, Y errors of N tests` | Per module, only when something failed |

Zero executed tests is not a pass. Use `odoo-dev:odoo-test-run` when the result has to be
trusted by a gate — it extracts these, fails closed when nothing ran, and drops
the throwaway database afterwards.

## Common Assertions

```python
self.assertEqual(a, b)
self.assertNotEqual(a, b)
self.assertTrue(expr)
self.assertFalse(expr)
self.assertIn(member, container)
self.assertRaises(ExceptionClass, callable, *args)

# With context manager
with self.assertRaises(ValidationError):
    record.action_confirm()

# Odoo recordset assertions
self.assertRecordValues(records, [
    {'name': 'Foo', 'state': 'draft'},
    {'name': 'Bar', 'state': 'confirmed'},
])
```

## Test Data Patterns

```python
# Use self.env.ref() for demo/base data
partner = self.env.ref('base.res_partner_1')

# Patch methods for isolation
from unittest.mock import patch

with patch.object(type(self.env['my.model']), '_send_email') as mock_send:
    record.action_confirm()
    mock_send.assert_called_once()

# with_user for access testing
user = self.env.ref('base.user_demo')
with self.assertRaises(AccessError):
    self.env['my.model'].with_user(user).create({'name': 'X'})
```
