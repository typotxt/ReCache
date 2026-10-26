"""Cache tab: browse Roblox's cache folder and preview each cached asset."""

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPalette
from PyQt6.QtWidgets import (
    QCheckBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..cache import mesh_processing
from ..cache.cache_viewer import CacheViewerTab
from ..cache.rbxm_preview import is_rbx_model_data
from ..cache.roblox_cache_writer import (
    BLOB_DIR,
    CacheEntry,
    cache_entries,
    imported_games,
    read_cache_body,
)
from ..localization import tr, tr_count, verbatim
from ..utils import open_folder, run_in_thread

_ROLE_ENTRY = Qt.ItemDataRole.UserRole
_ROLE_SORT = Qt.ItemDataRole.UserRole.value + 1
_IMAGE_KINDS = ('.png', '.jpg', '.webp', '.ktx2')
_AUDIO_KINDS = ('.ogg', '.mp3')
_COL_ITEM, _COL_ID, _COL_GAME, _COL_TYPE, _COL_SIZE, _COL_STATUS = range(6)


def _name_parts(entry: CacheEntry) -> list[str]:
    """The entry's key-list path, e.g. ['Models', 'Arms', 'Left Arm', 'Glove'] (first name only)."""
    return entry.name.split(' + ')[0].split(' / ') if entry.name else []


def _item_label(entry: CacheEntry, *, leaf_only: bool) -> str:
    """The asset's name (Models/Arms/Left Arm/Glove), or its cache file name when unnamed."""
    parts = _name_parts(entry)
    if not parts:
        return verbatim(entry.key.hex())
    label = verbatim(parts[-1] if leaf_only else '/'.join(parts))
    extra = entry.name.count(' + ')
    return tr('ui.gui.cache_files.more_names', name=label, count=extra) if extra else label


def _group_path(entry: CacheEntry) -> list[str]:
    """Where Item grouping puts an entry: its game's folders, or Unnamed / its type."""
    parts = _name_parts(entry)
    if parts:
        return [verbatim(entry.game), *map(verbatim, parts[:-1])]
    if entry.kind in _IMAGE_KINDS:
        kind = tr('ui.gui.cache_files.group_images')
    elif entry.kind in _AUDIO_KINDS:
        kind = tr('ui.gui.cache_files.group_sounds')
    elif entry.kind == '.mesh':
        kind = tr('ui.gui.cache_files.group_meshes')
    elif entry.kind in ('.rbxm', '.rbxmx'):
        kind = tr('ui.gui.cache_files.group_models')
    else:
        kind = tr('ui.gui.cache_files.group_other')
    return [tr('ui.gui.cache_files.unnamed'), kind]


def _type_label(kind: str) -> str:
    if kind in _IMAGE_KINDS:
        return tr('ui.gui.cache_files.type_image', ext=verbatim(kind[1:].upper()))
    if kind in _AUDIO_KINDS:
        return tr('ui.gui.cache_files.type_audio', ext=verbatim(kind[1:].upper()))
    if kind == '.mesh':
        return tr('ui.gui.cache_files.type_mesh')
    if kind in ('.rbxm', '.rbxmx'):
        return tr('ui.gui.cache_files.type_model')
    return tr('ui.gui.cache_files.type_other')


def _size_label(size: int) -> str:
    if size < 1024:
        return verbatim(f'{size} B')
    if size < 1024 * 1024:
        return verbatim(f'{size / 1024:.1f} KB')
    return verbatim(f'{size / (1024 * 1024):.1f} MB')


def _status_label(status: str, *, long: bool = False) -> str:
    if status == 'replaced':
        return tr(
            'ui.gui.cache_files.status_replaced_long'
            if long
            else 'ui.gui.cache_files.status_replaced'
        )
    if status == 'inserted':
        return tr(
            'ui.gui.cache_files.status_inserted_long'
            if long
            else 'ui.gui.cache_files.status_inserted'
        )
    return tr('ui.gui.cache_files.status_untouched') if long else ''


class _SortItem(QTreeWidgetItem):
    """Sorts by _ROLE_SORT when a column sets it (sizes, asset IDs)."""

    def __lt__(self, other):
        tree = self.treeWidget()
        column = tree.sortColumn() if tree is not None else 0
        left, right = self.data(column, _ROLE_SORT), other.data(column, _ROLE_SORT)
        if left is not None and right is not None:
            return left < right
        return self.text(column).casefold() < other.text(column).casefold()


class _CachePreview(CacheViewerTab):
    """Just the Scraper's preview panel, fed with bytes instead of a table row.

    Skips CacheViewerTab's table, scraper and name resolver; only the preview
    widgets and the loaders behind them are set up.
    """

    def __init__(self, cache_manager, config_manager, parent=None):
        QWidget.__init__(self, parent)
        self.cache_manager = cache_manager
        self.config_manager = config_manager
        self._selected_asset_id = None
        self._current = None
        self._current_pixmap = None
        self.obj_viewer = None
        self.animation_viewer = None
        self._modified_rbxm_drafts = {}
        self._rbxm_preview_asset_key = None
        self._rbxm_preview_cached_at = ''
        self._image_loader = None
        self._mesh_loader = None
        self._animation_loader = None
        self._texturepack_loader = None
        self._audio_key_filter_installed = False
        self._shutting_down = False
        # Inherited preview code touches these; they are never shown here
        self.table = QTableWidget(self)
        self.stop_preview_btn = QPushButton(self)
        self.rbxm_view_btn = QPushButton(self)
        for widget in (self.table, self.stop_preview_btn, self.rbxm_view_btn):
            widget.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.preview_panel = self._create_preview_panel()
        layout.addWidget(self.preview_panel)
        self.preview_title_label.hide()

    def shutdown(self) -> None:
        self._stop_all_loaders()

    def resizeEvent(self, event):  # noqa: N802
        QWidget.resizeEvent(self, event)
        if self._current_pixmap is not None and self.image_label.isVisible():
            self._scale_and_show_image(self._current_pixmap)

    def changeEvent(self, event):  # noqa: N802
        QWidget.changeEvent(self, event)

    def _get_selected_asset(self):
        return self._current

    def show_body(self, token: str, body: bytes, type_name: str) -> None:
        """Preview one asset's bytes; ``token`` identifies it for stale-result checks."""
        self._clear_preview()
        self._remove_audio_key_filter()
        self.preview_panel.show()
        self.image_label.hide()
        self.json_viewer.hide()
        self.font_wrapper.hide()
        self._selected_asset_id = token
        self._current = {'id': token, 'type': None, 'type_name': type_name}
        kind = body[:8]
        try:
            if mesh_processing.is_mesh_data(body):
                self._show_loading()
                self._preview_mesh(body, token)
            elif is_rbx_model_data(body):
                if b'KeyframeSequence' in body or b'CurveAnimation' in body:
                    self._preview_animation(body, token)
                elif b'PartOperationAsset' in body:  # CSG union: 3D, structure on failure
                    self._show_loading()
                    self._preview_solidmodel(body, token)
                else:
                    self._preview_rbxm(body, self._current)
            elif kind.startswith((b'OggS', b'ID3', b'\xff\xfb')):
                self._preview_audio(body, token)
            elif kind.startswith((b'RIFF', b'\x89PNG', b'\xff\xd8\xff', b'\xabKTX 20')):
                self._show_loading()
                self._preview_image(body)
            else:
                is_json, parsed = self._is_json_data(body)
                if is_json and parsed is not None:
                    self.json_viewer.load_json(parsed)
                    self.json_viewer.show()
                else:
                    self._preview_hex(body, self._current)
        except Exception as exc:
            self._show_text_preview(tr('cache.preview.asset_error', error=exc))

    def clear(self) -> None:
        self._clear_preview()
        self._remove_audio_key_filter()
        self.preview_panel.show()
        self.text_viewer.hide()
        self.image_label.setText(tr('ui.cache.cache_viewer.select_an_asset_to_preview'))
        self.image_label.show()


class CacheFilesTab(QWidget):
    """Every asset in Roblox's cache, grouped by cache folder, with a preview."""

    _entries_loaded = pyqtSignal(object)

    def __init__(self, cache_manager, config_manager, parent=None):
        super().__init__(parent)
        self._config_manager = config_manager
        self._entries: list[CacheEntry] = []
        self._loading = False
        self._loaded_once = False
        self._entries_loaded.connect(self._on_entries_loaded)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 12, 24, 16)
        layout.setSpacing(12)

        bar = QHBoxLayout()
        bar.setSpacing(10)
        hint = QLabel(tr('ui.gui.cache_files.hint'))
        hint.setWordWrap(True)
        hint.setStyleSheet('color: palette(placeholder-text);')
        bar.addWidget(hint, 1)
        open_btn = QPushButton(tr('ui.gui.cache_files.open_folder'))
        open_btn.clicked.connect(
            lambda: open_folder(BLOB_DIR if BLOB_DIR.is_dir() else BLOB_DIR.parent)
        )
        bar.addWidget(open_btn)
        self._refresh_btn = QPushButton(tr('ui.gui.cache_files.refresh'))
        self._refresh_btn.setObjectName('primaryButton')
        self._refresh_btn.clicked.connect(self.refresh)
        bar.addWidget(self._refresh_btn)
        layout.addLayout(bar)

        filters = QGroupBox(tr('ui.cache.cache_viewer.filters'))
        filter_row = QHBoxLayout(filters)
        filter_row.setSpacing(12)
        self._search = QLineEdit()
        self._search.setPlaceholderText(tr('ui.gui.cache_files.search'))
        self._search.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._search, 1)
        self._only_recache = QCheckBox(tr('ui.gui.cache_files.only_recache'))
        self._only_recache.toggled.connect(self._apply_filter)
        filter_row.addWidget(self._only_recache)
        self._summary = QLabel()
        self._summary.setStyleSheet('color: palette(placeholder-text);')
        filter_row.addWidget(self._summary)
        # Folders / Item grouping, shown when Item grouping is on in Settings
        self._view_switch = QWidget()
        view_row = QHBoxLayout(self._view_switch)
        view_row.setContentsMargins(0, 0, 0, 0)
        view_row.setSpacing(0)
        self._folders_btn = QCheckBox(tr('ui.gui.cache_files.view_folders'))
        self._items_btn = QCheckBox(tr('ui.gui.cache_files.view_items'))
        for btn in (self._folders_btn, self._items_btn):
            btn.setObjectName('segment')
            btn.setAutoExclusive(True)
            view_row.addWidget(btn)
        self._folders_btn.setChecked(True)
        self._items_btn.toggled.connect(lambda _checked: self._rebuild_tree())
        self._view_switch.hide()
        filter_row.addWidget(self._view_switch)
        layout.addWidget(filters)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(20)  # Same gap as between the Replacer's panels; still draggable
        self.tree = QTreeWidget()
        self.tree.setHeaderLabels(
            [
                tr('ui.gui.cache_files.col_item'),
                tr('ui.gui.cache_files.col_asset_id'),
                tr('ui.gui.cache_files.col_game'),
                tr('ui.gui.cache_files.col_type'),
                tr('ui.gui.cache_files.col_size'),
                tr('ui.gui.cache_files.col_status'),
            ]
        )
        self.tree.setUniformRowHeights(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(_COL_ITEM, Qt.SortOrder.AscendingOrder)
        self.tree.setIndentation(14)
        # No solid Highlight block in the branch gutter of the selected row
        palette = self.tree.palette()
        palette.setColor(QPalette.ColorRole.Highlight, QColor(Qt.GlobalColor.transparent))
        self.tree.setPalette(palette)
        header = self.tree.header()
        header.setStretchLastSection(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setSectionResizeMode(_COL_ITEM, QHeaderView.ResizeMode.Stretch)
        for column, width in (
            (_COL_ID, 140),
            (_COL_GAME, 130),
            (_COL_TYPE, 110),
            (_COL_SIZE, 90),
            (_COL_STATUS, 90),
        ):
            self.tree.setColumnWidth(column, width)
        self.tree.currentItemChanged.connect(self._on_current_changed)
        # A single click opens or closes a cache folder
        self.tree.itemClicked.connect(
            lambda item, _column: item.childCount() and item.setExpanded(not item.isExpanded())
        )
        splitter.addWidget(self.tree)

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(10)
        details = QGroupBox(tr('ui.gui.cache_files.details'))
        grid = QGridLayout(details)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(6)
        self._detail_values: dict[str, QLabel] = {}
        detail_labels = {
            'key': tr('ui.gui.cache_files.detail_key'),
            'asset_id': tr('ui.gui.cache_files.detail_asset_id'),
            'name': tr('ui.gui.cache_files.detail_name'),
            'type': tr('ui.gui.cache_files.detail_type'),
            'size': tr('ui.gui.cache_files.detail_size'),
            'status': tr('ui.gui.cache_files.detail_status'),
        }
        for row, (key, text) in enumerate(detail_labels.items()):
            label = QLabel(text)
            label.setStyleSheet('color: palette(placeholder-text); font-weight: 600;')
            value = QLabel()
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            value.setWordWrap(True)
            grid.addWidget(label, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(value, row, 1)
            self._detail_values[key] = value
        grid.setColumnStretch(1, 1)
        right_layout.addWidget(details)

        self.preview = _CachePreview(cache_manager, config_manager)
        right_layout.addWidget(self.preview, 1)
        splitter.addWidget(right)
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter, 1)

        self._show_details(None)

    def showEvent(self, event):  # noqa: N802
        super().showEvent(event)
        grouping = bool(getattr(self._config_manager, 'cache_item_grouping', True))
        if self._view_switch.isHidden() == grouping:  # the Settings toggle changed
            self._view_switch.setVisible(grouping)
            if self._items_btn.isChecked():
                self._rebuild_tree()
        if not self._loaded_once:
            self.refresh()

    def refresh(self) -> None:
        if self._loading:
            return
        self._loading = True
        self._loaded_once = True
        self._refresh_btn.setEnabled(False)
        self._summary.setText(tr('ui.gui.cache_files.loading'))
        run_in_thread(lambda: self._entries_loaded.emit(cache_entries()))()

    def _on_entries_loaded(self, entries: list[CacheEntry]) -> None:
        self._loading = False
        self._refresh_btn.setEnabled(True)
        self._entries = entries
        self._rebuild_tree()
        replaced = sum(e.status == 'replaced' for e in entries)
        inserted = sum(e.status == 'inserted' for e in entries)
        known = sum(e.asset_id is not None for e in entries)
        games = ', '.join(sorted(g.replace('_', ' ').title() for g in imported_games()))
        self._summary.setText(
            tr(
                'ui.gui.cache_files.summary',
                total=len(entries),
                replaced=replaced,
                inserted=inserted,
                known=known,
            )
            + (tr('ui.gui.cache_files.summary_games', games=verbatim(games)) if games else '')
        )

    @staticmethod
    def _path_of(item: QTreeWidgetItem | None) -> tuple[str, ...]:
        path: list[str] = []
        while item is not None:
            path.append(item.text(_COL_ITEM))
            item = item.parent()
        return tuple(reversed(path))

    def _group_items(self) -> list[QTreeWidgetItem]:
        """Every folder/group row, parents before children."""
        groups: list[QTreeWidgetItem] = []
        pending = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while pending:
            item = pending.pop(0)
            if item.data(_COL_ITEM, _ROLE_ENTRY) is None:
                groups.append(item)
                pending.extend(item.child(i) for i in range(item.childCount()))
        return groups

    def _rebuild_tree(self) -> None:
        """Lay the entries out by cache folder, or by item when Item grouping is on."""
        selected = self._selected_entry()
        expanded = {self._path_of(g) for g in self._group_items() if g.isExpanded()}
        by_item = self._items_btn.isChecked() and not self._view_switch.isHidden()
        self.tree.setSortingEnabled(False)
        self.tree.clear()
        groups: dict[tuple[str, ...], QTreeWidgetItem] = {}
        restore: QTreeWidgetItem | None = None

        def group_for(path: list[str], last: bool) -> QTreeWidgetItem:
            parent: QTreeWidgetItem | None = None
            for depth in range(1, len(path) + 1):
                key = tuple(path[:depth])
                group = groups.get(key)
                if group is None:
                    group = _SortItem([path[depth - 1]])
                    # Unnamed / the inline folder sort after everything else
                    group.setData(_COL_ITEM, _ROLE_SORT, (last, path[depth - 1].casefold()))
                    if parent is None:
                        self.tree.addTopLevelItem(group)
                    else:
                        parent.addChild(group)
                    groups[key] = group
                parent = group
            assert parent is not None
            return parent

        for entry in self._entries:
            if by_item:
                group = group_for(_group_path(entry), not entry.name)
            elif entry.inline:
                group = group_for([tr('ui.gui.cache_files.inline_folder')], True)
            else:
                group = group_for([verbatim(entry.key.hex()[:2])], False)
            item = _SortItem(
                [
                    _item_label(entry, leaf_only=by_item),
                    verbatim(entry.asset_id or ''),
                    verbatim(entry.game),
                    _type_label(entry.kind),
                    _size_label(entry.size),
                    _status_label(entry.status),
                ]
            )
            item.setToolTip(_COL_ITEM, verbatim(entry.key.hex()))
            item.setData(_COL_ITEM, _ROLE_ENTRY, entry)
            item.setData(_COL_ITEM, _ROLE_SORT, (not entry.name, item.text(_COL_ITEM).casefold()))
            item.setData(_COL_ID, _ROLE_SORT, entry.asset_id or 0)
            item.setData(_COL_SIZE, _ROLE_SORT, entry.size)
            item.setData(_COL_STATUS, _ROLE_SORT, entry.status)
            group.addChild(item)
            if selected is not None and entry.key == selected.key:
                restore = item
        for group in reversed(self._group_items()):  # children first, so counts add up
            files = sum(
                1
                if child.data(_COL_ITEM, _ROLE_ENTRY) is not None
                else child.data(_COL_SIZE, _ROLE_SORT)
                for child in (group.child(i) for i in range(group.childCount()))
            )
            group.setText(
                _COL_SIZE,
                tr_count(
                    files, 'ui.gui.cache_files.folder_count_one', 'ui.gui.cache_files.folder_count'
                ),
            )
            group.setData(_COL_SIZE, _ROLE_SORT, files)
            group.setExpanded(self._path_of(group) in expanded)
        self.tree.setSortingEnabled(True)
        self._apply_filter()
        if restore is not None:
            self.tree.setCurrentItem(restore)

    def _apply_filter(self) -> None:
        needle = self._search.text().strip().casefold()
        only_recache = self._only_recache.isChecked()

        def visit(item: QTreeWidgetItem) -> bool:
            entry: CacheEntry | None = item.data(_COL_ITEM, _ROLE_ENTRY)
            if entry is None:  # a folder/group: shown when anything inside is
                visible = sum(visit(item.child(i)) for i in range(item.childCount())) > 0
                if needle or only_recache:
                    item.setExpanded(visible)
            else:
                haystack = (
                    item.text(_COL_ITEM),
                    item.text(_COL_ID),
                    item.text(_COL_GAME),
                    entry.key.hex(),
                    entry.name,
                )
                visible = not (only_recache and not entry.status) and (
                    not needle or any(needle in text.casefold() for text in haystack)
                )
            item.setHidden(not visible)
            return visible

        for i in range(self.tree.topLevelItemCount()):
            visit(self.tree.topLevelItem(i))

    def _selected_entry(self) -> CacheEntry | None:
        item = self.tree.currentItem()
        return item.data(_COL_ITEM, _ROLE_ENTRY) if item is not None else None

    def _on_current_changed(self, *_args) -> None:
        self._show_details(self._selected_entry())
        self._show_preview()

    def _show_preview(self) -> None:
        entry = self._selected_entry()
        if entry is None:
            self.preview.clear()
            return
        body = read_cache_body(entry.key)
        if body is None:
            self.preview.clear()
            return
        self.preview.show_body(entry.key.hex(), body, _type_label(entry.kind))

    def _show_details(self, entry: CacheEntry | None) -> None:
        none = tr('ui.gui.cache_files.none')
        values = {
            'key': verbatim(entry.key.hex()) if entry else none,
            'asset_id': verbatim(entry.asset_id) if entry and entry.asset_id else none,
            'name': verbatim(f'{entry.game} / {entry.name}') if entry and entry.name else none,
            'type': _type_label(entry.kind) if entry else none,
            'size': _size_label(entry.size) if entry else none,
            'status': _status_label(entry.status, long=True) if entry else none,
        }
        for key, value in values.items():
            self._detail_values[key].setText(value)
