"""Utility functions for file handling and data operations.

This module contains shared utilities used by both unitysvc-core SDK
and unitysvc backend, including:
- Content hashing and content-addressable storage key generation
- File extension and MIME type utilities
- Data file loading and merging
- ``$preset`` sentinel expansion via :func:`expand_presets` and
  :func:`load_data_file`'s ``preset_fns`` parameter
"""

import hashlib
import json
import os
import tomllib
from collections.abc import Callable, Mapping
from functools import lru_cache
from pathlib import Path
from typing import Any

import json5
import tomli_w
from unitysvc_data import doc_preset as _doc_preset_raw
from unitysvc_data import file_preset as _file_preset_raw

# =============================================================================
# Content Hashing and File Utilities
# These functions are shared with unitysvc backend for content-addressable storage
# =============================================================================


def compute_file_hash(content: bytes) -> str:
    """Compute SHA256 hash of file content.

    Args:
        content: File content as bytes

    Returns:
        Hexadecimal hash string
    """
    return hashlib.sha256(content).hexdigest()


def generate_content_based_key(content: bytes, extension: str | None = None) -> str:
    """Generate content-based object key using file hash.

    This creates a content-addressable storage key that ensures:
    - Automatic deduplication (same content = same object_key)
    - Optimal caching (content-addressable URLs)

    Args:
        content: File content as bytes
        extension: File extension (without dot)

    Returns:
        Content-based object key (hash.extension or just hash)
    """
    file_hash = compute_file_hash(content)

    if extension:
        # Remove leading dot if present
        extension = extension.lstrip(".")
        return f"{file_hash}.{extension}"

    return file_hash


def get_file_extension(filename: str) -> str | None:
    """Extract file extension from filename.

    Args:
        filename: Filename with or without path

    Returns:
        Extension without dot, or None if no extension
    """
    if not filename:
        return None

    # Get basename first (remove path)
    basename = os.path.basename(filename)

    # Split extension
    _, ext = os.path.splitext(basename)

    # Return without the dot
    return ext.lstrip(".") if ext else None


def get_basename(filename: str) -> str:
    """Get basename from filename (removes path).

    Args:
        filename: Filename with or without path

    Returns:
        Basename without path
    """
    return os.path.basename(filename) if filename else ""


def mime_type_to_extension(mime_type: str) -> str:
    """Convert MIME type to file extension.

    Args:
        mime_type: MIME type string

    Returns:
        File extension without dot
    """
    # Common MIME type to extension mappings
    mime_map = {
        "text": "txt",
        "plain": "txt",
        "text/plain": "txt",
        "text/html": "html",
        "text/markdown": "md",
        "text/csv": "csv",
        "application/json": "json",
        "application/pdf": "pdf",
        "application/xml": "xml",
        "application/x-yaml": "yaml",
        "application/octet-stream": "bin",
        "image/png": "png",
        "image/jpeg": "jpg",
        "image/gif": "gif",
        "markdown": "md",
        "html": "html",
        "json": "json",
        "pdf": "pdf",
        "xml": "xml",
        "yaml": "yaml",
        "csv": "csv",
        "url": "url",
    }

    # Try exact match first
    mime_lower = mime_type.lower()
    if mime_lower in mime_map:
        return mime_map[mime_lower]

    # Try to extract from mime type parts
    if "/" in mime_lower:
        _, subtype = mime_lower.split("/", 1)
        if subtype in mime_map:
            return mime_map[subtype]

    # Default to txt
    return "txt"


# =============================================================================
# Data File Operations
# =============================================================================

# -----------------------------------------------------------------------------
# $preset sentinel expansion
#
# Catalog data files may embed ``{"$doc_preset": ...}`` or
# ``{"$file_preset": ...}`` sentinels that reference bundled examples
# shipped in ``unitysvc-data``. ``load_data_file`` walks the parsed
# payload after the override merge and replaces each sentinel with the
# corresponding function's return value; every downstream consumer —
# upload, validate, show, run-tests — sees the fully-expanded shape
# without needing to opt in.
#
# Pass ``preset_fns=None`` to disable expansion (e.g. for raw round-trip
# tooling that wants to preserve the sentinel on disk).
# -----------------------------------------------------------------------------


