import os
import sys
from pathlib import Path
import json


from PyQt6.QtCore import (
    QSortFilterProxyModel,
    Qt,
    QPointF,
    QStandardPaths,
    pyqtSignal,
    qInstallMessageHandler,
    QtMsgType,
    QSize
)
from PyQt6.QtGui import (
    QColor,
    QAction,
    QIcon,
    QKeySequence,
    QFileSystemModel,
    QIcon
)
from PyQt6.QtWidgets import (
    QApplication, QWidget, QMainWindow, QFileDialog, QSplitter, QVBoxLayout,
    QHBoxLayout, QStackedLayout, QLabel, QLineEdit, QPushButton, QSlider,
    QTabWidget, QTreeView, QListWidget, QListWidgetItem, QStyle, QToolButton,
    QFrame, QMessageBox
)
from PyQt6.QtPdf import QPdfDocument, QPdfBookmarkModel
from PyQt6.QtPdfWidgets import QPdfView


# ---------- Suppress specific Qt warnings ----------
def qt_message_filter(msg_type, context, message):
    """Suppress noisy invalid-bookmark warnings but keep all others."""
    if (
        msg_type == QtMsgType.QtWarningMsg
        and "bookmark with invalid location" in message
    ):
        return  # swallow it quietly

    # Forward all other messages
    sys.stderr.write(message + "\n")

qInstallMessageHandler(qt_message_filter)


APP_NAME = "FastPdfViewer"
ORG_NAME = "K Novak & Co."

icon_size = QSize(32, 32)

def app_data_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppDataLocation)
    p = Path(base)
    p.mkdir(parents=True, exist_ok=True)
    return p

FAVORITES_FILE = app_data_dir() / "favorites.txt"
LAST_ROOT_FILE = app_data_dir() / "last_root.txt"
WINDOW_STATE_FILE = app_data_dir() / "window_state.json"

# ---------- Small helpers ----------
def icon_from_style(hint: QStyle.StandardPixmap, widget: QWidget) -> QIcon:
    return widget.style().standardIcon(hint)

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

def icon(name: str) -> QIcon:
    """Load an icon from the icons folder."""
    base = Path(__file__).parent / "icons"
    return QIcon(str(base / f"{name}.png"))

def QColor_from_hex(hexstr: str) -> QColor:
    # small color helper
    c = QColor()
    c.setNamedColor(hexstr)
    return c

# --- Filter to show only directories + PDF files ---
class PdfFilterProxyModel(QSortFilterProxyModel):
    def filterAcceptsRow(self, source_row, source_parent):
        model = self.sourceModel()
        index = model.index(source_row, 0, source_parent)
        if not index.isValid():
            return False
        if model.isDir(index):
            return True
        return model.fileName(index).lower().endswith(".pdf")

    def lessThan(self, left, right):
        model = self.sourceModel()
        left_is_dir = model.isDir(left)
        right_is_dir = model.isDir(right)
        if left_is_dir != right_is_dir:
            return left_is_dir
        return super().lessThan(left, right)

# ---------- File System Explorer Widget ----------
class PdfExplorerWidget(QWidget):
    def __init__(self, root_path, parent=None):
        super().__init__(parent)

        if not os.path.exists(root_path):
            raise FileNotFoundError(f"Root path not found: {root_path}")

        self.fs_model = QFileSystemModel()
        self.setRootPath(root_path)

        self.proxy_model = PdfFilterProxyModel()
        self.proxy_model.setSourceModel(self.fs_model)
        self.proxy_model.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy_model.sort(0, Qt.SortOrder.AscendingOrder)

        self.tree = QTreeView()
        self.tree.setModel(self.proxy_model)
        self.tree.setRootIndex(self.proxy_model.mapFromSource(self.fs_model.index(root_path)))
        self.tree.setSortingEnabled(True)
        self.tree.setUniformRowHeights(True)
        self.tree.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        # Hide columns except file name
        self.tree.setColumnHidden(1, True)
        self.tree.setColumnHidden(2, True)
        self.tree.setColumnHidden(3, True)

        layout = QVBoxLayout()
        layout.addWidget(self.tree)
        self.setLayout(layout)

    def setRootPath(self, root_path):
        if not os.path.exists(root_path):
            raise FileNotFoundError(f"Root path not found: {root_path}")
        self.fs_model.setRootPath(root_path)

