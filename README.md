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

The shared validator rejects required user parameters that no channel, interface,
or document consumes. Only those consumer fields count — the offering's
`upstream_access_config`, the listing's `user_access_interfaces`, and document
content (a `.j2` document's template source, which the seller validator reads from
disk as the SDK uploads it); metadata such as `display_name` or `description` is
never rendered with `params`. File validation checks a listing against its paired offering
when present; backend ingest calls the same `validate_required_parameter_usage`
helper with the complete listing and offering. Schema declarations alone are not
consumers. Jinja attribute access, string-key lookups, and `params.get("name")`
are inspected; dynamic access and whole-map iteration conservatively consume all
parameters. Deprecated content can still be retired.

This validates usage, not channel attribution: a document reference counts as a
consumer without proving that the intended paid channel takes enrollment.

### Canonical user access interfaces

A user access interface is the service's canonical address, the same for every caller, so `validate_listing_interfaces_are_canonical` refuses one that reads a per-enrollment value (`enrollment.*`, `params.*`). An enrollment is reached through the address the platform gives it: `/e/<CODE>` on the API gateway, which a listing never declares. The only per-enrollment form allowed is where the protocol has no such path and the code itself is the address: an SMTP `routing_key.username` or an S3 bucket segment that is exactly `{{ enrollment.code }}`. Values the same for every caller (`service_name`, `routing_vars`) stay allowed. The shared validator runs it on every `listing_v1`; backend ingest calls the same function (unitysvc/unitysvc#2726).

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

- `invoke` is always exported and covers every route; it may not be redeclared. A service that declares nothing (most of them) exports `invoke` only, with no options, which `service:<name>` or `service:*` covers.
- `permissions`: a name → the routes it covers, each `"<METHOD> /<path>"` (`*` for any method, `*` globs in the path). Permissions are never inferred from HTTP methods.
- `options`: a JSON Schema (draft 7) object of the query arguments the service accepts as customizations, like an enrollment's `user_parameters_schema`, with an optional react-jsonschema-form `options_ui_schema`; the frontend renders it with the same form component. It covers limits only the service can apply (a message size). Restrictions so every value is one query argument: property names are lowercase letters, digits and `_`; each property is a `string`, `integer`, `number`, `boolean` or an `array` of strings, serialized as an OpenAPI form-style query parameter with arrays comma-joined (`style: form, explode: false`); options are optional (no `required`) and only declared properties are accepted; the schema is self-contained (no `$ref`), so validating against it never fetches anything. A property's `default` is what the service does when the option is not sent.

**2. A customer access request (`upstream_access_config.<channel>.customer_access_requests`), and the policy entry it becomes.** Any channel may declare its own list; each approved request becomes an entry (a grant) in the issued key's policy, in the same shape:

```json
"customer_access_requests": [{"object": "service:log", "permissions": ["invoke"], "required": true,
                "justification": "Records each check you run",
                "options": "log_level=warn",
                "limits": {"count_per_day": 1000}}]
```

- `object` names one service (`service:<name>`, no wildcards). `enrollment:<CODE>` (one enrollment of an enrollable service) may be allowed later.
- `options` is a query string (`parse_options`): each name once, each value non-empty; with a catalog, each value is converted to its declared type and validated against the target's `options` schema.
- **Enrollment is where options and limits are set.** In a request, `options` and `limits` only pre-fill the enrollment form for convenience. When enrolling, the customer confirms and sets the options (a form rendered from the target's `options` schema) and the limits, and adds their own limits; the result is the issued key's policy.
- `limits` (`validate_limits`): `count_per_hour` / `count_per_day` / `count_per_month` / `count_total` (positive integer) and the same for `amount_` (finite positive decimal, as a number or a string). Limits are universal: the platform meters every call, so any service can be limited without exporting anything. The seller's limits are a pre-fill; the customer sets them at enrollment, and an enrollment's own limits (unitysvc/unitysvc#2687) use the same shape. They are counted per policy entry (the enrollment's calls to that service) from the usage stream (counters keyed by scope, metric and window bucket), and the gateway refuses calls once one is exceeded.

**3. How options reach the service.** No new channel: the upstream URL. When the gateway constructs the target's upstream URL for a call made under a policy entry, it appends that entry's `options` as query arguments, `?log_level=warn&max_size=2000`, the way query arguments already reach every upstream, `log://` included. Rules:

- The gateway first strips any query argument the client sent under one of the target's exported option names, then appends the policy's values. Today the client's arguments win on a name collision, so without this a caller could override its own policy.
- An option the policy does not set is not sent; the service applies its own default. A plain `service:*` key sends none.
- Enrollment parameters are unchanged and keep working alongside: `{{ params.X }}` still renders into the channel config, including a templated `base_url` query.
- The platform's own services are terminated in gateway stages before the upstream URI is built, so each stage reads its options from the request's arguments (`log_dispatch` drops entries below `log_level`).
- SMTP has no query string; options for pure-SMTP delivery are out of scope.

**4. Where exports are published.** In the public catalog bundle: every service row carries its `exports` exactly as declared (`null` when none). `GET /v1/catalog/bundle` returns the bundle's URL; a `service:<name>` is the row whose `name` is `<name>`.

**5. Validating `customer_access_requests`.** `validate_channel_access_requests(upstream_access_config, resolve)` checks every channel's requests. Limits and a default claim (`invoke`, no options) need no lookup. For any other claim, `resolve(name)` returns the target's `exports` from the catalog (`{}` when it declares none) or `None` when no such service is published; core parses it with `parse_exports` and checks the claimed permissions and the options (names, then values against its `options` schema) against it, refusing an unpublished target. Without a resolver only the shape is checked.

- `DataValidator(data_dir, schema_dir, exports_resolver=...)` runs both checks on every `offering_v1` (`exports` and each channel's `customer_access_requests`). The seller SDK passes a resolver that downloads the catalog bundle once and reads the target row's `exports`; offline it passes none and gets the shape checks.
- Backend ingest calls the same function with a resolver over the published services, so an unknown target, permission or option fails at upload.
