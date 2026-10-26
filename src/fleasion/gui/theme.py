"""Theme management for PyQt6."""

import re
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QApplication


_DARK = {
    'bg': '#17171a',
    'card': '#202024',
    'raised': '#2b2b31',
    'raised2': '#35353d',
    'input': '#131316',
    'line': '#34343b',
    'text': '#f2f2f5',
    'muted': '#a0a0ab',
    'faint': '#6d6d78',
    'accent': '#7b5cff',
    'accent_soft': 'rgba(123, 92, 255, 0.22)',
    'accent_text': '#d2c6ff',
    'good': '#40c773',
    'good_soft': 'rgba(64, 199, 115, 0.16)',
    'bad': '#ff6b6b',
}
_LIGHT = {
    'bg': '#f4f4f7',
    'card': '#ffffff',
    'raised': '#ececf1',
    'raised2': '#e0e0e8',
    'input': '#ffffff',
    'line': '#dcdce3',
    'text': '#18181b',
    'muted': '#62626d',
    'faint': '#9a9aa6',
    'accent': '#7b5cff',
    'accent_soft': 'rgba(123, 92, 255, 0.16)',
    'accent_text': '#5a3fd6',
    'good': '#1f9d55',
    'good_soft': 'rgba(31, 157, 85, 0.14)',
    'bad': '#d64545',
}

