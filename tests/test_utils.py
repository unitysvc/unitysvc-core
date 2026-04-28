"""Tests for utility functions."""

import json
from pathlib import Path

import pytest

from unitysvc_core.utils import (
    DEFAULT_PRESET_FNS,
    DataFileLoadWarning,
    compute_file_hash,
    deep_merge_dicts,
    expand_presets,
    find_file_by_schema_and_name,
    find_files_by_schema,
    generate_content_based_key,
    get_basename,
    get_file_extension,
    load_data_file,
    mime_type_to_extension,
    read_override_file,
    write_override_file,
)

# =============================================================================
# File hashing / content-addressable key generation
# =============================================================================


def test_compute_file_hash_deterministic() -> None:
    assert compute_file_hash(b"hello") == compute_file_hash(b"hello")
    assert compute_file_hash(b"hello") != compute_file_hash(b"world")


def test_generate_content_based_key_with_extension() -> None:
    key = generate_content_based_key(b"hello", extension="txt")
    assert key.endswith(".txt")
    # Same content produces same key
    assert generate_content_based_key(b"hello", extension="txt") == key


def test_generate_content_based_key_no_extension() -> None:
    key = generate_content_based_key(b"hello")
    assert "." not in key


# =============================================================================
# Filename helpers
# =============================================================================


def test_get_file_extension() -> None:
    assert get_file_extension("foo.txt") == "txt"
    assert get_file_extension("foo.tar.gz") == "gz"
    assert get_file_extension("foo") is None


def test_get_basename() -> None:
    assert get_basename("foo.txt") == "foo.txt"
    assert get_basename("path/to/foo.txt") == "foo.txt"
    assert get_basename("") == ""


def test_mime_type_to_extension() -> None:
    assert mime_type_to_extension("image/png") == "png"
    assert mime_type_to_extension("application/pdf") == "pdf"


# =============================================================================
# deep_merge_dicts
# =============================================================================


def test_deep_merge_dicts_simple() -> None:
    base = {"a": 1, "b": 2, "c": 3}
    override = {"b": 20, "d": 4}
    assert deep_merge_dicts(base, override) == {"a": 1, "b": 20, "c": 3, "d": 4}


def test_deep_merge_dicts_nested() -> None:
    base = {"config": {"host": "localhost", "port": 8080}, "name": "service1"}
    override = {"config": {"port": 9000, "ssl": True}, "status": "active"}
    assert deep_merge_dicts(base, override) == {
        "config": {"host": "localhost", "port": 9000, "ssl": True},
        "name": "service1",
        "status": "active",
    }


def test_deep_merge_dicts_lists_replaced() -> None:
    base = {"tags": ["python", "web"], "name": "service1"}
    override = {"tags": ["backend"]}
    # Lists are replaced, not merged
    assert deep_merge_dicts(base, override) == {"tags": ["backend"], "name": "service1"}


def test_deep_merge_dicts_empty_override() -> None:
    assert deep_merge_dicts({"a": 1, "b": 2}, {}) == {"a": 1, "b": 2}


def test_deep_merge_dicts_deeply_nested() -> None:
    base = {"level1": {"level2": {"level3": {"value": "old", "keep": True}}}}
    override = {"level1": {"level2": {"level3": {"value": "new"}}}}
    assert deep_merge_dicts(base, override) == {
        "level1": {"level2": {"level3": {"value": "new", "keep": True}}}
    }


# =============================================================================
# load_data_file
# =============================================================================


def test_load_data_file_json(tmp_path: Path) -> None:
    f = tmp_path / "x.json"
    payload = {"schema": "test_v1", "name": "foo", "value": 100}
    f.write_text(json.dumps(payload), encoding="utf-8")
    data, fmt = load_data_file(f)
    assert fmt == "json"
    assert data == payload


def test_load_data_file_toml(tmp_path: Path) -> None:
    f = tmp_path / "x.toml"
    f.write_text('schema = "test_v1"\nname = "foo"\nvalue = 100\n', encoding="utf-8")
    data, fmt = load_data_file(f)
    assert fmt == "toml"
    assert data == {"schema": "test_v1", "name": "foo", "value": 100}


