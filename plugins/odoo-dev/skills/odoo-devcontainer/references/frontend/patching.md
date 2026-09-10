# JS Patching

`patch()` modifies existing components, classes, or plain objects at runtime without forking them.

## Import

```javascript
/** @odoo-module */
import { patch } from "@web/core/utils/patch";
```

## Patching a Component

```javascript
import { FormController } from "@web/views/form/form_controller";
import { patch } from "@web/core/utils/patch";

patch(FormController.prototype, {
    setup() {
        super.setup();
        this.myService = useService("my_service");
    },

    async saveRecord(...args) {
        await super.saveRecord(...args);
        await this.myService.afterSave(this.model.root.resId);
    },
});
```

## Patching a Plain Object / Service

```javascript
import { browser } from "@web/core/browser/browser";

patch(browser, {
    setTimeout(fn, delay, ...args) {
        console.log(`setTimeout called with delay ${delay}`);
        return super.setTimeout(fn, delay, ...args);
    },
});
```

## Patching a Class (non-Component)

```javascript
import { SomeClass } from "@some_addon/core/some_class";

patch(SomeClass.prototype, {
    myMethod() {
        const result = super.myMethod();
        return result + " patched";
    },
});
```

## How `super` Works in patch()

Inside a patch, `super.method()` calls the **previous** version (either original or a prior patch). The patch system chains patches as a linked list — each layer calls the previous layer's version.

```javascript
// Patch A applied first
patch(Foo.prototype, { greet() { return "A " + super.greet(); } });

// Patch B applied second — super.greet() calls A's greet, which calls original
patch(Foo.prototype, { greet() { return "B " + super.greet(); } });

// Result: new Foo().greet() === "B A original"
```

## Unpatching (for Tests)

```javascript
import { patch } from "@web/core/utils/patch";

const unpatch = patch(MyClass.prototype, { myMethod() { ... } });

// Later (e.g., in afterEach):
unpatch();
```

Always unpatch in tests to avoid cross-test contamination.

## Gotchas

| Issue | Cause | Fix |
|-------|-------|-----|
| `super is not defined` | Arrow function used in patch | Use regular function: `myMethod() {}` not `myMethod: () => {}` |
| Patch not taking effect | Module not imported | Ensure patch file is in an assets bundle |
| `this` is wrong | Arrow function | Use regular function to preserve OWL's `this` binding |
| Infinite loop | Patch calls itself | Always call `super.method()` not `this.method()` |
| Test pollution | Forgot to unpatch | Store return value of `patch()` and call it in `afterEach` |

## Patch vs Registry

| Approach | When to use |
|----------|-------------|
| `patch()` | Modify existing component behavior, extend methods |
| `registry.category("fields").add(...)` | Replace/add a field widget by widget name |
| `registry.category("views").add(...)` | Replace/add a view type |

Prefer registry-based extension when the extension point exists. Use `patch()` only when no registry hook covers the needed behavior.
