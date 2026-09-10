"""Small supported appearance overrides; no selectors or executable CSS values."""

import re

COLOR_VARIABLES = frozenset(f"--klai-{name}" for name in (
    "primary-color", "primary-text-color", "background-color", "card-color",
    "text-color", "text-muted", "border-color", "link-color",
    "header-background", "header-text-color", "header-control-background",
))
SIZE_RANGES = {
    "--klai-message-font-size": (12, 24),
    "--klai-message-gap": (0, 40),
    "--klai-content-padding": (0, 40),
    "--klai-border-radius": (0, 32),
    "--klai-window-width": (300, 640),
    "--klai-window-height": (320, 900),
}


def validate_widget_style_overrides(values: dict[str, str]) -> dict[str, str]:
    for key, value in values.items():
        if key in COLOR_VARIABLES:
            if value == "transparent" or re.fullmatch(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})", value):
                continue
        elif key in SIZE_RANGES:
            match = re.fullmatch(r"(\d+(?:\.\d+)?)px", value)
            lower, upper = SIZE_RANGES[key]
            if match and lower <= float(match[1]) <= upper:
                continue
        elif key == "--klai-message-line-height":
            if re.fullmatch(r"\d+(?:\.\d+)?", value) and 1.2 <= float(value) <= 2.4:
                continue
        raise ValueError(f"Unsupported widget style or value: {key}")
    return values