def _unwrap_flat_preset_form(source: Any) -> tuple[Any, dict[str, Any]]:
    """Split a flat sentinel value into ``(preset_name, overrides)``.

    Catalog data uses a flat shape — ``name`` is the preset identifier;
    every other key is a per-field override forwarded as a kwarg::

        {"name": "s3_code_example", "description": "ours"}
        -> ("s3_code_example", {"description": "ours"})

    Any other value passes through untouched in the first slot with an
    empty override dict; the preset function handles bare strings and
    its own internal sentinel shapes natively.
    """
    if isinstance(source, dict) and isinstance(source.get("name"), str):
        name = source["name"]
        return name, {k: v for k, v in source.items() if k != "name"}
    return source, {}


def _doc_preset_sentinel(source: Any) -> dict[str, Any]:
    arg, overrides = _unwrap_flat_preset_form(source)
    return _doc_preset_raw(arg, **overrides)


def _file_preset_sentinel(source: Any) -> str:
    arg, overrides = _unwrap_flat_preset_form(source)
    if overrides:
        raise ValueError(
            "$file_preset does not accept per-field overrides — the file "
            "content is immutable. Unexpected keys alongside 'name': "
            f"{sorted(overrides)!r}."
        )
    return _file_preset_raw(arg)


#: Preset functions recognised as ``$<fn>`` sentinel keys. Callers that
#: need a custom set can pass their own mapping to ``load_data_file``
#: or :func:`expand_presets`.
DEFAULT_PRESET_FNS: Mapping[str, Callable[[Any], Any]] = {
    "doc_preset": _doc_preset_sentinel,
    "file_preset": _file_preset_sentinel,
}


def expand_presets(
    data: Any,
    preset_fns: Mapping[str, Callable[[Any], Any]] = DEFAULT_PRESET_FNS,
) -> Any:
    """Recursively replace preset sentinel nodes with their expanded values.

    A **preset sentinel** is a dict containing exactly one ``$<fn_name>``
    key where ``<fn_name>`` matches an entry in ``preset_fns``. The
    value under that key is passed as the single positional argument
    to the function; the return value replaces the whole sentinel
    node.

    - Non-sentinel dicts and lists are walked recursively.
    - Scalars pass through unchanged.
    - ``$``-prefixed keys that do not match a registered function are
      treated as ordinary data (so Mongo-style operators etc. do not
      collide with the walker).
    - A dict carrying a ``$<fn>`` key *alongside* other keys is a
      footgun — raise :class:`ValueError` instead of silently
      ignoring the preset call.

    The input is never mutated; a new structure is returned.
    """
    if isinstance(data, dict):
        if len(data) == 1:
            (only_key,) = data.keys()
            if isinstance(only_key, str) and only_key.startswith("$"):
                fn_name = only_key[1:]
                fn = preset_fns.get(fn_name)
                if fn is not None:
                    return fn(expand_presets(data[only_key], preset_fns))

        for key in data:
            if isinstance(key, str) and key.startswith("$"):
                fn_name = key[1:]
                if fn_name in preset_fns:
                    raise ValueError(
                        f"Preset sentinel key {key!r} must appear alone in its "
                        f"dict — found alongside {sorted(k for k in data if k != key)!r}. "
                        "If you meant per-field overrides, nest them inside the "
                        f"sentinel value: {{{key!r}: {{'name': '<preset>', <override>: ...}}}}."
                    )

        return {key: expand_presets(value, preset_fns) for key, value in data.items()}

    if isinstance(data, list):
        return [expand_presets(item, preset_fns) for item in data]

    return data