# ---------- Center View (PDF + error overlay) ----------
class CenterPdfView(QWidget):
    """A stack: PDF view underneath and a big red error label on top when needed."""
    pageChanged = pyqtSignal(int)
    documentLoaded = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doc = QPdfDocument(self)

        self.view = QPdfView(self)
        self.view.setDocument(self.doc)
        # Continuous vertical scrolling through all pages
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)

        # Error / empty overlay
        self.overlay = QLabel("", self)
        self.overlay.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.overlay.setStyleSheet("color: red; font-size: 16pt;")
        self.overlay.setWordWrap(True)

        self.stack = QStackedLayout()
        self.stack.addWidget(self.view)     # index 0
        self.stack.addWidget(self.overlay)  # index 1
        self.setLayout(self.stack)
        self.show_overlay("No document loaded.")  # start empty

        # Signals to propagate current page changes out
        # QPdfView doesn't emit pageChanged; we poll via viewport events.
        self.view.verticalScrollBar().valueChanged.connect(self._emit_current_page)

    def _emit_current_page(self, *_):
        # For multipage view: estimate current page by top-most fully/partially visible
        nav = self.view.pageNavigator()
        current = nav.currentPage()
        self.pageChanged.emit(current)

    # ---------- Loading ----------
    def load_file(self, file_path: str) -> bool:
        self.doc.close()
        self.overlay.clear()

        self.doc.load(file_path)
        st = self.doc.status()
        page_count = self.doc.pageCount()

        # In Qt 6.10, only Null/Loading/Ready/Unloading exist.
        if st != QPdfDocument.Status.Ready or page_count == 0:
            name = os.path.basename(file_path)
            self.show_overlay(f"{name}\n\nNot readable or empty.")
            return False

        # Valid document
        self.hide_overlay()
        self.view.pageNavigator().jump(0, QPointF(), 0.0)
        self.documentLoaded.emit()
        self._emit_current_page()
        return True


    # ---------- Overlay control ----------
    def show_overlay(self, text: str):
        self.overlay.setText(text)
        self.stack.setCurrentIndex(1)

    def hide_overlay(self):
        self.stack.setCurrentIndex(0)

    # ---------- Navigation ----------
    def page_count(self) -> int:
        return self.doc.pageCount()

    def current_page(self) -> int:
        return self.view.pageNavigator().currentPage()

    def go_to_page(self, page_zero_based: int):
        page = clamp(page_zero_based, 0, max(0, self.page_count() - 1))
        self.view.pageNavigator().jump(page, QPointF(), 0.0)

    def next_page(self):
        self.go_to_page(self.current_page() + 1)

    def prev_page(self):
        self.go_to_page(self.current_page() - 1)

    # ---------- Zoom ----------
    def zoom_factor(self) -> float:
        return self.view.zoomFactor()

    def set_zoom_factor(self, f: float):
        self.view.setZoomFactor(clamp(f, 0.05, 6.0))

    def fit_width(self):
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

    def fit_height(self):
        self.view.setZoomMode(QPdfView.ZoomMode.FitInView)  # Fit entire page in viewport
        # Note: FitInView in MultiPage mode behaves like fit height of viewport

    def custom_zoom(self):
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)

