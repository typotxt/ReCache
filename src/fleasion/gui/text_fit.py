"""Keep text-sized widgets fitted to the current UI font."""

from __future__ import annotations

import weakref

from PyQt6.QtWidgets import QWidget

_WIDGETS: list[tuple[weakref.ref[QWidget], int, bool]] = []
_UNBOUNDED = 16777215


def _fit(widget: QWidget, minimum_width: int, fixed: bool) -> None:
    widget.setMinimumWidth(0)
    widget.setMaximumWidth(_UNBOUNDED)
    width = max(minimum_width, widget.sizeHint().width())
    if fixed:
        widget.setFixedWidth(width)
    else:
        widget.setMinimumWidth(width)


def ensure_text_width(widget: QWidget, minimum_width: int = 0, fixed: bool = False) -> None:
    """Keep translated widget text from being clipped by legacy fixed widths."""
    _fit(widget, minimum_width, fixed)
    _WIDGETS.append((weakref.ref(widget), minimum_width, fixed))


def refit_text_widths() -> None:
    """Re-measure every registered widget so a smaller font can shrink them again."""
    alive = []
    for ref, minimum_width, fixed in _WIDGETS:
        widget = ref()
        if widget is None:
            continue
        try:
            _fit(widget, minimum_width, fixed)
        except RuntimeError:
            continue
        alive.append((ref, minimum_width, fixed))
    _WIDGETS[:] = alive
