"""File-level validation model for service group spec files.

Adds the ``schema`` field required for file identification and
strict validation (``extra="forbid"``).
"""

from pydantic import ConfigDict, Field

from .service_group_data import SERVICE_GROUP_SCHEMA_VERSION, ServiceGroupData


class ServiceGroupV1(ServiceGroupData):
    """Service group file model with schema version validation.

    Example file:
    ```json
    {
        "schema": "service_group_v1",
        "name": "my-llm-services",
        "display_name": "My LLM Services",
        "description": "LLM services for targeted promotions",
        "membership_rules": {
            "expression": "service_type == 'llm'"
        },
        "status": "private"
    }
    ```
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(
        default=SERVICE_GROUP_SCHEMA_VERSION,
        description="Schema identifier for validation",
        alias="schema",
    )
