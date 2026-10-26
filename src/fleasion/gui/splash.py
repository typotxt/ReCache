"""Startup splash: the ReCache logo fades in while the app loads, then fades out."""

import sys
import time
from pathlib import Path

from PyQt6.QtCore import QEasingCurve, QPropertyAnimation, Qt, QTimer
from PyQt6.QtGui import QColor, QGuiApplication, QPixmap
from PyQt6.QtWidgets import QApplication, QGraphicsDropShadowEffect, QLabel, QVBoxLayout, QWidget

from . import transitions

LOGO_SIZE = 260
FADE_IN_MS = 550
FADE_OUT_MS = 400
MIN_VISIBLE_S = 1.2


def _logo_path() -> Path:
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).parent.parent))
    return base / 'recache_logo_transparent.png'


class Splash(QWidget):
    """A frameless, see-through window holding only the logo."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.SplashScreen
            | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        logo = QLabel()
        pixmap = QPixmap(str(_logo_path()))
        if not pixmap.isNull():
            ratio = self.devicePixelRatioF()
            pixmap = pixmap.scaled(
                round(LOGO_SIZE * ratio),
                round(LOGO_SIZE * ratio),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            pixmap.setDevicePixelRatio(ratio)
            logo.setPixmap(pixmap)
        # A soft white glow keeps the black logo readable on dark desktops
        glow = QGraphicsDropShadowEffect(logo)
        glow.setBlurRadius(48)
        glow.setOffset(0, 0)
        glow.setColor(QColor(255, 255, 255, 150))
        logo.setGraphicsEffect(glow)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.addWidget(logo)
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            center = screen.availableGeometry().center()
            self.move(center.x() - self.width() // 2, center.y() - self.height() // 2)
        self._shown_at = 0.0
        self._anim = QPropertyAnimation(self, b'windowOpacity', self)
        self._anim.setEasingCurve(QEasingCurve.Type.InOutSine)

    def fade_in(self, app: QApplication) -> None:
        """Show the logo and fade it in, keeping the UI responsive while it does."""
        animate = transitions.animations_enabled()
        self.setWindowOpacity(0.0 if animate else 1.0)
        self.show()
        self._shown_at = time.monotonic()
        if not animate:
            app.processEvents()
            return
        self._anim.setDuration(FADE_IN_MS)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.start()
        # Startup continues synchronously after this, so let the fade-in play first
        end = self._shown_at + FADE_IN_MS / 1000
        while time.monotonic() < end:
            app.processEvents()
            time.sleep(0.005)

    def finish(self) -> None:
        """Fade out and close, once the logo has been up long enough to register."""
        wait_ms = max(0, round((MIN_VISIBLE_S - (time.monotonic() - self._shown_at)) * 1000))
        QTimer.singleShot(wait_ms, self._fade_out)

    def _fade_out(self) -> None:
        if not transitions.animations_enabled():
            self.close()
            return
        self._anim.stop()
        self._anim.setDuration(FADE_OUT_MS)
        self._anim.setStartValue(self.windowOpacity())
        self._anim.setEndValue(0.0)
        self._anim.finished.connect(self.close)
        self._anim.start()