# ---------- TOC (Bookmarks) Panel ----------
class TocPanel(QWidget):
    """Tree view backed by QPdfBookmarkModel; emits page+location jumps when activated."""
    jumpToDestination = pyqtSignal(int, QPointF, float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.model = QPdfBookmarkModel(self)
        self.tree = QTreeView()
        self.tree.setHeaderHidden(True)
        self.tree.setModel(self.model)
        self.tree.activated.connect(self._activated)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addWidget(self.tree)

    def set_document(self, doc: QPdfDocument):
        self.model.setDocument(doc)
        self.tree.expandToDepth(1)

    def has_toc(self) -> bool:
        return self.model.rowCount() > 0

    def _activated(self, idx):
        """Called when user clicks a TOC entry."""
        try:
            # Qt < 6.10
            dest = idx.data(QPdfBookmarkModel.Role.Destination)
        except AttributeError:
            # Qt 6.10+ → only Page/Location/Zoom roles remain
            dest = None

        if dest is not None:
            # Newer Qt uses QPdfLink-like object, but may still be QVariant
            try:
                page = dest.page()
                point = dest.location()
                zoom = dest.zoom()
                if page >= 0:
                    self.jumpToDestination.emit(page, point, zoom)
                    return
            except Exception:
                pass

        # --- Fallback path (always available) ---
        page = idx.data(QPdfBookmarkModel.Role.Page)
        if isinstance(page, int) and page >= 0:
            self.jumpToDestination.emit(page, QPointF(), 0.0)

# ---------- Files + Favorites Panel ----------
class FilesPanel(QWidget):
    fileActivated = pyqtSignal(str)
    rootChanged = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)

        # Load last root or default to home
        start_root = self._load_last_root() or str(Path.home())

        # --- File operation buttons above browser ---
        self.btn_delete_file = QToolButton()
        self.btn_delete_file.setIcon(icon("delete_pdf"))  # reuse bin icon
        self.btn_delete_file.setToolTip("Delete selected file")

        self.btn_rename_file = QToolButton()
        self.btn_rename_file.setIcon(icon("rename_file"))  # temporary icon, replace if you have rename.png
        self.btn_rename_file.setToolTip("Rename selected file")

        # connect signals
        # self.btn_delete_file.clicked.connect(self._delete_selected_file)
        # self.btn_rename_file.clicked.connect(self._rename_selected_file)

        file_btn_row = QHBoxLayout()
        file_btn_row.addWidget(self.btn_delete_file)
        file_btn_row.addWidget(self.btn_rename_file)
        file_btn_row.addStretch(1)

        # Top: file browser
        self.browser = PdfExplorerWidget(start_root, self)
        self.browser.tree.doubleClicked.connect(self._on_double_clicked)

        # Bottom: favorites list + buttons
        self.fav_list = QListWidget()
        self._load_favorites()

        self.btn_add = QToolButton()
        self.btn_add.setIcon(icon("add_folder"))
        self.btn_add.setToolTip("Add favorite folder")

        self.btn_del = QToolButton()
        self.btn_del.setIcon(icon("delete_folder"))
        self.btn_del.setToolTip("Remove selected favorite")

        btn_row = QHBoxLayout()
        btn_row.addWidget(self.btn_add)
        btn_row.addWidget(self.btn_del)
        btn_row.addStretch(1)

        self.btn_add.clicked.connect(self._add_favorite)
        self.btn_del.clicked.connect(self._remove_selected_favorite)
        self.fav_list.itemActivated.connect(self._activate_favorite)

        # Icon sizes
        for btn in (
            self.btn_add,
            self.btn_del,
            self.btn_delete_file,
            self.btn_rename_file,
        ):
            btn.setIconSize(icon_size)

        # Layout
        v = QVBoxLayout(self)
        v.setContentsMargins(0, 0, 0, 0)
        v.addLayout(file_btn_row)
        v.addWidget(self.browser, 4)
        bar = QFrame()
        bar.setFrameShape(QFrame.Shape.HLine)
        v.addWidget(bar)
        v.addWidget(QLabel("Favorites"))
        v.addWidget(self.fav_list, 2)
        v.addLayout(btn_row)

    # ---- persistence helpers ----
    def _favorites_path(self) -> Path:
        return FAVORITES_FILE

    def _last_root_path(self) -> Path:
        return LAST_ROOT_FILE

    def _load_favorites(self):
        self.fav_list.clear()
        p = self._favorites_path()
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line and Path(line).exists():
                    self.fav_list.addItem(line)

    def _save_favorites(self):
        items = [self.fav_list.item(i).text() for i in range(self.fav_list.count())]
        self._favorites_path().write_text("\n".join(items), encoding="utf-8")

    def _load_last_root(self) -> str | None:
        p = self._last_root_path()
        if p.exists():
            root = p.read_text(encoding="utf-8").strip()
            if root and Path(root).exists():
                return root
        return None

    def _save_last_root(self, root: str):
        self._last_root_path().write_text(root, encoding="utf-8")

    # ---- favorites actions ----
    def _add_favorite(self):
        folder = QFileDialog.getExistingDirectory(self, "Add Favorite Folder", str(Path.home()))
        if folder:
            # avoid duplicates
            for i in range(self.fav_list.count()):
                if self.fav_list.item(i).text() == folder:
                    return
            self.fav_list.addItem(folder)
            self._save_favorites()

    def _remove_selected_favorite(self):
        row = self.fav_list.currentRow()
        if row >= 0:
            self.fav_list.takeItem(row)
            self._save_favorites()

    def _activate_favorite(self, item: QListWidgetItem):
        folder = item.text()
        if Path(folder).exists():
            try:
                self.browser.setRootPath(folder)
                # Update tree's root index mapping
                self.browser.tree.setRootIndex(
                    self.browser.proxy_model.mapFromSource(self.browser.fs_model.index(folder))
                )
                self._save_last_root(folder)
                self.rootChanged.emit(folder)
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to open folder:\n{e}")

    # ---- double click open ----
    def _on_double_clicked(self, proxy_index):
        # map to source -> get file path
        source_index = self.browser.proxy_model.mapToSource(proxy_index)
        path = self.browser.fs_model.filePath(source_index)
        if path and path.lower().endswith(".pdf") and Path(path).exists():
            self.fileActivated.emit(path)
        else:
            # If it's a folder, change root
            if Path(path).is_dir():
                try:
                    self.browser.setRootPath(path)
                    self.browser.tree.setRootIndex(
                        self.browser.proxy_model.mapFromSource(self.browser.fs_model.index(path))
                    )
                    self._save_last_root(path)
                    self.rootChanged.emit(path)
                except Exception as e:
                    QMessageBox.warning(self, "Error", f"Failed to open folder:\n{e}")

    # ---- file operations ----
    # def _delete_selected_file(self):
    #     index = self.browser.tree.currentIndex()
    #     if not index.isValid():
    #         return

    #     source_index = self.browser.proxy_model.mapToSource(index)
    #     path = self.browser.fs_model.filePath(source_index)
    #     if not Path(path).is_file():
    #         QMessageBox.information(self, "Delete", "Please select a file to delete.")
    #         return

    #     reply = QMessageBox.question(
    #         self,
    #         "Delete File",
    #         f"Are you sure you want to delete:\n{os.path.basename(path)}?",
    #         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
    #     )
    #     if reply != QMessageBox.StandardButton.Yes:
    #         return

    #     # --- Close document only if it is the same file ---
    #     main_window = self.window()
    #     if isinstance(main_window, QMainWindow) and hasattr(main_window, "center"):
    #         center = main_window.center
    #         doc = center.doc

    #         # Detect if currently loaded document corresponds to this file
    #         current_file = getattr(center, "_current_file", None)
    #         if current_file and os.path.samefile(current_file, path):
    #             doc.close()
    #             center.show_overlay("Document closed.")
    #             center._emit_current_page()  # keep UI consistent
    #             center._current_file = None  # reset tracking

    #     # --- Delete file safely ---
    #     import time
    #     for _ in range(5):  # try a few times if Windows keeps it locked
    #         try:
    #             os.remove(path)
    #             break
    #         except PermissionError:
    #             time.sleep(0.1)
    #     else:
    #         QMessageBox.warning(
    #             self,
    #             "Error",
    #             f"Failed to delete file:\n{path}\n\nIt may still be open by another process.",
    #         )

    # def _rename_selected_file(self):
    #     index = self.browser.tree.currentIndex()
    #     if not index.isValid():
    #         return

    #     source_index = self.browser.proxy_model.mapToSource(index)
    #     path = self.browser.fs_model.filePath(source_index)
    #     if not Path(path).is_file():
    #         QMessageBox.information(self, "Rename", "Please select a file to rename.")
    #         return

    #     new_name, ok = QFileDialog.getSaveFileName(self, "Rename File", path, "PDF files (*.pdf)")
    #     if not ok or not new_name:
    #         return

    #     try:
    #         os.rename(path, new_name)
    #     except Exception as e:
    #         QMessageBox.warning(self, "Error", f"Failed to rename file:\n{e}")

