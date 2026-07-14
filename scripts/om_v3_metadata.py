#!/usr/bin/env python3
"""Read the root array dimensions from an Open-Meteo OM v3 file."""

from __future__ import annotations

import struct
from pathlib import Path


OM_TRAILER_SIZE = 24


def read_array_dimensions(path: Path) -> tuple[int, ...]:
    size = path.stat().st_size
    if size < OM_TRAILER_SIZE + 3:
        raise ValueError(f"OM file is too small: {path}")
    with path.open("rb") as handle:
        if handle.read(3) != b"OM\x03":
            raise ValueError(f"not an OM v3 file: {path}")
        handle.seek(size - OM_TRAILER_SIZE)
        trailer = handle.read(OM_TRAILER_SIZE)
        if trailer[:3] != b"OM\x03":
            raise ValueError(f"invalid OM v3 trailer: {path}")
        root_offset, root_size = struct.unpack_from("<QQ", trailer, 8)
        if root_size > 1024 * 1024 or root_offset + root_size > size:
            raise ValueError(f"invalid OM root metadata range: {path}")
        handle.seek(root_offset)
        root = handle.read(root_size)
    if len(root) < 40 or not 12 <= root[0] <= 21:
        raise ValueError(f"OM root is not an array: {path}")
    child_count = struct.unpack_from("<I", root, 4)[0]
    dimension_count = struct.unpack_from("<Q", root, 24)[0]
    if dimension_count == 0 or dimension_count > 16:
        raise ValueError(f"invalid OM dimension count: {path}")
    cursor = 40 + child_count * 16
    end = cursor + dimension_count * 8
    if end > len(root):
        raise ValueError(f"truncated OM dimensions: {path}")
    return struct.unpack_from(f"<{dimension_count}Q", root, cursor)
