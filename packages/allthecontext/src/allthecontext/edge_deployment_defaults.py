"""Reviewed hosted Edge defaults embedded in packaged Core builds.

This file deliberately ships disabled until an operator has published a public,
digest-addressed Edge image and committed the generated Render blueprint.  The
distribution preparation tool emits a reviewed replacement for release builds.
"""

from __future__ import annotations

EDGE_DEPLOY_URL: str | None = None
EDGE_DEPLOY_BRANCH: str | None = None
EDGE_IMAGE_REFERENCE: str | None = None
EDGE_SOURCE_COMMIT: str | None = None
EDGE_BLUEPRINT_COMMIT: str | None = None
