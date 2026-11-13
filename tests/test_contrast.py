"""WCAG AA contrast for the colour tokens in static/styles.css (light and dark)."""
import re
from pathlib import Path

CSS = (Path(__file__).resolve().parents[1] / "static" / "styles.css").read_text()


def tokens(block: str) -> dict[str, str]:
    return dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", block))


def lum(hex_: str) -> float:
    r, g, b = (int(hex_[i:i + 2], 16) / 255 for i in (1, 3, 5))
    f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def ratio(a: str, b: str) -> float:
    la, lb = sorted((lum(a), lum(b)), reverse=True)
    return (la + 0.05) / (lb + 0.05)


LIGHT = tokens(re.search(r":root \{(.*?)\n\}", CSS, re.S).group(1))
DARK = tokens(re.search(r"prefers-color-scheme: dark\) \{\s*:root \{(.*?)\}", CSS, re.S).group(1))
# (foreground, background, minimum). Text needs 4.5:1; teal is only used for graphics (voice line, rule, icons): 3:1.
PAIRS = [("ink", "mist", 4.5), ("slate", "mist", 4.5), ("slate", "surface", 4.5), ("on-teal", "teal", 4.5), ("on-coral", "coral", 4.5),
         ("mist", "ink", 4.5), ("coral", "mist", 4.5), ("teal", "mist", 3.0)]


def test_text_and_button_contrast_meets_aa():
    for name, theme in (("light", LIGHT), ("dark", DARK)):
        merged = {**LIGHT, **theme}
        for fg, bg, minimum in PAIRS:
            r = ratio(merged[fg], merged[bg])
            assert r >= minimum, f"{name}: {fg} on {bg} is {r:.2f}:1, needs {minimum}"
