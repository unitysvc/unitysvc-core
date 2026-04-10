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
├── schema/                # Generated JSON schemas
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
`json5`, `tomli-w`. No `typer`, no `rich`, no `httpx`.

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
