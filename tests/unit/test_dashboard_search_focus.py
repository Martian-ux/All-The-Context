from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
DASHBOARD_CSS = ROOT / "apps" / "dashboard" / "src" / "styles.css"
PACKAGED_CSS_DIR = (
    ROOT / "packages" / "allthecontext" / "src" / "allthecontext" / "web" / "assets"
)

_COMMENT = re.compile(r"/\*.*?\*/", re.S)
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_POSITIVE_LENGTH = re.compile(r"[1-9]\d*(?:\.\d+)?(?:px|em|rem)")
_ZERO_LENGTH = re.compile(r"^0(?:\.0+)?(?:px|em|rem)?$", re.I)
_SEARCH_CLASS = re.compile(r"\.search-input(?![\w-])")
_FOCUS_WITHIN = re.compile(r":focus-within(?![\w-])")
_HAS_FOCUS = re.compile(r":has\([^)]*:focus")
_INPUT_FOCUS = re.compile(r"\s+input(?![\w-])(?:[^,{]*?)?:focus(?:-visible)?(?![\w-])")
_OUTLINE_STYLE = re.compile(
    r"(?<![\w-])(solid|dashed|dotted|double|groove|ridge|inset|outset|auto|none|hidden)(?![\w-])",
    re.I,
)
_NAMED_WIDTH = re.compile(r"(?<![\w-])(thin|medium|thick)(?![\w-])", re.I)
_TRANSPARENT = re.compile(r"(?<![\w-])transparent(?![\w-])", re.I)
_ALPHA_ZERO = re.compile(
    r"(?:rgba|hsla)\(\s*[^)/]+[, ]\s*0(?:\.0+)?\s*\)"
    r"|(?:rgba?|hsla?)\([^)]+/\s*0(?:\.0+)?\s*\)",
    re.I,
)
_VISIBLE_OUTLINE_STYLES = frozenset(
    {"solid", "dashed", "dotted", "double", "groove", "ridge", "inset", "outset", "auto"}
)


def _strip_comments(css: str) -> str:
    return _COMMENT.sub("", css)


def _declarations(body: str) -> dict[str, str]:
    decls: dict[str, str] = {}
    for chunk in body.split(";"):
        if ":" not in chunk:
            continue
        name, _, value = chunk.partition(":")
        key = name.strip().casefold()
        if key:
            decls[key] = value.strip()
    return decls


def iter_css_rules(css: str) -> list[tuple[list[str], dict[str, str]]]:
    rules: list[tuple[list[str], dict[str, str]]] = []
    for match in _RULE.finditer(_strip_comments(css)):
        prelude = match.group(1).strip()
        selectors = [re.sub(r"\s+", " ", item).strip() for item in prelude.split(",")]
        selectors = [item for item in selectors if item]
        rules.append((selectors, _declarations(match.group(2))))
    return rules


def _is_search_focus_selector(selector: str) -> bool:
    if not _SEARCH_CLASS.search(selector):
        return False
    return bool(
        _HAS_FOCUS.search(selector)
        or _FOCUS_WITHIN.search(selector)
        or _INPUT_FOCUS.search(selector)
    )


def _is_transparent(value: str) -> bool:
    return _TRANSPARENT.search(value) is not None or _ALPHA_ZERO.search(value) is not None


def _is_positive_width(value: str) -> bool:
    token = value.strip()
    if not token or _ZERO_LENGTH.fullmatch(token):
        return False
    if token.casefold() in {"thin", "medium", "thick"}:
        return True
    return _POSITIVE_LENGTH.search(token) is not None


def _parse_outline_shorthand(value: str) -> tuple[str | None, str | None, str | None]:
    raw = value.strip()
    folded = raw.casefold()
    if folded in {"none", "hidden", "0", "0px", "invert"}:
        return "0", "none", raw
    style_match = _OUTLINE_STYLE.search(raw)
    style = style_match.group(1).casefold() if style_match else None
    width_match = _POSITIVE_LENGTH.search(raw) or _NAMED_WIDTH.search(raw)
    if width_match:
        width = width_match.group(0)
    elif re.search(r"(?<![\w.-])0(?:\.0+)?(?:px|em|rem)?(?![\w-])", folded):
        width = "0"
    else:
        width = None
    return width, style, raw


def _has_visible_outline(decls: dict[str, str]) -> bool:
    width: str | None = None
    style: str | None = None
    color: str | None = None
    if "outline" in decls:
        width, style, color = _parse_outline_shorthand(decls["outline"])
    if "outline-width" in decls:
        width = decls["outline-width"]
    if "outline-style" in decls:
        style = decls["outline-style"].strip().casefold()
    if "outline-color" in decls:
        color = decls["outline-color"]
    if style is None or style not in _VISIBLE_OUTLINE_STYLES:
        return False
    if width is None or not _is_positive_width(width):
        return False
    return color is None or not _is_transparent(color)


