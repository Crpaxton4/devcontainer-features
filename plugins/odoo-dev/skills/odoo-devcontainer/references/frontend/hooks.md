# JS Hooks

Custom hooks in OWL follow the same pattern as React hooks: functions starting with `use`, called inside `setup()`.

## useBus

Subscribe to an `EventBus` and automatically unsubscribe on unmount.

```javascript
import { useBus } from "@web/core/utils/hooks";

setup() {
    // Subscribe to the global env bus
    useBus(this.env.bus, "WEB_CLIENT_READY", () => {
        console.log("webclient ready");
    });

    // Subscribe to a service bus
    const notif = useService("notification");
    useBus(notif.bus, "update", (ev) => this.onUpdate(ev.detail));
}
```

## usePager

Manages pagination state and syncs with URL.

```javascript
import { usePager } from "@web/core/pager/pager_hook";

setup() {
    this.pager = usePager({
        get offset() { return self.state.offset; },
        get limit() { return self.state.limit; },
        get total() { return self.state.total; },
        onUpdate({ offset, limit }) {
            self.state.offset = offset;
            self.state.limit = limit;
            self.loadData();
        },
    });
}
```

## usePosition

Positions a floating element relative to a reference element (popovers, dropdowns).

```javascript
import { usePosition } from "@web/core/position/position_hook";

setup() {
    this.ref = useRef("anchor");
    this.popoverRef = useRef("popover");
    usePosition("popover", () => this.ref.el, {
        position: "bottom-start",  // top/bottom/left/right + start/middle/end
    });
}
```

## useSpellCheck

Enables spellcheck on an input/textarea ref.

```javascript
import { useSpellCheck } from "@web/core/utils/hooks";

setup() {
    this.inputRef = useRef("input");
    useSpellCheck(this.inputRef);
}
```

## useAutofocus

Focuses an element after render.

```javascript
import { useAutofocus } from "@web/core/utils/hooks";

setup() {
    this.inputRef = useRef("input");
    useAutofocus({ refName: "input" });
    // OR pass the ref directly
    useAutofocus({ ref: this.inputRef });
}
```

## useAssets

Loads additional asset bundles lazily inside a component.

```javascript
import { useAssets } from "@web/core/assets";

setup() {
    useAssets({ jsLibs: ["/my_addon/static/lib/chart.min.js"] });
    // Component won't render until assets are loaded
}
```

## useChildSubEnv / useSubEnv

Provide context values to child components without prop drilling.

```javascript
import { useChildSubEnv, useSubEnv } from "@odoo/owl";

setup() {
    // Make available to all children via this.env
    useChildSubEnv({ myConfig: { theme: "dark" } });

    // Also extend own env (use sparingly)
    useSubEnv({ debug: true });
}

// Child component access:
// this.env.myConfig.theme
```

## useService (from hooks)

```javascript
import { useService } from "@web/core/utils/hooks";

setup() {
    this.orm = useService("orm");
}
```

## Writing Custom Hooks

```javascript
/** @odoo-module */
import { useState, onWillStart } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";

export function usePartnerData(partnerId) {
    const orm = useService("orm");
    const state = useState({ partner: null, loading: true });

    onWillStart(async () => {
        const [partner] = await orm.read("res.partner", [partnerId], ["name", "email"]);
        state.partner = partner;
        state.loading = false;
    });

    return state;
}

// Usage in component setup():
// this.partnerState = usePartnerData(this.props.partnerId);
```
