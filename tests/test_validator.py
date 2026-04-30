"""Tests for the data validator."""

from pathlib import Path

import pytest

from unitysvc_core.validator import DataValidator


@pytest.fixture
def schema_dir():
    """Get the schema directory path."""
    pkg_path = Path(__file__).parent.parent / "src" / "unitysvc_core"
    return pkg_path / "schema"


@pytest.fixture
def example_data_dir():
    """Get the example data directory path."""
    return Path(__file__).parent / "example_data"


def test_validator_loads_schemas(schema_dir, example_data_dir):
    """Test that the validator can load all schemas."""
    validator = DataValidator(example_data_dir, schema_dir)

    # Check that schemas were loaded
    assert len(validator.schemas) > 0
    assert "base" in validator.schemas
    assert "provider_v1" in validator.schemas
    assert "offering_v1" in validator.schemas
    assert "listing_v1" in validator.schemas


def test_validate_provider_toml(schema_dir, example_data_dir):
    """Test validation of provider TOML file."""
    validator = DataValidator(example_data_dir, schema_dir)

    provider_file = example_data_dir / "provider1" / "provider.toml"
    is_valid, errors = validator.validate_data_file(provider_file)

    if not is_valid:
        print(f"Validation errors for {provider_file}:")
        for error in errors:
            print(f"  - {error}")

    assert is_valid, f"Provider TOML validation failed: {errors}"


def test_validate_provider_json(schema_dir, example_data_dir):
    """Test validation of provider JSON file."""
    validator = DataValidator(example_data_dir, schema_dir)

    provider_file = example_data_dir / "provider2" / "provider.json"
    is_valid, errors = validator.validate_data_file(provider_file)

    if not is_valid:
        print(f"Validation errors for {provider_file}:")
        for error in errors:
            print(f"  - {error}")

    assert is_valid, f"Provider JSON validation failed: {errors}"


def test_validate_service_toml(schema_dir, example_data_dir):
    """Test validation of service TOML file."""
    validator = DataValidator(example_data_dir, schema_dir)

    service_file = example_data_dir / "provider1" / "services" / "service1" / "offering.toml"
    is_valid, errors = validator.validate_data_file(service_file)

    if not is_valid:
        print(f"Validation errors for {service_file}:")
        for error in errors:
            print(f"  - {error}")

    assert is_valid, f"Service TOML validation failed: {errors}"


def test_validate_service_json(schema_dir, example_data_dir):
    """Test validation of service JSON file."""
    validator = DataValidator(example_data_dir, schema_dir)

    service_file = example_data_dir / "provider2" / "services" / "service2" / "offering.json"
    is_valid, errors = validator.validate_data_file(service_file)

    if not is_valid:
        print(f"Validation errors for {service_file}:")
        for error in errors:
            print(f"  - {error}")

    assert is_valid, f"Service JSON validation failed: {errors}"


def test_validate_listing_toml(schema_dir, example_data_dir):
    """Test validation of listing TOML file."""
    validator = DataValidator(example_data_dir, schema_dir)

    listing_file = example_data_dir / "provider1" / "services" / "service1" / "listing.toml"
    is_valid, errors = validator.validate_data_file(listing_file)

    if not is_valid:
        print(f"Validation errors for {listing_file}:")
        for error in errors:
            print(f"  - {error}")

    assert is_valid, f"Listing TOML validation failed: {errors}"


def test_validate_listing_json(schema_dir, example_data_dir):
    """Test validation of listing JSON file."""
    validator = DataValidator(example_data_dir, schema_dir)

    listing_file = example_data_dir / "provider2" / "services" / "service2" / "listing.json"
    is_valid, errors = validator.validate_data_file(listing_file)

    if not is_valid:
        print(f"Validation errors for {listing_file}:")
        for error in errors:
            print(f"  - {error}")

    assert is_valid, f"Listing JSON validation failed: {errors}"


def test_validate_jinja2_files(schema_dir, example_data_dir, tmp_path):
    """Test validation of Jinja2 template files."""
    validator = DataValidator(example_data_dir, schema_dir)

    # Create valid Jinja2 template files for testing
    valid_template = tmp_path / "valid.md.j2"
    valid_template.write_text("# {{ listing.service_name }}\n\nProvider: {{ provider.provider_name }}")

    invalid_template = tmp_path / "invalid.py.j2"
    invalid_template.write_text("# {{ listing.service_name\n")  # Missing closing braces

    # Test valid template
    is_valid, errors = validator.validate_jinja2_file(valid_template)
    assert is_valid, f"Valid Jinja2 template failed validation: {errors}"

    # Test invalid template
    is_valid, errors = validator.validate_jinja2_file(invalid_template)
    assert not is_valid, "Invalid Jinja2 template should fail validation"
    assert len(errors) > 0, "Should have validation errors for invalid template"
    assert "Jinja2 syntax error" in errors[0], f"Error should mention Jinja2 syntax: {errors}"


def test_validate_all_files(schema_dir, example_data_dir):
    """Test validation of all files in example_data."""
    validator = DataValidator(example_data_dir, schema_dir)

    results = validator.validate_all()

    # Should have found multiple files
    assert len(results) > 0

    # Count valid vs invalid
    valid_count = sum(1 for is_valid, _ in results.values() if is_valid)
    invalid_count = len(results) - valid_count

    # Print summary
    print("\nValidation Summary:")
    print(f"  Total files: {len(results)}")
    print(f"  Valid: {valid_count}")
    print(f"  Invalid: {invalid_count}")

    if invalid_count > 0:
        print("\nInvalid files:")
        for file_path, (is_valid, errors) in results.items():
            if not is_valid:
                print(f"\n  {file_path}:")
                for error in errors:
                    print(f"    - {error}")

    # All files should be valid
    assert invalid_count == 0, f"{invalid_count} files failed validation"