def _has_visible_box_shadow(decls: dict[str, str]) -> bool:
    value = decls.get("box-shadow")
    if value is None:
        return False
    folded = value.strip().casefold()
    if not folded or re.search(r"(?<![\w-])none(?![\w-])", folded):
        return False
    if _is_transparent(value):
        return False
    return _POSITIVE_LENGTH.search(value) is not None


def has_visible_focus_dependent_search_indicator(css: str) -> bool:
    for selectors, decls in iter_css_rules(css):
        if not any(_is_search_focus_selector(selector) for selector in selectors):
            continue
        if _has_visible_outline(decls) or _has_visible_box_shadow(decls):
            return True
    return False


def css_files_under_test() -> list[Path]:
    packaged = sorted(PACKAGED_CSS_DIR.glob("*.css"))
    assert DASHBOARD_CSS.is_file(), f"missing dashboard stylesheet: {DASHBOARD_CSS}"
    assert len(packaged) == 1, f"expected exactly one packaged css file, found {packaged}"
    return [DASHBOARD_CSS, packaged[0]]


POSITIVE_SNIPPETS = [
    pytest.param(
        ".search-input:focus-within { outline: 2px solid var(--amber); }",
        id="focus-within-outline-shorthand",
    ),
    pytest.param(
        ".search-input:has(:focus) { outline: 2px solid var(--amber); }",
        id="has-focus-outline",
    ),
    pytest.param(
        ".search-input:has(input:focus-visible) { outline: 2px solid red; }",
        id="has-focus-visible-outline",
    ),
    pytest.param(
        ".search-input input:focus { outline: 2px solid var(--amber); }",
        id="descendant-input-focus",
    ),
    pytest.param(
        ".search-input input:focus-visible { outline: 2px solid var(--amber); }",
        id="descendant-input-focus-visible",
    ),
    pytest.param(
        ".search-input:focus-within { box-shadow: 0 0 0 2px red; }",
        id="focus-within-box-shadow-ring",
    ),
    pytest.param(
        ".search-input:focus-within { outline-width: 2px; outline-style: solid; }",
        id="focus-within-outline-longhands",
    ),
    pytest.param(
        ".search-input { border: 0; }\n"
        ".search-input:focus-within { outline: 2px solid var(--amber); }",
        id="static-rule-plus-focus-within",
    ),
]


NEGATIVE_SNIPPETS = [
    pytest.param(
        ".search-input:focus { outline: 2px solid red; }",
        id="wrapper-focus-rejected",
    ),
    pytest.param(
        ".search-input { outline: 2px solid red; }",
        id="static-wrapper-rejected",
    ),
    pytest.param(
        ".search-input:focus-within { outline: transparent; }",
        id="transparent-outline",
    ),
    pytest.param(
        ".search-input:focus-within { outline: 2px solid transparent; }",
        id="transparent-outline-color",
    ),
    pytest.param(
        ".search-input:focus-within { outline: none; }",
        id="none-outline",
    ),
    pytest.param(
        ".search-input:focus-within { outline: 0; }",
        id="zero-outline",
    ),
    pytest.param(
        ".search-input:focus-within { outline: hidden; }",
        id="hidden-outline",
    ),
    pytest.param(
        ".search-input:focus-within { outline-width: 2px; }",
        id="outline-width-without-style",
    ),
    pytest.param(
        ".search-input:focus-within { outline-width: 2px; outline-style: none; }",
        id="outline-width-with-none-style",
    ),
    pytest.param(
        ".search-input:focus-within { box-shadow: none; }",
        id="none-box-shadow",
    ),
    pytest.param(
        ".search-input:focus-within { box-shadow: 0 0 0 2px transparent; }",
        id="transparent-box-shadow",
    ),
    pytest.param(
        ".other:focus-within { outline: 2px solid red; }",
        id="unrelated-selector",
    ),
]


@pytest.mark.parametrize("css", POSITIVE_SNIPPETS)
def test_accepts_visible_focus_dependent_search_indicator(css: str) -> None:
    assert has_visible_focus_dependent_search_indicator(css)


@pytest.mark.parametrize("css", NEGATIVE_SNIPPETS)
def test_rejects_non_visible_or_non_focus_search_indicator(css: str) -> None:
    assert not has_visible_focus_dependent_search_indicator(css)


def test_does_not_require_input_outline_zero() -> None:
    css = ".search-input:focus-within { outline: 2px solid var(--amber); }"
    assert re.search(r"outline\s*:\s*0\b", css) is None
    assert has_visible_focus_dependent_search_indicator(css)


def test_dashboard_and_packaged_css_have_search_focus_indicator() -> None:
    for path in css_files_under_test():
        css = path.read_text(encoding="utf-8")
        assert has_visible_focus_dependent_search_indicator(css), (
            f"{path} must declare a visible focus-dependent search indicator"
        )
