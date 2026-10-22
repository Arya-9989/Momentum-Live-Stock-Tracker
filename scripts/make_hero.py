"""Generate the README hero, docs/hero-light.svg and docs/hero-dark.svg.

    uv run python scripts/make_hero.py

The chart is an illustration (a seeded random walk with no axes or values),
not market data. Colours are the app's own tokens (src/momentum/theme.py).
The committed files were then minified with SVGO 4 (keeping the title/desc ids
that aria-labelledby points to, and role="img").
"""

from __future__ import annotations

import random
from pathlib import Path
from xml.sax.saxutils import escape

DOCS = Path(__file__).resolve().parents[1] / "docs"
WIDTH, HEIGHT = 1280, 560
FONT = "Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif"
RANGES = ("1D", "5D", "1M", "3M", "6M", "YTD", "1Y", "2Y", "5Y", "Max")
SELECTED = "6M"

THEMES = {
    "light": {
        "card": "#f5f5f7",
        "ink": "#1d1d1f",
        "secondary": "#6e6e73",
        "line": "#1f7a35",
        "track": "#e8e8ed",
        "thumb": "#ffffff",
        "grid": "#e0e0e5",
        "logo_mark": "#ffffff",
    },
    "dark": {
        "card": "#000000",
        "ink": "#f5f5f7",
        "secondary": "#a1a1a6",
        "line": "#30d158",
        "track": "#1c1c1e",
        "thumb": "#636366",
        "grid": "#2c2c2e",
        "logo_mark": "#000000",
    },
}


def walk(points: int, seed: int = 7) -> list[float]:
    """A gently rising, seeded random walk normalised to 0..1."""
    rng = random.Random(seed)
    values, level = [], 0.0
    for i in range(points):
        level += rng.gauss(0.022, 0.11) + 0.12 * (1 if i % 37 < 18 else -1) / 18
        values.append(level)
    low, high = min(values), max(values)
    return [(v - low) / (high - low) for v in values]


def chart_paths(x0: float, y0: float, w: float, h: float) -> tuple[str, str]:
    values = walk(140)
    step = w / (len(values) - 1)
    coords = [(x0 + i * step, y0 + h - v * h) for i, v in enumerate(values)]
    line = "M" + " L".join(f"{x:.1f},{y:.1f}" for x, y in coords)
    area = f"{line} L{x0 + w:.1f},{y0 + h + 40:.1f} L{x0:.1f},{y0 + h + 40:.1f} Z"
    return line, area


def segmented(x: float, y: float, colors: dict[str, str]) -> str:
    widths = [44 if len(r) < 3 else 56 for r in RANGES]
    total = sum(widths) + 8
    track = colors["track"]
    parts = [f'<rect x="{x}" y="{y}" width="{total}" height="40" rx="10" fill="{track}"/>']
    cx = x + 4
    for label, w in zip(RANGES, widths, strict=True):
        if label == SELECTED:
            parts.append(
                f'<rect x="{cx}" y="{y + 4}" width="{w}" height="32" rx="8" '
                f'fill="{colors["thumb"]}" filter="url(#lift)"/>'
            )
        weight = 600 if label == SELECTED else 500
        parts.append(
            f'<text x="{cx + w / 2}" y="{y + 25}" text-anchor="middle" font-size="14" '
            f'font-weight="{weight}" fill="{colors["ink"]}">{label}</text>'
        )
        cx += w
    return "".join(parts)


def hero(theme: str) -> str:
    c = THEMES[theme]
    line, area = chart_paths(560, 250, 640, 210)
    grid = "".join(
        f'<line x1="560" x2="1200" y1="{y}" y2="{y}" stroke="{c["grid"]}" stroke-width="1"/>'
        for y in (270, 340, 410)
    )
    title = "Momentum: precision markets dashboard"
    desc = (
        "Momentum wordmark and headline beside an illustrative area-line chart with a "
        "segmented range control. The chart is decorative and shows no market data."
    )
    return f"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {WIDTH} {HEIGHT}" \
width="{WIDTH}" height="{HEIGHT}" role="img" aria-labelledby="t d">
<title id="t">{escape(title)}</title>
<desc id="d">{escape(desc)}</desc>
<defs>
<linearGradient id="fade" x1="0" y1="0" x2="0" y2="1">
<stop offset="0" stop-color="{c["line"]}" stop-opacity="0.26"/>
<stop offset="1" stop-color="{c["line"]}" stop-opacity="0"/>
</linearGradient>
<filter id="lift" x="-20%" y="-20%" width="140%" height="160%">
<feDropShadow dx="0" dy="1" stdDeviation="1.2" flood-color="#000" flood-opacity="0.18"/>
</filter>
<clipPath id="card"><rect width="{WIDTH}" height="{HEIGHT}" rx="28"/></clipPath>
</defs>
<g clip-path="url(#card)" font-family="{FONT}">
<rect width="{WIDTH}" height="{HEIGHT}" fill="{c["card"]}"/>
<rect x="80" y="80" width="40" height="40" rx="10" fill="{c["ink"]}"/>
<path d="M88.5 106 96 98.5l5 5L111.5 93" fill="none" stroke="{c["logo_mark"]}" \
stroke-width="3.4" stroke-linecap="round" stroke-linejoin="round"/>
<text x="134" y="110" font-size="28" font-weight="600" letter-spacing="-0.3" \
fill="{c["ink"]}">Momentum</text>
<text x="80" y="226" font-size="56" font-weight="600" letter-spacing="-1.4" \
fill="{c["ink"]}">Every market.</text>
<text x="80" y="290" font-size="56" font-weight="600" letter-spacing="-1.4" \
fill="{c["ink"]}">One calm view.</text>
<text x="80" y="346" font-size="21" fill="{c["secondary"]}">Global stocks, indices, FX \
and crypto,</text>
<text x="80" y="374" font-size="21" fill="{c["secondary"]}">charted with tested \
indicators.</text>
<text x="80" y="456" font-size="15" fill="{c["secondary"]}">Streamlit · Plotly · \
yfinance · typed and tested</text>
{segmented(560, 160, c)}
{grid}
<path d="{area}" fill="url(#fade)"/>
<path d="{line}" fill="none" stroke="{c["line"]}" stroke-width="3" \
stroke-linejoin="round" stroke-linecap="round"/>
</g>
</svg>
"""


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    for theme in THEMES:
        path = DOCS / f"hero-{theme}.svg"
        path.write_text(hero(theme), encoding="utf-8", newline="\n")
        print(f"saved {path}")


if __name__ == "__main__":
    main()
