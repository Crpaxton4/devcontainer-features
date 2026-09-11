# JS Services

Services are singletons providing shared functionality across components. Access via `useService()`.

## Using a Service

```javascript
/** @odoo-module */
import { useService } from "@web/core/utils/hooks";

export class MyComponent extends Component {
    setup() {
        this.orm = useService("orm");
        this.notification = useService("notification");
        this.rpc = useService("rpc");
        this.dialog = useService("dialog");
        this.action = useService("action");
        this.router = useService("router");
        this.user = useService("user");
    }
}
```

## Built-in Services

### orm

```javascript
// search_read
const records = await this.orm.searchRead(
    "res.partner",
    [["is_company", "=", true]],
    ["name", "email"],
    { limit: 10 }
);

// read
const data = await this.orm.read("res.partner", [1, 2], ["name"]);

// create
const id = await this.orm.create("res.partner", [{ name: "New" }]);

// write
await this.orm.write("res.partner", [1], { name: "Updated" });

// unlink
await this.orm.unlink("res.partner", [1]);

// call arbitrary method
const result = await this.orm.call("res.partner", "my_method", [arg1], { kwarg: val });

// search
const ids = await this.orm.search("res.partner", [["active", "=", true]]);

// searchCount
const count = await this.orm.searchCount("res.partner", []);
```

### notification

```javascript
this.notification.add("Saved successfully!", { type: "success" });
// type: "info" | "warning" | "danger" | "success"
// sticky: true  → must be manually dismissed
this.notification.add("Error occurred", { type: "danger", sticky: true });
```

### rpc

```javascript
// Raw JSON-RPC call (prefer orm service for model ops)
const result = await this.rpc("/web/dataset/call_kw", {
    model: "res.partner",
    method: "search_read",
    args: [[["active", "=", true]], ["name"]],
    kwargs: { limit: 5 },
});
```

### dialog

```javascript
import { ConfirmationDialog } from "@web/core/confirmation_dialog/confirmation_dialog";

this.dialog.add(ConfirmationDialog, {
    title: "Confirm Delete",
    body: "Are you sure?",
    confirm: () => this.doDelete(),
    cancel: () => {},
});

// Custom dialog
this.dialog.add(MyCustomDialog, { recordId: this.record.id });
```

### action

```javascript
// Execute an action by XML ID or dict
await this.action.doAction("my_addon.action_my_view");
await this.action.doAction({
    type: "ir.actions.act_window",
    res_model: "res.partner",
    views: [[false, "form"]],
    res_id: partnerId,
});

// Navigate to URL
this.action.doAction({ type: "ir.actions.act_url", url: "/report/pdf/..." });
```

### router

```javascript
// Current URL state
const state = this.router.current;  // { action, model, id, ... }

// Push state (update URL without reload)
this.router.pushState({ action: "my_action" });
```

### user

```javascript
this.user.userId      // current user ID
this.user.name        // display name
this.user.partnerId   // partner ID
this.user.isAdmin     // boolean
this.user.context     // user context dict
await this.user.hasGroup("base.group_system");  // async group check
```

## Defining a Custom Service

```javascript
/** @odoo-module */
import { registry } from "@web/core/registry";

const myService = {
    dependencies: ["orm", "notification"],  // other services injected

    start(env, { orm, notification }) {
        // Called once at startup; return the service API object
        return {
            async fetchData(id) {
                const [record] = await orm.read("my.model", [id], ["name", "value"]);
                return record;
            },
            notify(msg) {
                notification.add(msg, { type: "info" });
            },
        };
    },
};

registry.category("services").add("my_service", myService);
```

```javascript
// Usage
this.myService = useService("my_service");
await this.myService.fetchData(42);
```
