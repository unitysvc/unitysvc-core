#!/usr/bin/env python3
"""Regenerate the bundled JSON schemas from the pydantic models.

Each ``schema/<name>_v1.json`` is the JSON Schema of the matching ``*V1``
model; data files declare ``schema: <name>_v1`` and the validator checks
them against these files. They MUST stay in sync with the models — a model
change that isn't reflected here silently rejects (or accepts) the wrong
data. CI runs this script and fails if the output differs from what's
committed (see ``.github/workflows/ci.yml``); run it after any model change.

``schema/base.json`` (the pricing-union reference schema) is intentionally
NOT generated here: no data file validates against it and it is maintained
separately.

Usage::

    python scripts/generate_schemas.py            # write the files
    python scripts/generate_schemas.py --check     # exit 1 if out of date
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from unitysvc_core.models.listing_v1 import ListingV1
from unitysvc_core.models.offering_v1 import OfferingV1
from unitysvc_core.models.promotion_v1 import PromotionV1
from unitysvc_core.models.provider_v1 import ProviderV1
from unitysvc_core.models.service_group_v1 import ServiceGroupV1

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "src" / "unitysvc_core" / "schema"

MODELS = {
    "offering_v1": OfferingV1,
    "listing_v1": ListingV1,
    "provider_v1": ProviderV1,
    "promotion_v1": PromotionV1,
    "service_group_v1": ServiceGroupV1,
}


def render(model: type) -> str:
    """Serialize a model's JSON schema in the committed on-disk format."""
    return json.dumps(model.model_json_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Fail if any schema is out of date instead of writing.")
    args = parser.parse_args()

    stale: list[str] = []
    for name, model in MODELS.items():
        path = SCHEMA_DIR / f"{name}.json"
        rendered = render(model)
        if args.check:
            current = path.read_text(encoding="utf-8") if path.exists() else ""
            if current != rendered:
                stale.append(name)
        else:
            path.write_text(rendered, encoding="utf-8")
            print(f"wrote {path.relative_to(SCHEMA_DIR.parent.parent.parent)}")

    if args.check and stale:
        print(f"::error::Schemas out of sync with models: {', '.join(stale)}", file=sys.stderr)
        print("Run `python scripts/generate_schemas.py` and commit the result.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
