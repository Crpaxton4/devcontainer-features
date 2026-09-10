# JS Assets

## Bundle Names

| Bundle | Purpose |
|--------|---------|
| `web.assets_backend` | All backend (admin) JS/CSS |
| `web.assets_frontend` | Public website JS/CSS |
| `web.assets_common` | Loaded everywhere (backend + frontend) |
| `web.assets_backend_lazy` | Loaded lazily in backend |
| `web.assets_frontend_lazy` | Loaded lazily on website |
| `web.qunit_suite_tests` | Unit test files |
| `web.qunit_mobile_suite_tests` | Mobile test files |
| `point_of_sale.assets` | POS-specific bundle |

## Adding Assets via Manifest

```python
# __manifest__.py
{
    'assets': {
        'web.assets_backend': [
            'my_addon/static/src/components/my_widget.js',
            'my_addon/static/src/components/my_widget.xml',
            'my_addon/static/src/css/my_addon.css',
            # Glob patterns supported
            'my_addon/static/src/**/*.js',
            'my_addon/static/src/**/*.xml',
            'my_addon/static/src/**/*.scss',
        ],
        'web.assets_frontend': [
            'my_addon/static/src/website/**/*',
        ],
    },
    # NOTE: 'qweb' key is REMOVED in v16+ — use 'assets' only
}
```

## Asset Directives (order in bundle matters)

```python
'web.assets_backend': [
    # Prepend before existing bundle entries
    ('prepend', 'my_addon/static/src/override.scss'),

    # Insert after a specific file
    ('after', 'web/static/src/core/utils.js', 'my_addon/static/src/patch.js'),

    # Insert before a specific file
    ('before', 'web/static/src/webclient/webclient.js', 'my_addon/static/src/early.js'),

    # Remove a file from parent bundle
    ('remove', 'other_addon/static/src/unwanted.js'),

    # Replace a file
    ('replace', 'other_addon/static/src/old.js', 'my_addon/static/src/new.js'),
],
```

## File Types

| Extension | Handling |
|-----------|---------|
| `.js` | ES module (must use `/** @odoo-module */`) |
| `.xml` | OWL templates (auto-registered) |
| `.css` | Plain CSS |
| `.scss` | Compiled via dart-sass |
| `.less` | Legacy, avoid for new code |

## ES Module Syntax

```javascript
/** @odoo-module */
// Required header — tells bundler this is an ES module

import { Component } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { MyService } from "./my_service";

export class MyComponent extends Component { ... }
```

## Lazy Loading (Dynamic Import)

```javascript
// Load a bundle lazily at runtime
import { loadBundle } from "@web/core/assets";

async myAction() {
    await loadBundle("my_addon.heavy_bundle");
    // bundle is now available
}
```

## ir.asset (Database-Level Assets)

For assets that must be added/removed without a module upgrade (e.g., themes):

```xml
<record id="my_asset" model="ir.asset">
    <field name="name">My Custom Style</field>
    <field name="bundle">web.assets_backend</field>
    <field name="path">my_addon/static/src/extra.css</field>
    <field name="directive">append</field>  <!-- append/prepend/after/before/remove/replace -->
    <field name="active" eval="True"/>
    <field name="sequence">10</field>
</record>
```

## v19 Notes

- `qweb` manifest key is **removed** — all assets via `assets` key only
- `/** @odoo-module */` comment required for ES modules; omitting falls back to legacy mode
- SCSS variables from `web/static/src/scss/` available globally (Bootstrap + Odoo vars)
