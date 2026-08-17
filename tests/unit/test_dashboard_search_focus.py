from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STYLES = ROOT / "apps" / "dashboard" / "src" / "styles.css"

_RULE = re.compile(r"([^{}]+)\{([^{}]+)\}")
_WRAPPER_FOCUS = re.compile(
    r"(?:^|[\s>+~])\.search-input(?![\w-]).*"
    r"(:(?:focus-within|focus-visible|focus)\b|:has\([^)]*:focus)"
)
_INPUT_DESCENDANT = re.compile(r"\.search-input\s+input\b")
_OUTLINE_WIDTH = re.compile(r"(?:^|;)\s*outline-width\s*:\s*([^;]+)", re.I)
_OUTLINE = re.compile(r"(?:^|;)\s*outline\s*:\s*([^;]+)", re.I)
_BOX_SHADOW = re.compile(r"(?:^|;)\s*box-shadow\s*:\s*([^;]+)", re.I)
_POSITIVE_LENGTH = re.compile(r"[1-9]\d*(?:\.\d+)?(?:px|em|rem)")
_INPUT_OUTLINE_ZERO = re.compile(
    r"\.search-input\s+input[^{]*\{[^}]*\boutline\s*:\s*0\b",
    re.I,
)


def _is_nonzero_non_none(value: str) -> bool:
    normalized = value.strip().casefold()
    if not normalized or normalized in {"none", "0", "0px", "initial", "unset", "inherit", "auto"}:
        return False
    if re.match(r"0(?:px)?(?:\s|$)", normalized):
        return False
    if re.search(r"\bnone\b", normalized):
        return False
    return _POSITIVE_LENGTH.search(normalized) is not None


def search_wrapper_focus_indicator(css: str) -> str | None:
    for match in _RULE.finditer(css):
        selectors = [item.replace("\n", " ").strip() for item in match.group(1).split(",")]
        targets_wrapper = any(
            _WRAPPER_FOCUS.search(selector) and not _INPUT_DESCENDANT.search(selector)
            for selector in selectors
        )
        if not targets_wrapper:
            continue
        body = match.group(2)
        for pattern in (_OUTLINE_WIDTH, _OUTLINE, _BOX_SHADOW):
            candidate = pattern.search(body)
            if candidate and _is_nonzero_non_none(candidate.group(1)):
                return candidate.group(1).strip()
    return None


def test_search_wrapper_has_focus_dependent_indicator() -> None:
    css = STYLES.read_text(encoding="utf-8")
    assert _INPUT_OUTLINE_ZERO.search(css), "search input must stay borderless/outline-free"
    indicator = search_wrapper_focus_indicator(css)
    assert indicator is not None, "search wrapper must declare a focus-dependent indicator"
    assert _POSITIVE_LENGTH.search(indicator)
    assert "none" not in indicator.casefold()
