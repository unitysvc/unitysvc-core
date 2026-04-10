"""UnitySVC Core — shared data models and validation helpers.

This package is audience-neutral. It contains the pydantic models, JSON
schemas, and validator consumed by the UnitySVC backend, the customer SDK,
the admin CLI, and the seller SDK.

It intentionally does NOT include any CLI, HTTP client, or audience-
specific helpers (seller catalog builders, customer query helpers, etc.).
Those live in the corresponding audience packages (``unitysvc-sellers``,
``unitysvc`` customer SDK, ``unitysvc-admin``).
"""

__author__ = """Bo Peng"""
__email__ = "bo.peng@unitysvc.com"

# Shared file / data utilities used by the backend and SDK consumers
from .utils import (
    compute_file_hash,
    generate_content_based_key,
    get_basename,
    get_file_extension,
    mime_type_to_extension,
)

__all__ = [
    # File utilities
    "compute_file_hash",
    "generate_content_based_key",
    "get_basename",
    "get_file_extension",
    "mime_type_to_extension",
]
