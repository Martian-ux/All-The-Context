"""Deterministic physically allocated boundary canary for raw-import acceptance.

The generator writes non-sparse, high-entropy JSONL with embedded durable
statements so a successful import cannot pass by being parse-empty or
pathologically compressible. Exact ``2_000_000_000``-byte candidate runs are
acceptance work (B-204); this module provides the stable generator contract
and scalable synthetic tests.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import MAX_IMPORT_BYTES
from .import_boundary import (
    BOUNDARY_BYTES,
    BOUNDARY_PLUS_ONE_BYTES,
    expected_chunk_count,
)
from .storage import SOURCE_BLOB_CHUNK_BYTES, InvalidStateError

BOUNDARY_CANARY_GENERATOR_VERSION = "boundary-canary-v1"
BOUNDARY_CANARY_MEDIA_TYPE = "application/x-ndjson"
BOUNDARY_CANARY_FILENAME = "atc-boundary-canary.jsonl"
BOUNDARY_CANARY_SIZE_BYTES = BOUNDARY_BYTES
BOUNDARY_CANARY_PLUS_ONE_BYTES = BOUNDARY_PLUS_ONE_BYTES

# Embedded durable statements at known relative offsets (interruption checkpoints).
# Fractions are of the requested total size, exclusive of the trailing newline.
_CHECKPOINT_FRACTIONS: tuple[float, ...] = (0.0, 0.25, 0.50, 0.75, 0.99)

# Fixed high-entropy stream seed material (not a secret).
_STREAM_LABEL = b"allthecontext-boundary-canary-v1"


@dataclass(frozen=True, slots=True)
class BoundaryCanarySpec:
    generator_version: str
    size_bytes: int
    chunk_bytes: int
    expected_chunk_count: int
    expected_sha256: str
    filename: str
    media_type: str
    checkpoint_offsets: tuple[int, ...]
    expected_min_candidates: int
    expected_publication_nonzero: bool
    sparse_allowed: bool
    empty_parse_allowed: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "generator_version": self.generator_version,
            "size_bytes": self.size_bytes,
            "chunk_bytes": self.chunk_bytes,
            "expected_chunk_count": self.expected_chunk_count,
            "expected_sha256": self.expected_sha256,
            "filename": self.filename,
            "media_type": self.media_type,
            "checkpoint_offsets": list(self.checkpoint_offsets),
            "expected_min_candidates": self.expected_min_candidates,
            "expected_publication_nonzero": self.expected_publication_nonzero,
            "sparse_allowed": self.sparse_allowed,
            "empty_parse_allowed": self.empty_parse_allowed,
            "boundary_bytes": BOUNDARY_CANARY_SIZE_BYTES,
            "boundary_plus_one_bytes": BOUNDARY_CANARY_PLUS_ONE_BYTES,
        }


def checkpoint_offsets(size_bytes: int) -> tuple[int, ...]:
    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")
    if size_bytes == 0:
        return ()
    offsets: list[int] = []
    for fraction in _CHECKPOINT_FRACTIONS:
        offset = int(size_bytes * fraction)
        if offset >= size_bytes:
            offset = size_bytes - 1
        if offset not in offsets:
            offsets.append(offset)
    return tuple(offsets)


def _block_bytes(block_index: int) -> bytes:
    """Return 32 high-entropy bytes for one stream block (SHA-256 expansion)."""
    material = _STREAM_LABEL + b":" + BOUNDARY_CANARY_GENERATOR_VERSION.encode("ascii")
    material += b":" + block_index.to_bytes(8, "big", signed=False)
    return hashlib.sha256(material).digest()


def _filler_line(line_index: int, payload_width: int) -> bytes:
    """One JSONL object of approximately payload_width content bytes."""
    # Build deterministic hex-ish filler from the hash stream so ZIP/deflate
    # and SQLite compression cannot collapse the canary into a tiny footprint.
    needed = max(payload_width, 32)
    parts: list[bytes] = []
    block = line_index * 64
    while sum(len(part) for part in parts) < needed:
        parts.append(_block_bytes(block).hex().encode("ascii"))
        block += 1
    blob = b"".join(parts)[:needed]
    document = {
        "kind": "canary_filler",
        "index": line_index,
        "generator": BOUNDARY_CANARY_GENERATOR_VERSION,
        "blob": blob.decode("ascii"),
    }
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n"


def _checkpoint_line(checkpoint_index: int, absolute_offset: int) -> bytes:
    """Durable labeled statement used for nonzero parse/publication expectations."""
    document = {
        "kind": "interaction_preference",
        "content": (
            f"Boundary canary checkpoint {checkpoint_index} prefers local SQLite "
            f"context at offset {absolute_offset}."
        ),
        "generator": BOUNDARY_CANARY_GENERATOR_VERSION,
        "checkpoint_index": checkpoint_index,
        "offset": absolute_offset,
    }
    return json.dumps(document, separators=(",", ":"), sort_keys=True).encode("ascii") + b"\n"


def _pad_bytes(start_offset: int, length: int) -> bytes:
    """High-entropy pad that never injects raw newlines into the JSONL stream."""
    if length <= 0:
        return b""
    # Hex expansion stays printable and newline-free so later JSONL lines remain intact.
    parts: list[bytes] = []
    block = 20_000_000 + start_offset
    while sum(len(item) for item in parts) < length:
        parts.append(_block_bytes(block).hex().encode("ascii"))
        block += 1
    pad = b"".join(parts)[:length]
    # Prefer ending on a newline when there is room so the next record starts cleanly.
    if length >= 2 and not pad.endswith(b"\n"):
        pad = pad[:-1] + b"\n"
    return pad


def _iter_canary_bytes(size_bytes: int) -> Iterator[bytes]:
    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")
    if size_bytes == 0:
        return
    if size_bytes > MAX_IMPORT_BYTES + 1:
        # Allow boundary+1 generation for refusal tests only.
        raise ValueError(f"size_bytes must be at most {MAX_IMPORT_BYTES + 1}")

    targets = list(checkpoint_offsets(size_bytes))
    next_checkpoint = 0
    produced = 0
    line_index = 0
    while produced < size_bytes:
        remaining = size_bytes - produced
        emit_checkpoint = next_checkpoint < len(targets) and produced >= targets[next_checkpoint]
        if emit_checkpoint:
            line = _checkpoint_line(next_checkpoint, produced)
            next_checkpoint += 1
            if len(line) > remaining:
                line = _pad_bytes(produced, remaining)
        else:
            # Stay short of the next checkpoint so its offset remains reachable.
            room = remaining
            if next_checkpoint < len(targets):
                room = min(room, max(targets[next_checkpoint] - produced, 1))
            if room < 48:
                line = _pad_bytes(produced, room)
            else:
                width = 400 if room > 512 else max(room - 64, 16)
                line = _filler_line(line_index, width)
                line_index += 1
                if len(line) > room:
                    line = _pad_bytes(produced, room)
        yield line
        produced += len(line)


def stream_boundary_canary(size_bytes: int) -> Iterator[bytes]:
    """Yield deterministic canary bytes without allocating the full artifact."""
    yield from _iter_canary_bytes(size_bytes)


def boundary_canary_sha256(size_bytes: int) -> str:
    digest = hashlib.sha256()
    for chunk in stream_boundary_canary(size_bytes):
        digest.update(chunk)
    return digest.hexdigest()


def boundary_canary_spec(size_bytes: int = BOUNDARY_CANARY_SIZE_BYTES) -> BoundaryCanarySpec:
    if size_bytes < 0:
        raise ValueError("size_bytes must be non-negative")
    return BoundaryCanarySpec(
        generator_version=BOUNDARY_CANARY_GENERATOR_VERSION,
        size_bytes=size_bytes,
        chunk_bytes=SOURCE_BLOB_CHUNK_BYTES,
        expected_chunk_count=expected_chunk_count(size_bytes),
        expected_sha256=boundary_canary_sha256(size_bytes),
        filename=BOUNDARY_CANARY_FILENAME,
        media_type=BOUNDARY_CANARY_MEDIA_TYPE,
        checkpoint_offsets=checkpoint_offsets(size_bytes),
        expected_min_candidates=max(1, len(checkpoint_offsets(size_bytes))),
        expected_publication_nonzero=True,
        sparse_allowed=False,
        empty_parse_allowed=False,
    )


def write_boundary_canary(
    path: Path,
    *,
    size_bytes: int = BOUNDARY_CANARY_SIZE_BYTES,
    overwrite: bool = False,
) -> BoundaryCanarySpec:
    """Physically allocate a non-sparse canary file and return its stable spec.

    The file is written with ordinary buffered writes (no seeking past EOF) so
    platform sparse-file optimizations cannot create a hollow artifact. On
    Windows, ``FILE_ATTRIBUTE_SPARSE_FILE`` is never requested.
    """
    if size_bytes < 0:
        raise InvalidStateError("canary size must be non-negative")
    if size_bytes > MAX_IMPORT_BYTES + 1:
        raise InvalidStateError(f"canary size must be at most {MAX_IMPORT_BYTES + 1} bytes")
    target = path.expanduser().resolve()
    if target.exists() and not overwrite:
        raise InvalidStateError(f"canary path already exists: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".partial")
    digest = hashlib.sha256()
    written = 0
    try:
        with temporary.open("wb") as handle:
            for chunk in stream_boundary_canary(size_bytes):
                handle.write(chunk)
                digest.update(chunk)
                written += len(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        if written != size_bytes:
            raise InvalidStateError(
                f"canary write size mismatch: expected {size_bytes}, wrote {written}"
            )
        # Refuse sparse hollow files when the platform reports allocated size.
        allocated = _allocated_size(temporary)
        if allocated is not None and allocated < size_bytes:
            raise InvalidStateError(
                "canary file appears sparse or under-allocated: "
                f"logical={size_bytes}, allocated={allocated}"
            )
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    actual_sha = digest.hexdigest()
    expected = boundary_canary_sha256(size_bytes)
    if actual_sha != expected:
        target.unlink(missing_ok=True)
        raise InvalidStateError("canary SHA-256 diverged from the deterministic generator")
    return BoundaryCanarySpec(
        generator_version=BOUNDARY_CANARY_GENERATOR_VERSION,
        size_bytes=size_bytes,
        chunk_bytes=SOURCE_BLOB_CHUNK_BYTES,
        expected_chunk_count=expected_chunk_count(size_bytes),
        expected_sha256=actual_sha,
        filename=target.name,
        media_type=BOUNDARY_CANARY_MEDIA_TYPE,
        checkpoint_offsets=checkpoint_offsets(size_bytes),
        expected_min_candidates=max(1, len(checkpoint_offsets(size_bytes))),
        expected_publication_nonzero=True,
        sparse_allowed=False,
        empty_parse_allowed=False,
    )


def verify_boundary_canary_file(path: Path, *, size_bytes: int | None = None) -> BoundaryCanarySpec:
    """Verify size, non-sparseness (when measurable), and SHA-256 of a canary."""
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise InvalidStateError("canary file does not exist")
    logical = resolved.stat().st_size
    expected_size = logical if size_bytes is None else size_bytes
    if logical != expected_size:
        raise InvalidStateError(f"canary size mismatch: expected {expected_size}, found {logical}")
    allocated = _allocated_size(resolved)
    if allocated is not None and allocated < logical:
        raise InvalidStateError(
            f"canary file appears sparse: logical={logical}, allocated={allocated}"
        )
    digest = hashlib.sha256()
    with resolved.open("rb") as handle:
        while chunk := handle.read(SOURCE_BLOB_CHUNK_BYTES):
            digest.update(chunk)
    actual = digest.hexdigest()
    expected = boundary_canary_sha256(expected_size)
    if actual != expected:
        raise InvalidStateError("canary SHA-256 does not match the generator contract")
    return BoundaryCanarySpec(
        generator_version=BOUNDARY_CANARY_GENERATOR_VERSION,
        size_bytes=expected_size,
        chunk_bytes=SOURCE_BLOB_CHUNK_BYTES,
        expected_chunk_count=expected_chunk_count(expected_size),
        expected_sha256=actual,
        filename=resolved.name,
        media_type=BOUNDARY_CANARY_MEDIA_TYPE,
        checkpoint_offsets=checkpoint_offsets(expected_size),
        expected_min_candidates=max(1, len(checkpoint_offsets(expected_size))),
        expected_publication_nonzero=True,
        sparse_allowed=False,
        empty_parse_allowed=False,
    )


def _allocated_size(path: Path) -> int | None:
    """Best-effort allocated size; ``None`` when the platform does not report it."""
    try:
        stat_result = path.stat()
    except OSError:
        return None
    # st_blocks is POSIX (512-byte units). Windows Path.stat often omits it.
    blocks = getattr(stat_result, "st_blocks", None)
    if isinstance(blocks, int) and blocks >= 0:
        return blocks * 512
    return None