def test_file_reference_validation(schema_dir, example_data_dir):
    """Test that file references are properly validated."""
    validator = DataValidator(example_data_dir, schema_dir)

    # This should validate that referenced files (like logo, documents) exist
    results = validator.validate_all()

    # Check for file reference errors
    file_ref_errors = []
    for file_path, (_is_valid, errors) in results.items():
        for error in errors:
            if "does not exist" in error or "file reference" in error.lower():
                file_ref_errors.append((file_path, error))

    if file_ref_errors:
        print("\nFile reference errors found:")
        for file_path, error in file_ref_errors:
            print(f"  {file_path}: {error}")

    # Should have no file reference errors in example data
    assert len(file_ref_errors) == 0, "File reference validation failed"


class TestFilePathAbsolutePresetExpansion:
    """validate_file_references must accept absolute file_path values that exist.

    When a $doc_preset sentinel is expanded by load_data_file, the resulting
    file_path is an absolute path inside the installed unitysvc_data package.
    The validator must not reject it as "must be a relative path".
    """

    def _make_validator(self, schema_dir, example_data_dir):
        return DataValidator(example_data_dir, schema_dir)

    def test_absolute_existing_file_path_is_accepted(self, schema_dir, example_data_dir, tmp_path):
        """An absolute file_path that resolves to a real file should pass."""
        real_file = tmp_path / "bundled.sh"
        real_file.write_text("#!/bin/sh\necho hello\n")

        data = {
            "documents": {
                "My Doc": {
                    "category": "connectivity_test",
                    "description": "desc",
                    "mime_type": "text/x-shellscript",
                    "file_path": str(real_file),
                }
            }
        }
        validator = self._make_validator(schema_dir, example_data_dir)
        listing_json = tmp_path / "listing.json"
        errors = validator.validate_file_references(data, listing_json, set())
        assert errors == [], f"Unexpected errors: {errors}"

    def test_absolute_nonexistent_file_path_is_rejected(self, schema_dir, example_data_dir, tmp_path):
        """An absolute file_path pointing to a missing file should be rejected."""
        data = {
            "documents": {
                "My Doc": {
                    "category": "connectivity_test",
                    "description": "desc",
                    "mime_type": "text/x-shellscript",
                    "file_path": "/nonexistent/path/to/file.sh",
                }
            }
        }
        validator = self._make_validator(schema_dir, example_data_dir)
        listing_json = tmp_path / "listing.json"
        errors = validator.validate_file_references(data, listing_json, set())
        assert any("must be a relative path" in e for e in errors), f"Expected rejection, got: {errors}"

    def test_relative_existing_file_path_is_accepted(self, schema_dir, example_data_dir, tmp_path):
        """A relative file_path that resolves to a real file should pass."""
        (tmp_path / "bundled.sh").write_text("#!/bin/sh\n")
        data = {"documents": {"My Doc": {"file_path": "bundled.sh"}}}
        validator = self._make_validator(schema_dir, example_data_dir)
        errors = validator.validate_file_references(data, tmp_path / "listing.json", set())
        assert errors == [], f"Unexpected errors: {errors}"


