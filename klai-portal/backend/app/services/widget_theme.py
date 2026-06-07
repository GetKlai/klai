"""Widget theming: hex parsing, WCAG-luminance text color, CSS-variable derivation.

Pure string -> dict transforms lifted out of ``app/api/partner.py`` — translate
stored widget-config fields into the CSS custom properties the embed script
applies inside its Shadow DOM. No DB, auth, or request coupling. ``app.api.partner``
re-imports only ``_merge_css_variables`` (the widget-config / public-bot-config
route handlers call it); the hex helpers + regex are internal to this module.
"""

from __future__ import annotations

import re

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert ``#RRGGBB`` or ``#RGB`` to an (r, g, b) tuple of 0-255 ints."""
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def _readable_text_color(primary_hex: str) -> str:
    """Return ``#191918`` for light primaries and ``#ffffff`` for dark
    ones using the WCAG-relative-luminance formula.

    Picked thresholds match the WCAG 2.x AA cutoff (~0.179): primaries
    brighter than that get the dark Klai foreground; darker primaries
    get pure white. Without this the bubble icon, send-arrow, and user
    message text inherit ``--klai-primary-text-color: var(--klai-text-color)``
    (dark) on every brand colour — illegible the moment an admin picks a
    dark hex like #2b32fd or any deep brown.
    """

    def _channel(c: int) -> float:
        s = c / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = _hex_to_rgb(primary_hex)
    lum = 0.2126 * _channel(r) + 0.7152 * _channel(g) + 0.0722 * _channel(b)
    return "#191918" if lum > 0.179 else "#ffffff"


def _merge_css_variables(widget_config: dict) -> dict[str, str]:
    """Translate stored widget-config fields into CSS custom properties
    the embed script (klai-chat.js) applies inside its Shadow DOM.

    The admin "Brand kleur" field is stored as ``primary_color`` (a hex
    string). The widget script only reads ``css_variables`` for per-widget
    overrides — without this translation step the configured brand colour
    silently never reaches the widget. Any keys already in
    ``css_variables`` win over the derived ones so a power-user can still
    override granularly.

    Beside the colour itself we derive ``--klai-primary-text-color`` from
    its luminance so the icon / arrow / user-message text rendered on top
    of the primary surface stays legible on any brand hex the admin picks.

    Validation: ``primary_color`` must match ``#RRGGBB`` or ``#RGB``.
    Anything else (empty, invalid, attempted CSS injection) is dropped
    silently so a malformed admin field can never poison the stylesheet.
    """
    css_vars: dict[str, str] = {}
    if widget_config.get("theme") == "dark":
        css_vars.update(
            {
                "--klai-text-color": "#fffef2",
                "--klai-text-muted": "#fffef299",
                "--klai-background-color": "#191918",
                "--klai-card-color": "#27251f",
                "--klai-border-color": "#3a3831",
            }
        )

    if widget_config.get("widget_position") == "left":
        css_vars["--klai-widget-left"] = "20px"
        css_vars["--klai-widget-right"] = "auto"
    elif widget_config.get("widget_position") == "right":
        css_vars["--klai-widget-left"] = "auto"
        css_vars["--klai-widget-right"] = "20px"

    primary = widget_config.get("primary_color")
    if isinstance(primary, str) and _HEX_COLOR_RE.match(primary):
        css_vars["--klai-primary-color"] = primary
        css_vars["--klai-primary-text-color"] = _readable_text_color(primary)

    overrides = widget_config.get("css_variables") or {}
    if isinstance(overrides, dict):
        for key, value in overrides.items():
            if isinstance(key, str) and isinstance(value, str):
                css_vars[key] = value
    return css_vars