_QSS = """
* { font-family: "Segoe UI Variable Text", "Segoe UI", "Inter", sans-serif; font-size: 13px; }
QToolTip { background: @raised; color: @text; border: 1px solid @line; padding: 5px 8px; border-radius: 6px; }

QTabWidget::pane { border: none; border-top: 1px solid @line; background: @bg; top: 0px; }
QTabBar { qproperty-drawBase: 0; background: transparent; }
QTabBar::tab { background: transparent; color: @muted; padding: 0 16px; margin: 13px 3px; min-height: 30px;
    border: none; border-radius: 15px; font-weight: 600; }
QTabBar::tab:hover { color: @text; background: @raised; }
QTabBar::tab:selected { color: @accent_text; background: @accent_soft; }
QLabel#brandMark { background: transparent; }
QLabel#brandName { font-size: 17px; font-weight: 700; color: @text; padding: 0 0 0 9px; }

QGroupBox { background: @card; border: 1px solid @line; border-radius: 8px; margin-top: 0; padding: 40px 16px 16px 16px; }
QGroupBox::title { subcontrol-origin: padding; subcontrol-position: top left; left: 16px; top: 14px; padding: 0;
    color: @text; font-weight: 700; font-size: 15px; }

QPushButton { background: @raised; color: @text; border: 1px solid @line; border-radius: 6px; padding: 7px 14px; font-weight: 600; }
QPushButton:hover { background: @raised2; }
QPushButton:pressed { background: @line; }
QPushButton:disabled { color: @faint; background: @raised; }
QGroupBox#flatGroup { background: transparent; border: none; margin: 0; padding: 0; }
QPushButton[flat="true"] { background: transparent; border: none; padding: 0; color: @muted; }
QPushButton[flat="true"]:hover { color: @text; }
QPushButton[compact="true"] { padding: 0 10px; }
QPushButton#linkButton { background: transparent; border: none; color: @muted; padding: 6px 18px; }
QPushButton#linkButton:hover { color: @text; }
QPushButton#applyButton { background: @accent; color: @on_accent; border: none; padding: 9px 22px; }
QPushButton#primaryButton { background: @accent; color: @on_accent; border: none; padding: 9px 18px; }
QPushButton#primaryButton:hover { background: @accent_hover; }
QPushButton#primaryButton:disabled { background: @raised2; color: @faint; }
QPushButton#applyButton:hover { background: @accent_hover; }
QPushButton#applyButton:disabled { background: @raised2; color: @faint; }
QPushButton#ghostButton { background: transparent; border: none; color: @muted; }
QPushButton#ghostButton:hover { color: @text; background: @raised; }
QPushButton#statusChip { border: none; border-radius: 4px; padding: 6px 12px; }

QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox { background: @input; color: @text;
    border: 1px solid @line; border-radius: 9px; padding: 7px 10px; selection-background-color: @accent; selection-color: @on_accent; }
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus, QComboBox:focus { border: 1px solid @accent; }
QComboBox::drop-down { border: none; width: 22px; }
QComboBox QAbstractItemView { background: @card; border: 1px solid @line; selection-background-color: @accent_soft;
    selection-color: @text; outline: none; }
QCheckBox, QRadioButton { spacing: 8px; color: @text; }
QCheckBox::indicator { width: 34px; height: 18px; border-radius: 9px; border: none; background: @raised2; image: url(@sw_off); }
QCheckBox::indicator:checked { background: @accent; image: url(@sw_on); }
QCheckBox::indicator:disabled { background: @raised; }
QCheckBox#segment { background: @input; color: @muted; border: 1px solid @line; border-radius: 6px; padding: 7px 20px; spacing: 0; font-weight: 600; }
QCheckBox#segment:hover { color: @text; }
QCheckBox#segment:checked { background: @accent_soft; color: @text; border: 1px solid @accent; }
QCheckBox#segment::indicator { width: 0px; height: 0px; image: none; border: none; }
QRadioButton::indicator { width: 16px; height: 16px; border-radius: 9px; border: 1px solid @faint; background: @input; }
QRadioButton::indicator:checked { background: @accent; border: 4px solid @input; }

QTreeView, QTreeWidget, QTableView, QTableWidget, QListView, QListWidget { background: @card; alternate-background-color: @card;
    color: @text; border: 1px solid @line; border-radius: 8px; padding: 3px; outline: none; gridline-color: @line; }
QTreeView::item, QTableView::item, QListView::item { padding: 6px 4px; border: none; }
QTreeView::item:selected, QTableView::item:selected, QListView::item:selected { background: @accent_soft; color: @text; }
QTreeView::item:hover, QTableView::item:hover, QListView::item:hover { background: @raised; }
QHeaderView { background: transparent; }
QHeaderView::section { background: transparent; color: @faint; border: none; border-bottom: 1px solid @line; padding: 8px;
    font-size: 11px; font-weight: 700; }
QTableCornerButton::section { background: transparent; border: none; }

QScrollArea { border: none; background: transparent; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: @raised2; border-radius: 4px; min-height: 30px; }
QScrollBar::handle:vertical:hover { background: @faint; }
QScrollBar:horizontal { background: transparent; height: 10px; margin: 2px; }
QScrollBar::handle:horizontal { background: @raised2; border-radius: 4px; min-width: 30px; }
QScrollBar::add-line, QScrollBar::sub-line, QScrollBar::add-page, QScrollBar::sub-page { background: none; border: none; height: 0; width: 0; }

QMenu { background: @card; border: 1px solid @line; border-radius: 6px; padding: 6px; }
QMenu::item { padding: 7px 18px; border-radius: 6px; color: @text; }
QMenu::item:selected { background: @accent_soft; }
QMenu::separator { height: 1px; background: @line; margin: 5px 8px; }
QProgressBar { background: @raised; border: none; border-radius: 4px; height: 8px; text-align: center; }
QProgressBar::chunk { background: @accent; border-radius: 4px; }

QWidget#commandBar { background: @card; border-top: 1px solid @line; }
QLabel#footPath { color: @faint; font-size: 11px; }
"""


@dataclass(frozen=True)
class PanelThemeColors:
    """Resolved colors for custom-painted tab panels."""

    section_background: QColor
    section_border: QColor
    container_background_css: str