# =============================================================================
# Override files
# =============================================================================


def test_load_data_file_json_with_override(tmp_path: Path) -> None:
    base = tmp_path / "offering.json"
    base.write_text(json.dumps({"schema": "offering_v1", "name": "svc", "version": 1}))
    override = tmp_path / "offering.override.json"
    override.write_text(json.dumps({"version": 2, "service_id": "abc"}))

    data, fmt = load_data_file(base)
    assert fmt == "json"
    assert data == {"schema": "offering_v1", "name": "svc", "version": 2, "service_id": "abc"}


def test_load_data_file_skip_override(tmp_path: Path) -> None:
    base = tmp_path / "offering.json"
    base.write_text(json.dumps({"name": "svc", "version": 1}))
    (tmp_path / "offering.override.json").write_text(json.dumps({"version": 2}))

    data, _ = load_data_file(base, skip_override=True)
    assert data == {"name": "svc", "version": 1}


def test_write_and_read_override_file(tmp_path: Path) -> None:
    base = tmp_path / "listing.json"
    base.write_text(json.dumps({"name": "thing"}))

    override_path = write_override_file(base, {"service_id": "xyz"})
    assert override_path is not None
    assert override_path.name == "listing.override.json"

    assert read_override_file(base) == {"service_id": "xyz"}

    # Subsequent writes deep-merge
    write_override_file(base, {"extra": 1})
    assert read_override_file(base) == {"service_id": "xyz", "extra": 1}


def test_write_override_file_delete_if_empty(tmp_path: Path) -> None:
    base = tmp_path / "listing.json"
    base.write_text(json.dumps({"name": "thing"}))
    override_path = write_override_file(base, {"tmp": 1})
    assert override_path is not None and override_path.exists()

    # Clear the override and request deletion
    override_path.unlink()
    override_path = write_override_file(base, {}, delete_if_empty=True)
    assert override_path is None


# =============================================================================
# find_files_by_schema
# =============================================================================