class TestRequiredParameterDefaults:
    """Tests for validate_required_parameter_defaults method."""

    def test_no_user_parameters_schema(self, schema_dir, example_data_dir):
        """Test validation passes when user_parameters_schema is not present."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"schema": "listing_v1"}
        errors = validator.validate_required_parameter_defaults(data, "listing_v1")
        assert len(errors) == 0

    def test_no_required_parameters(self, schema_dir, example_data_dir):
        """Test validation passes when user_parameters_schema has no required field."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {
            "schema": "listing_v1",
            "user_parameters_schema": {
                "type": "object",
                "properties": {"param1": {"type": "string"}},
            },
        }
        errors = validator.validate_required_parameter_defaults(data, "listing_v1")
        assert len(errors) == 0

    def test_empty_required_parameters(self, schema_dir, example_data_dir):
        """Test validation passes when required is an empty list."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {
            "schema": "listing_v1",
            "user_parameters_schema": {
                "type": "object",
                "properties": {"param1": {"type": "string"}},
                "required": [],
            },
        }
        errors = validator.validate_required_parameter_defaults(data, "listing_v1")
        assert len(errors) == 0

    def test_required_params_with_defaults(self, schema_dir, example_data_dir):
        """Test validation passes when all required params have defaults."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {
            "schema": "listing_v1",
            "user_parameters_schema": {
                "type": "object",
                "properties": {
                    "param1": {"type": "string"},
                    "param2": {"type": "integer"},
                },
                "required": ["param1", "param2"],
            },
            "service_options": {
                "ops_testing_parameters": {
                    "param1": "default_value",
                    "param2": 42,
                }
            },
        }
        errors = validator.validate_required_parameter_defaults(data, "listing_v1")
        assert len(errors) == 0

    def test_required_params_missing_service_options(self, schema_dir, example_data_dir):
        """Test validation fails when required params exist but service_options is missing."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {
            "schema": "listing_v1",
            "user_parameters_schema": {
                "type": "object",
                "properties": {"param1": {"type": "string"}},
                "required": ["param1"],
            },
        }
        errors = validator.validate_required_parameter_defaults(data, "listing_v1")
        assert len(errors) == 1
        assert "service_options is missing" in errors[0]

    def test_required_params_missing_ops_testing_parameters(self, schema_dir, example_data_dir):
        """Test validation fails when required params exist but ops_testing_parameters is missing."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {
            "schema": "listing_v1",
            "user_parameters_schema": {
                "type": "object",
                "properties": {"param1": {"type": "string"}},
                "required": ["param1"],
            },
            "service_options": {"other_option": "value"},
        }
        errors = validator.validate_required_parameter_defaults(data, "listing_v1")
        assert len(errors) == 1
        assert "ops_testing_parameters is missing" in errors[0]

    def test_required_params_missing_some_defaults(self, schema_dir, example_data_dir):
        """Test validation fails when some required params are missing defaults."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {
            "schema": "listing_v1",
            "user_parameters_schema": {
                "type": "object",
                "properties": {
                    "param1": {"type": "string"},
                    "param2": {"type": "integer"},
                    "param3": {"type": "boolean"},
                },
                "required": ["param1", "param2", "param3"],
            },
            "service_options": {
                "ops_testing_parameters": {
                    "param1": "default_value",
                    # param2 and param3 are missing
                }
            },
        }
        errors = validator.validate_required_parameter_defaults(data, "listing_v1")
        assert len(errors) == 1
        assert "param2" in errors[0]
        assert "param3" in errors[0]
        assert "missing default values" in errors[0]

    def test_non_listing_schema_skipped(self, schema_dir, example_data_dir):
        """Test validation is skipped for non-listing_v1 schemas."""
        validator = DataValidator(example_data_dir, schema_dir)

        # This data would fail if validated, but should be skipped for offering_v1
        data = {
            "schema": "offering_v1",
            "user_parameters_schema": {
                "type": "object",
                "required": ["param1"],
            },
        }
        errors = validator.validate_required_parameter_defaults(data, "offering_v1")
        assert len(errors) == 0


class TestApiKeySecretsValidation:
    """Tests for validate_api_key_secrets method."""

    def test_valid_secrets_format_with_spaces(self, schema_dir, example_data_dir):
        """Test that api_key with spaces in secrets format is valid."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"upstream_access_config": {"API": {"api_key": "${ secrets.MY_API_KEY }"}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 0

    def test_valid_secrets_format_without_spaces(self, schema_dir, example_data_dir):
        """Test that api_key without spaces in secrets format is valid."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"upstream_access_config": {"API": {"api_key": "${secrets.MY_API_KEY}"}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 0

    def test_valid_secrets_format_with_underscore_prefix(self, schema_dir, example_data_dir):
        """Test that api_key with underscore prefix is valid."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"upstream_access_config": {"API": {"api_key": "${ secrets._PRIVATE_KEY }"}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 0

    def test_valid_secrets_format_with_numbers(self, schema_dir, example_data_dir):
        """Test that api_key with numbers in name is valid."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"upstream_access_config": {"API": {"api_key": "${ secrets.API_KEY_V2 }"}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 0

    def test_invalid_plain_text_api_key(self, schema_dir, example_data_dir):
        """Test that plain text api_key is invalid."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"upstream_access_config": {"API": {"api_key": "sk-abc123xyz"}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 1
        assert "secrets reference format" in errors[0]
        assert "upstream_access_config.API.api_key" in errors[0]

    def test_invalid_placeholder_api_key(self, schema_dir, example_data_dir):
        """Test that placeholder api_key is invalid."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"upstream_access_config": {"API": {"api_key": "your_api_key_here"}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 1
        assert "secrets reference format" in errors[0]

    def test_invalid_secrets_format_missing_secrets(self, schema_dir, example_data_dir):
        """Test that missing 'secrets.' prefix is invalid."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"upstream_access_config": {"API": {"api_key": "${ MY_API_KEY }"}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 1
        assert "secrets reference format" in errors[0]

    def test_invalid_secrets_format_wrong_braces(self, schema_dir, example_data_dir):
        """Test that wrong brace format is invalid."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"upstream_access_config": {"API": {"api_key": "{{ secrets.MY_API_KEY }}"}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 1
        assert "secrets reference format" in errors[0]

    def test_invalid_secrets_starting_with_number(self, schema_dir, example_data_dir):
        """Test that secret name starting with number is invalid."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"upstream_access_config": {"API": {"api_key": "${ secrets.123_KEY }"}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 1
        assert "secrets reference format" in errors[0]

    def test_null_api_key_is_valid(self, schema_dir, example_data_dir):
        """Test that null/None api_key is valid (optional field)."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"upstream_access_config": {"API": {"api_key": None}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 0

    def test_missing_api_key_is_valid(self, schema_dir, example_data_dir):
        """Test that missing api_key field is valid (optional field)."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"upstream_access_config": {"API": {"base_url": "https://api.example.com"}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 0

    def test_service_options_ops_testing_parameters_api_key(self, schema_dir, example_data_dir):
        """Test that api_key in service_options.ops_testing_parameters is validated."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"service_options": {"ops_testing_parameters": {"api_key": "plain_text_key"}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 1
        assert "service_options.ops_testing_parameters.api_key" in errors[0]

    def test_service_options_ops_testing_parameters_api_key_valid(self, schema_dir, example_data_dir):
        """Test that valid api_key in service_options.ops_testing_parameters passes."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"service_options": {"ops_testing_parameters": {"api_key": "${ secrets.USER_API_KEY }"}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 0

    def test_user_access_interfaces_api_key(self, schema_dir, example_data_dir):
        """Test that api_key in user_access_interfaces is validated."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {"user_access_interfaces": {"User API": {"api_key": "invalid_key"}}}
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 1
        assert "user_access_interfaces.User API.api_key" in errors[0]

    def test_multiple_invalid_api_keys(self, schema_dir, example_data_dir):
        """Test that multiple invalid api_keys are all reported."""
        validator = DataValidator(example_data_dir, schema_dir)

        data = {
            "upstream_access_config": {"API1": {"api_key": "invalid1"}, "API2": {"api_key": "invalid2"}},
            "service_options": {"ops_testing_parameters": {"api_key": "invalid3"}},
        }
        errors = validator.validate_api_key_secrets(data)
        assert len(errors) == 3


class TestServiceOptionsValidation:
    """Tests for service_options key and value type validation."""

    def test_valid_options_pass(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {
                "enrollment_limit": 10,
                "enrollment_limit_per_customer": 2,
                "enrollment_limit_per_user": 3,
                "ops_testing_parameters": {"model": "gpt-4"},
            },
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert errors == []

    def test_unrecognized_key_produces_error(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {"enrollment_limt": 5},
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 1
        assert "Unrecognized service_option 'enrollment_limt'" in errors[0]
        assert "Supported options:" in errors[0]

    def test_wrong_type_enrollment_limit(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {"enrollment_limit": "five"},
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 1
        assert "must be int, got str" in errors[0]

    def test_wrong_type_ops_testing_parameters(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {"ops_testing_parameters": "not a dict"},
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 1
        assert "must be dict, got str" in errors[0]

    def test_non_positive_enrollment_limit(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {"enrollment_limit": 0},
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 1
        assert "must be a positive integer, got 0" in errors[0]

    def test_negative_enrollment_limit(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {"enrollment_limit_per_user": -1},
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 1
        assert "must be a positive integer, got -1" in errors[0]

    def test_boolean_enrollment_limit_rejected(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {"enrollment_limit": True},
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 1
        assert "must be int, got bool" in errors[0]

    def test_none_service_options_passes(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {"schema": "listing_v1", "service_options": None}
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert errors == []

    def test_missing_service_options_passes(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {"schema": "listing_v1"}
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert errors == []

    def test_non_listing_schema_skipped(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "offering_v1",
            "service_options": {"bogus_key": 123},
        }
        errors = validator.validate_service_options_keys(data, "offering_v1")
        assert errors == []

    def test_multiple_errors_at_once(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {
                "enrollment_limt": 5,
                "enrollment_limit": "bad",
            },
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 2

    def test_valid_recurrence_options(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {
                "recurrence_min_interval_seconds": 300,
                "recurrence_max_interval_seconds": 86400,
                "recurrence_allow_cron": True,
            },
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert errors == []

    def test_recurrence_interval_wrong_type_str(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {"recurrence_min_interval_seconds": "fast"},
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 1
        assert "must be int, got str" in errors[0]

    def test_recurrence_interval_wrong_type(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {"recurrence_min_interval_seconds": 5.0},
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 1
        assert "must be int, got float" in errors[0]

    def test_recurrence_interval_zero(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {"recurrence_min_interval_seconds": 0},
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 1
        assert "must be >= 1" in errors[0]

    def test_recurrence_min_exceeds_max(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {
                "recurrence_min_interval_seconds": 1000,
                "recurrence_max_interval_seconds": 500,
            },
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 1
        assert "must be <= recurrence_max_interval_seconds" in errors[0]

    def test_recurrence_interval_bool_rejected(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {"recurrence_min_interval_seconds": True},
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 1
        assert "must be int, got bool" in errors[0]

    def test_valid_env_option(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {
                "enrollment_vars": {"user_id": "{{ enrollment_code(6) }}"},
            },
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert errors == []

    def test_enrollment_vars_empty_dict_valid(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {"enrollment_vars": {}},
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert errors == []

    def test_enrollment_vars_wrong_type(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {"enrollment_vars": "not a dict"},
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 1
        assert "must be dict, got str" in errors[0]

    def test_enrollment_vars_value_must_be_string(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {"enrollment_vars": {"count": 42}},
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert len(errors) == 1
        assert "service_options.enrollment_vars.count must be str" in errors[0]

    def test_enrollment_vars_with_other_options(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "listing_v1",
            "service_options": {
                "enrollment_vars": {"topic": "{{ enrollment_code() }}"},
                "enrollment_limit_per_customer": 5,
            },
        }
        errors = validator.validate_service_options_keys(data, "listing_v1")
        assert errors == []


class TestSecretReferencesValidation:
    """Tests for validate_secret_references.

    Each test writes a small offering + (optional) listing pair into a
    tmp_path and invokes the validator against the offering. The
    validator locates the sibling listing itself.
    """

    def _write_pair(
        self,
        tmp_path: Path,
        offering: dict,
        listing: dict | None,
    ) -> Path:
        import json

        svc_dir = tmp_path / "services" / "demo"
        svc_dir.mkdir(parents=True, exist_ok=True)
        offering_path = svc_dir / "offering.json"
        offering_path.write_text(json.dumps({"schema": "offering_v1", **offering}))
        if listing is not None:
            (svc_dir / "listing.json").write_text(json.dumps({"schema": "listing_v1", **listing}))
        return offering_path

    def test_literal_identifier_is_ok(self, schema_dir, example_data_dir, tmp_path):
        validator = DataValidator(example_data_dir, schema_dir)
        offering_path = self._write_pair(
            tmp_path,
            offering={"upstream_access_config": {"API": {"api_key": "${ customer_secrets.ECHO_API_KEY }"}}},
            listing={"service_options": {}},
        )
        import json

        data = json.loads(offering_path.read_text())
        errors = validator.validate_secret_references(data, "offering_v1", offering_path)
        assert errors == []

    def test_jinja_params_expansion_is_ok(self, schema_dir, example_data_dir, tmp_path):
        """`{{ params.KEY }}` that resolves to an identifier is accepted."""
        validator = DataValidator(example_data_dir, schema_dir)
        offering_path = self._write_pair(
            tmp_path,
            offering={
                "upstream_access_config": {
                    "S3": {"access_key": "${ customer_secrets.{{ params.s3_access_key_secret }} }"}
                }
            },
            listing={
                "service_options": {"ops_testing_parameters": {"s3_access_key_secret": "SVCMARKET_S3_ACCESS_KEY_ID"}}
            },
        )
        import json

        data = json.loads(offering_path.read_text())
        errors = validator.validate_secret_references(data, "offering_v1", offering_path)
        assert errors == []

    def test_jinja_routing_vars_and_enrollment_vars(self, schema_dir, example_data_dir, tmp_path):
        """`routing_vars` and `enrollment_vars` namespaces are also accepted."""
        validator = DataValidator(example_data_dir, schema_dir)
        offering_path = self._write_pair(
            tmp_path,
            offering={
                "upstream_access_config": {
                    "A": {"api_key": "${ secrets.{{ routing_vars.region }}_KEY }"},
                    "B": {"api_key": "${ secrets.{{ enrollment_vars.env }}_KEY }"},
                }
            },
            listing={
                "service_options": {
                    "routing_vars": {"region": "US"},
                    "enrollment_vars": {"env": "PROD"},
                }
            },
        )
        import json

        data = json.loads(offering_path.read_text())
        errors = validator.validate_secret_references(data, "offering_v1", offering_path)
        assert errors == []

    def test_bracket_lookup_is_rejected(self, schema_dir, example_data_dir, tmp_path):
        """Bracket syntax like `customer_secrets[params.X]` is not a valid form."""
        validator = DataValidator(example_data_dir, schema_dir)
        offering_path = self._write_pair(
            tmp_path,
            offering={
                "upstream_access_config": {
                    "S3": {"access_key": ("${ customer_secrets[enrollment_params.s3_access_key_secret] }")}
                }
            },
            listing={"service_options": {"ops_testing_parameters": {}}},
        )
        import json

        data = json.loads(offering_path.read_text())
        errors = validator.validate_secret_references(data, "offering_v1", offering_path)
        assert len(errors) == 1
        assert "upstream_access_config.S3.access_key" in errors[0]
        assert "not a valid form" in errors[0]

    def test_undefined_jinja_variable_is_rejected(self, schema_dir, example_data_dir, tmp_path):
        """Referencing `params.MISSING` when ops_testing_parameters has no such key."""
        validator = DataValidator(example_data_dir, schema_dir)
        offering_path = self._write_pair(
            tmp_path,
            offering={
                "upstream_access_config": {"S3": {"access_key": "${ customer_secrets.{{ params.missing_key }} }"}}
            },
            listing={"service_options": {"ops_testing_parameters": {"other_key": "VALUE"}}},
        )
        import json

        data = json.loads(offering_path.read_text())
        errors = validator.validate_secret_references(data, "offering_v1", offering_path)
        assert len(errors) == 1
        assert "Jinja expansion failed" in errors[0]
        assert "upstream_access_config.S3.access_key" in errors[0]

    def test_rendered_name_with_hyphen_is_rejected(self, schema_dir, example_data_dir, tmp_path):
        """Composition that yields an invalid identifier (e.g., hyphen) fails."""
        validator = DataValidator(example_data_dir, schema_dir)
        offering_path = self._write_pair(
            tmp_path,
            offering={
                "upstream_access_config": {"API": {"api_key": "${ customer_secrets.{{ params.a }}-{{ params.b }} }"}}
            },
            listing={"service_options": {"ops_testing_parameters": {"a": "FOO", "b": "BAR"}}},
        )
        import json

        data = json.loads(offering_path.read_text())
        errors = validator.validate_secret_references(data, "offering_v1", offering_path)
        assert len(errors) == 1
        assert "FOO-BAR" in errors[0]
        assert "not a valid form" in errors[0]

    def test_missing_sibling_listing_is_tolerated(self, schema_dir, example_data_dir, tmp_path):
        """An offering without a sibling listing still validates literal refs."""
        validator = DataValidator(example_data_dir, schema_dir)
        # No listing written — only offering.
        offering_path = self._write_pair(
            tmp_path,
            offering={"upstream_access_config": {"API": {"api_key": "${ secrets.MY_KEY }"}}},
            listing=None,
        )
        import json

        data = json.loads(offering_path.read_text())
        errors = validator.validate_secret_references(data, "offering_v1", offering_path)
        assert errors == []

    def test_non_offering_schema_skipped(self, schema_dir, example_data_dir, tmp_path):
        """`validate_secret_references` is a no-op for non-offering schemas."""
        validator = DataValidator(example_data_dir, schema_dir)
        errors = validator.validate_secret_references(
            {"upstream_access_config": {"X": {"api_key": "${ customer_secrets[bad] }"}}},
            "listing_v1",
            tmp_path / "whatever.json",
        )
        assert errors == []

    def test_default_value_syntax_is_ok(self, schema_dir, example_data_dir, tmp_path):
        """`${ customer_secrets.X ?? default }` is accepted — the `??`
        syntax introduced in backend/app/core/var_substitution.py."""
        validator = DataValidator(example_data_dir, schema_dir)
        offering_path = self._write_pair(
            tmp_path,
            offering={"upstream_access_config": {"S3": {"region": "${ customer_secrets.S3_REGION ?? us-east-1 }"}}},
            listing={"service_options": {}},
        )
        import json

        data = json.loads(offering_path.read_text())
        errors = validator.validate_secret_references(data, "offering_v1", offering_path)
        assert errors == []

    def test_default_with_hyphen_and_slash(self, schema_dir, example_data_dir, tmp_path):
        """Defaults may contain hyphens, slashes, spaces — matches backend
        `??` parser semantics (defaults are free-form)."""
        validator = DataValidator(example_data_dir, schema_dir)
        offering_path = self._write_pair(
            tmp_path,
            offering={
                "upstream_access_config": {
                    "API": {
                        "api_key": "${ secrets.FOO ?? prod-east-1 }",
                        "base_url": "${ secrets.URL ?? https://api.example.com/v1 }",
                    }
                }
            },
            listing={"service_options": {}},
        )
        import json

        data = json.loads(offering_path.read_text())
        errors = validator.validate_secret_references(data, "offering_v1", offering_path)
        assert errors == []

    def test_default_after_jinja_rendered_name(self, schema_dir, example_data_dir, tmp_path):
        """`??` default works after a Jinja-rendered secret name."""
        validator = DataValidator(example_data_dir, schema_dir)
        offering_path = self._write_pair(
            tmp_path,
            offering={
                "upstream_access_config": {
                    "S3": {"region": ("${ customer_secrets.{{ params.region_secret }} ?? us-east-1 }")}
                }
            },
            listing={"service_options": {"ops_testing_parameters": {"region_secret": "SVCPASS_S3_REGION"}}},
        )
        import json

        data = json.loads(offering_path.read_text())
        errors = validator.validate_secret_references(data, "offering_v1", offering_path)
        assert errors == []

    def test_empty_default_is_ok(self, schema_dir, example_data_dir, tmp_path):
        """`${ secrets.X ?? }` (empty default) is valid — matches backend
        behaviour where `default=''` is distinct from `default=None`."""
        validator = DataValidator(example_data_dir, schema_dir)
        offering_path = self._write_pair(
            tmp_path,
            offering={"upstream_access_config": {"API": {"api_key": "${ secrets.OPTIONAL_KEY ?? }"}}},
            listing={"service_options": {}},
        )
        import json

        data = json.loads(offering_path.read_text())
        errors = validator.validate_secret_references(data, "offering_v1", offering_path)
        assert errors == []


class TestAccessInterfaceNameValidation:
    """Tests for ``validate_access_interface_names``.

    The dictionary keys of ``user_access_interfaces`` become the SDK
    handle for routing (``service.dispatch(interface=name)``) and end
    up as ``AccessInterface.name`` on the backend, which enforces the
    same slug pattern. Surfacing violations at CLI-validation time
    means sellers see them before publishing.
    """

    def test_none_input_passes(self):
        from unitysvc_core.models.validators import validate_access_interface_names

        assert validate_access_interface_names(None) == []

    def test_empty_dict_passes(self):
        from unitysvc_core.models.validators import validate_access_interface_names

        assert validate_access_interface_names({}) == []

    def test_valid_slugs_pass(self):
        from unitysvc_core.models.validators import validate_access_interface_names

        assert (
            validate_access_interface_names(
                {
                    "default": {},
                    "openai-compat": {},
                    "v1_chat": {},
                    "abc123": {},
                    "0fallback": {},
                }
            )
            == []
        )

    def test_uppercase_rejected(self):
        from unitysvc_core.models.validators import validate_access_interface_names

        errors = validate_access_interface_names({"Default": {}})
        assert len(errors) == 1
        assert "user_access_interfaces.'Default'" in errors[0]
        assert "Suggestion: rename to 'default'" in errors[0]

    def test_space_in_name_rejected(self):
        """The motivating offender from production data."""
        from unitysvc_core.models.validators import validate_access_interface_names

        errors = validate_access_interface_names({"Provider SDK": {}})
        assert len(errors) == 1
        assert "user_access_interfaces.'Provider SDK'" in errors[0]
        assert "rename to 'provider_sdk'" in errors[0]

    def test_leading_dash_rejected(self):
        from unitysvc_core.models.validators import validate_access_interface_names

        errors = validate_access_interface_names({"-default": {}})
        assert len(errors) == 1
        assert "URL-friendly slug" in errors[0]

    def test_dot_in_name_rejected(self):
        """Dots are excluded — interface names aren't versioned/qualified."""
        from unitysvc_core.models.validators import validate_access_interface_names

        errors = validate_access_interface_names({"v1.0": {}})
        assert len(errors) == 1

    def test_multiple_offenders_each_reported(self):
        from unitysvc_core.models.validators import validate_access_interface_names

        errors = validate_access_interface_names(
            {
                "Provider SDK": {},
                "OK_one": {},  # uppercase invalid
                "ok_two": {},  # valid
            }
        )
        assert len(errors) == 2
        joined = "\n".join(errors)
        assert "'Provider SDK'" in joined
        assert "'OK_one'" in joined
        assert "'ok_two'" not in joined


