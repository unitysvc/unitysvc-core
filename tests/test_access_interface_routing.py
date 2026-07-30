"""Tests for the interface / channel split introduced in unitysvc/unitysvc#1717.

- ``AccessInterfaceData`` is a pure routing-resolution object: it no longer
  accepts ``rate_limits`` / ``constraints`` / ``response_rules``.
- ``upstream_access_config`` is an opaque, open per-channel object (no typed
  ``UpstreamAccessConfigData`` model). Channels are heterogeneous (http /
  smtp / s3 / raw) so nothing is enforced at the schema level; the gateway
  reads them as raw JSONB.
"""

import pytest
from pydantic import ValidationError

from unitysvc_core import models
from unitysvc_core.models import AccessInterfaceData


class TestAccessInterfaceIsPureRouting:
    def test_minimal_interface_validates(self):
        ai = AccessInterfaceData(base_url="https://api.example.com")
        assert ai.base_url == "https://api.example.com"

    @pytest.mark.parametrize("field", ["rate_limits", "constraints", "response_rules"])
    def test_upstream_only_fields_rejected_on_interface(self, field):
        # These moved to the channel (rate limits) or were dropped (constraints);
        # extra="forbid" means the pure-routing interface now rejects them.
        with pytest.raises(ValidationError):
            AccessInterfaceData(base_url="https://api.example.com", **{field: {}})


class TestUpstreamConfigIsOpen:
    def test_no_typed_channel_model_is_exported(self):
        # The typed channel model was intentionally removed — channels are opaque.
        assert not hasattr(models, "UpstreamAccessConfigData")