class ThemeManager:
    """Manages application theme."""

    _system_style_name: ClassVar[str | None] = None
    _current_theme: ClassVar[str] = 'System'
    # Visual theme after resolving the System option.  Windows System maps to
    # one of Fleasion's explicit Light/Dark palettes; Linux and macOS keep
    # their Qt-provided system theme behavior.
    _effective_theme: ClassVar[str] = 'System'
    # User-chosen accent colour (hex); see set_accent()
    _accent: ClassVar[str] = '#7b5cff'

    @staticmethod
    def apply_theme(theme: str) -> None:
        """Apply a theme to the application."""
        app = QApplication.instance()
        if not isinstance(app, QApplication):
            return

        ThemeManager._remember_system_theme(app)
        ThemeManager._current_theme = theme
        ThemeManager._effective_theme = theme

        if theme == 'System':
            ThemeManager._apply_system_theme(app)
        else:
            ThemeManager._apply_forced_theme(app, theme)

        app.setStyleSheet(ThemeManager.stylesheet(app))
        ThemeManager._refresh_widgets(app)

    @staticmethod
    def _remember_system_theme(app: QApplication) -> None:
        if ThemeManager._system_style_name is not None:
            return

        style = app.style()
        ThemeManager._system_style_name = style.objectName() if style else None

    @staticmethod
    def _apply_system_theme(app: QApplication) -> None:
        ThemeManager._set_color_scheme(app, 'Unknown')

        if sys.platform.startswith('linux'):
            # Linux deliberately keeps the desktop's real Qt style/palette.
            # Users commonly choose a global Qt theme (Breeze, Kvantum, etc.)
            # and System should continue to mean exactly that.
            ThemeManager._effective_theme = 'System'
            current_style = app.style()
            current_style_name = current_style.objectName() if current_style else None
            if (
                ThemeManager._system_style_name
                and current_style_name != ThemeManager._system_style_name
            ):
                app.setStyle(ThemeManager._system_style_name)
            app.setPalette(QPalette())
            return

        if sys.platform == 'win32':
            # Qt's Windows system dark palette is not the palette Fleasion uses
            # for its Dark option (and can be noticeably darker).  Keep the
            # saved/selected option as System, but resolve Windows' actual app
            # appearance and use the exact same Fleasion palette as Light/Dark.
            effective = ThemeManager._windows_system_theme(app)
            ThemeManager._effective_theme = effective
            app.setStyle('Fusion')
            app.setPalette(
                ThemeManager._dark_palette()
                if effective == 'Dark'
                else ThemeManager._light_palette()
            )
            return

        # macOS already reports its current appearance through Qt's platform
        # theme.  Unlike Windows, keep System dynamic/native instead of mapping
        # it to Fleasion's forced palettes.
        ThemeManager._effective_theme = 'System'
        app.setStyle('Fusion')
        app.setPalette(app.style().standardPalette())

    @staticmethod
    def _windows_system_theme(app: QApplication) -> str:
        """Return the Windows app appearance as ``Light`` or ``Dark``.

        Read the Windows personalization setting directly instead of inferring
        it from Qt's palette -- the palette is the part we intentionally do not
        trust for System mode on Windows.  Qt/palette checks are only fallbacks
        for unusual environments where the registry value is unavailable.
        """
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r'Software\Microsoft\Windows\CurrentVersion\Themes\Personalize',
                0,
                winreg.KEY_QUERY_VALUE,
            ) as key:
                use_light, _value_type = winreg.QueryValueEx(key, 'AppsUseLightTheme')
            return 'Light' if int(use_light) else 'Dark'
        except (ImportError, OSError, TypeError, ValueError):
            pass

        style_hints = app.styleHints()
        color_scheme_getter = getattr(style_hints, 'colorScheme', None)
        if callable(color_scheme_getter):
            color_scheme = color_scheme_getter()
            if color_scheme == ThemeManager._qt_color_scheme('Dark'):
                return 'Dark'
            if color_scheme == ThemeManager._qt_color_scheme('Light'):
                return 'Light'

        return 'Dark' if app.palette().window().color().lightness() < 128 else 'Light'

    @staticmethod
    def _apply_forced_theme(app: QApplication, theme: str) -> None:
        app.setStyle('Fusion')
        if theme == 'Dark':
            ThemeManager._set_color_scheme(app, 'Dark')
            app.setPalette(ThemeManager._dark_palette())
        elif theme == 'Light':
            ThemeManager._set_color_scheme(app, 'Light')
            app.setPalette(ThemeManager._light_palette())
        else:
            app.setPalette(app.style().standardPalette())

    @staticmethod
    def _dark_palette() -> QPalette:
        palette = QPalette()
        text = QColor('#f2f2f5')
        palette.setColor(QPalette.ColorRole.Window, QColor('#17171a'))
        palette.setColor(QPalette.ColorRole.WindowText, text)
        palette.setColor(QPalette.ColorRole.Base, QColor('#131316'))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor('#202024'))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor('#ffffdc'))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor('#000000'))
        palette.setColor(QPalette.ColorRole.Text, text)
        palette.setColor(QPalette.ColorRole.Button, QColor('#2b2b31'))
        palette.setColor(QPalette.ColorRole.ButtonText, text)
        palette.setColor(QPalette.ColorRole.BrightText, QColor('#4b4b4b'))
        palette.setColor(QPalette.ColorRole.Link, QColor(ThemeManager._accent))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(ThemeManager._accent))
        palette.setColor(QPalette.ColorRole.HighlightedText, text)
        palette.setColor(QPalette.ColorRole.PlaceholderText, ThemeManager._placeholder_color(text))
        palette.setColor(QPalette.ColorRole.Light, QColor('#4b4b4b'))
        palette.setColor(QPalette.ColorRole.Midlight, QColor('#2a2a2a'))
        palette.setColor(QPalette.ColorRole.Dark, QColor('#212121'))
        palette.setColor(QPalette.ColorRole.Mid, QColor('#262626'))
        palette.setColor(QPalette.ColorRole.Shadow, QColor('#191919'))
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor('#828282'),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText,
            QColor('#828282'),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.WindowText,
            QColor('#828282'),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.HighlightedText,
            QColor('#f0f0f0'),
        )
        return palette

    @staticmethod
    def _light_palette() -> QPalette:
        palette = QPalette()
        text = QColor('#18181b')
        palette.setColor(QPalette.ColorRole.Window, QColor('#f4f4f7'))
        palette.setColor(QPalette.ColorRole.WindowText, text)
        palette.setColor(QPalette.ColorRole.Base, QColor('#f4f4f7'))
        palette.setColor(QPalette.ColorRole.AlternateBase, QColor('#ffffff'))
        palette.setColor(QPalette.ColorRole.ToolTipBase, QColor('#ffffdc'))
        palette.setColor(QPalette.ColorRole.ToolTipText, QColor('#000000'))
        palette.setColor(QPalette.ColorRole.Text, text)
        palette.setColor(QPalette.ColorRole.Button, QColor('#ececf1'))
        palette.setColor(QPalette.ColorRole.ButtonText, text)
        palette.setColor(QPalette.ColorRole.BrightText, QColor('#ffffff'))
        palette.setColor(QPalette.ColorRole.Link, QColor('#0000ff'))
        palette.setColor(QPalette.ColorRole.Highlight, QColor(ThemeManager._accent))
        palette.setColor(QPalette.ColorRole.HighlightedText, QColor('#ffffff'))
        palette.setColor(QPalette.ColorRole.PlaceholderText, ThemeManager._placeholder_color(text))
        palette.setColor(QPalette.ColorRole.Light, QColor('#ffffff'))
        palette.setColor(QPalette.ColorRole.Midlight, QColor('#cacaca'))
        palette.setColor(QPalette.ColorRole.Dark, QColor('#9f9f9f'))
        palette.setColor(QPalette.ColorRole.Mid, QColor('#b8b8b8'))
        palette.setColor(QPalette.ColorRole.Shadow, QColor('#767676'))
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.Text,
            QColor('#bebebe'),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.ButtonText,
            QColor('#bebebe'),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.WindowText,
            QColor('#bebebe'),
        )
        palette.setColor(
            QPalette.ColorGroup.Disabled,
            QPalette.ColorRole.HighlightedText,
            QColor('#ffffff'),
        )
        return palette

    @staticmethod
    def set_accent(color: str) -> None:
        """Change the accent colour and repaint the whole application."""
        parsed = QColor(color)
        if not parsed.isValid():
            return
        ThemeManager._accent = parsed.name()
        if isinstance(QApplication.instance(), QApplication):
            ThemeManager.apply_theme(ThemeManager._current_theme)

    @staticmethod
    def _switch_images(knob: str, off_color: str) -> tuple[str, str]:
        """Write the toggle-switch knob images the stylesheet points at."""
        folder = Path(tempfile.gettempdir()) / 'recache_ui'
        folder.mkdir(exist_ok=True)
        files = []
        for name, cx, color in (('off', 9, off_color), ('on', 25, knob)):
            path = folder / f'switch_{name}_{color.lstrip("#")}.svg'
            path.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="34" height="18" viewBox="0 0 34 18">'
                f'<circle cx="{cx}" cy="9" r="6" fill="{color}"/></svg>',
                encoding='utf-8',
            )
            files.append(path.as_posix())
        return files[0], files[1]

    @staticmethod
    def stylesheet(app: QApplication) -> str:
        """Return the Recache application stylesheet for the active light or dark look."""
        dark = ThemeManager._effective_theme == 'Dark' or (
            ThemeManager._effective_theme == 'System'
            and app.palette().window().color().lightness() < 128
        )
        tokens = dict(_DARK if dark else _LIGHT)
        accent = QColor(ThemeManager._accent)
        tokens['accent'] = accent.name()
        tokens['accent_soft'] = (
            f'rgba({accent.red()}, {accent.green()}, {accent.blue()}, {0.22 if dark else 0.16})'
        )
        tokens['accent_text'] = accent.lighter(165).name() if dark else accent.darker(130).name()
        tokens['accent_hover'] = accent.lighter(115).name()
        luma = 0.299 * accent.red() + 0.587 * accent.green() + 0.114 * accent.blue()
        tokens['on_accent'] = '#111111' if luma > 160 else '#ffffff'
        tokens['sw_off'], tokens['sw_on'] = ThemeManager._switch_images(
            tokens['on_accent'], tokens['faint']
        )
        css = _QSS
        # Longest names first so @accent does not eat @accent_soft
        for name, value in sorted(tokens.items(), key=lambda kv: -len(kv[0])):
            css = css.replace(f'@{name}', value)
        return css

    @staticmethod
    def _placeholder_color(text_color: QColor) -> QColor:
        color = QColor(text_color)
        color.setAlpha(128)
        return color

    @staticmethod
    def _set_color_scheme(app: QApplication, color_scheme_name: str) -> None:
        color_scheme = ThemeManager._qt_color_scheme(color_scheme_name)
        if color_scheme is None:
            return

        style_hints = app.styleHints()
        set_color_scheme = getattr(style_hints, 'setColorScheme', None)
        if callable(set_color_scheme):
            set_color_scheme(color_scheme)

    @staticmethod
    def _qt_color_scheme(color_scheme_name: str):
        color_scheme_type = getattr(Qt, 'ColorScheme', None)
        return getattr(color_scheme_type, color_scheme_name, None)

    @staticmethod
    def _refresh_widgets(app: QApplication) -> None:
        for widget in app.allWidgets():
            style = widget.style()
            if style:
                style.unpolish(widget)
                style.polish(widget)
            widget.update()

    @staticmethod
    def panel_colors(palette: QPalette | None = None) -> PanelThemeColors:
        """Return colors for custom panels without losing forced-theme styling.

        Forced Light/Dark keep the Fleasion palette.  Windows System resolves
        to one of those same palettes, while Linux/macOS System continue using
        the active Qt palette.
        """
        app = QApplication.instance()
        if palette is None:
            if isinstance(app, QApplication):
                palette = app.palette()
            else:
                palette = QPalette()

        panel_theme = ThemeManager._current_theme
        if panel_theme == 'System':
            panel_theme = ThemeManager._effective_theme

        if panel_theme == 'System':
            return PanelThemeColors(
                section_background=palette.alternateBase().color(),
                section_border=palette.mid().color(),
                container_background_css='background-color: palette(alternate-base);',
            )

        is_dark = panel_theme == 'Dark'
        return PanelThemeColors(
            section_background=QColor('#202024') if is_dark else QColor('#ffffff'),
            section_border=QColor('#34343b') if is_dark else QColor('#dcdce3'),
            container_background_css=(
                'background-color: #17171a;'
                if is_dark
                else 'background-color: #f4f4f7;'
            ),
        )