# ---------- Right Toolbar ----------
class RightToolbar(QWidget):
    goPrev = pyqtSignal()
    goNext = pyqtSignal()
    requestPageJump = pyqtSignal(int)
    zoomIn = pyqtSignal()
    zoomOut = pyqtSignal()
    fitWidth = pyqtSignal()
    fitHeight = pyqtSignal()
    zoomSliderChanged = pyqtSignal(float)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._page_total = 0
        self._current_page = 0

        # Buttons
        self.btn_prev = QToolButton()
        self.btn_next = QToolButton()
        self.btn_zoom_out = QToolButton()
        self.btn_zoom_in = QToolButton()
        self.btn_fit_w = QToolButton()
        self.btn_fit_h = QToolButton()

        self.btn_prev.setIcon(icon("prev_page"))
        self.btn_next.setIcon(icon("next_page"))
        self.btn_zoom_out.setIcon(icon("zoom_out"))
        self.btn_zoom_in.setIcon(icon("zoom_in"))
        self.btn_fit_w.setIcon(icon("fit_width"))
        self.btn_fit_h.setIcon(icon("fit_height"))

        for btn in (
            self.btn_prev,
            self.btn_next,
            self.btn_zoom_in,
            self.btn_zoom_out,
            self.btn_fit_w,
            self.btn_fit_h,
        ):
            btn.setIconSize(icon_size)

        self.btn_prev.clicked.connect(self.goPrev.emit)
        self.btn_next.clicked.connect(self.goNext.emit)
        self.btn_zoom_in.clicked.connect(self.zoomIn.emit)
        self.btn_zoom_out.clicked.connect(self.zoomOut.emit)
        self.btn_fit_w.clicked.connect(self.fitWidth.emit)
        self.btn_fit_h.clicked.connect(self.fitHeight.emit)

        # Vertical page display (moved to bottom)
        self.page_label = QLabel()
        self.page_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.page_label.setStyleSheet("font-weight: bold; font-size: 16pt;")
        self._update_label()

        # Layout
        v = QVBoxLayout(self)
        v.setContentsMargins(6, 6, 6, 6)
        v.addWidget(self.btn_prev)
        v.addWidget(self.btn_next)
        v.addWidget(self.btn_zoom_in)
        v.addWidget(self.btn_zoom_out)
        v.addWidget(self.btn_fit_w)
        v.addWidget(self.btn_fit_h)
        v.addStretch(1)
        v.addWidget(self.page_label)  # moved to bottom

    def _update_label(self):
        def vertical_digits(n: int) -> str:
            if n <= 0:
                return "-"
            return "<br>".join(ch for ch in str(n))

        if self._page_total <= 0:
            html = (
                '<div style="text-align:center; line-height:1;"><div>-</div><hr style="width:20px; margin:0; border:1px solid black;"><div>-</div></div>'
            )
        else:
            cur = vertical_digits(self._current_page + 1)
            total = vertical_digits(self._page_total)
            html = (
                # f"<div align='center' style='font-weight:bold;font-size:16pt; line-height:0.7;'>{cur}<hr width='10' style='margin:0;'>{total}</div>"
                f'<div style="text-align:center; line-height:1;"><div>{cur}</div><hr style="width:20px; margin:0; border:1px solid black;"><div>{total}</div></div>'
            )
        self.page_label.setText(html)


    def set_total_pages(self, n: int):
        self._page_total = max(0, n)
        self._update_label()

    def set_current_page(self, page_zero_based: int):
        self._current_page = max(0, page_zero_based)
        self._update_label()

