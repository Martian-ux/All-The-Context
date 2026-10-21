"""Synthetic classification controls for the project-qualified workflow boundary."""

from __future__ import annotations

import pytest
from allthecontext.memory_policy import extract_live_user_claim


@pytest.mark.parametrize(
    "content",
    [
        "We use DuckDB for Juniper.",
        "I use PostgreSQL for rook.",
        "We use an unknown tool for an unknown project.",
        "We use DuckDB in Juniper.",
        "We use DuckDB on Juniper.",
        "We use DuckDB within Juniper.",
        "We use DuckDB across Juniper and Rook.",
        "My workflow is terminal based for Juniper.",
        "Our workflow is terminal based in Rook.",
        "My stack is Python on Juniper.",
        "Our stack is PostgreSQL within Rook.",
        'We use DuckDB for "Juniper 2".',
        "We use DuckDB for Érable.",
        "We use DuckDB for 松.",
        "We use DuckDB for juniper-rook.",
        "Ｗｅ ｕｓｅ ＤｕｃｋＤＢ ｆｏｒ Ｊｕｎｉｐｅｒ.",  # noqa: RUF001 - NFKC refusal control
        "WE\tUSE DuckDB\nFOR\tJuniper.",
        "We use DuckDB for Juniper for all projects.",
        "We use DuckDB for all projects except Juniper.",
        "We use DuckDB for all projects. Rook uses PostgreSQL.",
        "We use DuckDB for Juniper globally.",
        "We use DuckDB for this project.",
        "I use Python for scripting.",
        "My workflow is based on Python.",
    ],
)
def test_qualified_workflow_does_not_supply_an_automatic_claim(content: str) -> None:
    assert extract_live_user_claim(content) is None


@pytest.mark.parametrize(
    "content",
    [
        "We use Python.",
        "I use Fortran.",
        "We use Informix and Onyx.",
        "My workflow is terminal based.",
        "Our stack is Python and DuckDB.",
        "We use Python globally.",
        "We use Python for all projects.",
        "I use Python in every project.",
        "My stack is Python on each project.",
        "We use Python within all projects.",
        "Our workflow is terminal based across all projects.",
    ],
)
def test_unqualified_and_explicit_global_workflows_keep_full_content(content: str) -> None:
    claim = extract_live_user_claim(content)
    assert claim is not None and claim.kind == "workflow"
    assert claim.content == content and claim.attribute_key == "tooling"
    assert claim.structured_value == {"claim_type": "workflow", "value": claim.value}


@pytest.mark.parametrize(
    ("content", "attribute"),
    [
        ("I prefer tabs for code indentation.", "response_format"),
        ("I prefer concise summaries.", "response_style"),
        ("I prefer answers in paragraphs.", "response_layout"),
    ],
)
def test_global_personal_preferences_keep_their_independent_facets(
    content: str, attribute: str
) -> None:
    claim = extract_live_user_claim(content)
    assert claim is not None and claim.kind == "interaction_preference"
    assert claim.content == content and claim.attribute_key == attribute
