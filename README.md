# unitysvc-core

Shared data models, JSON schemas, and validation helpers for the UnitySVC
ecosystem. This package is **audience-neutral**: it contains only the
foundational types and helpers that are consumed by the UnitySVC backend,
the customer SDK, the admin CLI, and the seller SDK.

It intentionally does **not** include any CLI, HTTP client, or
audience-specific helpers. Those live in the corresponding packages:

- `unitysvc-sellers` — seller SDK + `usvc_seller` CLI, catalog builders
  (`populate_from_iterator`, `render_template_file`,
  `convert_convenience_fields_to_documents`, etc.)
- `unitysvc` — customer SDK + `usvc` CLI
- `unitysvc-admin` — admin CLI

## What's inside

```
src/unitysvc_core/
├── models/
│   ├── base.py            # All enums (status, type, currency, rate-limit, etc.)
│   ├── pricing.py         # PriceStr, UsageData, all *PriceData classes,
│   │                      # Pricing union, validate_pricing, cost calculation
│   ├── channel.py         # UpstreamAccessConfig, ChannelConfig, per-protocol
│   │                      # servers, ChannelParams, RETIRED_CHANNEL_KEYS
│   ├── documents.py       # DocumentData
│   ├── service.py         # RateLimit, ServiceConstraints, AccessInterfaceData,
│   │                      # UpstreamAccessConfigData
│   ├── validators.py      # validate_name, validate_service_options,
│   │                      # suggest_valid_name, SUPPORTED_SERVICE_OPTIONS
│   ├── provider_v1.py     # Provider file schema + ProviderData
│   ├── offering_v1.py     # Offering file schema + ServiceOfferingData
│   ├── listing_v1.py      # Listing file schema + ServiceListingData
│   ├── promotion_v1.py    # Promotion file schema + PromotionData
│   └── service_group_v1.py
├── schema/                # Generated JSON schemas (run scripts/generate_schemas.py; CI enforces sync)
├── validator.py           # DataValidator — per-file schema validation,
│                          # union-field reference checks, API-key secret
│                          # scanning, Jinja2 syntax validation, etc.
└── utils.py               # Content hashing, mime/extension helpers,
                           # data file loaders (JSON/TOML + overrides),
                           # schema-based file discovery
```

### What's deliberately NOT here

- **CLI code** — `typer`, `rich`, no entry points
- **HTTP clients** — no `httpx`, no API wrappers
- **Seller catalog builders** — `populate_from_iterator`,
  `render_template_file`, `convert_convenience_fields_to_documents`,
  `execute_script_content` live in `unitysvc-sellers`
- **Seller-catalog-layout validation** — `validate_provider_status`,
  `validate_all_service_directories`, `resolve_provider_name`,
  `resolve_service_name_for_listing` live in `unitysvc-sellers`

## Installation

```bash
pip install unitysvc-core
```

Dependencies: `pydantic`, `email-validator`, `jsonschema`, `jinja2`,
`tomli-w`. No `typer`, no `rich`, no `httpx`.

## Usage

```python
from pathlib import Path

import unitysvc_core
from unitysvc_core.models import ProviderV1, OfferingV1, ListingV1
from unitysvc_core.models.pricing import UsageData, validate_pricing
from unitysvc_core.validator import DataValidator

# Validate a single catalog directory
validator = DataValidator(
    data_dir=Path("./catalog"),
    schema_dir=Path(unitysvc_core.__file__).parent / "schema",
)
results = validator.validate_all()

# Typed pricing + cost calculation
pricing = validate_pricing({
    "type": "one_million_tokens",
    "input": "0.50",
    "output": "1.50",
})
cost = pricing.calculate_cost(UsageData(input_tokens=1_000, output_tokens=500))
```

## History