# ---------- Main Window ----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Fast PDF Viewer")
        self.resize(1280, 800)

        # Central splitter: left tabs | center | right toolbar
        self.splitter = QSplitter()
        self.splitter.setChildrenCollapsible(False)

        # Left Tabs
        self.tabs = QTabWidget()
        self.toc_panel = TocPanel()
        self.files_panel = FilesPanel()
        self.files_panel.fileActivated.connect(self.open_pdf)
        self.files_panel.rootChanged.connect(self._persist_root)
        self.toc_panel.jumpToDestination.connect(lambda page, point, zoom: self.center.view.pageNavigator().jump(page, point, zoom))

        # Tab order: Sheet 2 primary → Files first, TOC second
        self.tabs.addTab(self.files_panel, "Files")
        self.tabs.addTab(self.toc_panel, "TOC")
        self.tabs.setCurrentIndex(0)

        # Center PDF stack
        self.center = CenterPdfView()
        self.center.pageChanged.connect(self._sync_current_page)
        self.center.documentLoaded.connect(self._after_document_loaded)

        # Right toolbar
        self.tools = RightToolbar()
        self.tools.goPrev.connect(self.center.prev_page)
        self.tools.goNext.connect(self.center.next_page)
        self.tools.requestPageJump.connect(self.center.go_to_page)
        self.tools.zoomIn.connect(self._zoom_in)
        self.tools.zoomOut.connect(self._zoom_out)
        self.tools.fitWidth.connect(self._fit_width)
        self.tools.fitHeight.connect(self._fit_height)

        # Put into splitter
        self.splitter.addWidget(self.tabs)
        self.splitter.addWidget(self.center)
        self.splitter.addWidget(self.tools)

        # Reasonable initial sizes
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setStretchFactor(2, 0)

        # No menu bar, no status bar (height is precious!)
        w = QWidget()
        w.setLayout(QHBoxLayout())
        w.layout().setContentsMargins(0, 0, 0, 0)
        w.layout().addWidget(self.splitter)
        self.setCentralWidget(w)

        # Shortcuts
        self._install_shortcuts()

        # Initial UI state
        self._update_toc_tab_color(has_toc=False)
        self._apply_fit_height_default()
        self._load_window_settings()

    # ------ Window state persistence ------
    def _load_window_settings(self):
        """Restore window geometry/state from JSON file."""
        if not WINDOW_STATE_FILE.exists():
            self.showMaximized()
            return

        try:
            data = json.loads(WINDOW_STATE_FILE.read_text(encoding="utf-8"))
            geom = data.get("geometry")
            is_max = data.get("is_maximized", False)

            if geom and len(geom) == 4:
                x, y, w, h = geom
                self.setGeometry(x, y, w, h)
            if is_max:
                self.showMaximized()
            else:
                self.showNormal()
        except Exception as e:
            print("Failed to restore window state:", e)
            self.showMaximized()

    def _save_window_settings(self):
        """Save window geometry/state to JSON file."""
        try:
            is_max = self.isMaximized()
            if is_max:
                self.showNormal()
                geom = [self.x(), self.y(), self.width(), self.height()]
                self.showMaximized()
            else:
                geom = [self.x(), self.y(), self.width(), self.height()]

            data = {"geometry": geom, "is_maximized": is_max}
            WINDOW_STATE_FILE.write_text(json.dumps(data), encoding="utf-8")
        except Exception as e:
            print("Failed to save window state:", e)



    # ------ File I/O ------
    def _persist_root(self, root: str):
        try:
            LAST_ROOT_FILE.write_text(root, encoding="utf-8")
        except Exception:
            pass

    def open_pdf(self, path: str | None = None):
        if not path:
            path, _ = QFileDialog.getOpenFileName(self, "Open PDF", str(Path.home()), "PDF files (*.pdf)")
            if not path:
                return

        ok = self.center.load_file(path)
        if ok:
            self.toc_panel.set_document(self.center.doc)
            self._update_toc_tab_color(self.toc_panel.has_toc())
            self._sync_total_pages()
        else:
            self._update_toc_tab_color(False)
            self.tools.set_total_pages(0)

    # ------ Toolbar sync ------
    def _sync_total_pages(self):
        self.tools.set_total_pages(self.center.page_count())

    def _sync_current_page(self, page_zero_based: int):
        self.tools.set_current_page(page_zero_based)

    def _zoom_in(self):
        self.center.custom_zoom()
        self.center.set_zoom_factor(self.center.zoom_factor() * 1.10)

    def _zoom_out(self):
        self.center.custom_zoom()
        self.center.set_zoom_factor(self.center.zoom_factor() / 1.10)

    def _fit_width(self):
        self.center.fit_width()

    def _fit_height(self):
        self.center.fit_height()

    def _apply_fit_height_default(self):
        self._fit_height()

    def _after_document_loaded(self):
        self._sync_total_pages()
        self._sync_current_page(self.center.current_page())

    # ------ TOC tab color ------
    def _update_toc_tab_color(self, has_toc: bool):
        idx = 1  # TOC tab index
        color = "#2e7d32" if has_toc else "#777777"
        self.tabs.tabBar().setTabTextColor(idx, QColor_from_hex(color))

    # ------ Shortcuts ------
    def _install_shortcuts(self):
        def add_short(seq, slot):
            act = QAction(self)
            act.setShortcut(QKeySequence(seq))
            act.triggered.connect(slot)
            self.addAction(act)

        add_short("Ctrl+O", self.open_pdf)
        add_short("Ctrl+=", self._zoom_in)
        add_short("Ctrl++", self._zoom_in)  # some keyboards
        add_short("Ctrl+-", self._zoom_out)
        add_short("Ctrl+0", self._fit_width)
        add_short("Ctrl+9", self._fit_height)

        add_short(Qt.Key.Key_PageDown, self.center.next_page)
        add_short(Qt.Key.Key_PageUp, self.center.prev_page)
        add_short(Qt.Key.Key_Home, lambda: self.center.go_to_page(0))
        add_short(Qt.Key.Key_End, lambda: self.center.go_to_page(max(0, self.center.page_count()-1)))
        add_short(Qt.Key.Key_F2, lambda: self.tools.page_edit.setFocus())

    # ------ Overrides ------
    def keyPressEvent(self, e):
        # Allow bare +/- to zoom too
        if e.key() in (Qt.Key.Key_Plus, Qt.Key.Key_Equal):
            self._zoom_in(); e.accept(); return
        if e.key() == Qt.Key.Key_Minus:
            self._zoom_out(); e.accept(); return
        super().keyPressEvent(e)

    def closeEvent(self, event):
        self._save_window_settings()
        super().closeEvent(event)

# ---------- main ----------
def main():
    QApplication.setApplicationName(APP_NAME)
    QApplication.setOrganizationName(ORG_NAME)
    app = QApplication(sys.argv)

    win = MainWindow()

    from PyQt6.QtCore import QTimer
    QTimer.singleShot(0, win._load_window_settings)

    win.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

