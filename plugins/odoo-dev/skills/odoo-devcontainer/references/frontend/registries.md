# JS Registries

The registry is the central extension point for all frontend customization.

## Core API

```javascript
/** @odoo-module */
import { registry } from "@web/core/registry";

// Get a category
const actionRegistry = registry.category("actions");

// Register a value
actionRegistry.add("my_addon.MyAction", MyActionComponent);

// Get a value
const Component = actionRegistry.get("my_addon.MyAction");

// Check existence
actionRegistry.contains("my_addon.MyAction");

// Get all entries
const entries = actionRegistry.getAll();  // [value, ...]

// Get entries as ordered array of [name, value]
const ordered = actionRegistry.getEntries();

// Remove (rarely needed)
actionRegistry.remove("my_addon.MyAction");
```

## Built-in Registry Categories

| Category | Key format | Registers |
|----------|-----------|-----------|
| `"actions"` | XML ID or tag | Client action components |
| `"views"` | view type string | View components (`form`, `list`, `kanban`, …) |
| `"fields"` | widget name | Field widget components |
| `"formatters"` | field type | Format functions for display |
| `"parsers"` | field type | Parse functions for input |
| `"services"` | service name | Service definitions |
| `"main_components"` | name | Components mounted in the webclient root |
| `"systray"` | name | Top-right systray icons |
| `"command_categories"` | name | Command palette categories |
| `"commands"` | name | Command palette entries |
| `"error_handlers"` | name | Custom error handlers |
| `"error_dialogs"` | error class name | Dialog shown for specific errors |
| `"user_menuitems"` | name | Items in user (gear) menu |
| `"cogMenu"` | name | Cog (action) menu items |
| `"search_filters"` | model | Default search filters |
| `"search_defaults"` | model | Default search values |
| `"debug"` | name | Debug menu items |
| `"tours"` | tour name | Web tours |

## Common Registration Patterns

### Client Action

```javascript
import { registry } from "@web/core/registry";
import { MyDashboard } from "./my_dashboard";

registry.category("actions").add("my_addon.MyDashboard", MyDashboard);
```

```xml
<!-- action definition -->
<record id="action_my_dashboard" model="ir.actions.client">
    <field name="name">My Dashboard</field>
    <field name="tag">my_addon.MyDashboard</field>
</record>
```

### Custom Field Widget

```javascript
import { registry } from "@web/core/registry";
import { ColorPicker } from "./color_picker";

registry.category("fields").add("color_picker", {
    component: ColorPicker,
    supportedTypes: ["char"],
    extractProps: ({ attrs }) => ({ size: attrs.size || 'sm' }),
});
```

```xml
<field name="color" widget="color_picker"/>
```

### Systray Item

```javascript
import { registry } from "@web/core/registry";
import { MyNotifIcon } from "./my_notif_icon";

registry.category("systray").add("my_addon.MyNotifIcon", {
    Component: MyNotifIcon,
    sequence: 5,  // lower = further right
});
```

### Service

```javascript
registry.category("services").add("my_service", myServiceDefinition);
```

## Sequence / Priority

Most categories accept a `sequence` option to control ordering:

```javascript
registry.category("systray").add("my_addon.icon", { Component: Foo }, { sequence: 10 });
```

Lower sequence values render first / take priority.