def test_find_files_by_schema(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text(json.dumps({"schema": "provider_v1", "name": "a"}))
    (tmp_path / "b.json").write_text(json.dumps({"schema": "offering_v1", "name": "b"}))
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.json").write_text(json.dumps({"schema": "provider_v1", "name": "c"}))

    provider_files = find_files_by_schema(tmp_path, "provider_v1")
    provider_paths = {Path(fp).name for fp, _fmt, _data in provider_files}
    assert provider_paths == {"a.json", "c.json"}

    offering_files = find_files_by_schema(tmp_path, "offering_v1")
    offering_paths = {Path(fp).name for fp, _fmt, _data in offering_files}
    assert offering_paths == {"b.json"}


def test_find_files_by_schema_skips_non_dict_roots(tmp_path: Path) -> None:
    """Discovery walks every ``*.json``/``*.toml`` under ``data_dir``.  A
    real seller catalog directory typically has *some* sibling content
    whose root isn't a dict — openapi specs (top-level array of routes),
    Node lock files, mypy cache files.  A previous version of this
    function called ``data.get("schema")`` unconditionally and aborted
    the whole walk with ``AttributeError: 'list' object has no
    attribute 'get'`` on the first such file.  Skip them cleanly so the
    catalog scan keeps going.
    """
    # One real catalog file, plus three non-dict roots discovery should
    # tolerate without crashing.
    (tmp_path / "good.json").write_text(
        json.dumps({"schema": "provider_v1", "name": "good"})
    )
    (tmp_path / "list_root.json").write_text(json.dumps([1, 2, 3]))
    (tmp_path / "string_root.json").write_text(json.dumps("hello"))
    (tmp_path / "null_root.json").write_text("null")

    # Use a unique schema name to bypass ``find_files_by_schema``'s
    # ``lru_cache`` — the prior test in this file populates the cache
    # with ``provider_v1`` on a different ``tmp_path``.
    files = find_files_by_schema(tmp_path, "provider_v1")
    assert {Path(fp).name for fp, _fmt, _data in files} == {"good.json"}


def test_find_file_by_schema_and_name_skips_non_dict_roots(tmp_path: Path) -> None:
    """Sibling discovery helper has the same surface — pin both."""
    (tmp_path / "good.json").write_text(
        json.dumps({"schema": "listing_v1", "name": "wanted"})
    )
    (tmp_path / "list_root.json").write_text(json.dumps([{"schema": "listing_v1"}]))

    result = find_file_by_schema_and_name(tmp_path, "listing_v1", "name", "wanted")
    assert result is not None
    fp, _fmt, data = result
    assert Path(fp).name == "good.json"
    assert data["name"] == "wanted"


# =============================================================================
# $preset sentinel expansion
# =============================================================================



def test_expand_presets_bare_string_doc_preset(tmp_path: Path) -> None:
    node = {"Connectivity": {"$doc_preset": "s3_connectivity"}}
    result = expand_presets(node)
    record = result["Connectivity"]
    assert record["category"] == "connectivity_test"
    assert record["mime_type"] == "python"
    assert Path(record["file_path"]).is_file()


def test_expand_presets_flat_form_with_overrides() -> None:
    node = {
        "$doc_preset": {
            "name": "s3_code_example",
            "description": "ours",
            "is_public": False,
        }
    }
    record = expand_presets(node)
    assert record["description"] == "ours"
    assert record["is_public"] is False
    assert record["category"] == "code_example"


def test_expand_presets_file_preset_returns_raw_content() -> None:
    content = expand_presets({"$file_preset": "s3_connectivity_v1"})
    assert isinstance(content, str)
    assert "boto3" in content


def test_expand_presets_walks_recursively() -> None:
    data = {
        "documents": {
            "a": {"$doc_preset": "s3_connectivity_v1"},
            "b": {"category": "custom"},
        },
        "snippets": [{"$file_preset": "s3_connectivity_v1"}, "plain"],
    }
    result = expand_presets(data)
    assert result["documents"]["a"]["category"] == "connectivity_test"
    assert isinstance(result["snippets"][0], str)
    assert result["snippets"][1] == "plain"
    assert result["documents"]["b"] == {"category": "custom"}


def test_expand_presets_does_not_mutate_input() -> None:
    original = {"a": {"$doc_preset": "s3_connectivity_v1"}}
    before = json.dumps(original, sort_keys=True)
    expand_presets(original)
    assert json.dumps(original, sort_keys=True) == before


def test_expand_presets_rejects_mixed_sentinel_keys() -> None:
    node = {"$doc_preset": "s3_connectivity", "category": "extra"}
    with pytest.raises(ValueError, match="must appear alone in its dict"):
        expand_presets(node)


def test_expand_presets_leaves_unknown_dollar_keys_alone() -> None:
    node = {"$eq": "foo"}
    assert expand_presets(node) == {"$eq": "foo"}


def test_file_preset_rejects_overrides() -> None:
    # file_preset takes only ``source`` — no **kwargs. The decorator's
    # wrapper unpacks the flat form and delegates directly, so extra
    # keys surface as Python's native TypeError on the call, which
    # is as helpful as a custom message (names the bad kwargs).
    with pytest.raises(TypeError, match="unexpected keyword argument"):
        expand_presets({"$file_preset": {"name": "s3_connectivity_v1", "desc": "x"}})


def test_load_data_file_expands_presets_by_default(tmp_path: Path) -> None:
    listing = tmp_path / "listing.json"
    listing.write_text(json.dumps({
        "schema": "listing_v1",
        "documents": {"Test": {"$doc_preset": "s3_connectivity_v1"}},
    }))
    data, _ = load_data_file(listing)
    record = data["documents"]["Test"]
    assert record["category"] == "connectivity_test"
    assert "$doc_preset" not in record


def test_load_data_file_preset_fns_none_preserves_sentinel(tmp_path: Path) -> None:
    listing = tmp_path / "listing.json"
    listing.write_text(json.dumps({
        "schema": "listing_v1",
        "documents": {"Test": {"$doc_preset": "s3_connectivity_v1"}},
    }))
    data, _ = load_data_file(listing, preset_fns=None)
    assert data["documents"]["Test"] == {"$doc_preset": "s3_connectivity_v1"}


def test_load_data_file_expands_after_override_merge(tmp_path: Path) -> None:
    base = tmp_path / "listing.json"
    base.write_text(json.dumps({
        "schema": "listing_v1",
        "documents": {"Test": {
            "$doc_preset": {"name": "s3_connectivity_v1", "is_active": True},
        }},
    }))
    override = tmp_path / "listing.override.json"
    override.write_text(json.dumps({"name": "from-override"}))

    data, _ = load_data_file(base)
    assert data["name"] == "from-override"
    assert data["documents"]["Test"]["category"] == "connectivity_test"


def test_find_files_by_schema_returns_expanded_data(tmp_path: Path) -> None:
    """find_files_by_schema goes through load_data_file; sentinels are expanded."""
    listing = tmp_path / "listing.json"
    listing.write_text(json.dumps({
        "schema": "listing_v1",
        "documents": {"T": {"$doc_preset": "s3_connectivity_v1"}},
    }))
    results = find_files_by_schema(tmp_path, "listing_v1")
    assert len(results) == 1
    _, _, data = results[0]
    assert data["documents"]["T"]["category"] == "connectivity_test"
    assert "$doc_preset" not in data["documents"]["T"]


def test_default_preset_fns_exports_doc_and_file() -> None:
    assert set(DEFAULT_PRESET_FNS) == {"doc_preset", "file_preset"}


# =============================================================================
# DataFileLoadWarning — discovery surfaces load failures instead of hiding them
# =============================================================================


def test_find_files_by_schema_warns_on_unknown_preset(tmp_path: Path) -> None:
    """A listing referencing a non-existent preset must not be silently skipped.

    Discovery used to swallow every exception with a bare ``except Exception:``,
    which made "no listings found" indistinguishable from "every listing has a
    typo." The function now emits :class:`DataFileLoadWarning` naming the file
    and the underlying error so callers can debug.
    """
    listing = tmp_path / "listing.json"
    listing.write_text(json.dumps({
        "schema": "listing_v1",
        "documents": {"T": {"$doc_preset": "this_preset_does_not_exist"}},
    }))

    find_files_by_schema.cache_clear()
    with pytest.warns(DataFileLoadWarning, match="this_preset_does_not_exist"):
        results = find_files_by_schema(tmp_path, "listing_v1")
    assert results == []


def test_find_files_by_schema_warns_on_malformed_json(tmp_path: Path) -> None:
    bad = tmp_path / "broken.json"
    bad.write_text("{ this is not json")

    find_files_by_schema.cache_clear()
    with pytest.warns(DataFileLoadWarning, match="broken.json"):
        results = find_files_by_schema(tmp_path, "listing_v1")
    assert results == []


def test_find_file_by_schema_and_name_warns_on_load_failure(tmp_path: Path) -> None:
    """A malformed file in the search directory must surface as a warning,
    not as a silent ``None`` (which would be indistinguishable from
    "the name didn't match anything")."""
    bad = tmp_path / "broken.json"
    bad.write_text("{ this is not json")

    with pytest.warns(DataFileLoadWarning, match="broken.json"):
        result = find_file_by_schema_and_name(tmp_path, "provider_v1", "name", "p")
    assert result is None


def test_find_files_by_schema_can_be_promoted_to_error(tmp_path: Path) -> None:
    """Callers that want fail-fast can promote the warning to an exception."""
    import warnings as _warnings

    listing = tmp_path / "listing.json"
    listing.write_text(json.dumps({
        "schema": "listing_v1",
        "documents": {"T": {"$doc_preset": "this_preset_does_not_exist"}},
    }))

    find_files_by_schema.cache_clear()
    with _warnings.catch_warnings():
        _warnings.simplefilter("error", DataFileLoadWarning)
        with pytest.raises(DataFileLoadWarning):
            find_files_by_schema(tmp_path, "listing_v1")
