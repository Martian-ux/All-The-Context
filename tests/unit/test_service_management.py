from __future__ import annotations

from pathlib import Path

from allthecontext.service_management import service_install_plan


def test_service_plan_is_per_user_and_uses_explicit_paths(tmp_path: Path) -> None:
    executable = tmp_path / "atc executable"
    plan = service_install_plan(executable, tmp_path / "data")
    assert plan.command == (str(executable), "serve-core")
    assert plan.requires_elevation is False
    assert plan.config_path.is_absolute()