class TestLLMOfferingMetadataValidation:
    """Tests for ``validate_llm_offering_metadata``.

    Mirrors the backend's ingest-time check at
    ``backend/app/workers/ingest_tasks.py``: LLM offerings must declare
    ``details.context_length`` and ``details.parameter_count`` as either
    a positive integer (real value) or null (asserted unknown). The CLI
    validator catches violations at ``usvc data validate`` time so
    authors see the error before submission.
    """

    def test_valid_positive_integers_pass(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "offering_v1",
            "service_type": "llm",
            "details": {"context_length": 128_000, "parameter_count": 7_000_000_000},
        }
        assert validator.validate_llm_offering_metadata(data, "offering_v1") == []

    def test_explicit_null_values_pass(self, schema_dir, example_data_dir):
        """null is the canonical 'unknown' marker — both fields may use it."""
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "offering_v1",
            "service_type": "llm",
            "details": {"context_length": None, "parameter_count": None},
        }
        assert validator.validate_llm_offering_metadata(data, "offering_v1") == []

    def test_one_value_one_null_passes(self, schema_dir, example_data_dir):
        """Closed-source models (GPT-*, Claude-*) typically have a known
        context_length but unknown parameter_count — common shape."""
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "offering_v1",
            "service_type": "llm",
            "details": {"context_length": 200_000, "parameter_count": None},
        }
        assert validator.validate_llm_offering_metadata(data, "offering_v1") == []

    def test_missing_context_length_rejected(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "offering_v1",
            "service_type": "llm",
            "details": {"parameter_count": 7_000_000_000},
        }
        errors = validator.validate_llm_offering_metadata(data, "offering_v1")
        assert len(errors) == 1
        assert "context_length" in errors[0]
        assert "null" in errors[0]

    def test_missing_parameter_count_rejected(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "offering_v1",
            "service_type": "llm",
            "details": {"context_length": 128_000},
        }
        errors = validator.validate_llm_offering_metadata(data, "offering_v1")
        assert len(errors) == 1
        assert "parameter_count" in errors[0]

    def test_both_missing_rejected_separately(self, schema_dir, example_data_dir):
        """Two distinct errors — easier to triage than one combined message."""
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "offering_v1",
            "service_type": "llm",
            "details": {},
        }
        errors = validator.validate_llm_offering_metadata(data, "offering_v1")
        assert len(errors) == 2
        joined = "\n".join(errors)
        assert "context_length" in joined
        assert "parameter_count" in joined

    def test_details_missing_entirely_rejected(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {"schema": "offering_v1", "service_type": "llm"}
        errors = validator.validate_llm_offering_metadata(data, "offering_v1")
        assert len(errors) == 1
        assert "context_length" in errors[0]
        assert "parameter_count" in errors[0]

    def test_zero_rejected(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "offering_v1",
            "service_type": "llm",
            "details": {"context_length": 0, "parameter_count": 7_000_000_000},
        }
        errors = validator.validate_llm_offering_metadata(data, "offering_v1")
        assert len(errors) == 1
        assert "context_length" in errors[0]
        assert "positive integer" in errors[0]

    def test_negative_rejected(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "offering_v1",
            "service_type": "llm",
            "details": {"context_length": 128_000, "parameter_count": -1},
        }
        errors = validator.validate_llm_offering_metadata(data, "offering_v1")
        assert len(errors) == 1
        assert "parameter_count" in errors[0]

    def test_string_rejected(self, schema_dir, example_data_dir):
        """A common manual-data mistake — '7B' as a string. Reject."""
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "offering_v1",
            "service_type": "llm",
            "details": {"context_length": 128_000, "parameter_count": "7B"},
        }
        errors = validator.validate_llm_offering_metadata(data, "offering_v1")
        assert len(errors) == 1
        assert "parameter_count" in errors[0]
        assert "'7B'" in errors[0]

    def test_bool_rejected(self, schema_dir, example_data_dir):
        """bool is a subclass of int in Python — guard against
        ``parameter_count: True`` silently coercing to 1."""
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "offering_v1",
            "service_type": "llm",
            "details": {"context_length": True, "parameter_count": 7_000_000_000},
        }
        errors = validator.validate_llm_offering_metadata(data, "offering_v1")
        assert len(errors) == 1
        assert "context_length" in errors[0]

    def test_float_rejected(self, schema_dir, example_data_dir):
        validator = DataValidator(example_data_dir, schema_dir)
        data = {
            "schema": "offering_v1",
            "service_type": "llm",
            "details": {"context_length": 128_000.0, "parameter_count": 7_000_000_000},
        }
        errors = validator.validate_llm_offering_metadata(data, "offering_v1")
        assert len(errors) == 1
        assert "context_length" in errors[0]

    def test_non_llm_offering_skipped(self, schema_dir, example_data_dir):
        """Other service types (proxy, content, email, embedding) are
        not subject to this rule — no error even with empty details."""
        validator = DataValidator(example_data_dir, schema_dir)
        for service_type in ("proxy", "content", "email", "embedding"):
            data = {
                "schema": "offering_v1",
                "service_type": service_type,
                "details": {},
            }
            assert validator.validate_llm_offering_metadata(data, "offering_v1") == []

    def test_non_offering_schema_skipped(self, schema_dir, example_data_dir):
        """The rule only applies to offering_v1. Listing / provider /
        seller schemas are unaffected."""
        validator = DataValidator(example_data_dir, schema_dir)
        for schema_name in ("listing_v1", "provider_v1", "seller_v1"):
            data = {"schema": schema_name, "service_type": "llm"}
            assert validator.validate_llm_offering_metadata(data, schema_name) == []


