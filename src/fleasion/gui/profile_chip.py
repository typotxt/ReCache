"""The signed-in Roblox account (avatar and name) in the dashboard's top-left corner."""

from PyQt6.QtCore import QEvent, QRectF, QSize, Qt, QTimer, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QPushButton

from ..localization import tr, verbatim
from ..utils import log_buffer, run_in_thread
from .theme import _DARK, ThemeManager

AVATAR_PX = 30
RING_PX = 1
REFRESH_MS = 60_000


def _fetch_profile(cookie: str) -> tuple[int, str, bytes | None] | None:
    """(user ID, username, circular headshot PNG) for a login, or None if it is not valid."""
    import requests

    sess = requests.Session()
    sess.trust_env = False
    sess.proxies = {}
    sess.cookies.set('.ROBLOSECURITY', cookie, domain='.roblox.com')
    resp = sess.get('https://users.roblox.com/v1/users/authenticated', timeout=10)
    if resp.status_code != 200:
        return None
    user = resp.json()
    user_id, name = int(user['id']), str(user.get('name') or '')
    avatar = None
    try:
        thumbs = sess.get(
            'https://thumbnails.roblox.com/v1/users/avatar-headshot',
            params={'userIds': user_id, 'size': '48x48', 'format': 'Png', 'isCircular': 'true'},
            timeout=10,
        ).json()
        url = (thumbs.get('data') or [{}])[0].get('imageUrl')
        if url:
            avatar = sess.get(url, timeout=10).content
    except requests.RequestException, ValueError, IndexError:
        pass
    return user_id, name, avatar


class ProfileChip(QPushButton):
    """Avatar (in an accent ring) + username; highlights on hover, opens the profile on click.

    Hidden until a valid login is found. Not part of the tab bar, so the tab
    bar's hover fade never applies to it.
    """

    _profile_loaded = pyqtSignal(object)
    # Shown/hidden/renamed, so the dashboard can re-centre its tabs
    changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('profileChip')
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setIconSize(QSize(AVATAR_PX, AVATAR_PX))
        self.setToolTip(tr('ui.gui.profile_chip.open_profile'))
        self._user_id: int | None = None
        self._cookie: str | None = ''  # Not looked up yet, so the first result always applies
        self._avatar: bytes | None = None
        self._accent: tuple[str, str] | None = None  # (accent, ring colour) last styled with
        self._apply_style()
        self.clicked.connect(self._open_profile)
        self._profile_loaded.connect(self._on_profile_loaded)
        self.hide()
        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh)
        self._timer.start(REFRESH_MS)
        QTimer.singleShot(0, self.refresh)

    def _apply_style(self) -> None:
        """Hover colour and avatar ring follow the accent colour."""
        if self._accent == (ThemeManager._accent, self._ring_color().name()):
            return
        self._accent = (ThemeManager._accent, self._ring_color().name())
        accent = QColor(ThemeManager._accent)
        soft = f'rgba({accent.red()}, {accent.green()}, {accent.blue()}, 0.16)'
        self.setStyleSheet(
            'QPushButton#profileChip { background: transparent; border: none; border-radius: 19px;'
            ' padding: 4px 14px 4px 4px; margin: 9px 0 9px 16px; font-weight: 700; font-size: 14px;'
            ' text-align: left; }'
            f'QPushButton#profileChip:hover {{ background: {soft}; }}'
        )
        self._update_icon()

    def _ring_color(self) -> QColor:
        """The panels' edge colour in dark mode (like the windows above the background), black in light."""
        app = QApplication.instance()
        palette = app.palette() if isinstance(app, QApplication) else self.palette()
        dark = palette.window().color().lightness() < 128
        return QColor(_DARK['line']) if dark else QColor(Qt.GlobalColor.black)

    def _update_icon(self) -> None:
        """Draw the circular avatar inside a thin accent ring."""
        source = QPixmap()
        if not self._avatar or not source.loadFromData(self._avatar):
            return
        ratio = self.devicePixelRatioF()
        size = round(AVATAR_PX * ratio)
        ring = RING_PX * ratio
        icon = QPixmap(size, size)
        icon.fill(Qt.GlobalColor.transparent)
        painter = QPainter(icon)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        inset = round(ring * 2)
        painter.drawPixmap(
            inset,
            inset,
            source.scaled(
                size - 2 * inset,
                size - 2 * inset,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ),
        )
        painter.setPen(QPen(self._ring_color(), ring))
        painter.drawEllipse(QRectF(ring / 2, ring / 2, size - ring, size - ring))
        painter.end()
        icon.setDevicePixelRatio(ratio)
        self.setIcon(QIcon(icon))

    def refresh(self) -> None:
        """Look up the login again; only fetches when it changed."""
        from ..utils.roblox_auth import get_roblosecurity

        def work() -> None:
            cookie = get_roblosecurity()
            if cookie == self._cookie:
                return
            try:
                profile = _fetch_profile(cookie) if cookie else None
            except Exception as exc:
                log_buffer.log(
                    'Profile', f'Could not load the Roblox profile: {type(exc).__name__}'
                )
                return
            self._cookie = cookie
            self._profile_loaded.emit(profile)

        run_in_thread(work)()

    def _on_profile_loaded(self, profile) -> None:
        if profile is None:
            self._user_id = None
            self.hide()
            self.changed.emit()
            return
        self._user_id, name, self._avatar = profile
        self._update_icon()
        self.setText(verbatim(f' {name}'))
        self.show()
        self.changed.emit()

    def sizeHint(self):  # noqa: N802
        # The tab widget places corner widgets by their size hint; match the tab bar height
        return QSize(super().sizeHint().width(), self.maximumHeight())

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        if self._user_id is None:  # The tab widget shows its corner widgets; stay hidden
            QTimer.singleShot(0, self.hide)

    def _open_profile(self) -> None:
        if self._user_id is not None:
            QDesktopServices.openUrl(QUrl(f'https://www.roblox.com/users/{self._user_id}/profile'))

    def enterEvent(self, event):  # noqa: N802
        self._apply_style()  # Follow accent colour changes
        super().enterEvent(event)

    def changeEvent(self, event):  # noqa: N802
        super().changeEvent(event)
        # Light/dark switch: redraw the ring (deferred; restyling changes the palette again)
        if event.type() == QEvent.Type.PaletteChange:
            QTimer.singleShot(0, self._apply_style)
