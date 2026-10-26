"""Tree whose row highlights fade: the Replacer's look, shared with other trees."""

from PyQt6.QtCore import QEvent, QRect, Qt, QTimer
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import QTreeWidget, QTreeWidgetItem

from .theme import ThemeManager
from .transitions import animations_enabled


class HighlightTree(QTreeWidget):
    """Selected rows get the accent, hovering a selected row softens it, and
    hovering any other row brightens it. Highlights are painted in drawRow so
    they can ease in and out (instantly with Remove UI animations).
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('highlightTree')
        # Only items: styling ::branch would make Qt stop drawing the expand arrows
        self.setStyleSheet(
            'QTreeWidget#highlightTree::item:selected, QTreeWidget#highlightTree::item:hover'
            ' { background: transparent; }'
        )
        self.setMouseTracking(True)
        # The style fills the branch gutter of selected rows with Highlight; drawRow covers it
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Highlight, QColor(Qt.GlobalColor.transparent))
        self.setPalette(palette)
        self._hover_item: QTreeWidgetItem | None = None
        # id(item) -> (item, [selected, hovered]); tree items are not hashable
        self._highlight: dict[int, tuple[QTreeWidgetItem, list[float]]] = {}
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setInterval(15)
        self._highlight_timer.timeout.connect(self._step_highlight)
        self.itemSelectionChanged.connect(self._highlight_timer.start)

    def _set_hover_item(self, item: QTreeWidgetItem | None) -> None:
        if item is not self._hover_item:
            self._hover_item = item
            self._highlight_timer.start()

    def mouseMoveEvent(self, event):  # noqa: N802
        self._set_hover_item(self.itemAt(event.position().toPoint()))
        super().mouseMoveEvent(event)

    def viewportEvent(self, event):  # noqa: N802
        if event.type() == QEvent.Type.Leave:
            self._set_hover_item(None)
        return super().viewportEvent(event)

    def _step_highlight(self) -> None:
        """Ease every row's highlight toward its target; stop once all have settled."""
        items = {key: item for key, (item, _) in self._highlight.items()}
        for item in [*self.selectedItems(), self._hover_item]:
            if item is not None:
                items[id(item)] = item
        moving = False
        for key, item in items.items():
            try:
                targets = (float(item.isSelected()), float(item is self._hover_item))
            except RuntimeError:  # deleted when the tree was rebuilt
                self._highlight.pop(key, None)
                continue
            values = self._highlight.setdefault(key, (item, [0.0, 0.0]))[1]
            for i, target in enumerate(targets):
                step = (target - values[i]) * (0.28 if animations_enabled() else 1.0)
                values[i] = target if abs(step) < 0.004 else values[i] + step
                moving |= values[i] != target
            if values == [0.0, 0.0]:
                self._highlight.pop(key, None)
        if not moving:
            self._highlight_timer.stop()
        self.viewport().update()

    def drawRow(self, painter, option, index):  # noqa: N802
        entry = self._highlight.get(id(self.itemFromIndex(index)))
        if entry is not None:
            selected, hovered = entry[1]
            dark = self.palette().base().color().lightness() < 128
            row = QRect(0, option.rect.y(), self.viewport().width(), option.rect.height())
            hover = QColor(255, 255, 255, 22) if dark else QColor(0, 0, 0, 16)
            hover.setAlphaF(hover.alphaF() * hovered * (1 - selected))
            painter.fillRect(row, hover)
            accent = QColor(ThemeManager._accent)
            accent.setAlphaF((0.24 if dark else 0.17) * selected * (1 - 0.5 * hovered))
            painter.fillRect(row, accent)
        super().drawRow(painter, option, index)
