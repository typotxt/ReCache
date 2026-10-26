"""Subtle fades for tab switches and newly opened windows."""

from PyQt6.QtCore import QEasingCurve, QEvent, QObject, QPropertyAnimation, Qt
from PyQt6.QtGui import QPalette
from PyQt6.QtWidgets import QApplication, QGraphicsOpacityEffect, QLabel, QWidget

FADE_MS = 180

# Settings > Remove UI animations turns every fade (and the highlight easing) off
_animations_enabled = True


def animations_enabled() -> bool:
    return _animations_enabled


def set_animations_enabled(enabled: bool) -> None:
    global _animations_enabled
    _animations_enabled = enabled


def fade_in(widget: QWidget, previous: QWidget | None = None) -> None:
    """Crossfade to a page, e.g. the one a tab switch just showed.

    A snapshot of ``previous`` (the page being left) is laid over the new page
    and fades away, so the old page dissolves straight into the new one. The
    new page keeps painting normally underneath. Without a previous page the
    cover is plain background colour.
    """
    if not _animations_enabled:
        return
    cover = QLabel(widget)
    cover.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    # The cover is opaque: pages do not paint their own background, so a bare
    # snapshot would let parts of the new page pop in before the fade
    cover.setAutoFillBackground(True)
    palette = cover.palette()
    palette.setColor(QPalette.ColorRole.Window, widget.palette().color(QPalette.ColorRole.Window))
    cover.setPalette(palette)
    snapshot = previous.grab() if previous is not None else None
    if snapshot is not None and not snapshot.isNull():
        cover.setPixmap(snapshot)
    cover.setGeometry(widget.rect())
    effect = QGraphicsOpacityEffect(cover)
    cover.setGraphicsEffect(effect)
    cover.show()
    cover.raise_()
    anim = QPropertyAnimation(effect, b'opacity', cover)
    anim.setDuration(FADE_MS)
    anim.setEasingCurve(QEasingCurve.Type.InOutSine)
    anim.setStartValue(1.0)
    anim.setEndValue(0.0)
    anim.finished.connect(cover.deleteLater)
    anim.start()


class _WindowFade(QObject):
    """Fades every normal window and dialog in when it is shown."""

    def eventFilter(self, obj, event):  # noqa: N802
        if (
            _animations_enabled
            and event.type() == QEvent.Type.Show
            and isinstance(obj, QWidget)
            and obj.isWindow()
            and obj.windowType() in (Qt.WindowType.Window, Qt.WindowType.Dialog)
        ):
            obj.setWindowOpacity(0.0)
            anim = QPropertyAnimation(obj, b'windowOpacity', obj)
            anim.setDuration(FADE_MS)
            anim.setEasingCurve(QEasingCurve.Type.OutCubic)
            anim.setStartValue(0.0)
            anim.setEndValue(1.0)
            anim.finished.connect(anim.deleteLater)
            anim.start()
        return False


def install_window_fade(app: QApplication) -> None:
    """Fade in every window the app opens (dashboard, Scraped games, dialogs)."""
    app._window_fade = _WindowFade(app)  # Kept alive with the app
    app.installEventFilter(app._window_fade)
