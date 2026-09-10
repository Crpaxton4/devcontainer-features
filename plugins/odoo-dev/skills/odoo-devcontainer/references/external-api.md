# External API

## JSON-2 API (v19 Preferred)

New REST-style API introduced in v17, preferred for new integrations.

### Base URL

```
POST https://{host}/json/2/{model}/{method}
Content-Type: application/json
Authorization: Bearer {api_key}
```

### Get API Key

Settings → Technical → API Keys → Create

### Search Read

```bash
curl -X POST https://myodoo.com/json/2/res.partner/search_read \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "domain": [["is_company", "=", true]],
    "fields": ["name", "email", "phone"],
    "limit": 10
  }'
```

```python
import requests

API_KEY = "your_api_key_here"
BASE_URL = "https://myodoo.com/json/2"

def call(model, method, **params):
    resp = requests.post(
        f"{BASE_URL}/{model}/{method}",
        headers={"Authorization": f"Bearer {API_KEY}"},
        json=params,
    )
    resp.raise_for_status()
    return resp.json()

partners = call("res.partner", "search_read",
    domain=[["is_company", "=", True]],
    fields=["name", "email"],
    limit=10,
)
```

### Create / Write / Unlink

```python
# Create
record_id = call("my.model", "create", values={"name": "New", "state": "draft"})

# Write
call("my.model", "write", ids=[record_id], values={"state": "confirmed"})

# Unlink
call("my.model", "unlink", ids=[record_id])

# Call arbitrary method
result = call("my.model", "action_confirm", ids=[record_id])
```

### Search / Read

```python
ids = call("res.partner", "search", domain=[["active", "=", True]], limit=100)
records = call("res.partner", "read", ids=ids, fields=["name", "email"])
count = call("res.partner", "search_count", domain=[["is_company", "=", True]])
```

---

## Legacy XML-RPC API

Still fully supported. Use for compatibility with existing integrations.

### Connection

```python
import xmlrpc.client

url = "https://myodoo.com"
db = "mydb"
username = "admin"
password = "admin"

# Two endpoints
common = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/common")
models = xmlrpc.client.ServerProxy(f"{url}/xmlrpc/2/object")

# Authenticate — returns uid
uid = common.authenticate(db, username, password, {})
```

### CRUD via XML-RPC

```python
def execute(model, method, *args, **kwargs):
    return models.execute_kw(db, uid, password, model, method, args, kwargs)

# search_read
records = execute('res.partner', 'search_read',
    [[['is_company', '=', True]]],
    {'fields': ['name', 'email'], 'limit': 10}
)

# create
new_id = execute('res.partner', 'create', [{'name': 'New Partner'}])

# write
execute('res.partner', 'write', [[new_id]], {'name': 'Updated'})

# unlink
execute('res.partner', 'unlink', [[new_id]])

# search_count
count = execute('res.partner', 'search_count', [[['active', '=', True]]])
```

---

## Domain Syntax (Both APIs)

```python
# Leaf: [field, operator, value]
[['name', 'ilike', 'acme']]

# Logical — prefix notation
['|', ['type', '=', 'out_invoice'], ['type', '=', 'out_refund']]
['&', ['active', '=', True], ['partner_id.country_id.code', '=', 'US']]
['!', ['state', '=', 'cancel']]

# Empty domain = all records
[]
```

### Operators

| Operator | Meaning |
|----------|---------|
| `=`, `!=` | Exact match |
| `<`, `>`, `<=`, `>=` | Comparison |
| `like`, `ilike` | Pattern match (`%` wildcards); `i` = case-insensitive |
| `=like`, `=ilike` | Pattern without auto-wildcard |
| `in`, `not in` | Membership in list |
| `child_of` | Hierarchical (parent_id tree) |
| `parent_of` | Hierarchical (upward) |

---

## Fields Introspection

```python
# XML-RPC
fields = execute('res.partner', 'fields_get',
    [], {'attributes': ['string', 'type', 'required']}
)

# JSON-2
fields = call('res.partner', 'fields_get',
    attributes=['string', 'type', 'required']
)
```

## Authentication via API Key (XML-RPC)

```python
# Use API key as password in XML-RPC
uid = common.authenticate(db, username, api_key, {})
```