This package was split out of `unitysvc-services` (see
[unitysvc/unitysvc-services#99](https://github.com/unitysvc/unitysvc-services/issues/99)).
The seller CLI, catalog builders, and seller-catalog-layout validators
moved to `unitysvc-sellers`.

## License

MIT

### Required enrollment parameter usage

Each channel's required parameters must be read by that channel or by document
content (a `.j2` document's template source, which the seller validator reads
from disk as the SDK uploads it); another channel's use does not count. Metadata
such as `display_name` or `description` is never rendered with `params`, and a
user access interface never reads them. File validation checks a listing against
its paired offering when present; backend ingest calls the same
`validate_required_parameter_usage` helper with the complete listing and
offering. Schema declarations alone are not consumers. Jinja attribute access,
string-key lookups, and `params.get("name")` are inspected; dynamic access and
whole-map iteration conservatively consume all parameters. Deprecated content can
still be retired.

### Canonical user access interfaces

A user access interface is the service's canonical address, the same for every caller. `validate_listing_interfaces_are_canonical` renders `{{ service_name }}` (the one value the platform fills in when it stores an interface) and then requires:

- a `base_url` that is one of the four gateways, `${API_GATEWAY_BASE_URL}`, `${S3_GATEWAY_BASE_URL}`, `${SMTP_GATEWAY_BASE_URL}` or `${MCP_GATEWAY_BASE_URL}`, optionally followed by `/` and a path (`/a/<alias>` included); never an external URL, another variable or a secret reference;
- no template left over. `{{ routing_vars.X }}` would stay literal in the stored interface and never route, and a per-enrollment value (`enrollment.*`, `params.*`) is never canonical: an enrollment is reached through `/e/<CODE>` on the API gateway, which a listing never declares.

The one exception is `{{ enrollment.code }}` as the whole SMTP `routing_key.username` or the whole S3 bucket segment, a reminder of the per-enrollment address on protocols without a path for it. The shared validator runs it on every `listing_v1`; backend ingest calls the same function (unitysvc/unitysvc#2726).

### Listing audience

A listing may say who the service is for and what a buyer must know before enrolling, as `audience`: a short list of plain-text phrases (unitysvc/unitysvc#2717):

```json
"audience": [
  "Rice University students (enrollment at Rice required)",
  "US customers only / export restrictions may apply"
]
```

At most 8 phrases, each 1-120 characters after trimming, no URLs or markup (details belong in the description or documents), and no phrase twice. The phrases are shown before enrollment and read by assistants matching services to customers; the platform never enforces them. `capabilities` says what a service does; `audience` says who it is for.

### Exports and customer access requests

Three stages (unitysvc/unitysvc#2720): a target service's **`exports`** (what it exposes), a seller's **`customer_access_requests`** (what the seller asks the enrolling customer to approve: using other services through that customer's account and authority), and the issued **key's policy** (what the customer actually approved). There is no central list of services. `unitysvc_core.grants` holds the formats and the checks; it does no I/O.

**Invariant.** Calls made under an approved request run under the enrolling customer's delegated identity, within the approved permissions, options and limits. Looking up a target's `exports` validates a request's *capabilities*; customer consent and key issuance establish its *authority*. A request grants nothing by itself.

**1. The `exports` format (the offering, top level).** How other services may use this one:

```json
"exports": {
  "permissions": {"read": ["GET /records/*"], "write": ["POST /records", "PUT /records/*"]},
  "options": {
    "type": "object",
    "properties": {
      "log_level": {"type": "string", "enum": ["debug", "info", "warn", "error"], "default": "info",
                    "title": "Lowest level recorded"},
      "max_size": {"type": "integer", "minimum": 1, "maximum": 10000}
    }
  },
  "options_ui_schema": {"log_level": {"ui:widget": "radio"}}
}
```

- **Without `permissions`** (most services, including one that declares only `options`), the service exports one permission, `invoke`, on every route (`"* /*"`); `service:<name>` and the `service:*` wildcard grant it.
- **With `permissions`**, the service exports exactly those, and `invoke` only if it declares it too. A data service (`me`, `log`, `needs`) declares `read` / `write` and no `invoke`: it holds the customer's own data, so only a grant that names its permissions reaches it, never `invoke` or a wildcard.
- `permissions`: a name (lowercase letters, digits and `_`) → the routes it covers, each `"<METHOD> /<path>"` (`*` for any method, `*` globs in the path). Permissions are never inferred from HTTP methods.
- `options`: a JSON Schema (draft 7) object of the customizations a grant may set on the service, like a channel's `params.schema`, with an optional react-jsonschema-form `options_ui_schema`; the frontend renders it with the same form component. It covers limits only the service can apply (a message size). Restrictions so a grant can write every value in query-string syntax: property names are lowercase letters, digits and `_`; each property is a `string`, `integer`, `number`, `boolean` or an `array` of strings, serialized as an OpenAPI form-style query parameter with arrays comma-joined (`style: form, explode: false`); options are optional (no `required`) and only declared properties are accepted; the schema is self-contained (no `$ref`), so validating against it never fetches anything. A property's `default` is what the service does when the policy does not set the option.

**2. A customer access request (`upstream_access_config.<channel>.customer_access_requests`), and the policy entry it becomes.** Any channel may declare its own list; each approved request becomes an entry (a grant) in the issued key's policy, in the same shape:

```json
"customer_access_requests": [{"object": "service:log", "permissions": ["invoke"], "required": true,
                "justification": "Records each check you run",
                "options": "log_level=warn",
                "limits": {"count_per_day": 1000}}]
```

- `object` names one service (`service:<name>`, no wildcards), or `apikey` for the leased key's lifespan (below). `enrollment:<CODE>` (one enrollment of an enrollable service) may be allowed later.
- `options` is written in query-string syntax (`parse_options`; a notation, not how options are delivered): each name once, each value non-empty; with a catalog, each value is converted to its declared type and validated against the target's `options` schema.
- **Enrollment is where options and limits are set.** In a request, `options` and `limits` only pre-fill the enrollment form for convenience. When enrolling, the customer confirms and sets the options (a form rendered from the target's `options` schema) and the limits, and adds their own limits; the result is the issued key's policy.
- `limits` (`validate_limits`): `count_per_hour` / `count_per_day` / `count_per_month` / `count_total` (positive integer) and the same for `amount_` (finite positive decimal, as a number or a string). Limits are universal: the platform meters every call, so any service can be limited without exporting anything. The seller's limits are a pre-fill; the customer sets them at enrollment, and an enrollment's own limits (unitysvc/unitysvc#2687) use the same shape. They are counted per policy entry (the enrollment's calls to that service) from the usage stream (counters keyed by scope, metric and window bucket), and the gateway refuses calls once one is exceeded.
- **A longer-lived leased key** is asked for with one `apikey` entry: `{"object": "apikey", "limits": {"api_key_lifespan": 14400}, "justification": "Batch runs take up to four hours"}`. It names the key itself, so it takes only `limits` (exactly `api_key_lifespan`, whole seconds from 1 to 86400) and a `justification`, needs no catalog lookup, and grants nothing. Without it, a key the seller leases for the enrollment lives `DEFAULT_API_KEY_LIFESPAN` (3600 s). `api_key_lifespan` is valid only on this entry.

**3. How options reach the service.** Not through the upstream URL: the gateway never changes a target's `base_url` or query to carry policy. How an option is applied is up to the service's implementation:

- **A service implemented in the gateway** (`me`, `log`, `ai`, `notify`, `email`, `bill`) reads the calling key's policy from the routing context and applies the option itself (`me` gives values only for the grant's `profile` paths; `log` would drop entries below a grant's `log_level`).
- **A proxied service** can only be influenced through its own upstream's interface, so it applies an option only where its configuration maps it into the upstream request in a form that upstream understands, for example a request transformer setting a body field. Until such a mapping exists, options are enforced by gateway-implemented services only.
- The policy itself never reaches a seller: a seller's upstream sees at most the effect of an option, in its own vocabulary.
- An option the policy does not set takes the service's `default`. A plain `service:*` key sets none.
- Enrollment parameters are unchanged and keep working alongside: `{{ params.X }}` still renders into the channel config.

**4. Where exports are published.** In the public catalog bundle: every service row carries its `exports` exactly as declared (`null` when none). `GET /v1/catalog/bundle` returns the bundle's URL; a `service:<name>` is the row whose `name` is `<name>`.

**5. Validating `customer_access_requests`.** `validate_channel_access_requests(upstream_access_config, resolve)` checks every channel's requests. Limits need no lookup. Every claim's permissions and options, `invoke` included (a service that declares permissions may not export it), are looked up: `resolve(name)` returns the target's `exports` from the catalog (`{}` when it declares none) or `None` when no such service is published; core parses it with `parse_exports` and checks the claimed permissions and the options (names, then values against its `options` schema) against it, refusing an unpublished target. Without a resolver only the shape is checked.

- `DataValidator(data_dir, schema_dir, exports_resolver=...)` runs both checks on every `offering_v1` (`exports` and each channel's `customer_access_requests`). The seller SDK passes a resolver that downloads the catalog bundle once and reads the target row's `exports`; offline it passes none and gets the shape checks.
- Backend ingest calls the same function with a resolver over the published services, so an unknown target, permission or option fails at upload.

**6. From approvals to a policy (`build_policy`).** When the customer enrolls, `build_policy(requests, approvals, resolve)` turns their approvals of the channel's requests into the policy the enrollment keeps and every key leased for it copies (unitysvc/unitysvc#2766):

```json
[{"object": "service:log", "permissions": ["invoke"], "options": "log_level=error", "limits": {"count_per_day": 200}},
 {"object": "apikey", "limits": {"api_key_lifespan": 7200}}]
```

- Each approval names a requested object, at most once, with some of the permissions requested. Every `required` request must be approved; optional ones may be left out.
- Options and limits are the customer's. They are checked against the target's `exports` (with `resolve`) and `validate_limits`, not against the request, whose values only pre-fill the form.
- The `apikey` lifespan may be shortened, never lengthened beyond the request.
- The result drops `required` / `justification` and empty `options` / `limits`. `api_key_lifespan(policy)` reads the lifespan back (the default when there is none).

### Upstream access channels

`upstream_access_config` maps a channel name to a channel (`UpstreamAccessConfig`, `ChannelConfig`; unitysvc/unitysvc#2730). A channel describes one way the platform reaches the upstream, as a whole, and its `servers` hold the connection, typed by the channel's protocol:

| Channel key | What it is |
| --- | --- |
| `access_method` | `http` (default), `smtp`, `s3` or `mcp`; selects the server type |
| `type` | normally derived by the platform; declare `external` (or `enrollable`) to state it |
| `description`, `is_active`, `sort_order` | display text; inactive channels are skipped; rank, lowest first |
| `request_formats`, `applicable_interfaces` | the formats the channel serves; the interfaces that reach it (display only) |
| `rate_limit_refs` | the provider rate limits it consumes |
| `customer_access_requests` | what the enrolling customer is asked to approve (see above) |
| `params` | the enrollment parameters (below) |
| `servers` | the connection: `HttpServer`, `SmtpServer`, `S3Server` or `McpServer` |

The common one-server case writes the connection fields directly on the channel; they are read as `servers[0]`. When a channel has both, each server inherits the direct fields it does not set. Any string may be a template (`{{ params.X }}`, `${ secrets.X }`, `${ customer_secrets.X ?? default }`), so numeric and boolean connection fields also accept strings.

Unknown keys are refused. Retired keys are refused with what to do instead (`RETIRED_CHANNEL_KEYS`): `raw`, `is_primary`, the listing-style parameter trio, `grants`, `rate_limits` (nothing reads it; use `rate_limit_refs`), `storage_type` (use `access_method: s3`), `response_rules`, `timeout_ms`, `transport`, and the platform-derived `customer_secrets_required` / `customer_secrets_optional`.

### Enrollment parameters on channels

A channel declares the enrollment parameters it reads, so each plan of a service asks for what it needs (unitysvc/unitysvc#2726). A listing declares none: `user_parameters_schema`, `user_parameters_ui_schema` and `service_options.ops_testing_parameters` are refused.

```json
"upstream_access_config": {
  "plus": {
    "base_url": "https://u/{{ params.folder }}",
    "params": {
      "schema": {"type": "object", "properties": {"folder": {"type": "string"}}, "required": ["folder"]},
      "ui_schema": {},
      "testing_defaults": {"folder": "ops"}
    }
  }
}
```

`params.schema` is a JSON Schema of the parameters (a property's own `default` pre-fills the customer's form), `params.ui_schema` holds form hints, and `params.testing_defaults` holds the values the platform tests the channel with. Rules:

- a channel reads only the parameters its `params.schema` declares, and a channel that reads `{{ params.* }}` must declare `params` (`validate_channel_parameters`, on every `offering_v1`);
- each required parameter has a value in that channel's `params.testing_defaults`, which is also what `${ customer_secrets.{{ params.X }} }` renders with when secret references are checked;
- each required parameter is read by its own channel or by document content (`validate_required_parameter_usage`, which backend ingest also calls).
