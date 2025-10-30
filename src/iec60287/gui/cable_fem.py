from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from iec60287.fem import (
    CableFemAnalyzer,
    CableFemResult,
    MeshBuildOutput,
    MeshCableDefinition,
    build_structured_mesh,
)
from iec60287.gui.placement_scene import PlacementScene


@dataclass
class FemCableEntry:
    label: str
    x_mm: float
    y_mm: float
    radius_mm: float
    heat_w_per_m: float


class CableFEMPanel(QWidget):
    """Docked panel providing a simple 2D FEM-style thermal analysis."""

    _TABLE_HEADERS = ["Cable", "Radius (mm)", "X (mm)", "Y (mm)", "Heat (W/m)"]
    _RESULT_HEADERS = ["Cable", "Max Temp (°C)", "Avg Temp (°C)"]

    def __init__(self, scene: PlacementScene, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._scene = scene
        self._entries: List[FemCableEntry] = []
        self._user_heat_overrides: Dict[str, float] = {}
        self._soil_user_override = False
        self._missing_current_labels: List[str] = []
        self._mesh_output: Optional[MeshBuildOutput] = None

        self._ambient_spin = QDoubleSpinBox(self)
        self._soil_resistivity_spin = QDoubleSpinBox(self)
        self._grid_step_spin = QDoubleSpinBox(self)
        self._padding_spin = QDoubleSpinBox(self)
        self._max_iterations_spin = QSpinBox(self)
        self._tolerance_spin = QDoubleSpinBox(self)

        self._cable_table = QTableWidget(self)
        self._result_table = QTableWidget(self)
        self._status_label = QLabel(self)
        self._refresh_button = QPushButton("Refresh from Scene", self)
        self._run_button = QPushButton("Run FEM Analysis", self)

        self._build_ui()
        self._wire_signals()
        self.refresh_from_scene(force=True)

    # ------------------------------------------------------------------ setup
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)

        layout.addWidget(self._build_parameters_group())
        layout.addWidget(self._build_cable_group())
        layout.addWidget(self._build_results_group())

        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        button_row.addWidget(self._refresh_button)
        button_row.addWidget(self._run_button)
        button_row.addStretch()
        layout.addLayout(button_row)

        self._status_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._status_label.setWordWrap(True)
        layout.addWidget(self._status_label)

    def _build_parameters_group(self) -> QGroupBox:
        group = QGroupBox("Simulation Parameters", self)
        form = QFormLayout(group)
        form.setFormAlignment(Qt.AlignLeft)
        form.setLabelAlignment(Qt.AlignLeft)

        self._ambient_spin.setRange(-50.0, 200.0)
        self._ambient_spin.setDecimals(1)
        self._ambient_spin.setSuffix(" °C")
        self._ambient_spin.setValue(20.0)

        self._soil_resistivity_spin.setRange(0.05, 6.0)
        self._soil_resistivity_spin.setDecimals(3)
        self._soil_resistivity_spin.setSingleStep(0.05)
        self._soil_resistivity_spin.setSuffix(" K·m/W")
        self._soil_resistivity_spin.setValue(1.0)

        self._grid_step_spin.setRange(5.0, 200.0)
        self._grid_step_spin.setDecimals(0)
        self._grid_step_spin.setSuffix(" mm")
        self._grid_step_spin.setValue(25.0)

        self._padding_spin.setRange(50.0, 5000.0)
        self._padding_spin.setDecimals(0)
        self._padding_spin.setSuffix(" mm")
        self._padding_spin.setValue(500.0)

        self._max_iterations_spin.setRange(100, 20000)
        self._max_iterations_spin.setValue(5000)

        self._tolerance_spin.setRange(1e-5, 1.0)
        self._tolerance_spin.setDecimals(5)
        self._tolerance_spin.setSingleStep(0.0005)
        self._tolerance_spin.setValue(0.001)

        form.addRow("Ambient temperature", self._ambient_spin)
        form.addRow("Soil thermal ρ", self._soil_resistivity_spin)
        form.addRow("Grid spacing", self._grid_step_spin)
        form.addRow("Domain padding", self._padding_spin)
        form.addRow("Max iterations", self._max_iterations_spin)
        form.addRow("Convergence tolerance", self._tolerance_spin)
        return group

    def _build_cable_group(self) -> QGroupBox:
        group = QGroupBox("Cable Heat Inputs", self)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._cable_table.setColumnCount(len(self._TABLE_HEADERS))
        self._cable_table.setHorizontalHeaderLabels(self._TABLE_HEADERS)
        self._cable_table.verticalHeader().setVisible(False)
        self._cable_table.setAlternatingRowColors(True)
        self._cable_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._cable_table.setFocusPolicy(Qt.NoFocus)
        self._cable_table.setSelectionMode(QTableWidget.NoSelection)
        self._cable_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._cable_table)
        return group

    def _build_results_group(self) -> QGroupBox:
        group = QGroupBox("Results", self)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        self._result_table.setColumnCount(len(self._RESULT_HEADERS))
        self._result_table.setHorizontalHeaderLabels(self._RESULT_HEADERS)
        self._result_table.verticalHeader().setVisible(False)
        self._result_table.setAlternatingRowColors(True)
        self._result_table.setEditTriggers(QTableWidget.NoEditTriggers)
        self._result_table.setSelectionMode(QTableWidget.NoSelection)
        self._result_table.setFocusPolicy(Qt.NoFocus)
        self._result_table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self._result_table)
        return group

    def _wire_signals(self) -> None:
        self._refresh_button.clicked.connect(self.refresh_from_scene)
        self._run_button.clicked.connect(self._handle_run_clicked)
        self._soil_resistivity_spin.valueChanged.connect(self._handle_soil_changed)

    # ---------------------------------------------------------------- refresh
    def refresh_from_scene(self, force: bool = False) -> None:
        try:
            mesh_output = build_structured_mesh(
                self._scene,
                grid_step_mm=self._grid_step_spin.value(),
                padding_mm=self._padding_spin.value(),
                default_resistivity_k_m_per_w=self._soil_resistivity_spin.value(),
            )
        except ValueError as exc:
            self._mesh_output = None
            self._entries = []
            self._populate_cable_table()
            self._status_label.setText(f"Unable to build FEM mesh: {exc}")
            return

        self._mesh_output = mesh_output
        self._rebuild_entries(mesh_output, preserve_overrides=not force)
        if force or not self._soil_user_override:
            soil_rho = self._infer_soil_resistivity()
            if soil_rho is not None:
                self._soil_resistivity_spin.blockSignals(True)
                self._soil_resistivity_spin.setValue(soil_rho)
                self._soil_resistivity_spin.blockSignals(False)
        message = f"Loaded {len(self._entries)} cable phases for FEM analysis."
        if self._missing_current_labels:
            message += " Set operating current in the Cable System Editor to auto-compute heat."
        self._status_label.setText(message)

    def _rebuild_entries(self, mesh_output: MeshBuildOutput, preserve_overrides: bool) -> None:
        overrides = dict(self._user_heat_overrides) if preserve_overrides else {}
        entries: List[FemCableEntry] = []
        missing_labels: List[str] = []

        for cable in mesh_output.cables:
            radius = cable.overall_radius_mm
            default_heat = self._estimate_heat_from_definition(cable)
            if default_heat is None:
                missing_labels.append(cable.label)
            heat_value = overrides.get(cable.label, default_heat if default_heat is not None else 0.0)
            entries.append(
                FemCableEntry(
                    label=cable.label,
                    x_mm=cable.centre_x_mm,
                    y_mm=cable.centre_y_mm,
                    radius_mm=radius,
                    heat_w_per_m=heat_value,
                )
            )

        self._entries = entries
        self._missing_current_labels = missing_labels
        self._user_heat_overrides = {entry.label: entry.heat_w_per_m for entry in entries}
        self._populate_cable_table()

    def _populate_cable_table(self) -> None:
        self._cable_table.setRowCount(len(self._entries))

        for row, entry in enumerate(self._entries):
            self._set_item(row, 0, entry.label)
            self._set_item(row, 1, f"{entry.radius_mm:.1f}")
            self._set_item(row, 2, f"{entry.x_mm:.1f}")
            self._set_item(row, 3, f"{entry.y_mm:.1f}")

            spin = QDoubleSpinBox(self._cable_table)
            spin.setRange(0.0, 5000.0)
            spin.setDecimals(1)
            spin.setSingleStep(10.0)
            spin.blockSignals(True)
            spin.setValue(entry.heat_w_per_m)
            spin.blockSignals(False)
            spin.valueChanged.connect(partial(self._handle_heat_changed, row, entry.label))
            self._cable_table.setCellWidget(row, 4, spin)

        self._cable_table.resizeColumnsToContents()
        self._cable_table.horizontalHeader().setStretchLastSection(True)

    def _set_item(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled)
        self._cable_table.setItem(row, column, item)

    # -------------------------------------------------------------- simulation
    def _handle_run_clicked(self) -> None:
        try:
            mesh_output = build_structured_mesh(
                self._scene,
                grid_step_mm=self._grid_step_spin.value(),
                padding_mm=self._padding_spin.value(),
                default_resistivity_k_m_per_w=self._soil_resistivity_spin.value(),
            )
        except ValueError as exc:
            QMessageBox.critical(self, "Cable FEM", str(exc))
            return

        self._mesh_output = mesh_output
        self._rebuild_entries(mesh_output, preserve_overrides=True)

        if not self._entries:
            QMessageBox.information(self, "Cable FEM", "No cables available for analysis.")
            return

        analyzer = CableFemAnalyzer(
            max_iterations=self._max_iterations_spin.value(),
            tolerance_c=self._tolerance_spin.value(),
        )

        try:
            result = analyzer.solve(
                mesh_output.mesh,
                [entry.heat_w_per_m for entry in self._entries],
                ambient_temp_c=self._ambient_spin.value(),
                cable_definitions=mesh_output.cables,
            )
        except ValueError as exc:
            QMessageBox.critical(self, "Cable FEM", str(exc))
            return

        self._populate_results(result)

    def _populate_results(self, result: CableFemResult) -> None:
        self._result_table.setRowCount(len(result.cable_temperatures))
        for row, temp in enumerate(result.cable_temperatures):
            self._set_result_item(row, 0, temp.label)
            self._set_result_item(row, 1, f"{temp.max_temp_c:.2f}")
            self._set_result_item(row, 2, f"{temp.average_temp_c:.2f}")

        info = (
            f"Iterations: {result.iterations} ({'converged' if result.converged else 'max iterations reached'}). "
            f"Field min/max: {result.min_temp_c:.2f}°C / {result.max_temp_c:.2f}°C."
        )
        self._status_label.setText(info)

    def _set_result_item(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled)
        self._result_table.setItem(row, column, item)

    # ----------------------------------------------------------------- helpers
    def _infer_soil_resistivity(self) -> Optional[float]:
        layers = self._scene.config.layers
        if not layers:
            return None
        return max(layers[0].thermal_resistivity_k_m_per_w, 0.05)

    def _handle_heat_changed(self, row: int, label: str, value: float) -> None:
        if 0 <= row < len(self._entries):
            self._entries[row].heat_w_per_m = value
            self._user_heat_overrides[label] = value
        else:
            self._user_heat_overrides[label] = value

    def _handle_soil_changed(self, _: float) -> None:
        self._soil_user_override = True

    # ----------------------------------------------------------- estimations
    def _estimate_heat_from_definition(self, cable: MeshCableDefinition) -> Optional[float]:
        current = cable.nominal_current_a
        if current is None or current <= 0.0:
            return None
        resistance = self._conductor_resistance_from_definition(cable, temperature_c=self._target_conductor_temp())
        if resistance is None:
            return None
        return (current ** 2) * resistance

    def _conductor_resistance_from_definition(
        self,
        cable: MeshCableDefinition,
        *,
        temperature_c: float,
    ) -> Optional[float]:
        area = max(cable.conductor_area_mm2, 0.0)
        resistivity = cable.conductor_resistivity_ohm_mm2_per_m
        if area <= 0.0 or resistivity is None or resistivity <= 0.0:
            return None
        alpha = cable.conductor_temp_coefficient_per_c or 0.0
        rho_theta = resistivity * (1.0 + alpha * (temperature_c - 20.0))
        resistance = rho_theta / area
        return resistance if resistance > 0.0 else None

    def _target_conductor_temp(self) -> float:
        return 90.0
