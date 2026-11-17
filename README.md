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
│   ├── container.py       # Container channel parts: resources, quantities, entries
│   ├── manifest.py        # NormalizedManifest: what the compute agent receives
│   ├── probes.py          # Declarative connectivity probes (proposal)
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
├── instance_spec.py       # validate_instance_spec / compile_instance_spec (Compose)
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

**2. A customer access request (`upstream_access_config.<channel>.customer_access_requests`), and the policy entry it becomes.** Any channel may declare its own list: a fixed list of items the customer works on, and nothing else (unitysvc/unitysvc#2831). Each approved request becomes an entry (a grant) in the issued key's policy, in the same shape:

```json
"customer_access_requests": [{"object": "service:log", "permissions": ["invoke"], "required": true,
                "justification": "Records each check you run",
                "options": "log_level=warn",
                "limits": {"count_per_day": 1000}}]
```

- `object` names one service (`service:<name>`, no wildcards), a resource the platform provisions for the enrollment (`resource:room`, below), or `apikey` for the leased key's lifespan (below). `permissions` are plain names (lowercase letters, digits and `_`; no wildcards), checked against the target's exports with a catalog.
- `required` (`true` or `false`, on every entry, `apikey` included) says whether the customer may decline the request. A stored declaration from before this rule reads as optional when a policy is built.
- `options` is written in query-string syntax (`parse_options`; a notation, not how options are delivered): each name once, each value non-empty; with a catalog, each value is converted to its declared type and validated against the target's `options` schema.
- **A request's `options` are fixed.** Every option it declares is in the policy of an approved request, with the declared value; the customer may repeat it but not change or drop it. An option the request does not declare is the customer's to set at enrollment (a form rendered from the target's `options` schema). So `{"object": "service:ai", ...}` lets the customer pick the AI service, and `"options": "service=labs/llm-balanced"` pins it.
- Several requests may name one object (two kinds of room) only if each fixes some option to a different value (`kind=chat`, `kind=feed`), so an approval says which one it is.
- `limits` (`validate_limits`): `count_per_hour` / `count_per_day` / `count_per_month` / `count_total` (positive integer) and the same for `amount_` (finite positive decimal, as a number or a string). Limits are universal: the platform meters every call, so any service can be limited without exporting anything. The seller's limits only pre-fill the form; the customer sets them at enrollment, and an enrollment's own limits (unitysvc/unitysvc#2687) use the same shape. They are counted per policy entry (the enrollment's calls to that service) from the usage stream (counters keyed by scope, metric and window bucket), and the gateway refuses calls once one is exceeded.
- **A room the platform provisions** (unitysvc/unitysvc#2830) is asked for with `resource:room`: `{"object": "resource:room", "permissions": ["create"], "required": true, "justification": "Creates your chat room", "options": "kind=chat&max_members=100"}`. Core knows the resource, so no catalog is consulted. The request must fix `kind`, the room's identity; each kind bounds the permissions (`chat`: `create`, a room the customer owns; `support`: `join`, `read`, `post`, the seller's private room with the customer; `announcement`: `join`, `read`; `discussion`: `join`, `read`, `post`). `max_members` (a positive integer) caps only a room the customer creates; `max_retention` (seconds, 0 or more) caps how long any provisioned room keeps messages, within the platform's own ceiling for the kind, and the room's owner may keep less. A channel requests each kind at most once, and a resource takes no limits.
- **A longer-lived leased key** is asked for with one `apikey` entry: `{"object": "apikey", "required": false, "limits": {"api_key_lifespan": 14400}, "justification": "Batch runs take up to four hours"}`. It names the key itself, so it takes only `limits` (exactly `api_key_lifespan`, whole seconds from 1 to 86400), `required` and a `justification`, needs no catalog lookup, and grants nothing. Without it, a key the seller leases for the enrollment lives `DEFAULT_API_KEY_LIFESPAN` (3600 s). `api_key_lifespan` is valid only on this entry.

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

- Each approval matches one request (`match_request`): the request of its object whose fixed options it does not contradict. One that fits none is refused, as is one that fits several (it names the option that tells them apart). A request is approved at most once; every `required` one must be; optional ones may be left out. Nothing else may be added, apart from the `enrollment` entry below.
- An approved request keeps exactly its permissions (in any order) and its fixed options; the approval may leave the options out. Values compare as the target's schema types them (`max_size=02000` repeats `2000`).
- The options the request leaves open, and the limits, are the customer's, checked against the target's `exports` (with `resolve`) and `validate_limits`.
- The `apikey` lifespan may be shortened, never lengthened beyond the request.
- The result drops `required` / `justification` and empty `options` / `limits`, and writes options as one canonical query string (`options_text`: names sorted, only what must be escaped escaped), so equal policies are equal. `match_request` finds a stored entry's request again, for a readout or a diff. `api_key_lifespan(policy)` reads the lifespan back (the default when there is none).

A policy has three kinds of entry (unitysvc/unitysvc#2770):

| Entry | Means |
| --- | --- |
| `{"object": "enrollment", "limits": {"amount_per_day": "1.00"}}` | This enrollment's own limits (total spending, say). The customer adds it without a request; it takes only `limits`, at least one, at most one entry. It grants nothing. |
| `{"object": "service:ai", ...}` | A service the app calls for the customer. A seller may name a specific one (`service:labs/llm-premium` instead of `service:ai` with `options="service=labs/llm-premium"`), at the cost of the customer's choice of provider, which a customer may decline. |
| `{"object": "service:<name>", "enrollment": "<CODE>", ...}` | One of the customer's enrollments of a service that requires enrolling (a chain enrollment), called through `/e/<CODE>`. The customer picks the enrollment when approving the request for `service:<name>`, so a request never carries a code; a request with fixed options can't be approved with one, since an enrollment carries no options. A canonical code, no `options` (the enrollment's params are fixed), permissions and limits as for any service entry. Core checks the code's form; the caller checks the enrollment is the customer's. |

### Upstream access channels

`upstream_access_config` maps a channel name to a channel (`UpstreamAccessConfig`, `ChannelConfig`; unitysvc/unitysvc#2730). A channel describes one way the platform reaches the upstream, as a whole, and its `servers` hold the connection, typed by the channel's protocol:

| Channel key | What it is |
| --- | --- |
| `access_method` | `http` (default), `smtp`, `s3`, `mcp` or `container`; selects the server type |
| `type` | normally derived by the platform; declare `enrollable` to state it |
| `description`, `is_active`, `sort_order` | display text; inactive channels are skipped; rank, lowest first |
| `request_formats`, `applicable_interfaces` | the formats the channel serves; the interfaces that reach it (display only) |
| `rate_limit_refs` | the provider rate limits it consumes |
| `customer_access_requests` | what the enrolling customer is asked to approve (see above) |
| `params` | the enrollment parameters (below) |
| `request_rules` | `http` channels only: rules the gateway answers itself before dispatch (below) |
| `servers` | the connection: `HttpServer`, `SmtpServer`, `S3Server` or `McpServer` (a `container` channel's are `HttpServer`s) |
| `resources`, `entries` | `container` channels only: the tier's resources and its entries (below) |

The common one-server case writes the connection fields directly on the channel; they are read as `servers[0]`. When a channel has both, each server inherits the direct fields it does not set. Any string may be a template (`{{ params.X }}`, `${ secrets.X }`, `${ customer_secrets.X ?? default }`), so numeric and boolean connection fields also accept strings.

Unknown keys are refused. Retired keys are refused with what to do instead (`RETIRED_CHANNEL_KEYS`): `raw`, `is_primary`, the listing-style parameter trio, `grants`, `rate_limits` (nothing reads it; use `rate_limit_refs`), `storage_type` (use `access_method: s3`), `response_rules`, `timeout_ms`, `transport`, and the platform-derived `customer_secrets_required` / `customer_secrets_optional`.

### Container channels

A `container` channel is one tier of a service that runs as instances on Kubernetes (unitysvc/unitysvc-core#105; the compute agent is unitysvc-compute-k8s). Core only holds the data format and its validation: no Kubernetes client, image pulling, placement or pricing. One channel per tier (`base`, `team`), each with its own `resources` and its own price in the listing's channel-keyed `list_price`.

```json
"service_type": "container",
"instance_spec": "compose.yaml",
"upstream_access_config": {
  "base": {
    "access_method": "container",
    "resources": {"limits": {"cpu": "250m", "memory": "256Mi", "ephemeral-storage": "1Gi"}},
    "entries": [{"name": "web", "service": "web", "port": 5678, "auth": "key"}]
  }
}
```

- There is no per-channel spec: every container channel of the offering runs the offering's one instance spec (below), so tiers differ in `resources`, not in stack. The earlier `spec` field (a Document title) is removed in 0.3.11; a channel carrying it is refused as an unknown field.
- `resources` is the tier total, in Kubernetes' shape: `limits`, optionally `requests`, each a map from a resource name to a quantity (`250m`, `1Gi`, `1e3`, parsed as Kubernetes parses them). The names are `cpu`, `memory`, `ephemeral-storage` and `nvidia.com/gpu`; the first three limits are required; a request is at most its limit; a GPU is a whole number with a request equal to the limit. `resources.services` is an optional per-service split (`{"web": {"limits": {...}}}`) whose sums must stay within the tier (each share names `cpu`, `memory` and `ephemeral-storage`, and a resource without a request counts at its limit, as the agent resolves it); a spec with several services needs one. Quantities are at most 64 characters with an exponent of at most 30. A Compose `deploy.resources` is ignored: the channel is authoritative.
- The channel is always enrollable and needs no `customer_access_requests`: the customer runs their own instance, which grants the seller nothing (a request is for the seller to call the customer's services). A channel that does declare one is `me_bound` instead of `enrollable`.
- `servers` is optional and has no container-specific meaning: each is an ordinary `HttpServer` the gateway reaches, for example `{"base_url": "https://{{ enrollment.code }}.unitysvc.app/"}`. A container-only service sets no `servers`. An enrollable channel is reached at `/e/<CODE>` on the API gateway, which the platform provides; a listing never declares it (see Canonical user access interfaces).
- `entries` (`name`, `service`, `port`, optional `path`, `auth` of `login`, `key` or `app`) is **provisional** (open question 1 of #105: whether instances need URLs beyond `servers`, and which `auth` values apply; `gateway` is deliberately not accepted).
- `params` and `customer_access_requests` are as on the other channels.

**The instance spec.** An offering with a `container` channel has exactly one instance spec, from either of two forms (two, or one on an offering with no container channel, is an error); a different stack is a different offering:

- the offering-level shortcut, like `logo`: `"instance_spec": "compose.yaml"`, a path relative to the offering file or an https URL, in `compose` format;
- a Document of category `instance_spec`, `"meta": {"format": "compose"}` (the default and the only format accepted; any other is refused with a message saying so), with exactly one of `file_path` and `external_url`.

The seller tooling expands the shortcut before upload with `expand_instance_spec(offering)`, which returns the offering with `documents["Instance spec"]` added (`file_path` or `external_url`, not public) and the shortcut removed, and raises if the offering already has an instance-spec Document. Core accepts both forms. A format is accepted only if it compiles to the normalized manifest, which is why a Helm chart is not (it renders arbitrary Kubernetes objects). `resources.services` keys are checked against the services of that one file, for every container channel.

*A URL is a snapshot source, not a link.* For this category core validates only the URL's shape: https, a host, no credentials, no `localhost`/`.local`/`.internal` name, no private, loopback or link-local IP literal (including integer and hex forms). Core fetches nothing and cannot check the content of a URL, so it validates the file only when it is local. `meta.source` (the original URL) and `meta.sha256` (64 lowercase hex characters, the bytes the seller tested) are accepted on the Document, and a local file is checked against `meta.sha256`. The contract for the **backend** (not implemented in core; unitysvc/unitysvc-core#107):

1. At ingest, fetch the `external_url` (or the shortcut URL) once, store the bytes in the content-addressed object store, and keep the original URL in `meta.source`. Never fetch at enrollment; re-uploading is what refreshes the snapshot. The cluster runs what the policy approved.
2. Run `validate_instance_spec` on the snapshot (with each container channel) and refuse the upload if it has errors. A failed fetch fails the upload with a clear error.
3. https only, with a size cap and a timeout; limit redirects and apply the checks to each hop; refuse private, loopback and link-local addresses after resolving the host (SSRF); start with a host allowlist (`raw.githubusercontent.com`).
4. Rewrite `github.com/<o>/<r>/blob/<ref>/<path>` pages to the raw URL. Warn on a branch URL (`/main/`); a commit-SHA permalink is the recommended form.
5. If `meta.sha256` is set and the snapshot's differs, refuse the upload.

`specs validate` and the local Docker `run-tests` in the seller tooling download the file themselves.

**The Compose policy** is an allowlist: a key in none of these is rejected, so a new Compose feature is never silently accepted. The compose file is parsed as YAML without executing anything (duplicate keys are refused).

| Bucket | Features |
| --- | --- |
| Honor | `image` (a tag, or a literal digest `name:tag@sha256:...`; a tag-only image warns), `command`, `entrypoint`, `environment`, `ports` (container side only), `healthcheck`, `working_dir`, `user`, `read_only` |
| Ignore, warn | `container_name`, `restart`, `labels`, `logging`, `networks`, the host side of `ports`, `expose`, `profiles`, `links`, `x-*`, `deploy.resources`, top-level `name`, `version`, `networks` |
| Ignore, warn loudly | `depends_on` (no start ordering: the app must retry; readiness keeps traffic away), `env_file` (the values it would supply are missing: provide them with the channel's `environment`), named volumes (scratch space: data survives a container restart, not a replacement of the instance) |
| Reject | `build`, `privileged`, `cap_add`, `devices`, `network_mode`, `pid`, `ipc`, `security_opt`, `sysctls`, bind mounts and host paths, `volumes_from`, a named volume shared by two services, `runtime`, `extra_hosts`, `secrets`, `configs`, `extends`, `include`, an `image` with a malformed digest, two services that share no network, any other key |

A seller can keep one file for local Docker and the platform and leave `deploy.resources` in place. **Images.** A tag-only or untagged `image` (`:latest`, `:1.4`, none) is accepted with a note (`unpinned_image`, a warning): the platform runs it as written and reports the digest it pulled, and a moving tag can change at a restart; a re-upload does not update running instances. A digest the seller writes (`name:tag@sha256:...`) is honored as written; one that is malformed is an error (`bad_digest`). Core never contacts a registry. Sibling services reach each other by name (`redis:6379`); service names must be DNS labels. `${VAR}` in an `environment` value is kept as written with a warning (how a secret or parameter binds to it is open question 4 of #105); in `image` and `ports` a variable is an error.

**Reusing a generic Compose file, and the environment.** A seller keeps one generic, plain Compose file (it runs as it is with Docker) and sets the instance's environment on each container channel: `environment` is `{service: {NAME: "value"}}`; each service must exist in the file and each name is a valid variable name. A value may be, or contain, `{{ params.NAME }}`: that channel's own enrollment parameter, which the platform binds when the instance starts (any other `{{` is refused, and `{{ params` in the Compose file is a `bad_template` error). The channel's value overrides the file's own `environment` on a name conflict. It is applied only at compile time (`compile_instance_spec(text, resources, environment=...)`), so the stored Compose bytes stay an exact copy of the seller's file; `validate_instance_spec(text, channel=...)` checks the channel's environment against the file (`overlay_service`). There is no platform-issued instance key: a secret is an ordinary enrollment parameter. A Document's `meta.environment` is no longer supported. `meta.sha256` is recommended with a URL, so an upstream change that breaks the environment fails the upload.

```json
"documents": {"Instance spec": {
  "category": "instance_spec", "mime_type": "text",
  "external_url": "https://raw.githubusercontent.com/Kadxy/weixin-ilink-gateway/<commit-sha>/docker-compose.yaml",
  "meta": {"format": "compose", "sha256": "<64 hex>"}
}},
"upstream_access_config": {"base": {"access_method": "container",
  "params": {"schema": {"properties": {"WEIXIN_API_KEY": {"type": "string", "default": "{{ token_urlsafe(32) }}"}}, "required": ["WEIXIN_API_KEY"]}},
  "environment": {"gateway": {"API_KEY": "{{ params.WEIXIN_API_KEY }}"}}}}
```

**Validating.** `validate_instance_spec(text, format="compose", channel=ContainerChannel | None)` returns an `InstanceSpecReport` of `errors` and `warnings`, each an `InstanceSpecIssue(code, path, message, loud)`; with the channel it also checks that every entry targets a declared service and port and that `resources.services` names the spec's services. It never raises on bad input. The seller CLI and backend ingest call it, directly or through `DataValidator`: `validate_service` / `validate_data_file` report the errors of every container channel's spec under `upstream_access_config.<channel>.instance_spec`, and `DataValidator.service_warnings(offering, service_dir)` returns the warnings (`issue.loud` for the ones a seller must not miss) for `specs validate` to print after the errors.

**The normalized manifest** is what the agent receives for one instance: the compiled, validated spec. `NormalizedManifest` (JSON Schema `schema/normalized_manifest_v1.json`, which the agent repo vendors) has one service per Compose service with `name`, `image`, `ports`, `env`, `command`, `args`, `resources` (`requests`, `limits`), `replicas`, `health` and `scratch` (`[{"path": "/data"}]`, from each named or anonymous volume mount; the cluster gives each an `emptyDir`), in the shapes of the agent's `intent.go` (`scratch` is a core addition the agent vendors the schema for later). `compile_instance_spec(text, resources)` returns a `CompiledInstanceSpec`: the manifest, the keys it `dropped` and the warnings. Compose `entrypoint` becomes `command` and Compose `command` becomes `args`; `healthcheck` becomes an `exec` `health` (`interval`, `timeout`, `retries` and `start_period` map to `period_seconds`, `timeout_seconds`, `failure_threshold` and `start_period_seconds`) (bounded as the agent bounds them: period and timeout 1-300 s, retries 1-30, start period at most its default 120 s progress deadline); the tier `resources` go to the single service, or are split by `resources.services`. That core compiles at all is a proposal (open question 3 of #105). `working_dir`, `user` and `read_only` are accepted by the policy but the agent's current manifest has no field for them: they are recorded in `dropped` with reason `not_carried` and a warning.

**The connectivity test is derived.** `validate_connectivity_test_exists` exempts an offering whose channels are all `container`, as it does an external app: the platform starts the services, waits for each one's `health` (or the default TCP check), fetches each entry expecting a success status, and shuts down. A seller may add a declarative `connectivity_test` Document (a `.json` file, `ProbesV1`, schema `schema/probes_v1.json`; a **proposal**), a list of probes run on top of the derived checks:

```json
{"probes": [{"entry": "web", "path": "/", "expect_status": 200, "expect_body_contains": "healthy"}]}
```

Each `entry` must name an entry of the offering's container channels. A script (any other file) is run as before. Core publishes the schema and fixtures (`tests/fixtures/probes`); the runners live in unitysvc-sellers and unitysvc-compute-k8s. The derived test is only as strict as `health`.

Examples: `tests/example_data/hashicorp/services/echo` (one service, two tiers); `tests/example_data/kadxy/services/weixin-ilink-gateway` (secrets, scratch state, outbound traffic; its image digest is a marked placeholder); and `tests/example_data/kadxy/services/weixin-ilink-gateway-upstream` (the project's own Compose file by URL at a pinned commit with `meta.sha256`, plus a channel `environment`; `upstream-compose.yaml` is a byte-for-byte copy of that file, and `meta.sha256` is its hash, as core fetches nothing).

### Request rules

An `http` channel may list up to 32 `request_rules` (unitysvc/unitysvc#2947, unitysvc/apisix-gateways#94). The gateway checks them in order after authentication, channel selection and rate limiting and before anything is dispatched. The first rule whose `when` holds answers with its static `respond`, and the request goes no further; when none holds, the request continues unchanged. A rule without `when` answers every request.

```json
"request_rules": [
  {"name": "github-ping", "when": "header[\"x-github-event\"] == \"ping\"", "respond": {"status": 200}},
  {"name": "own-echo", "when": "body.user_id == \"${ customer_secrets.DISCORD_WEBHOOK_ID ?? }\"", "respond": {"status": 204}},
  {"name": "filter", "when": "body.count >= {{ params.MIN_COUNT }}", "respond": {"status": 204}}
]
```

`when` is a Lua expression that the gateway compiles and evaluates against the request. Its environment (the request's `body`, `header`, `query` and `method`, the approved functions), its template references (`${ customer_secrets.NAME ?? }`, `${ secrets.NAME }`, `{{ params.NAME }}`, `{{ enrollment.code }}`, bound as values and never spliced into the source; a resolved secret is an ordinary value the approved functions may inspect, so anchor a substring test on a secret so that only a whole value can match) and its limits are the gateway's, documented in apisix-gateways' README. Core does not parse it: it checks only that it is non-empty and at most 1024 bytes. Lua syntax errors and invalid references are reported by the gateway (the channel answers 503) and by a gateway-run test (`services run-tests`), not by `specs validate`.

`respond` takes a `status` (200–599), an optional `body` (an object or array is sent as JSON; a string needs `content_type`), and optional `headers` (not the gateway-managed or hop-by-hop ones).

Versions: 0.3.7 accepted a two-operator grammar; 0.3.8 refused template references and added `customer_secrets`; 0.3.9 follows the gateway as merged (template references in `when`, no `customer_secrets`). No published service used 0.3.7 or 0.3.8 rules.

### External apps and product links

An offering that declares no channel (`upstream_access_config` omitted or `{}`) is an **external app**: customers reach it at its own site, not through the gateway (unitysvc/unitysvc#2873). `external` is not a channel type. An external app:

- gives its homepage in `details.links.homepage` (required; the provider's homepage is not substituted);
- has no `user_access_interfaces` and no `list_price` (signup and payment happen at its site);
- needs no connectivity-test document.

`details.links` holds an offering's product links, for any service: up to 10 free display labels (at most 40 characters each) mapped to `https://` URLs without embedded credentials. When present it must include `homepage`, the primary product link. The rest of `details` stays free-form. `validate_details_links` checks it on every `offering_v1`.

```json
{
  "details": {
    "links": {
      "homepage": "https://app.example",
      "documentation": "https://app.example/docs"
    }
  }
}
```

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
- `params.schema` is a valid, self-contained JSON Schema (draft 7, no `$ref`), and `params.testing_defaults` validates against it with format checking (an `email` property needs an email address), so a malformed schema is refused at ingest rather than when a customer enrolls;
- each required parameter has a value in that channel's `params.testing_defaults`, which is also what `${ customer_secrets.{{ params.X }} }` renders with when secret references are checked;
- each required parameter is read by its own channel or by document content (`validate_required_parameter_usage`, which backend ingest also calls).
