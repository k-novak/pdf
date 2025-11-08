import sys, subprocess
from pathlib import Path

from PyQt6.QtPdf import *
from PyQt6.QtPdfWidgets import *
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *

APP_NAME = "Pdf Viewer"

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
class PdfExplorerWidget(QTreeView):
    def __init__(self, root_path, parent=None):
        super().__init__(parent)

        self.setSelectionMode(QTreeView.SelectionMode.ExtendedSelection)
        
        self.fs_model = QFileSystemModel()

        self.proxy_model = PdfFilterProxyModel()
        self.proxy_model.setSourceModel(self.fs_model)
        self.proxy_model.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.proxy_model.sort(0, Qt.SortOrder.AscendingOrder)

        self.setModel(self.proxy_model)
        self.setSortingEnabled(True)
        self.setUniformRowHeights(True)
        self.sortByColumn(0, Qt.SortOrder.AscendingOrder)

        self.setColumnHidden(1, True)
        self.setColumnHidden(2, True)
        self.setColumnHidden(3, True)

        self.setAlternatingRowColors(True)

        self.setStyleSheet("""
            QTreeView {
                alternate-background-color: #E5E4E2;
                outline: none; /* remove black border around items */
            }                          

            QTreeView::item:hover {
                color: black;
                background-color: #99C2FF;
            }

            QTreeView::item:selected {
                color: black;
                background-color: #4D94FF;
            }

            QTreeView::item:selected:hover {
                color: black;
                background-color: #1A75FF;
            }
        """)

        # ---- Context menu ----
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self.on_customContextMenuRequested)

        # ---- Populate the view ----
        self.setRootPath(root_path)

    def on_customContextMenuRequested(self, position: QPoint):

        proxy_index = self.indexAt(position)
        if not proxy_index.isValid(): return

        selected_files = []
        for proxy_index in self.selectionModel().selectedRows():
            source_index = self.proxy_model.mapToSource(proxy_index)
            path = self.fs_model.filePath(source_index)
            if Path(path).is_dir(): continue
            selected_files.append(path)

        if selected_files == []: return

        # ---- Create menu ----
        menu = QMenu(self)

        if len(selected_files) == 1:
            menu.addAction("Open in system viewer", lambda: self._openFile(selected_files[0]))
            menu.addAction("Reveal in Explorer", lambda: self._revealInExplorer(selected_files[0]))
            menu.addAction("Rename", lambda: self._renameFile(selected_files[0]))

        copy_action = QAction("Copy", self)
        copy_action.triggered.connect(lambda: self._copyOnClipboard(selected_files))
        copy_action.setShortcut(QKeySequence.StandardKey.Copy)
        self.addAction(copy_action)
        menu.addAction(copy_action)

        menu.addAction("Delete", lambda: self._deleteFile(selected_files))
        
        menu.exec(self.viewport().mapToGlobal(position))

    def _copyOnClipboard(self, file_paths):
        assert isinstance(file_paths, list), "file_paths should be a list"

        clipboard = QApplication.clipboard()
        clipboard.clear()
        mime_data = QMimeData()
        mime_data.setUrls([QUrl.fromLocalFile(file_path) for file_path in file_paths])
        clipboard.setMimeData(mime_data)

    def _openFile(self, file_path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))

    def _revealInExplorer(self, file_path):
        subprocess.run(f'explorer.exe /select,{Path(file_path)}')

    def _deleteFile(self, file_paths):
        assert isinstance(file_paths, list), "file_paths should be a list"

        result = QMessageBox.warning(self, "Delete File", f"Are you sure you want to delete:\n{'\n'.join(file_paths)}", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        if result == QMessageBox.StandardButton.No: return
        for file_path in file_paths:
            try:
                Path(file_path).unlink()
            except PermissionError as e:
                QMessageBox.warning(self, "Error", f"Could not delete file:\n{file_path}\n\n{str(e)}") 

    def _renameFile(self, file_path):
        old_path = Path(file_path)
        new_stem, ok = QInputDialog.getText(
            self,
            "Rename File",
            "Enter new name:",
            QLineEdit.EchoMode.Normal,
            old_path.stem,
        )
        new_stem = new_stem.strip()
        if not ok or not new_stem: return

        new_path = old_path.with_stem(new_stem)
        try:
            old_path.rename(new_path)
        except PermissionError as e:
            QMessageBox.warning(self, "Error", f"Could not rename file:\n{file_path}\n\n{str(e)}")

    def setRootPath(self, root_path):
        if not Path(root_path).exists():
            raise FileNotFoundError(f"Root path not found: {root_path}")
        self.fs_model.setRootPath(root_path)
        self.setRootIndex(self.proxy_model.mapFromSource(self.fs_model.index(root_path)))

# ---------- PDF Viewer -----------------
class PdfView(QPdfView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self.pdfDoc = QPdfDocument(self)    

        self.setDocument(self.pdfDoc)
        self.setPageMode(QPdfView.PageMode.MultiPage)
        self.setZoomMode(QPdfView.ZoomMode.FitInView)

        # --- Define widget-level actions ---
        jumpHomeAction = QAction("First Page", self)
        jumpHomeAction.setShortcut(QKeySequence(Qt.Key.Key_Home))
        jumpHomeAction.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        jumpHomeAction.triggered.connect(lambda: self.pageNavigator().jump(0, QPointF(0, 0)))
        self.addAction(jumpHomeAction)

        jumpEndAction = QAction("Last Page", self)
        jumpEndAction.setShortcut(QKeySequence(Qt.Key.Key_End))
        jumpEndAction.setShortcutContext(Qt.ShortcutContext.WidgetShortcut)
        jumpEndAction.triggered.connect(lambda: self.pageNavigator().jump(self.pdfDoc.pageCount() - 1, QPointF(0, 0)))
        self.addAction(jumpEndAction)

# ---------- TOC Widget ----------
class TocTree(QTreeView):
    def __init__(self, parent=None):
        super().__init__(parent)

        self.model = QPdfBookmarkModel(self)
        self.setHeaderHidden(True)
        self.setModel(self.model)

# ---------- Main Window ----------
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        # ---- window ----
        self.setWindowTitle(APP_NAME)
                
        # ---- view ----
        self.pdfView = PdfView(self)

        # ---- file browser ----
        # TODO: add path to favorites, select favorite to set root
        self.fileView = PdfExplorerWidget('C:/Users/krisz/Downloads')

        # ---- TOC ----
        self.tocView = TocTree()
        self.tocView.model.setDocument(self.pdfView.pdfDoc)

        # ---- favorites ----
        self.favorites = QListWidget()
        self._loadFavorites()

        # ---- current page/toltal pages
        self.pages = QLabel('- / -')
        self.pages.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.pages.setStyleSheet("font-weight: bold; font-size: 16pt;")

        # ---- Tabs ----
        self.leftSplitter = QSplitter()
        # self.leftSplitter.setChildrenCollapsible(False)
        self.leftSplitter.setOrientation(Qt.Orientation.Vertical)
        self.leftSplitter.addWidget(self.fileView)
        self.leftSplitter.addWidget(self.tocView)
        self.leftSplitter.addWidget(self.favorites)
        self.leftSplitter.setStretchFactor(0, 5)
        self.leftSplitter.setStretchFactor(1, 5)
        self.leftSplitter.setStretchFactor(2, 1)

        # ---- signals and slots ----
        self.pdfView.pdfDoc.statusChanged.connect(self.on_pdfView_statusChanged)
        self.pdfView.pageNavigator().currentPageChanged.connect(self.on_pdfView_currentPageChanged)
        self.fileView.activated.connect(self.on_fileView_activated)
        self.favorites.itemActivated.connect(self.on_favorites_itemActivated)
        self.tocView.activated.connect(lambda index: self.pdfView.pageNavigator().jump(index.data(QPdfBookmarkModel.Role.Page), index.data(QPdfBookmarkModel.Role.Location)))

        # ---- overall layout of the window ----
        self.leftPane = QWidget()
        self.leftPane.setLayout(QVBoxLayout())
        self.leftPane.layout().setContentsMargins(0, 0, 0, 0)
        self.leftPane.layout().setSpacing(0)
        self.leftPane.layout().addWidget(self.leftSplitter)
        self.leftPane.layout().addWidget(self.pages)

        self.splitter = QSplitter()
        self.setCentralWidget(self.splitter)
        self.splitter.setChildrenCollapsible(False)
        self.splitter.addWidget(self.leftPane)
        self.splitter.addWidget(self.pdfView)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)

        self.setWindowState(Qt.WindowState.WindowMaximized)

    def on_favorites_itemActivated(self, item):
        # TODO: add error handling
        # TODO: connect signal to self.fileView.setRootPath directly?
        folder = item.text()
        self.fileView.setRootPath(folder)

    def on_fileView_activated(self, proxy_index):
        proxy_model = proxy_index.model()
        source_model = proxy_model.sourceModel()
        source_index = proxy_model.mapToSource(proxy_index)
        currentFile = Path(source_model.filePath(source_index))
        if currentFile.is_dir(): return

        self.setWindowTitle(f"{APP_NAME} - {Path(currentFile).name}")
        self.pdfView.pdfDoc.load(str(currentFile))

        self.tocView.expandAll()

    def on_pdfView_statusChanged(self, status):
        if status == QPdfDocument.Status.Ready:
            self.pages.setText(f'{self.pdfView.pageNavigator().currentPage() + 1} / {self.pdfView.pdfDoc.pageCount()}')

    def on_pdfView_currentPageChanged(self, page):
        self.pages.setText(f'{page + 1} / {self.pdfView.pdfDoc.pageCount()}')

    def _loadFavorites(self):
        rows = Path('favorites.txt').read_text(encoding='utf-8').splitlines()
        for row in rows:
            self.favorites.addItem(row)
        
# ---------- main ----------
def main():

    app = QApplication(sys.argv)
    app.setWindowIcon(QIcon("pdf_viewer_icon.ico"))
    win = MainWindow()
    win.showMaximized()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()


# TODO list:
# [ ] select multiple files, but not the folders
# [ ] delete/rename: close file if opened in viewer
# [ ] move files inside the viewer (from download to a specific folder)

# [ ] jump to page dialog (pressing F2) - or change QLabel to QEdit

# [ ] ("Ctrl+O", self.open_pdf)
# [ ] ("Ctrl+=", self._zoom_in)
# [ ] ("Ctrl++", self._zoom_in)  # some keyboards
# [ ] ("Ctrl+-", self._zoom_out)
# [ ] ("Ctrl+0", self._fit_width)
# [ ] ("Ctrl+9", self._fit_height)
# [ ] zoom with wheel

# [ ] search for text

# [ ] add notes to pdf
# [ ] QToolBox with pages to implement collapsible panes

# [ ] icons: prev, next, zoom in, zoom out, fit widht, fit height
# [ ] add favorites, delete favorites, list of favorites -> not important, text file can be edited anytime






