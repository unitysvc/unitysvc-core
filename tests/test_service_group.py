"""Tests for ``ServiceGroupData`` / ``ServiceGroupV1`` and ``validate_service_group``.

These cover the slug-format constraint on ``name`` and
``parent_group_name`` (matching the unitysvc backend's CHECK
constraint pattern) and the admin-facing fields the model now
carries (``owner_type``, ``owner_id``, ``group_type``,
``access_interface_data_template``).
"""

import pytest
from pydantic import ValidationError

from unitysvc_core.models import (
    GroupOwnerTypeEnum,
    GroupTypeEnum,
    ServiceGroupData,
    ServiceGroupStatusEnum,
    ServiceGroupV1,
    validate_service_group,
)


class TestServiceGroupDataNameSlug:
    def test_valid_slugs_accepted(self):
        for slug in ("default", "fireworks-llm", "v1_chat", "abc123", "0fallback"):
            ServiceGroupData(name=slug, display_name="X")

    @pytest.mark.parametrize(
        "bad",
        ["Provider SDK", "Default", "llm.audio", "-default", "_leading", "", "x" * 101],
    )
    def test_invalid_names_rejected(self, bad: str):
        with pytest.raises(ValidationError):
            ServiceGroupData(name=bad, display_name="X")

    def test_parent_group_name_also_validated(self):
        ServiceGroupData(name="ok", display_name="X", parent_group_name="parent-ok")
        with pytest.raises(ValidationError):
            ServiceGroupData(name="ok", display_name="X", parent_group_name="Bad Name")


class TestServiceGroupDataAdminFields:
    """The admin-facing fields are optional with sensible platform defaults."""

    def test_defaults(self):
        g = ServiceGroupData(name="ok", display_name="X")
        assert g.owner_type is GroupOwnerTypeEnum.platform
        assert g.owner_id is None
        assert g.group_type is GroupTypeEnum.regular
        assert g.access_interface_data_template is None

    def test_admin_fields_set(self):
        g = ServiceGroupData(
            name="ok",
            display_name="X",
            owner_type=GroupOwnerTypeEnum.seller,
            owner_id="00000000-0000-0000-0000-000000000001",
            group_type=GroupTypeEnum.category,
            access_interface_data_template='{"base_url": "https://x"}',
        )
        assert g.owner_type is GroupOwnerTypeEnum.seller
        assert g.group_type is GroupTypeEnum.category
        assert g.access_interface_data_template == '{"base_url": "https://x"}'


class TestServiceGroupV1FileModel:
    """``extra='forbid'`` keeps file-level validation strict."""

    def test_minimal_valid_file(self):
        ServiceGroupV1(name="my-group", display_name="X")

    def test_unknown_field_rejected(self):
        with pytest.raises(ValidationError):
            ServiceGroupV1(name="my-group", display_name="X", bogus="value")

    def test_admin_fields_pass_through(self):
        g = ServiceGroupV1(
            name="my-group",
            display_name="X",
            owner_type="platform",
            group_type="regular",
            status="active",
        )
        assert g.status is ServiceGroupStatusEnum.active


class TestValidateServiceGroupDict:
    """Dict-level validator used by callers without Pydantic instantiation."""

    def test_valid_minimal(self):
        assert validate_service_group({"name": "ok", "display_name": "X"}) == []

    def test_missing_name_and_display(self):
        errors = validate_service_group({})
        assert any("Missing required field: name" in e for e in errors)
        assert any("Missing required field: display_name" in e for e in errors)

    def test_dotted_name_rejected(self):
        """Catches the production ``llm.audio``-style offenders."""
        errors = validate_service_group({"name": "llm.audio", "display_name": "X"})
        assert any("URL-friendly slug" in e for e in errors)

    def test_uppercase_name_rejected(self):
        errors = validate_service_group({"name": "MyGroup", "display_name": "X"})
        assert any("URL-friendly slug" in e for e in errors)

    def test_parent_group_name_validated(self):
        errors = validate_service_group(
            {"name": "ok", "display_name": "X", "parent_group_name": "Bad Parent"}
        )
        assert any("parent_group_name" in e for e in errors)

    def test_unknown_owner_type_rejected(self):
        errors = validate_service_group(
            {"name": "ok", "display_name": "X", "owner_type": "alien"}
        )
        assert any("owner_type" in e for e in errors)

    def test_unknown_group_type_rejected(self):
        errors = validate_service_group(
            {"name": "ok", "display_name": "X", "group_type": "exotic"}
        )
        assert any("group_type" in e for e in errors)
