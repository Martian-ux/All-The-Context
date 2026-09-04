"""Canonical, immutable identity for a shipped All The Context build."""

from __future__ import annotations

import hashlib
import importlib.resources
import json
import platform as host_platform
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

PRODUCT_NAME = "All The Context"
PRODUCT_VERSION = "0.1.0-beta.7"
BUILD_IDENTITY_SCHEMA_VERSION = 1
BUILD_IDENTITY_FILE_NAME = "build-identity-v1.json"
COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
PLATFORMS = frozenset({"windows", "macos", "linux"})
ARCHITECTURES = frozenset({"x86_64", "arm64"})
IDENTITY_FIELDS = frozenset(
    {"schema_version", "version", "channel", "platform", "architecture", "source_commit"}
)


class BuildIdentityError(ValueError):
    """A build identity is missing, malformed, or contradictory."""


def channel_for_version(version: str) -> str:
    if not isinstance(version, str):
        raise BuildIdentityError("build identity version is not a string")
    try:
        from .release_manifest import ReleaseVersion

        parsed = ReleaseVersion.parse(version)
    except (ImportError, ValueError) as exc:
        raise BuildIdentityError("build identity version is invalid") from exc
    return "beta" if parsed.stability == 0 else "stable"


def normalized_platform(system: str) -> str:
    values = {"Windows": "windows", "Darwin": "macos", "Linux": "linux"}
    try:
        return values[system]
    except KeyError as exc:
        raise BuildIdentityError("build identity platform is unsupported") from exc


def normalized_architecture(machine: str) -> str:
    values = {
        "amd64": "x86_64",
        "x86_64": "x86_64",
        "x86-64": "x86_64",
        "arm64": "arm64",
        "aarch64": "arm64",
    }
    try:
        return values[machine.casefold()]
    except KeyError as exc:
        raise BuildIdentityError("build identity architecture is unsupported") from exc


@dataclass(frozen=True, slots=True)
class BuildIdentity:
    version: str
    channel: str
    platform: str
    architecture: str
    source_commit: str

    def __post_init__(self) -> None:
        if self.channel not in {"stable", "beta"}:
            raise BuildIdentityError("build identity channel is invalid")
        if self.platform not in PLATFORMS:
            raise BuildIdentityError("build identity platform is invalid")
        if self.architecture not in ARCHITECTURES:
            raise BuildIdentityError("build identity architecture is invalid")
        if not COMMIT_PATTERN.fullmatch(self.source_commit):
            raise BuildIdentityError("build identity source commit is not a full lowercase SHA")
        if channel_for_version(self.version) != self.channel:
            raise BuildIdentityError("build identity version and channel contradict each other")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BUILD_IDENTITY_SCHEMA_VERSION,
            "version": self.version,
            "channel": self.channel,
            "platform": self.platform,
            "architecture": self.architecture,
            "source_commit": self.source_commit,
        }

    def canonical_json(self) -> bytes:
        return json.dumps(
            self.as_dict(), ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_json()).hexdigest()

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> BuildIdentity:
        if not isinstance(value, Mapping):
            raise BuildIdentityError("build identity is not an object")
        if set(value) != IDENTITY_FIELDS:
            raise BuildIdentityError("build identity fields differ from the version 1 schema")
        schema_version = value.get("schema_version")
        if schema_version != BUILD_IDENTITY_SCHEMA_VERSION:
            raise BuildIdentityError("unsupported build identity schema")
        fields = ("version", "channel", "platform", "architecture", "source_commit")
        if any(not isinstance(value.get(field), str) for field in fields):
            raise BuildIdentityError("build identity fields have invalid types")
        return cls(
            version=cast(str, value["version"]),
            channel=cast(str, value["channel"]),
            platform=cast(str, value["platform"]),
            architecture=cast(str, value["architecture"]),
            source_commit=cast(str, value["source_commit"]),
        )


def make_build_identity(
    *, version: str, source_commit: str, platform_name: str, architecture: str
) -> BuildIdentity:
    identity = BuildIdentity(
        version=version,
        channel=channel_for_version(version),
        platform=platform_name,
        architecture=architecture,
        source_commit=source_commit,
    )
    return identity


def load_embedded_build_identity() -> BuildIdentity:
    try:
        resource = importlib.resources.files("allthecontext").joinpath(BUILD_IDENTITY_FILE_NAME)
        raw = resource.read_bytes()
    except (FileNotFoundError, OSError, ModuleNotFoundError) as exc:
        raise BuildIdentityError("embedded build identity is missing") from exc
    if len(raw) > 16 * 1024:
        raise BuildIdentityError("embedded build identity is too large")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise BuildIdentityError("embedded build identity is unreadable") from exc
    if not isinstance(value, dict):
        raise BuildIdentityError("embedded build identity is not an object")
    return BuildIdentity.from_mapping(value)


def runtime_build_identity(*, required: bool = False) -> BuildIdentity | None:
    try:
        identity = load_embedded_build_identity()
    except BuildIdentityError:
        if required:
            raise
        return None
    expected_platform = normalized_platform(host_platform.system())
    expected_architecture = normalized_architecture(host_platform.machine())
    if identity.platform != expected_platform or identity.architecture != expected_architecture:
        raise BuildIdentityError("embedded build identity does not match the running host")
    if identity.version != PRODUCT_VERSION:
        raise BuildIdentityError("embedded build identity does not match the packaged version")
    return identity


def runtime_build_identity_status() -> dict[str, Any]:
    """Return safe diagnostics without inventing an identity for a source run."""

    try:
        identity = runtime_build_identity()
    except BuildIdentityError as exc:
        return {
            "status": "invalid",
            "reason": str(exc),
            "version": None,
            "channel": None,
            "platform": None,
            "architecture": None,
            "source_commit": None,
        }
    if identity is None and bool(getattr(sys, "frozen", False)):
        return {
            "status": "invalid",
            "reason": "embedded build identity is missing",
            "version": None,
            "channel": None,
            "platform": None,
            "architecture": None,
            "source_commit": None,
        }
    if identity is None:
        return {
            "status": "source-development",
            "reason": None,
            "version": PRODUCT_VERSION,
            "channel": channel_for_version(PRODUCT_VERSION),
            "platform": normalized_platform(host_platform.system()),
            "architecture": normalized_architecture(host_platform.machine()),
            "source_commit": None,
        }
    result = identity.as_dict()
    result["status"] = "verified"
    result["sha256"] = identity.sha256
    result["reason"] = None
    return result
