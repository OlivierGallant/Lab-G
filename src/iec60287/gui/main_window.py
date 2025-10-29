from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QAction, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDockWidget,
    QMainWindow,
    QStatusBar,
    QToolBar,
    QVBoxLayout,
    QWidget,
)

from iec60287.gui.placement_scene import PlacementScene
from iec60287.gui.view import PlacementView
from iec60287.gui.items import CableSystemItem
from iec60287.gui.system_editor import CableSystemEditor


class MainWindow(QMainWindow):
    """Main application window hosting the placement scene."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("IEC 60287 Cable Layout")
        self.resize(1200, 800)

        self.scene = PlacementScene()
        self.view = PlacementView(self.scene)

        container = QWidget()
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        container.setLayout(layout)
        self.setCentralWidget(container)

        self._system_editor = CableSystemEditor(self)
        self._editor_dock = self._create_editor_dock()

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        self._create_toolbar()
        self._create_menus()
        self._create_shortcuts()
        self._seed_scene()
        self._update_status()
        self._handle_selection_changed()

        self.scene.selectionChanged.connect(self._handle_selection_changed)

    def _create_toolbar(self) -> None:
        toolbar = QToolBar("Placement")
        toolbar.setMovable(False)
        toolbar.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.addToolBar(Qt.TopToolBarArea, toolbar)

        add_cable_action = QAction("Add Cable", self)
        add_cable_action.setShortcut(QKeySequence("Ctrl+Shift+C"))
        add_cable_action.triggered.connect(self._handle_add_cable)
        toolbar.addAction(add_cable_action)

        add_backfill_action = QAction("Add Backfill", self)
        add_backfill_action.setShortcut(QKeySequence("Ctrl+Shift+B"))
        add_backfill_action.triggered.connect(self._handle_add_backfill)
        toolbar.addAction(add_backfill_action)

        toolbar.addSeparator()

        delete_action = QAction("Delete Selected", self)
        delete_action.setShortcut(QKeySequence.Delete)
        delete_action.triggered.connect(self._handle_delete_selected)
        toolbar.addAction(delete_action)

        fit_action = QAction("Fit View", self)
        fit_action.setShortcut(QKeySequence("Ctrl+0"))
        fit_action.triggered.connect(self._fit_view)
        toolbar.addAction(fit_action)

        self._actions = {
            "add_cable": add_cable_action,
            "add_backfill": add_backfill_action,
            "delete": delete_action,
            "fit": fit_action,
        }

    def _create_menus(self) -> None:
        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction(self._editor_dock.toggleViewAction())

    def _create_shortcuts(self) -> None:
        delete_shortcut = QShortcut(QKeySequence.Delete, self)
        delete_shortcut.activated.connect(self._handle_delete_selected)

        add_cable_shortcut = QShortcut(QKeySequence("N"), self)
        add_cable_shortcut.activated.connect(self._handle_add_cable)

    def _create_editor_dock(self) -> QDockWidget:
        dock = QDockWidget("Cable System Editor", self)
        dock.setObjectName("CableSystemEditorDock")
        dock.setWidget(self._system_editor)
        dock.setAllowedAreas(Qt.BottomDockWidgetArea)
        dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        dock.setMinimumHeight(320)
        dock.setMaximumHeight(420)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.resizeDocks([dock], [360], Qt.Vertical)
        return dock

    def _seed_scene(self) -> None:
        center = QPointF(0.0, 0.0)
        self.scene.add_backfill(center)
        self.scene.add_cable(center)
        self._fit_view()

    def _scene_center(self) -> QPointF:
        rect = self.view.viewport().rect()
        return self.view.mapToScene(rect.center())

    def _handle_add_cable(self) -> None:
        self.scene.add_cable(self._scene_center())
        self._handle_selection_changed()

    def _handle_add_backfill(self) -> None:
        self.scene.add_backfill(self._scene_center())
        self._update_status()

    def _handle_delete_selected(self) -> None:
        if self.scene.selectedItems():
            self.scene.remove_selected()
            self._handle_selection_changed()

    def _fit_view(self) -> None:
        rect = self.scene.sceneRect()
        self.view.fitInView(rect, Qt.KeepAspectRatio)

    def _update_status(self) -> None:
        item_count = len(self.scene.items())
        selected = len(self.scene.selectedItems())
        systems = len(self.scene.systems())
        message = f"Systems: {systems} | Items: {item_count} | Selected: {selected}"
        self._status_bar.showMessage(message)

    def _handle_selection_changed(self) -> None:
        selected = next(
            (item for item in self.scene.selectedItems() if isinstance(item, CableSystemItem)),
            None,
        )
        self._system_editor.set_item(selected)
        self._update_status()
