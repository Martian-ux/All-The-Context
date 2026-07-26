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
ALLOWED_SCOPES = frozenset({"runtime", "build", "dev"})

# Declared top-level project license; transitive licenses stay NOASSERTION
# when the lock does not carry SPDX license identifiers.
PROJECT_LICENSE = "MIT"


def _require_mapping(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be a JSON/TOML object")
    return value


def _package_index(uv_lock: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    packages = uv_lock.get("package")
    if not isinstance(packages, list) or not packages:
        raise ManifestError("uv.lock does not contain locked packages")
    index: dict[str, dict[str, Any]] = {}
    for package in packages:
        item = _require_mapping(package, "uv.lock package")
        name = item.get("name")
        if not isinstance(name, str):
            raise ManifestError("uv.lock package is missing name")
        key = name.casefold()
        if key in index:
            # Same name may appear once per version; keep first for graph walks
            # and list every entry separately later.
            continue
        index[key] = item
    return index


def _dependency_names(package: Mapping[str, Any], *, extra: str | None = None) -> set[str]:
    names: set[str] = set()
    dependencies = package.get("dependencies")
    if isinstance(dependencies, list):
        for dependency in dependencies:
            if isinstance(dependency, dict) and isinstance(dependency.get("name"), str):
                names.add(dependency["name"].casefold())
    if extra is not None:
        optional = package.get("optional-dependencies")
        if isinstance(optional, dict):
            group = optional.get(extra)
            if isinstance(group, list):
                for dependency in group:
                    if isinstance(dependency, dict) and isinstance(dependency.get("name"), str):
                        names.add(dependency["name"].casefold())
    return names


def _transitive_closure(index: Mapping[str, Mapping[str, Any]], roots: set[str]) -> set[str]:
    seen: set[str] = set()
    stack = list(roots)
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        package = index.get(name)
        if package is None:
            continue
        stack.extend(_dependency_names(package) - seen)
    return seen


def _python_scope_sets(uv_lock: Mapping[str, Any]) -> dict[str, set[str]]:
    index = _package_index(uv_lock)
    project = index.get("all-the-context")
    if project is None:
        raise ManifestError("uv.lock is missing the all-the-context project package")
    runtime_roots = _dependency_names(project) | {"all-the-context"}
    build_roots = _dependency_names(project, extra="packaging")
    dev_roots = _dependency_names(project, extra="dev")
    runtime = _transitive_closure(index, runtime_roots)
    build = _transitive_closure(index, build_roots) - runtime
    dev = _transitive_closure(index, dev_roots) - runtime - build
    return {"runtime": runtime, "build": build, "dev": dev}


def _python_components(uv_lock: Mapping[str, Any]) -> list[dict[str, Any]]:
    packages = uv_lock.get("package")
    if not isinstance(packages, list) or not packages:
        raise ManifestError("uv.lock does not contain locked packages")
    scopes = _python_scope_sets(uv_lock)
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
        # uv.lock does not carry SPDX license text; never invent identifiers
        # beyond the project's declared MIT for the local package itself.
        license_id = PROJECT_LICENSE if name == "all-the-context" else "NOASSERTION"
        folded = name.casefold()
        if folded in scopes["runtime"]:
            scope = "runtime"
        elif folded in scopes["build"]:
            scope = "build"
        elif folded in scopes["dev"]:
            scope = "dev"
        else:
            # Transitive of an optional group not selected above; treat as build
            # tooling rather than claiming runtime inclusion.
            scope = "build"
        components.append(
            {
                "ecosystem": "python",
                "name": name,
                "version": version,
                "license": license_id,
                "locked": True,
                "source_kind": source_kind,
                "scope": scope,
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
                    "scope": "runtime",
                }
            )
            continue
        name = value.get("name")
        if not isinstance(name, str):
            name = install_path.rsplit("node_modules/", 1)[-1]
        version = value.get("version")
        if not isinstance(version, str):
            if value.get("optional") is True or value.get("peer") is True:
                continue
            raise ManifestError(f"package-lock entry lacks version: {install_path}")
        key = (name.casefold(), version)
        if key in seen:
            continue
        seen.add(key)
        # package-lock may include a short SPDX id, never full license text.
        license_value = value.get("license")
        if isinstance(license_value, str) and license_value.strip() and "\n" not in license_value:
            license_id = license_value.strip()
            if len(license_id) > 120:
                license_id = "NOASSERTION"
        else:
            license_id = "NOASSERTION"
        # npm marks pure development dependencies with dev=true.
        scope = "dev" if value.get("dev") is True or value.get("devOptional") is True else "runtime"
        components.append(
            {
                "ecosystem": "npm",
                "name": name,
                "version": version,
                "license": license_id,
                "locked": True,
                "source_kind": "registry",
                "scope": scope,
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
    if any(item.get("scope") not in ALLOWED_SCOPES for item in components):
        raise ManifestError("component inventory produced an invalid scope label")
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
        "License fields are SPDX identifiers when the reviewed lock provides them;",
        "otherwise NOASSERTION — this file never embeds SPDX license text.",
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
            f"- [{item.get('ecosystem')}/{item.get('scope')}] "
            f"{item.get('name')}@{item.get('version')} "
            f"(license={item.get('license')}, locked={item.get('locked')})"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path
