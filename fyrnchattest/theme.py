"""Design tokens: every colour in Fyrn Chat Test, in one place.

The look is the test bench, not Fyrn's ember: deep
green-charcoal surfaces, signal green for go and pass, red for
fail, amber for warnings. One change here changes the colour
everywhere: the stylesheet and every inline setStyleSheet call
read from this dict, so a hex value appears in exactly one
place in the fyrnchattest package (this file).

No Qt imports here on purpose: the tokens and the contrast
maths are plain data, testable without a QApplication.
"""

from string import Template

# The bench theme. Dark green-charcoal surfaces drawn from the
# icon tile; signal green for selection, highlights, and the
# pass stamp; red for the fail stamp; amber for warnings and
# tight fits.
DARK = {
    "bg": "#131f18",
    "bg_raised": "#1b2a21",
    "bg_deep": "#0d1510",
    "bg_button": "#24382c",
    "bg_button_pressed": "#182619",
    "bg_sunken": "#182619",
    "border": "#3d5547",
    "border_dim": "#555555",
    "border_disabled": "#2a3a2f",
    "text": "#e9f2ec",
    "text_muted": "#9db3a5",
    "text_dim": "#c2d2c7",
    "text_disabled": "#6b7d70",
    "accent": "#7ddb8a",
    "accent_deep": "#2e7d32",
    "badge_text": "#ffffff",
    "selection_bg": "#2e7d32",
    "selection_text": "#ffffff",
    "fit_ok": "#2e7d32",
    # Darkened until white text on it clears 4.5:1 (the lighter
    # amber reads 3.6:1, too dim for badge text).
    "fit_tight": "#8a5c14",
    "fit_big": "#b3261e",
    "fit_unknown": "#666666",
    "pass": "#2e7d32",
    "fail": "#b3261e",
    "untested": "#666666",
}

THEMES = {
    "dark": DARK,
}

# The stylesheet, as a template over the tokens. No fixed point
# sizes anywhere: the application font (and the user's system
# text size) shows through, and hierarchy comes from weight and
# colour, not size.
_STYLESHEET_TEMPLATE = Template("""
QWidget {
    background-color: $bg;
    color: $text;
}
QMainWindow, QDialog {
    background-color: $bg;
}
QLabel {
    color: $text;
}
QPushButton {
    background-color: $bg_button;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 6px 16px;
    color: $text;
}
QPushButton:hover {
    border-color: $accent;
}
QPushButton:pressed {
    background-color: $bg_button_pressed;
}
QPushButton:focus {
    border-color: $accent;
}
QPushButton:disabled {
    color: $text_disabled;
    border-color: $border_disabled;
}
QLineEdit, QTextEdit {
    background-color: $bg_deep;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 5px 8px;
    color: $text;
    selection-background-color: $selection_bg;
    selection-color: $selection_text;
}
QLineEdit:focus, QTextEdit:focus {
    border-color: $accent;
}
QTabWidget::pane {
    border: 1px solid $border;
    background-color: $bg;
}
QTabBar::tab {
    background-color: $bg_sunken;
    border: 1px solid $border;
    border-bottom: none;
    border-top-left-radius: 6px;
    border-top-right-radius: 6px;
    padding: 8px 20px;
    color: $text_dim;
}
QTabBar::tab:selected {
    background-color: $bg_button;
    color: $accent;
    border-bottom: 2px solid $accent;
}
QMenuBar {
    background-color: $bg;
    color: $text;
}
QMenuBar::item {
    padding: 4px 10px;
}
QMenuBar::item:selected {
    background-color: $bg_button;
}
QMenu {
    background-color: $bg_raised;
    border: 1px solid $border;
    color: $text;
}
QMenu::item:selected {
    background-color: $bg_button;
    color: $accent;
}
QProgressBar {
    background-color: $bg_deep;
    border: 1px solid $border;
    border-radius: 6px;
    text-align: center;
    color: $text;
}
QProgressBar::chunk {
    background-color: $accent_deep;
    border-radius: 6px;
}
QScrollBar:vertical {
    background-color: $bg;
    width: 12px;
}
QScrollBar::handle:vertical {
    background-color: $border;
    border-radius: 6px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background-color: $accent;
}
QScrollBar:horizontal {
    background-color: $bg;
    height: 12px;
}
QScrollBar::handle:horizontal {
    background-color: $border;
    border-radius: 6px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background-color: $accent;
}
QListWidget {
    background-color: $bg_deep;
    border: 1px solid $border;
    border-radius: 6px;
}
QListWidget::item:selected {
    background-color: $bg_button;
    color: $accent;
}
QTableWidget {
    background-color: $bg_deep;
    border: 1px solid $border;
    gridline-color: $border;
    selection-background-color: $bg_button;
    selection-color: $accent;
}
QHeaderView::section {
    background-color: $bg_raised;
    color: $text_dim;
    border: 1px solid $border;
    padding: 4px 8px;
}
QScrollArea {
    background-color: $bg;
    border: none;
}
QStatusBar {
    background-color: $bg_deep;
    color: $text_dim;
}
""")


def build_stylesheet(tokens):
    """The app stylesheet for a token dict."""
    return _STYLESHEET_TEMPLATE.substitute(tokens)


def fit_badges(tokens):
    """Fit badge (text, background) per fit status, for a theme."""
    return {
        "fits": ("Fits", tokens["fit_ok"]),
        "tight": ("Tight", tokens["fit_tight"]),
        "too big": ("Too big", tokens["fit_big"]),
        "unknown": ("Unknown", tokens["fit_unknown"]),
    }


def verdict_badges(tokens):
    """Verdict badge (text, background) per smoke verdict."""
    return {
        "pass": ("PASS", tokens["pass"]),
        "fail": ("FAIL", tokens["fail"]),
        "not tested": ("NOT TESTED", tokens["untested"]),
    }


def _linear(channel):
    """An sRGB channel (0-255) as linear light, per WCAG."""
    value = channel / 255.0
    if value <= 0.04045:
        return value / 12.92
    return ((value + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_color):
    """WCAG relative luminance of a #rrggbb colour (0-1)."""
    red = int(hex_color[1:3], 16)
    green = int(hex_color[3:5], 16)
    blue = int(hex_color[5:7], 16)
    return (0.2126 * _linear(red) + 0.7152 * _linear(green)
            + 0.0722 * _linear(blue))


def contrast_ratio(foreground, background):
    """WCAG contrast ratio between two #rrggbb colours (1-21)."""
    light = relative_luminance(foreground)
    dark = relative_luminance(background)
    if light < dark:
        light, dark = dark, light
    return (light + 0.05) / (dark + 0.05)