class TestListingJinjaVarReferences:
    """Tests for ``validate_listing_jinja_var_references``.

    Catches the exact failure mode that bit unitysvc-services-http: a
    ``{{ enrollment_vars.code }}`` in ``user_access_interfaces`` with no
    matching key in ``service_options.enrollment_vars`` (commit
    9fb93aa). Covers ``params`` / ``routing_vars`` / ``enrollment_vars``.
    """

    def test_undefined_enrollment_var_rejected(self):
        """The exact unitysvc-services-http regression: enrollment_vars.code
        referenced but service_options has no enrollment_vars."""
        from unitysvc_core.models.validators import validate_listing_jinja_var_references

        data = {
            "user_access_interfaces": {
                "http_gateway": {"base_url": "${API_GATEWAY_BASE_URL}/{{ enrollment_vars.code }}"},
            },
            "service_options": {"ops_testing_parameters": {"x": "1"}},
        }
        errors = validate_listing_jinja_var_references(data)
        assert len(errors) == 1
        assert "user_access_interfaces.http_gateway.base_url" in errors[0]
        assert "code" in errors[0]
        assert "enrollment_vars" in errors[0]

    def test_defined_enrollment_var_passes(self):
        from unitysvc_core.models.validators import validate_listing_jinja_var_references

        data = {
            "user_access_interfaces": {
                "http_gateway": {"base_url": "${API_GATEWAY_BASE_URL}/{{ enrollment_vars.code }}"},
            },
            "service_options": {"enrollment_vars": {"code": "{{ enrollment_code(6) }}"}},
        }
        assert validate_listing_jinja_var_references(data) == []

    def test_undefined_routing_var_rejected(self):
        from unitysvc_core.models.validators import validate_listing_jinja_var_references

        data = {
            "user_access_interfaces": {
                "default": {"api_key": "${ secrets.{{ routing_vars.env }}_KEY }"},
            },
            "service_options": {},
        }
        errors = validate_listing_jinja_var_references(data)
        assert len(errors) == 1
        assert "routing_vars" in errors[0] or "env" in errors[0]

    def test_defined_routing_var_passes(self):
        from unitysvc_core.models.validators import validate_listing_jinja_var_references

        data = {
            "user_access_interfaces": {
                "default": {"api_key": "${ secrets.{{ routing_vars.env }}_KEY }"},
            },
            "service_options": {"routing_vars": {"env": "PROD"}},
        }
        assert validate_listing_jinja_var_references(data) == []

    def test_params_resolved_from_user_parameters_schema(self):
        """A ``params.X`` ref is satisfied by user_parameters_schema.properties
        even without an ops_testing_parameters default — the runtime gets
        the value from the user."""
        from unitysvc_core.models.validators import validate_listing_jinja_var_references

        data = {
            "user_access_interfaces": {
                "default": {"base_url": "https://api/{{ params.region }}"},
            },
            "user_parameters_schema": {"properties": {"region": {"type": "string"}}},
        }
        assert validate_listing_jinja_var_references(data) == []

    def test_params_resolved_from_ops_testing_parameters(self):
        from unitysvc_core.models.validators import validate_listing_jinja_var_references

        data = {
            "user_access_interfaces": {
                "default": {"base_url": "https://api/{{ params.region }}"},
            },
            "service_options": {"ops_testing_parameters": {"region": "us-east-1"}},
        }
        assert validate_listing_jinja_var_references(data) == []

    def test_undefined_params_rejected(self):
        from unitysvc_core.models.validators import validate_listing_jinja_var_references

        data = {
            "user_access_interfaces": {
                "default": {"base_url": "https://api/{{ params.region }}"},
            },
            "user_parameters_schema": {"properties": {"other": {"type": "string"}}},
        }
        errors = validate_listing_jinja_var_references(data)
        assert len(errors) == 1
        assert "region" in errors[0]

    def test_nested_string_in_routing_key_checked(self):
        """References inside nested dicts (e.g. routing_key.username) are
        also validated."""
        from unitysvc_core.models.validators import validate_listing_jinja_var_references

        data = {
            "user_access_interfaces": {
                "smtp": {
                    "base_url": "${SMTP_GATEWAY_BASE_URL}",
                    "routing_key": {"username": "{{ enrollment_vars.mailbox }}"},
                },
            },
            "service_options": {},
        }
        errors = validate_listing_jinja_var_references(data)
        assert len(errors) == 1
        assert "user_access_interfaces.smtp.routing_key.username" in errors[0]

    def test_no_jinja_reference_skipped(self):
        from unitysvc_core.models.validators import validate_listing_jinja_var_references

        data = {
            "user_access_interfaces": {
                "default": {"base_url": "https://api.example.com/v1", "api_key": "${ secrets.KEY }"},
            },
            "service_options": {},
        }
        assert validate_listing_jinja_var_references(data) == []

    def test_jinja_syntax_error_reported(self):
        from unitysvc_core.models.validators import validate_listing_jinja_var_references

        data = {
            "user_access_interfaces": {
                "default": {"base_url": "https://api/{{ unclosed"},
            },
        }
        errors = validate_listing_jinja_var_references(data)
        assert len(errors) == 1
        assert "syntax error" in errors[0].lower()

    def test_no_user_access_interfaces_skipped(self):
        from unitysvc_core.models.validators import validate_listing_jinja_var_references

        assert validate_listing_jinja_var_references({"schema": "listing_v1"}) == []
        assert validate_listing_jinja_var_references({"user_access_interfaces": {}}) == []
        assert validate_listing_jinja_var_references(None) == []
