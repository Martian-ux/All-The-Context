"""Build a content-free component and license inventory from reviewed locks."""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .release_manifest import ManifestError, sha256_file

INVENTORY_SCHEMA_VERSION = 1
INVENTORY_FILE_NAME = "component-inventory-v1.json"
COMMIT = re.compile(r"[0-9a-f]{40}")

# Declared top-level project license; transitive licenses may be NOASSERTION
# when the lock does not carry SPDX license metadata.
PROJECT_LICENSE = "MIT"


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be a JSON/TOML object")
    return value


def _python_components(uv_lock: Mapping[str, Any]) -> list[dict[str, Any]]:
    packages = uv_lock.get("package")
    if not isinstance(packages, list) or not packages:
        raise ManifestError("uv.lock does not contain locked packages")
    components: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for package in packages:
        item = _require_mapping(package, "uv.lock package")
        name = item.get("name")
        version = item.get("version")
        if not isinstance(name, str) or not isinstance(version, str):
            raise ManifestError("uv.lock package is missing name or version")
        key = (name.casefold(), version)
        if key in seen:
            raise ManifestError(f"uv.lock contains duplicate package entries: {name}@{version}")
        seen.add(key)
        source = item.get("source")
        source_kind = "unknown"
        if isinstance(source, dict):
            if "registry" in source:
                source_kind = "registry"
            elif "editable" in source or "virtual" in source or "path" in source:
                source_kind = "path"
            elif "git" in source:
                source_kind = "git"
        license_id = PROJECT_LICENSE if name == "all-the-context" else "NOASSERTION"
        components.append(
            {
                "ecosystem": "python",
                "name": name,
                "version": version,
                "license": license_id,
                "locked": True,
                "source_kind": source_kind,
            }
        )
    return sorted(components, key=lambda item: (item["name"].casefold(), item["version"]))


def _npm_components(package_lock: Mapping[str, Any]) -> list[dict[str, Any]]:
    packages = package_lock.get("packages")
    if not isinstance(packages, dict) or not packages:
        raise ManifestError("package-lock.json does not contain locked packages")
    components: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for install_path, value in packages.items():
        if not isinstance(install_path, str) or not isinstance(value, dict):
            raise ManifestError("package-lock.json package entry is malformed")
        if install_path == "":
            name = value.get("name")
            version = value.get("version")
            if not isinstance(name, str) or not isinstance(version, str):
                raise ManifestError("package-lock root package metadata is incomplete")
            key = (name.casefold(), version)
            if key in seen:
                continue
            seen.add(key)
            components.append(
                {
                    "ecosystem": "npm",
                    "name": name,
                    "version": version,
                    "license": "NOASSERTION",
                    "locked": True,
                    "source_kind": "root",
                }
            )
            continue
        name = value.get("name")
        if not isinstance(name, str):
            # npm v2/v3 lock paths encode the package name under node_modules.
            name = install_path.rsplit("node_modules/", 1)[-1]
        version = value.get("version")
        if not isinstance(version, str):
            # Optional peer placeholders may omit versions; skip non-resolved entries.
            if value.get("optional") is True or value.get("peer") is True:
                continue
            raise ManifestError(f"package-lock entry lacks version: {install_path}")
        key = (name.casefold(), version)
        if key in seen:
            continue
        seen.add(key)
        license_value = value.get("license")
        if isinstance(license_value, str) and license_value.strip():
            license_id = license_value.strip()
        else:
            license_id = "NOASSERTION"
        components.append(
            {
                "ecosystem": "npm",
                "name": name,
                "version": version,
                "license": license_id,
                "locked": True,
                "source_kind": "registry",
            }
        )
    return sorted(components, key=lambda item: (item["name"].casefold(), item["version"]))


def build_component_inventory(
    repository_root: Path,
    *,
    source_commit: str,
    version: str,
) -> dict[str, Any]:
    if COMMIT.fullmatch(source_commit) is None:
        raise ManifestError("component inventory source commit must be a full lowercase SHA")
    repository_root = repository_root.resolve()
    uv_lock_path = repository_root / "uv.lock"
    package_lock_path = repository_root / "apps" / "dashboard" / "package-lock.json"
    pyproject_path = repository_root / "pyproject.toml"
    if not uv_lock_path.is_file() or not package_lock_path.is_file():
        raise ManifestError("reviewed Python and dashboard lock files are required")
    uv_lock = tomllib.loads(uv_lock_path.read_text(encoding="utf-8"))
    package_lock = json.loads(package_lock_path.read_text(encoding="utf-8"))
    pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    project_version = pyproject.get("project", {}).get("version")
    if not isinstance(project_version, str):
        raise ManifestError("pyproject.toml is missing project.version")
    components = _python_components(uv_lock) + _npm_components(package_lock)
    uv_digest, _ = sha256_file(uv_lock_path)
    npm_digest, _ = sha256_file(package_lock_path)
    inventory = {
        "schema_version": INVENTORY_SCHEMA_VERSION,
        "version": version,
        "source_commit": source_commit,
        "project_version": project_version,
        "locks": {
            "uv.lock": {"sha256": uv_digest},
            "apps/dashboard/package-lock.json": {"sha256": npm_digest},
        },
        "component_count": len(components),
        "components": components,
    }
    return inventory


def write_component_inventory(path: Path, inventory: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise ManifestError(f"refusing to replace component inventory: {path.name}")
    path.write_text(
        json.dumps(inventory, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    digest, _ = sha256_file(path)
    checksum = path.with_name(f"{path.name}.sha256")
    checksum.write_text(f"{digest}  {path.name}\n", encoding="ascii", newline="\n")
    return path


def write_notices(path: Path, inventory: Mapping[str, Any]) -> Path:
    """Write a human-readable NOTICE inventory without dependency license text blobs."""

    if path.exists():
        raise ManifestError(f"refusing to replace notices file: {path.name}")
    lines = [
        "All The Context component inventory notices",
        "==========================================",
        "",
        "This file lists locked components that compose a release candidate.",
        "It does not reproduce third-party license text. Consult each package's",
        "upstream license file. Project source is MIT unless a file states otherwise.",
        "",
        f"Source commit: {inventory.get('source_commit')}",
        f"Release version: {inventory.get('version')}",
        f"Component count: {inventory.get('component_count')}",
        "",
        "Components",
        "----------",
        "",
    ]
    components = inventory.get("components")
    if not isinstance(components, list):
        raise ManifestError("component inventory is missing components")
    for item in components:
        if not isinstance(item, dict):
            raise ManifestError("component inventory entry is malformed")
        lines.append(
            f"- [{item.get('ecosystem')}] {item.get('name')}@{item.get('version')} "
            f"(license={item.get('license')}, locked={item.get('locked')})"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path
