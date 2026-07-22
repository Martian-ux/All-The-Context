from __future__ import annotations

from pathlib import Path

from allthecontext.config import CoreConfig
from allthecontext.instance_identity import (
    ensure_instance_secret,
    instance_proof,
    proof_matches,
    read_instance_secret,
)


def test_instance_identity_is_stable_and_challenge_bound(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path)

    first = ensure_instance_secret(config)
    second = ensure_instance_secret(config)
    proof = instance_proof(config, "challenge-one")

    assert first == second == read_instance_secret(config)
    assert proof_matches(config, "challenge-one", proof)
    assert not proof_matches(config, "challenge-two", proof)


def test_instance_identity_rejects_invalid_file(tmp_path: Path) -> None:
    config = CoreConfig.in_directory(tmp_path)
    config.prepare()
    (config.data_dir / "instance-identity.json").write_text("[]", encoding="utf-8")

    assert not proof_matches(config, "challenge", "forged")
