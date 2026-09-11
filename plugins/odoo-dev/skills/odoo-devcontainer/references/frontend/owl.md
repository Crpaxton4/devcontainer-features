# OWL Components

OWL (Odoo Web Library) is Odoo's reactive component framework. Similar to Vue SFCs but with XML templates.

## Component Anatomy

```javascript
/** @odoo-module */
import { Component, useState, useRef, onMounted } from "@odoo/owl";

export class MyWidget extends Component {
    static template = "my_addon.MyWidget";  // REQUIRED: addon.ComponentName
    static props = {
        title: { type: String },
        count: { type: Number, optional: true },
        onSave: { type: Function },
    };
    static defaultProps = { count: 0 };

    setup() {
        // ALL setup here — NEVER override constructor()
        this.state = useState({ value: 0 });
        this.inputRef = useRef("myInput");
        onMounted(() => this.inputRef.el?.focus());
    }

    increment() {
        this.state.value++;
    }
}
```

## Template (XML)

```xml
<?xml version="1.0" encoding="UTF-8"?>
<templates xml:space="preserve">
    <t t-name="my_addon.MyWidget">
        <div class="my-widget">
            <h1 t-esc="props.title"/>
            <span t-esc="state.value"/>
            <input t-ref="myInput" t-on-input="onInput"/>
            <button t-on-click="() => this.increment()">+1</button>
            <MyChild count="state.value" onSave.bind="onChildSave"/>
        </div>
    </t>
</templates>
```

## t-* Directives

| Directive | Purpose |
|-----------|---------|
| `t-esc="expr"` | Output escaped text |
| `t-out="expr"` | Output raw HTML (XSS risk — use carefully) |
| `t-if="cond"` | Conditional render |
| `t-elif="cond"` / `t-else` | Branch |
| `t-foreach="list" t-as="item"` | Loop (always add `t-key`) |
| `t-key="item.id"` | Reconciliation key (required in loops) |
| `t-on-click="handler"` | DOM event (any event: `t-on-input`, `t-on-change`) |
| `t-ref="name"` | DOM/component ref (pair with `useRef`) |
| `t-model="state.field"` | Two-way binding on input |
| `t-att-class="expr"` | Dynamic attribute |
| `t-attf-class="static {{expr}}"` | Interpolated attribute |
| `t-component` | Dynamic component |
| `t-slot="name"` | Slot content |
| `t-set="var" t-value="expr"` | Local variable |
| `t-call="OtherTemplate"` | Include sub-template |

## Lifecycle Hooks

```javascript
import { onMounted, onWillStart, onWillUpdateProps, onWillUnmount, onPatched } from "@odoo/owl";

setup() {
    onWillStart(async () => { /* async data load before first render */ });
    onMounted(() => { /* DOM ready */ });
    onWillUpdateProps(async (nextProps) => { /* before props change */ });
    onPatched(() => { /* after DOM update */ });
    onWillUnmount(() => { /* cleanup */ });
}
```

## useState / useRef

```javascript
// useState — reactive proxy; mutations trigger re-render
this.state = useState({ items: [], loading: false });
this.state.items.push({ id: 1 });  // triggers update

// useRef — access DOM element or child component
this.myRef = useRef("refName");
// in template: t-ref="refName"
// access: this.myRef.el (HTMLElement)
```

## Props Validation

```javascript
static props = {
    record: { type: Object },               // any object
    id: { type: [Number, String] },          // union
    optional: { type: Boolean, optional: true },
    list: { type: Array, element: Number },  // typed array
    onChange: Function,                      // callback
    "*": true,                               // allow extra props
};
```

## React/Vue → OWL Mapping

| Concept | React | Vue | OWL |
|---------|-------|-----|-----|
| Reactive state | `useState` hook | `ref()` / `reactive()` | `useState()` |
| DOM ref | `useRef` | `ref` attr | `useRef()` |
| Props | props | `defineProps` | `static props = {}` |
| Lifecycle mount | `useEffect(fn, [])` | `onMounted` | `onMounted` |
| Context | `useContext` | `provide/inject` | `useService` / env |
| Conditional | JSX `&&` | `v-if` | `t-if` |
| Loop | `map()` | `v-for` | `t-foreach` + `t-key` |
| Event | `onClick` | `@click` | `t-on-click` |

## v19 OWL Rules

- **NEVER** override `constructor()` — always use `setup()`
- Template name **must** be `addon_name.ComponentName` format
- `t-key` is mandatory inside `t-foreach` — missing key causes bugs
- Use `.bind` suffix on callback props: `onSave.bind="method"` to auto-bind `this`
