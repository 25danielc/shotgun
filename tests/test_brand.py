"""Brand: logo direction A ("Lit Seat"). The dashboard inlines the small mark (it stays one
self-contained file); this keeps the inline copy and the favicon identical to
static/brand/shotgun-mark-small.svg."""

from pathlib import Path
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "static" / "dashboard.html").read_text()


def one_line(name: str) -> str:
    return " ".join((ROOT / "static" / "brand" / name).read_text().split())


def test_both_marks_exist_with_the_lit_seat():
    for name in ("shotgun-mark.svg", "shotgun-mark-small.svg"):
        svg = one_line(name)
        assert svg.startswith('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 120 120"')
        assert 'fill="#39ff9c"' in svg  # the lit passenger seat, in the dashboard accent
    assert "M26 30 H94" in one_line("shotgun-mark.svg")  # windshield only on the detailed one
    assert "M26 30 H94" not in one_line("shotgun-mark-small.svg")


def test_dashboard_inlines_the_small_mark_at_18_px():
    inline = one_line("shotgun-mark-small.svg").replace(
        "<svg ", '<svg class="mark" aria-hidden="true" ', 1
    )
    assert f"const MARK_SMALL = `{inline}`;" in PAGE
    assert ".sys .mark { width: 18px; height: 18px;" in PAGE


def test_favicon_is_the_small_mark():
    start = PAGE.index('<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,') + len(
        '<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,'
    )
    href = PAGE[start : PAGE.index('">', start)]
    assert unquote(href) == one_line("shotgun-mark-small.svg")
