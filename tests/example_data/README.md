# Example Data

This directory contains example data structures showing how to properly format provider and service data using the latest schemas.

## Structure

The examples demonstrate two different file formats:

- **provider1/**: TOML format examples (based on Fireworks.ai)
- **provider2/**: JSON format examples (based on OpenAI)
- **provider3/**: TOML format examples (based on Anthropic)

## Service Types Demonstrated

### provider1/services/service1/ - LLM Service (TOML)

- **Service Type**: `llm` (Large Language Model)
- **Example**: Chronos Hermes 13B v2
- **Pricing**: Per 1M tokens ($0.20)
- **Format**: TOML with complex nested structures

### provider2/services/service2/ - Image Generation Service (JSON)

- **Service Type**: `image_generation`
- **Example**: FLUX Pro Ultra
- **Pricing**: Per image ($0.04)
- **Format**: JSON with image-specific metadata

### provider3/services/service3/ - Embedding Service (TOML)

- **Service Type**: `embedding`
- **Example**: Nomic Embed Text v1.5
- **Pricing**: Per 1M tokens ($0.008)
- **Format**: TOML with embedding-specific fields

## Schema Compliance

All examples conform to the latest `offering_v1` and `provider_v1` schemas with:

-  Required fields populated
-  Proper service type classification
-  Decimal pricing values (no $ symbols)
-  Separate input/output pricing for token models
-  Real-world metadata from actual services

## Validation

Run validation with:

```bash
unitysvc_core validate-data tests/example_data
```

**Note**: Directory naming validation will show warnings since these are examples using generic names (provider1, service1, etc.) but contain realistic service names in the data.

## Key Schema Fields

### Service Schema (offering_v1)

- `schema`: Schema version identifier
- `time_created`: ISO timestamps
- `name`: Service identifier
- `service_type`: From ServiceTypeEnum (llm, embedding, image_generation, etc.)
- `display_name`: Human-readable name
- `description`: Service description
- `status`: draft/ready/deprecated
- `details`: Technical specifications
- `upstream_access_config`: API connection details (dict keyed by interface name)
- `payout_price`: Pricing structure

### Provider Schema (provider_v1)

- `schema`: Schema version identifier
- `name`: Provider identifier
- `contact_email`: Support contact
- `homepage`: Provider website
- `terms_of_service`: URL or file reference
- `services_populator`: Automation command (optional)

## Usage

Use these examples as templates for creating new provider/service data:

1. Copy the appropriate format directory
2. Update names, URLs, and metadata
3. Adjust pricing and technical specifications
4. Validate with the schema validation script
