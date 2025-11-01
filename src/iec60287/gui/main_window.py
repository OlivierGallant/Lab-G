from __future__ import annotations

from PySide6.QtCore import QPointF, Qt, QUrl
from PySide6.QtGui import QAction, QDesktopServices, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDockWidget,
    QMessageBox,
    QMenu,
    QMainWindow,
    QStatusBar,
    QToolBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from iec60287.gui.placement_scene import PlacementScene
from iec60287.gui.view import PlacementView
from iec60287.gui.items import CableSystemItem
from iec60287.gui.system_editor import CableSystemEditor
from iec60287.gui.trench_designer import TrenchDesigner
from iec60287.gui.ampacity_calculator import CableAmpacityCalculator
from iec60287.gui.cable_fem import CableFEMPanel


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
        self._trench_designer = TrenchDesigner(self.scene, self)
        self._ampacity_calculator = CableAmpacityCalculator(self.scene, self)
        self._fem_panel = CableFEMPanel(self.scene, self)
        self._open_fem_report_action = QAction("FEM Report", self)
        self._open_fem_report_action.triggered.connect(self._open_latest_fem_report)
        self._reports_menu = QMenu("Reports", self)
        self._reports_menu.addAction(self._open_fem_report_action)
        self._editor_dock = self._create_editor_dock()
        self._trench_dock = self._create_trench_dock()
        self._calculator_dock = self._create_calculator_dock()
        self._fem_dock = self._create_fem_dock()

        self._status_bar = QStatusBar()
        self.setStatusBar(self._status_bar)

        self._create_toolbar()
        self._create_menus()
        self._create_shortcuts()
        self._seed_scene()
        self._update_status()
        self._handle_selection_changed()
        self.scene.changed.connect(self._handle_scene_changed)
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

        toolbar.addSeparator()

        delete_action = QAction("Delete Selected", self)
        delete_action.setShortcut(QKeySequence.Delete)
        delete_action.triggered.connect(self._handle_delete_selected)
        toolbar.addAction(delete_action)

        fit_action = QAction("Fit View", self)
        fit_action.setShortcut(QKeySequence("Ctrl+0"))
        fit_action.triggered.connect(self._fit_view)
        toolbar.addAction(fit_action)

        toolbar.addSeparator()

        reports_button = QToolButton(self)
        reports_button.setText("Reports")
        reports_button.setPopupMode(QToolButton.InstantPopup)
        reports_button.setToolButtonStyle(Qt.ToolButtonTextOnly)
        reports_button.setMenu(self._reports_menu)
        toolbar.addWidget(reports_button)

        self._actions = {
            "add_cable": add_cable_action,
            "delete": delete_action,
            "fit": fit_action,
            "fem_report": self._open_fem_report_action,
        }

    def _create_menus(self) -> None:
        tools_menu = self.menuBar().addMenu("&Tools")
        tools_menu.addAction(self._editor_dock.toggleViewAction())
        tools_menu.addAction(self._trench_dock.toggleViewAction())
        calculations_menu = self.menuBar().addMenu("&Calculations")
        calculations_menu.addAction(self._calculator_dock.toggleViewAction())
        calculations_menu.addAction(self._fem_dock.toggleViewAction())
        self.menuBar().addMenu(self._reports_menu)
        about_menu = self.menuBar().addMenu("&About")
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

    def _create_trench_dock(self) -> QDockWidget:
        dock = QDockWidget("Trench Designer", self)
        dock.setObjectName("TrenchDesignerDock")
        dock.setWidget(self._trench_designer)
        dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.RightDockWidgetArea)
        dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        dock.setMinimumHeight(260)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.tabifyDockWidget(self._editor_dock, dock)
        dock.raise_()
        return dock

    def _create_calculator_dock(self) -> QDockWidget:
        dock = QDockWidget("Cable Ampacity Calculator", self)
        dock.setObjectName("CableAmpacityCalculatorDock")
        dock.setWidget(self._ampacity_calculator)
        dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.RightDockWidgetArea)
        dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        dock.setMinimumHeight(260)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.tabifyDockWidget(self._editor_dock, dock)
        return dock

    def _create_fem_dock(self) -> QDockWidget:
        dock = QDockWidget("Cable FEM", self)
        dock.setObjectName("CableFEMDock")
        dock.setWidget(self._fem_panel)
        dock.setAllowedAreas(Qt.BottomDockWidgetArea | Qt.RightDockWidgetArea)
        dock.setFeatures(QDockWidget.DockWidgetClosable | QDockWidget.DockWidgetMovable)
        dock.setMinimumHeight(260)
        self.addDockWidget(Qt.BottomDockWidgetArea, dock)
        self.tabifyDockWidget(self._calculator_dock, dock)
        return dock

    def _seed_scene(self) -> None:
        center = QPointF(0.0, 0.0)
        self.scene.add_cable(center)
        self._fit_view()
        self._trench_designer.refresh_systems()
        self._refresh_calculators(force_fem=True)

    def _scene_center(self) -> QPointF:
        rect = self.view.viewport().rect()
        return self.view.mapToScene(rect.center())

    def _handle_add_cable(self) -> None:
        self.scene.add_cable(self._scene_center())
        self._handle_selection_changed()
        self._trench_designer.refresh_systems()
        self._refresh_calculators()

    def _handle_delete_selected(self) -> None:
        if self.scene.selectedItems():
            self.scene.remove_selected()
            self._handle_selection_changed()
            self._trench_designer.refresh_systems()
            self._refresh_calculators()

    def _fit_view(self) -> None:
        rect = self.scene.sceneRect()
        self.view.fitInView(rect, Qt.KeepAspectRatio)

    def _open_latest_fem_report(self) -> None:
        path = self._fem_panel.latest_heatmap_path()
        if not path:
            QMessageBox.information(
                self,
                "FEM Report",
                "No FEM heatmap is available yet. Run the FEM analysis first.",
            )
            return
        if not path.exists():
            QMessageBox.warning(
                self,
                "FEM Report",
                f"The FEM heatmap file at {path} is no longer available.",
            )
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path))):
            QMessageBox.warning(
                self,
                "FEM Report",
                "Unable to open the FEM heatmap with the system viewer.",
            )

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
        self._trench_designer.set_selected_item(selected)
        self._trench_designer.refresh_systems()
        self._refresh_calculators()
        self._update_status()

    def _handle_scene_changed(self, *_) -> None:
        self._refresh_calculators()

    def _refresh_calculators(self, *, force_fem: bool = False) -> None:
        self._ampacity_calculator.refresh_from_scene()
        self._fem_panel.refresh_from_scene(force=force_fem)
