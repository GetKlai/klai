"""Direct characterization tests for the widget theming helpers.

Pins ``_hex_to_rgb`` / ``_readable_text_color`` (WCAG relative luminance) and
``_merge_css_variables`` BEFORE they are lifted out of ``app/api/partner.py``
into ``app/services/widget_theme.py``. The WCAG luminance branch, the dark-theme
/ widget-position vars, the hex validation, and the css_variables override-merge
previously had no isolated coverage (test_widget_config only asserts css_variables
on the route response, mostly for empty configs).
"""

from __future__ import annotations

from app.services.widget_theme import (
    _hex_to_rgb,
    _merge_css_variables,
    _readable_text_color,
)

# --- _hex_to_rgb --------------------------------------------------------------


def test_hex_to_rgb_six_digit():
    assert _hex_to_rgb("#191918") == (25, 25, 24)
    assert _hex_to_rgb("#ffffff") == (255, 255, 255)
    assert _hex_to_rgb("#000000") == (0, 0, 0)


def test_hex_to_rgb_three_digit_expands():
    assert _hex_to_rgb("#abc") == (170, 187, 204)


# --- _readable_text_color (WCAG relative luminance) ---------------------------


def test_readable_text_color_light_primary_gets_dark_text():
    assert _readable_text_color("#ffffff") == "#191918"
    assert _readable_text_color("#fcaa2d") == "#191918"  # Klai accent (bright)


def test_readable_text_color_dark_primary_gets_white_text():
    assert _readable_text_color("#000000") == "#ffffff"
    assert _readable_text_color("#2b32fd") == "#ffffff"  # deep blue (dark)


# --- _merge_css_variables -----------------------------------------------------


def test_merge_empty_config_is_empty():
    assert _merge_css_variables({}) == {}


def test_merge_dark_theme_vars():
    assert _merge_css_variables({"theme": "dark"}) == {
        "--klai-text-color": "#fffef2",
        "--klai-text-muted": "#fffef299",
        "--klai-background-color": "#191918",
        "--klai-card-color": "#27251f",
        "--klai-border-color": "#3a3831",
    }


def test_merge_widget_position_left_and_right():
    assert _merge_css_variables({"widget_position": "left"}) == {
        "--klai-widget-left": "20px",
        "--klai-widget-right": "auto",
    }
    assert _merge_css_variables({"widget_position": "right"}) == {
        "--klai-widget-left": "auto",
        "--klai-widget-right": "20px",
    }


def test_merge_valid_primary_color_derives_text_color():
    assert _merge_css_variables({"primary_color": "#fcaa2d"}) == {
        "--klai-primary-color": "#fcaa2d",
        "--klai-primary-text-color": "#191918",
    }


def test_merge_invalid_primary_color_dropped():
    assert _merge_css_variables({"primary_color": "red"}) == {}
    assert _merge_css_variables({"primary_color": "javascript:alert(1)"}) == {}


def test_merge_css_variables_overrides_filtered_and_win():
    out = _merge_css_variables(
        {
            "primary_color": "#fcaa2d",
            "css_variables": {
                "--klai-primary-color": "#000000",  # overrides derived
                "--custom": "5px",
                "--bad-value": 2,  # non-str value dropped
                3: "y",  # non-str key dropped
            },
        }
    )
    assert out["--klai-primary-color"] == "#000000"  # override wins
    assert out["--custom"] == "5px"
    assert "--bad-value" not in out
    assert 3 not in out
