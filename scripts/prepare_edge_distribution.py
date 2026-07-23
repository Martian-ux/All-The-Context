"""Generate deterministic digest-pinned Edge deployment handoff files."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "packages" / "allthecontext" / "src"
BLUEPRINT_TEMPLATE = REPOSITORY_ROOT / "deploy" / "edge" / "render.template.yaml"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from allthecontext.edge_distribution import (  # noqa: E402
    edge_image_metadata,
    render_blueprint,
)


def _write_new(path: Path, content: str, *, force: bool) -> None:
    resolved = path.expanduser().resolve()
    if resolved.exists() and not force:
        raise SystemExit(f"refusing to overwrite {resolved}; pass --force after review")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    temporary = resolved.with_name(f"{resolved.name}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(resolved)
    finally:
        temporary.unlink(missing_ok=True)


def generate(
    *,
    image_reference: str,
    source_commit: str,
    blueprint_template: Path,
    blueprint_output: Path,
    metadata_output: Path,
    force: bool = False,
) -> tuple[Path, Path]:
    """Write the pre-activation metadata and Blueprint artifacts."""

    template = blueprint_template.expanduser().resolve().read_text(encoding="utf-8")
    metadata = (
        json.dumps(
            edge_image_metadata(image_reference, source_commit),
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    blueprint = render_blueprint(template, image_reference)
    _write_new(metadata_output, metadata, force=force)
    _write_new(blueprint_output, blueprint, force=force)
    return (
        metadata_output.expanduser().resolve(),
        blueprint_output.expanduser().resolve(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image-reference", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--blueprint-template", type=Path, default=BLUEPRINT_TEMPLATE)
    parser.add_argument("--blueprint-output", type=Path, required=True)
    parser.add_argument("--metadata-output", type=Path, required=True)
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()
    outputs = generate(
        image_reference=args.image_reference,
        source_commit=args.source_commit,
        blueprint_template=args.blueprint_template,
        blueprint_output=args.blueprint_output,
        metadata_output=args.metadata_output,
        force=args.force,
    )
    print(
        json.dumps(
            {
                "generated": [str(path) for path in outputs],
                "operator_review_required": True,
                "secrets_written": False,
            },
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