def deep_merge_dicts(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """
    Deep merge two dictionaries, with override values taking precedence.

    For nested dictionaries, performs recursive merge. For all other types
    (lists, primitives), the override value completely replaces the base value.

    Args:
        base: Base dictionary
        override: Override dictionary (values take precedence)

    Returns:
        Merged dictionary
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # Recursively merge nested dictionaries
            result[key] = deep_merge_dicts(result[key], value)
        else:
            # For all other types (lists, primitives, etc.), override completely
            result[key] = value

    return result


def load_data_file(
    file_path: Path,
    *,
    skip_override: bool = False,
    preset_fns: Mapping[str, Callable[[Any], Any]] | None = DEFAULT_PRESET_FNS,
) -> tuple[dict[str, Any], str]:
    """
    Load a data file (JSON/JSON5 or TOML) and return (data, format).

    Automatically checks for and merges override files with the pattern:
    ``<base_name>.override.<extension>``. For example:

    - ``offering.json`` -> ``offering.override.json``
    - ``provider.toml`` -> ``provider.override.toml``

    If an override file exists, it is deep-merged on top of the base file,
    with override values taking precedence.

    After the merge, :func:`expand_presets` walks the result and replaces
    every ``$doc_preset`` / ``$file_preset`` sentinel with the matching
    preset record from ``unitysvc-data``. Pass ``preset_fns=None`` to
    skip the walk entirely (useful for tooling that wants to preserve
    sentinels on round-trip).

    Args:
        file_path: Path to the data file
        skip_override: If True, skip loading and merging override files.
        preset_fns: Mapping of sentinel key → callable for
            :func:`expand_presets`. Defaults to :data:`DEFAULT_PRESET_FNS`
            (``doc_preset`` and ``file_preset`` from ``unitysvc-data``).
            ``None`` disables expansion.

    Returns:
        Tuple of (data dict, format string "json" or "toml")

    Raises:
        ValueError: If file format is not supported
    """
    if file_path.suffix == ".json":
        with open(file_path, encoding="utf-8") as f:
            data = json5.load(f)
        file_format = "json"
    elif file_path.suffix == ".toml":
        with open(file_path, "rb") as f:
            data = tomllib.load(f)
        file_format = "toml"
    else:
        raise ValueError(f"Unsupported file format: {file_path.suffix}")

    if not skip_override:
        override_path = file_path.with_stem(f"{file_path.stem}.override")
        if override_path.exists():
            if override_path.suffix == ".json":
                with open(override_path, encoding="utf-8") as f:
                    override_data = json5.load(f)
            elif override_path.suffix == ".toml":
                with open(override_path, "rb") as f:
                    override_data = tomllib.load(f)
            else:
                override_data = {}
            data = deep_merge_dicts(data, override_data)

    if preset_fns:
        data = expand_presets(data, preset_fns)

    return data, file_format


def write_override_file(
    base_file: Path,
    override_data: dict[str, Any],
    delete_if_empty: bool = False,
) -> Path | None:
    """
    Write or update an override file for a data file.

    Override files follow the pattern: ``<stem>.override.<suffix>``.
    For example: ``listing.json`` -> ``listing.override.json``.

    If the override file exists, the new data is deep-merged with existing data.
    If it doesn't exist, a new file is created.

    Args:
        base_file: Path to the base data file (e.g., ``listing.json``)
        override_data: Data to write/merge into the override file
        delete_if_empty: If True, delete the override file when data is empty

    Returns:
        Path to the override file, or None if deleted
    """
    override_path = base_file.with_stem(f"{base_file.stem}.override")

    if base_file.suffix == ".json":
        file_format = "json"
    elif base_file.suffix == ".toml":
        file_format = "toml"
    else:
        file_format = "json"
        override_path = base_file.parent / f"{base_file.stem}.override.json"

    if override_path.exists():
        if file_format == "json":
            with open(override_path, encoding="utf-8") as f:
                existing_data = json5.load(f)
        else:
            with open(override_path, "rb") as f:
                existing_data = tomllib.load(f)
        merged_data = deep_merge_dicts(existing_data, override_data)
    else:
        merged_data = override_data

    if delete_if_empty and not merged_data:
        if override_path.exists():
            override_path.unlink()
        return None

    write_data_file(override_path, merged_data, file_format)
    return override_path


def read_override_file(base_file: Path) -> dict[str, Any]:
    """
    Read an override file for a data file if it exists.

    Args:
        base_file: Path to the base data file (e.g., ``listing.json``)

    Returns:
        Override data dict, or empty dict if no override file exists
    """
    override_path = base_file.with_stem(f"{base_file.stem}.override")

    if not override_path.exists():
        return {}

    if base_file.suffix == ".json":
        with open(override_path, encoding="utf-8") as f:
            return json5.load(f)
    if base_file.suffix == ".toml":
        with open(override_path, "rb") as f:
            return tomllib.load(f)
    try:
        with open(override_path, encoding="utf-8") as f:
            return json5.load(f)
    except Exception:
        return {}


def write_data_file(file_path: Path, data: dict[str, Any], format: str) -> None:
    """
    Write data back to file in the specified format.

    Args:
        file_path: Path to the data file
        data: Data dictionary to write
        format: Format string ("json" or "toml")

    Raises:
        ValueError: If format is not supported
    """
    if format == "json":
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
    elif format == "toml":
        with open(file_path, "wb") as f:
            tomli_w.dump(data, f)
    else:
        raise ValueError(f"Unsupported format: {format}")


def find_data_files(data_dir: Path, extensions: tuple[str, ...] | None = None) -> list[Path]:
    """
    Find all data files in a directory with specified extensions.

    Args:
        data_dir: Directory to search
        extensions: Tuple of extensions to search for (default: ("json", "toml"))

    Returns:
        List of Path objects for matching files
    """
    if extensions is None:
        extensions = ("json", "toml")

    data_files: list[Path] = []
    for ext in extensions:
        data_files.extend(data_dir.rglob(f"*.{ext}"))

    return data_files


def find_file_by_schema_and_name(
    data_dir: Path, schema: str, name_field: str, name_value: str
) -> tuple[Path, str, dict[str, Any]] | None:
    """
    Find a data file by schema type and name field value.

    Args:
        data_dir: Directory to search
        schema: Schema identifier (e.g., "offering_v1", "listing_v1")
        name_field: Field name to match (e.g., "name", "seller_name")
        name_value: Value to match in the name field

    Returns:
        Tuple of (file_path, format, data) if found, None otherwise
    """
    data_files = find_data_files(data_dir)

    for data_file in data_files:
        try:
            data, file_format = load_data_file(data_file)
            if data.get("schema") == schema and data.get(name_field) == name_value:
                return data_file, file_format, data
        except Exception:
            # Skip files that can't be loaded
            continue

    return None


@lru_cache(maxsize=256)
def find_files_by_schema(
    data_dir: Path,
    schema: str,
    path_filter: str | None = None,
    field_filter: tuple[tuple[str, Any], ...] | None = None,
    skip_override: bool = False,
) -> list[tuple[Path, str, dict[str, Any]]]:
    """
    Find all data files matching a schema with optional filters.

    Args:
        data_dir: Directory to search
        schema: Schema identifier (e.g., "offering_v1", "listing_v1")
        path_filter: Optional string that must be in the file path
        field_filter: Optional tuple of (key, value) pairs to filter by
        skip_override: If True, skip loading override files (use base data only)

    Returns:
        List of tuples (file_path, format, data) for matching files
    """
    data_files = find_data_files(data_dir)
    matching_files: list[tuple[Path, str, dict[str, Any]]] = []

    # Convert field_filter tuple back to dict for filtering
    field_filter_dict = dict(field_filter) if field_filter else None

    for data_file in data_files:
        try:
            # Apply path filter
            if path_filter and path_filter not in str(data_file):
                continue

            data, file_format = load_data_file(data_file, skip_override=skip_override)

            # Check schema
            if data.get("schema") != schema:
                continue

            # Apply field filters
            if field_filter_dict:
                if not all(data.get(k) == v for k, v in field_filter_dict.items()):
                    continue

            matching_files.append((data_file, file_format, data))

        except Exception:
            # Skip files that can't be loaded
            continue

    return matching_files


