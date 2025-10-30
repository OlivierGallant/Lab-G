from __future__ import annotations

import math
from dataclasses import dataclass
from functools import partial
from typing import Dict, Optional, Sequence

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHeaderView,
    QLabel,
    QLineEdit,
    QScrollArea,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from iec60287.gui.items import CableSystemItem
from iec60287.model import (
    CablePhase,
    CableSystem,
    CableSystemKind,
    ConductorSpec,
    LayerRole,
    LayerSpec,
    Material,
    MaterialClassification,
    SingleCoreArrangement,
    materials as material_catalog,
)


@dataclass
class LayerControl:
    role: LayerRole
    allowed_classes: Sequence[MaterialClassification]
    row: int
    enable: Optional[QCheckBox]
    material_combo: QComboBox
    thickness_spin: QDoubleSpinBox
    rho_e_spin: QDoubleSpinBox
    rho_th_spin: QDoubleSpinBox
    filling_spin: QDoubleSpinBox
    resistance_label: QLabel
    outer_diameter_label: QLabel


@dataclass
class ConductorControl:
    material_combo: QComboBox
    thickness_spin: QDoubleSpinBox
    rho_e_spin: QDoubleSpinBox
    rho_th_spin: QDoubleSpinBox
    filling_spin: QDoubleSpinBox
    resistance_label: QLabel
    outer_diameter_label: QLabel


class CableSystemEditor(QWidget):
    """Form for editing properties of the selected cable system."""

    _CUSTOM_MATERIAL_KEY = "__custom__"
    _ROLE_DEFAULT_THICKNESS = {
        LayerRole.INNER_SCREEN: 1.2,
        LayerRole.OUTER_SCREEN: 1.2,
        LayerRole.INSULATION: 5.0,
        LayerRole.SHEATH: 2.5,
        LayerRole.SERVING: 1.0,
    }
    _DEFAULT_CONDUCTOR_DIAMETER = 17.6
    _DEFAULT_CONDUCTOR_FILLING = 1.0

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_item: Optional[CableSystemItem] = None
        self._updating = False
        self._placeholder = QLabel("Select a cable system to edit.", self)
        self._placeholder.setAlignment(Qt.AlignCenter)

        self._name_edit = QLineEdit(self)
        self._arrangement_combo = QComboBox(self)
        self._phase_spacing_spin = QDoubleSpinBox(self)
        self._nominal_voltage_spin = QDoubleSpinBox(self)
        self._operating_current_spin = QDoubleSpinBox(self)

        self._layer_controls: Dict[LayerRole, LayerControl] = {}
        self._layers_table: Optional[QTableWidget] = None
        self._conductor_row_index: Optional[int] = None
        self._conductor_control: Optional[ConductorControl] = None

        self._overall_diameter_label = QLabel("Overall outer diameter: — mm", self)
        self._overall_diameter_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._connected_item: Optional[CableSystemItem] = None

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._form_container = QWidget()
        self._scroll_area.setWidget(self._form_container)
        self._build_ui()
        self._wire_signals()
        self._update_visibility()

    def set_item(self, item: Optional[CableSystemItem]) -> None:
        """Attach the editor to a cable system item."""
        if self._connected_item:
            try:
                self._connected_item.positionChanged.disconnect(self._handle_item_position_changed)
            except (RuntimeError, TypeError):
                pass
            self._connected_item = None

        self._current_item = item
        if item:
            item.positionChanged.connect(self._handle_item_position_changed)
            self._connected_item = item

        self._populate_fields(item.system if item else None)
        self._update_visibility()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.addWidget(self._placeholder)
        layout.addWidget(self._scroll_area)
        layout.addStretch()

        form_layout = QVBoxLayout(self._form_container)
        form_layout.setContentsMargins(0, 0, 0, 0)

        general_group = QGroupBox("General", self._form_container)
        general_form = QFormLayout(general_group)
        general_form.setLabelAlignment(Qt.AlignLeft)

        self._name_edit.setPlaceholderText("Cable system name")
        general_form.addRow("Name", self._name_edit)

        self._arrangement_combo.addItem("Flat", SingleCoreArrangement.FLAT)
        self._arrangement_combo.addItem("Trefoil", SingleCoreArrangement.TREFOIL)
        general_form.addRow("Arrangement", self._arrangement_combo)

        self._phase_spacing_spin.setRange(1.0, 2000.0)
        self._phase_spacing_spin.setDecimals(1)
        self._phase_spacing_spin.setSuffix(" mm")
        general_form.addRow("Phase spacing", self._phase_spacing_spin)

        self._nominal_voltage_spin.setRange(0.0, 500.0)
        self._nominal_voltage_spin.setDecimals(2)
        self._nominal_voltage_spin.setSuffix(" kV")
        general_form.addRow("Nominal voltage", self._nominal_voltage_spin)

        self._operating_current_spin.setRange(0.0, 5000.0)
        self._operating_current_spin.setDecimals(1)
        self._operating_current_spin.setSuffix(" A")
        general_form.addRow("Operating current", self._operating_current_spin)

        layers_group = QGroupBox("Radial layers", self._form_container)
        layers_layout = QVBoxLayout(layers_group)
        layers_layout.setContentsMargins(6, 6, 6, 6)
        layers_layout.setSpacing(12)

        self._layers_table = QTableWidget(layers_group)
        self._layers_table.setColumnCount(8)
        self._layers_table.setHorizontalHeaderLabels(
            [
                "Layer",
                "Material",
                "Thickness (mm)",
                "ρe (Ω·mm²/m)",
                "ρth (K·m/W)",
                "Filling grade",
                "Resistance (Ω/km)",
                "Outer Ø (mm)",
            ]
        )
        header = self._layers_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self._layers_table.verticalHeader().setVisible(False)
        self._layers_table.setAlternatingRowColors(True)
        self._layers_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._layers_table.setFocusPolicy(Qt.NoFocus)
        self._layers_table.setEditTriggers(QAbstractItemView.NoEditTriggers)

        layers_layout.addWidget(self._layers_table)
        layers_layout.addWidget(self._overall_diameter_label)

        self._add_conductor_row()

        layer_definitions = [
            ("Inner Screen", LayerRole.INNER_SCREEN, (MaterialClassification.SEMICONDUCTIVE,), True),
            ("Insulation", LayerRole.INSULATION, (MaterialClassification.INSULATING,), False),
            ("Outer Screen", LayerRole.OUTER_SCREEN, (MaterialClassification.SEMICONDUCTIVE,), True),
            ("Sheath", LayerRole.SHEATH, (MaterialClassification.PROTECTIVE,), True),
            ("Serving / Jacket", LayerRole.SERVING, (MaterialClassification.PROTECTIVE,), True),
        ]
        for definition in layer_definitions:
            self._add_layer_row(*definition)

        form_layout.addWidget(general_group)
        form_layout.addWidget(layers_group)
        form_layout.addStretch()

    def _add_layer_row(
        self,
        title: str,
        role: LayerRole,
        allowed_classes: Sequence[MaterialClassification],
        optional: bool,
    ) -> None:
        if not self._layers_table:
            return

        row = self._layers_table.rowCount()
        self._layers_table.insertRow(row)

        enable_checkbox: Optional[QCheckBox] = None
        if optional:
            enable_checkbox = QCheckBox(title, self._layers_table)
            enable_checkbox.setChecked(True)
            self._layers_table.setCellWidget(row, 0, enable_checkbox)
        else:
            name_label = QLabel(title, self._layers_table)
            name_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
            self._layers_table.setCellWidget(row, 0, name_label)

        material_combo = QComboBox(self._layers_table)
        material_combo.setMinimumWidth(140)
        self._populate_material_combo(material_combo, allowed_classes)
        self._layers_table.setCellWidget(row, 1, material_combo)

        thickness_spin = QDoubleSpinBox(self._layers_table)
        thickness_spin.setRange(0.0, 50.0)
        thickness_spin.setDecimals(2)
        thickness_spin.setValue(self._ROLE_DEFAULT_THICKNESS.get(role, 1.0))
        self._layers_table.setCellWidget(row, 2, thickness_spin)

        rho_e_spin = QDoubleSpinBox(self._layers_table)
        rho_e_spin.setRange(0.0, 5.0)
        rho_e_spin.setDecimals(5)
        rho_e_spin.setSingleStep(0.0001)
        self._layers_table.setCellWidget(row, 3, rho_e_spin)

        rho_th_spin = QDoubleSpinBox(self._layers_table)
        rho_th_spin.setRange(0.0, 200.0)
        rho_th_spin.setDecimals(2)
        rho_th_spin.setSingleStep(0.05)
        self._layers_table.setCellWidget(row, 4, rho_th_spin)

        filling_spin = QDoubleSpinBox(self._layers_table)
        filling_spin.setRange(0.0, 1.0)
        filling_spin.setDecimals(3)
        filling_spin.setSingleStep(0.01)
        filling_spin.setValue(1.0)
        self._layers_table.setCellWidget(row, 5, filling_spin)

        resistance_label = QLabel("—", self._layers_table)
        resistance_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._layers_table.setCellWidget(row, 6, resistance_label)

        outer_label = QLabel("—", self._layers_table)
        outer_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._layers_table.setCellWidget(row, 7, outer_label)

        control = LayerControl(
            role=role,
            allowed_classes=allowed_classes,
            row=row,
            enable=enable_checkbox,
            material_combo=material_combo,
            thickness_spin=thickness_spin,
            rho_e_spin=rho_e_spin,
            rho_th_spin=rho_th_spin,
            filling_spin=filling_spin,
            resistance_label=resistance_label,
            outer_diameter_label=outer_label,
        )
        self._layer_controls[role] = control
        self._apply_layer_material(control)
        initial_enabled = enable_checkbox.isChecked() if enable_checkbox else True
        self._refresh_layer_enabled_state(control, initial_enabled)

    def _wire_signals(self) -> None:
        self._name_edit.editingFinished.connect(self._apply_general_changes)
        self._arrangement_combo.currentIndexChanged.connect(self._apply_general_changes)
        self._phase_spacing_spin.valueChanged.connect(self._apply_general_changes)
        self._nominal_voltage_spin.valueChanged.connect(self._apply_general_changes)
        self._operating_current_spin.valueChanged.connect(self._apply_general_changes)

        if self._conductor_control:
            cc = self._conductor_control
            cc.material_combo.currentIndexChanged.connect(self._handle_conductor_material_changed)
            cc.thickness_spin.valueChanged.connect(self._handle_conductor_value_changed)
            cc.rho_e_spin.valueChanged.connect(self._handle_conductor_value_changed)
            cc.rho_th_spin.valueChanged.connect(self._handle_conductor_value_changed)
            cc.filling_spin.valueChanged.connect(self._handle_conductor_value_changed)

        for role, control in self._layer_controls.items():
            if control.enable:
                control.enable.toggled.connect(partial(self._handle_layer_enable, role))
            control.material_combo.currentIndexChanged.connect(partial(self._handle_layer_material_changed, role))
            control.thickness_spin.valueChanged.connect(partial(self._handle_layer_value_changed, role))
            control.rho_e_spin.valueChanged.connect(partial(self._handle_layer_value_changed, role))
            control.rho_th_spin.valueChanged.connect(partial(self._handle_layer_value_changed, role))
            control.filling_spin.valueChanged.connect(partial(self._handle_layer_value_changed, role))

    def _populate_fields(self, system: Optional[CableSystem]) -> None:
        self._updating = True
        if system is None:
            self._name_edit.clear()
            self._phase_spacing_spin.setValue(1.0)
            self._nominal_voltage_spin.setValue(0.0)
            self._operating_current_spin.setValue(0.0)
            self._reset_conductor_controls()
            for control in self._layer_controls.values():
                if control.enable:
                    control.enable.blockSignals(True)
                    control.enable.setChecked(False)
                    control.enable.blockSignals(False)
                control.material_combo.blockSignals(True)
                control.material_combo.setCurrentIndex(0)
                control.material_combo.blockSignals(False)
                control.thickness_spin.blockSignals(True)
                control.thickness_spin.setValue(self._ROLE_DEFAULT_THICKNESS.get(control.role, 1.0))
                control.thickness_spin.blockSignals(False)
                control.rho_e_spin.blockSignals(True)
                control.rho_e_spin.setValue(0.0)
                control.rho_e_spin.blockSignals(False)
                control.rho_th_spin.blockSignals(True)
                control.rho_th_spin.setValue(0.0)
                control.rho_th_spin.blockSignals(False)
                control.filling_spin.blockSignals(True)
                control.filling_spin.setValue(1.0)
                control.filling_spin.blockSignals(False)
                enabled = control.enable.isChecked() if control.enable else True
                self._refresh_layer_enabled_state(control, enabled)
                if enabled:
                    self._apply_layer_material(control)
            self._updating = False
            self._recompute_layer_metrics()
            return

        self._name_edit.setText(system.name)
        index = self._arrangement_combo.findData(system.arrangement)
        self._arrangement_combo.setCurrentIndex(index if index >= 0 else 0)

        self._phase_spacing_spin.setValue(max(system.phase_spacing_mm, 1.0))
        self._nominal_voltage_spin.setValue(system.nominal_voltage_kv or 0.0)
        self._operating_current_spin.setValue(system.nominal_current_a or 0.0)

        phase = self._single_core_phase(system)
        if phase:
            self._apply_conductor_from_phase(phase)

            for role, control in self._layer_controls.items():
                existing = phase.get_layer(role)
                enabled = existing is not None if control.enable else True
                if control.enable:
                    control.enable.blockSignals(True)
                    control.enable.setChecked(enabled)
                    control.enable.blockSignals(False)

                if existing:
                    control.thickness_spin.blockSignals(True)
                    control.thickness_spin.setValue(existing.thickness_mm)
                    control.thickness_spin.blockSignals(False)

                    control.filling_spin.blockSignals(True)
                    control.filling_spin.setValue(existing.filling_grade if existing.filling_grade is not None else 1.0)
                    control.filling_spin.blockSignals(False)

                    self._set_combo_to_material(control.material_combo, existing.material)

                    control.rho_e_spin.blockSignals(True)
                    control.rho_e_spin.setValue(existing.electrical_resistivity() or 0.0)
                    control.rho_e_spin.blockSignals(False)

                    control.rho_th_spin.blockSignals(True)
                    control.rho_th_spin.setValue(existing.thermal_resistivity() or 0.0)
                    control.rho_th_spin.blockSignals(False)
                else:
                    control.material_combo.blockSignals(True)
                    control.material_combo.setCurrentIndex(0)
                    control.material_combo.blockSignals(False)

                    control.thickness_spin.blockSignals(True)
                    control.thickness_spin.setValue(self._ROLE_DEFAULT_THICKNESS.get(role, 1.0))
                    control.thickness_spin.blockSignals(False)

                    control.rho_e_spin.blockSignals(True)
                    control.rho_e_spin.setValue(0.0)
                    control.rho_e_spin.blockSignals(False)

                    control.rho_th_spin.blockSignals(True)
                    control.rho_th_spin.setValue(0.0)
                    control.rho_th_spin.blockSignals(False)

                    control.filling_spin.blockSignals(True)
                    control.filling_spin.setValue(1.0)
                    control.filling_spin.blockSignals(False)

                self._refresh_layer_enabled_state(control, enabled)
                if enabled:
                    self._apply_layer_material(control)
        else:
            self._reset_conductor_controls()
            for control in self._layer_controls.values():
                enabled = control.enable.isChecked() if control.enable else True
                self._refresh_layer_enabled_state(control, enabled)
                if enabled:
                    self._apply_layer_material(control)

        self._updating = False
        self._recompute_layer_metrics()

    def _populate_material_combo(
        self,
        combo: QComboBox,
        classifications: Sequence[MaterialClassification],
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        for material in material_catalog.materials_for_classifications(classifications):
            combo.addItem(material.name, material)
        combo.addItem("Other", self._CUSTOM_MATERIAL_KEY)
        combo.blockSignals(False)

    def _set_combo_to_material(self, combo: QComboBox, material: Material) -> None:
        combo.blockSignals(True)
        found = False
        for index in range(combo.count()):
            candidate = combo.itemData(index)
            if isinstance(candidate, Material) and candidate.name == material.name:
                combo.setCurrentIndex(index)
                found = True
                break
        if not found:
            custom_index = combo.findData(self._CUSTOM_MATERIAL_KEY)
            if custom_index >= 0:
                combo.setCurrentIndex(custom_index)
            elif combo.count() > 0:
                combo.setCurrentIndex(0)
        combo.blockSignals(False)

    def _apply_layer_material(self, control: LayerControl) -> None:
        material_data = control.material_combo.currentData()
        if isinstance(material_data, Material):
            control.rho_e_spin.blockSignals(True)
            electrical = material_data.electrical_resistivity_ohm_mm2_per_m or 0.0
            control.rho_e_spin.setValue(electrical)
            control.rho_e_spin.setReadOnly(True)
            control.rho_e_spin.blockSignals(False)

            control.rho_th_spin.blockSignals(True)
            thermal = material_data.thermal_resistivity_k_m_per_w or 0.0
            control.rho_th_spin.setValue(thermal)
            control.rho_th_spin.setReadOnly(True)
            control.rho_th_spin.blockSignals(False)
        else:
            # Allow editing for custom material selection.
            control.rho_e_spin.blockSignals(True)
            control.rho_e_spin.setReadOnly(False)
            control.rho_e_spin.blockSignals(False)

            control.rho_th_spin.blockSignals(True)
            control.rho_th_spin.setReadOnly(False)
            control.rho_th_spin.blockSignals(False)

    def _apply_general_changes(self) -> None:
        if self._updating or not self._current_item:
            return

        system = self._current_item.system
        new_name = self._name_edit.text().strip()
        system.name = new_name or system.name
        if not new_name:
            self._updating = True
            self._name_edit.setText(system.name)
            self._updating = False

        if system.kind is CableSystemKind.SINGLE_CORE:
            arrangement = self._arrangement_combo.currentData()
            if isinstance(arrangement, SingleCoreArrangement):
                system.arrangement = arrangement

        system.phase_spacing_mm = max(self._phase_spacing_spin.value(), 1.0)
        voltage = self._nominal_voltage_spin.value()
        system.nominal_voltage_kv = voltage or None
        current = self._operating_current_spin.value()
        system.nominal_current_a = current or None

        self._current_item.update_system(system)

    def _reset_conductor_controls(self) -> None:
        control = self._conductor_control
        if not control:
            return

        control.material_combo.blockSignals(True)
        if control.material_combo.count() > 0:
            control.material_combo.setCurrentIndex(0)
        control.material_combo.blockSignals(False)

        default_thickness = self._DEFAULT_CONDUCTOR_DIAMETER / 2.0
        control.thickness_spin.blockSignals(True)
        control.thickness_spin.setValue(default_thickness)
        control.thickness_spin.blockSignals(False)

        control.filling_spin.blockSignals(True)
        control.filling_spin.setValue(self._DEFAULT_CONDUCTOR_FILLING)
        control.filling_spin.blockSignals(False)

        control.rho_e_spin.blockSignals(True)
        control.rho_e_spin.setValue(0.0)
        control.rho_e_spin.blockSignals(False)

        control.rho_th_spin.blockSignals(True)
        control.rho_th_spin.setValue(0.0)
        control.rho_th_spin.blockSignals(False)

        self._apply_conductor_material(set_values=True)

    def _apply_conductor_from_phase(self, phase: CablePhase) -> None:
        control = self._conductor_control
        if not control:
            return

        combo = control.material_combo
        material = phase.conductor.material
        override_e = phase.conductor.electrical_resistivity_override_ohm_mm2_per_m
        override_th = phase.conductor.thermal_resistivity_override_k_m_per_w

        combo.blockSignals(True)
        found_index = -1
        for index in range(combo.count()):
            candidate = combo.itemData(index)
            if isinstance(candidate, Material) and candidate.name == material.name:
                found_index = index
                break
        use_custom = override_e is not None or override_th is not None or found_index < 0
        if use_custom:
            custom_index = combo.findData(self._CUSTOM_MATERIAL_KEY)
            if custom_index >= 0:
                combo.setCurrentIndex(custom_index)
            elif found_index >= 0:
                combo.setCurrentIndex(found_index)
            else:
                combo.setCurrentIndex(0)
        else:
            combo.setCurrentIndex(found_index)
        combo.blockSignals(False)

        radius = max(phase.conductor.diameter_mm, 0.0) / 2.0
        control.thickness_spin.blockSignals(True)
        control.thickness_spin.setValue(radius)
        control.thickness_spin.blockSignals(False)

        inferred_fill = phase.conductor.filling_grade
        if inferred_fill is None:
            radius = max(phase.conductor.diameter_mm, 0.0) / 2.0
            area = math.pi * radius * radius
            if area > 0.0:
                inferred_fill = max(min(phase.conductor.area_mm2 / area, 1.0), 0.0)
            else:
                inferred_fill = self._DEFAULT_CONDUCTOR_FILLING
        control.filling_spin.blockSignals(True)
        control.filling_spin.setValue(inferred_fill)
        control.filling_spin.blockSignals(False)

        control.rho_e_spin.blockSignals(True)
        control.rho_e_spin.setValue(phase.conductor.electrical_resistivity() or 0.0)
        control.rho_e_spin.blockSignals(False)

        control.rho_th_spin.blockSignals(True)
        control.rho_th_spin.setValue(phase.conductor.thermal_resistivity() or 0.0)
        control.rho_th_spin.blockSignals(False)

        self._apply_conductor_material(set_values=False)

    def _apply_conductor_material(self, set_values: bool) -> None:
        control = self._conductor_control
        if not control:
            return
        material_data = control.material_combo.currentData()
        if isinstance(material_data, Material):
            electrical = material_data.electrical_resistivity_ohm_mm2_per_m or 0.0
            thermal = material_data.thermal_resistivity_k_m_per_w or 0.0

            control.rho_e_spin.blockSignals(True)
            if set_values:
                control.rho_e_spin.setValue(electrical)
            control.rho_e_spin.setReadOnly(True)
            control.rho_e_spin.blockSignals(False)

            control.rho_th_spin.blockSignals(True)
            if set_values:
                control.rho_th_spin.setValue(thermal)
            control.rho_th_spin.setReadOnly(True)
            control.rho_th_spin.blockSignals(False)
        else:
            control.rho_e_spin.blockSignals(True)
            control.rho_e_spin.setReadOnly(False)
            control.rho_e_spin.blockSignals(False)

            control.rho_th_spin.blockSignals(True)
            control.rho_th_spin.setReadOnly(False)
            control.rho_th_spin.blockSignals(False)

    def _handle_conductor_material_changed(self, _index: int) -> None:
        self._apply_conductor_material(set_values=True)
        if self._updating:
            return
        self._sync_phase_layers()

    def _handle_conductor_value_changed(self, _value: float) -> None:
        if self._updating:
            return
        self._sync_phase_layers()

    def _handle_item_position_changed(self, pos: QPointF) -> None:
        None

    def _handle_layer_enable(self, role: LayerRole, checked: bool) -> None:
        control = self._layer_controls[role]
        self._refresh_layer_enabled_state(control, checked)
        if checked:
            self._apply_layer_material(control)
        if self._updating:
            return
        self._sync_phase_layers()

    def _handle_layer_material_changed(self, role: LayerRole, _index: int) -> None:
        control = self._layer_controls[role]
        self._apply_layer_material(control)
        if self._updating:
            return
        self._sync_phase_layers()

    def _handle_layer_value_changed(self, role: LayerRole, _value: float) -> None:
        if self._updating:
            return
        self._sync_phase_layers()

    def _refresh_layer_enabled_state(self, control: LayerControl, enabled: bool) -> None:
        widgets = (
            control.material_combo,
            control.thickness_spin,
            control.rho_e_spin,
            control.rho_th_spin,
            control.filling_spin,
        )
        for widget in widgets:
            widget.setEnabled(enabled)
        if not enabled:
            control.resistance_label.setText("Disabled")
            control.outer_diameter_label.setText("Disabled")
        else:
            control.resistance_label.setText("—")
            control.outer_diameter_label.setText("—")

    def _sync_phase_layers(self) -> None:
        self._recompute_layer_metrics()
        if self._updating or not self._current_item:
            return

        system = self._current_item.system
        phase = self._single_core_phase(system)
        if not phase:
            return

        self._sync_conductor_spec(phase)

        for role, control in self._layer_controls.items():
            enabled = control.enable.isChecked() if control.enable else True
            if not enabled:
                phase.set_layer(role, None)
                continue

            data = control.material_combo.currentData()
            if data == self._CUSTOM_MATERIAL_KEY:
                material = self._make_custom_material(control)
                electrical_override = control.rho_e_spin.value()
                thermal_override = control.rho_th_spin.value()
            elif isinstance(data, Material):
                material = data
                electrical_override = None
                thermal_override = None
            else:
                phase.set_layer(role, None)
                continue

            thickness = control.thickness_spin.value()
            if thickness <= 0.0:
                phase.set_layer(role, None)
                continue

            filling_grade = control.filling_spin.value()
            phase.set_layer(
                role,
                LayerSpec(
                    role=role,
                    thickness_mm=thickness,
                    material=material,
                    electrical_resistivity_override_ohm_mm2_per_m=electrical_override,
                    thermal_resistivity_override_k_m_per_w=thermal_override,
                    filling_grade=filling_grade,
                ),
            )

        self._current_item.update_system(system)

    def _make_custom_material(self, control: LayerControl) -> Material:
        classification = control.allowed_classes[0] if control.allowed_classes else MaterialClassification.PROTECTIVE
        name = f"Custom {control.role.name.replace('_', ' ').title()}"
        return Material(
            name=name,
            classification=classification,
            electrical_resistivity_ohm_mm2_per_m=control.rho_e_spin.value(),
            thermal_resistivity_k_m_per_w=control.rho_th_spin.value(),
        )

    def _make_custom_conductor_material(self) -> Material:
        control = self._conductor_control
        if not control:
            return Material(
                name="Custom Conductor",
                classification=MaterialClassification.CONDUCTIVE,
            )
        electrical = control.rho_e_spin.value()
        thermal = control.rho_th_spin.value()
        return Material(
            name="Custom Conductor",
            classification=MaterialClassification.CONDUCTIVE,
            electrical_resistivity_ohm_mm2_per_m=electrical if electrical > 0.0 else None,
            thermal_resistivity_k_m_per_w=thermal if thermal > 0.0 else None,
        )

    def _sync_conductor_spec(self, phase: CablePhase) -> None:
        control = self._conductor_control
        if not control:
            return

        thickness = control.thickness_spin.value()
        if thickness <= 0.0:
            return

        data = control.material_combo.currentData()
        if data == self._CUSTOM_MATERIAL_KEY:
            material = self._make_custom_conductor_material()
            electrical_override = control.rho_e_spin.value()
            thermal_override = control.rho_th_spin.value()
        elif isinstance(data, Material):
            material = data
            electrical_override = None
            thermal_override = None
        else:
            return

        electrical_override = electrical_override if electrical_override and electrical_override > 0.0 else None
        thermal_override = thermal_override if thermal_override and thermal_override > 0.0 else None

        filling = max(control.filling_spin.value(), 0.0)
        radius = thickness
        diameter = radius * 2.0
        area = math.pi * radius * radius
        effective_area = area * filling if filling > 0.0 else area
        area_mm2 = max(effective_area, 1e-6)

        phase.conductor = ConductorSpec(
            area_mm2=area_mm2,
            diameter_mm=diameter,
            material=material,
            electrical_resistivity_override_ohm_mm2_per_m=electrical_override,
            thermal_resistivity_override_k_m_per_w=thermal_override,
            filling_grade=filling if filling > 0.0 else None,
        )

    def _update_conductor_row_metrics(self) -> float:
        control = self._conductor_control
        if not control:
            return 0.0

        radius = max(control.thickness_spin.value(), 0.0)
        diameter = radius * 2.0
        rho = self._effective_conductor_resistivity()
        filling = max(control.filling_spin.value(), 0.0)

        area = math.pi * radius * radius
        effective_area = area * filling if filling > 0.0 else 0.0

        if rho is not None and rho > 0.0 and effective_area > 0.0:
            resistance = (rho * 1000.0) / effective_area
            control.resistance_label.setText(f"{resistance:.4f} Ω/km")
        else:
            control.resistance_label.setText("—")

        if diameter > 0.0:
            control.outer_diameter_label.setText(f"{diameter:.2f} mm")
        else:
            control.outer_diameter_label.setText("—")

        return radius

    def _effective_layer_resistivity(self, control: LayerControl) -> Optional[float]:
        data = control.material_combo.currentData()
        if data == self._CUSTOM_MATERIAL_KEY:
            value = control.rho_e_spin.value()
            return value if value > 0.0 else None
        if isinstance(data, Material):
            return data.electrical_resistivity_ohm_mm2_per_m
        return None

    def _effective_conductor_resistivity(self) -> Optional[float]:
        control = self._conductor_control
        if not control:
            return None
        data = control.material_combo.currentData()
        if data == self._CUSTOM_MATERIAL_KEY:
            value = control.rho_e_spin.value()
            return value if value > 0.0 else None
        if isinstance(data, Material):
            return data.electrical_resistivity_ohm_mm2_per_m
        return None

    def _recompute_layer_metrics(self) -> None:
        controls = sorted(self._layer_controls.values(), key=lambda c: c.role.order_index())
        radius = self._update_conductor_row_metrics()
        total_diameter = radius * 2.0 if radius > 0.0 else 0.0
        for control in controls:
            enabled = control.enable.isChecked() if control.enable else True
            thickness = control.thickness_spin.value()

            if enabled and thickness > 0.0:
                outer_radius = radius + thickness
                rho = self._effective_layer_resistivity(control)
                filling = control.filling_spin.value()
                if rho is not None and rho > 0.0 and filling > 0.0:
                    area = math.pi * (outer_radius**2 - radius**2)
                    effective_area = area * filling
                    if effective_area > 0.0:
                        resistance = (rho * 1000.0) / effective_area
                        control.resistance_label.setText(f"{resistance:.4f} Ω/km")
                    else:
                        control.resistance_label.setText("—")
                else:
                    control.resistance_label.setText("—")
                control.outer_diameter_label.setText(f"{outer_radius * 2.0:.2f} mm")
                radius = outer_radius
                total_diameter = max(total_diameter, outer_radius * 2.0)
            else:
                control.resistance_label.setText("Disabled" if not enabled else "—")
                if not enabled:
                    control.outer_diameter_label.setText("Disabled")
                else:
                    current_diameter = radius * 2.0
                    control.outer_diameter_label.setText(f"{current_diameter:.2f} mm" if current_diameter > 0.0 else "—")

        if total_diameter > 0.0:
            self._overall_diameter_label.setText(f"Overall outer diameter: {total_diameter:.2f} mm")
        else:
            self._overall_diameter_label.setText("Overall outer diameter: — mm")

    def _single_core_phase(self, system: CableSystem) -> Optional[CablePhase]:
        if system.kind is not CableSystemKind.SINGLE_CORE:
            return None
        return system.single_core_phase

    def _update_visibility(self) -> None:
        has_item = self._current_item is not None
        self._placeholder.setVisible(not has_item)
        self._form_container.setVisible(has_item)
        self._form_container.setEnabled(has_item)
        if not has_item:
            self._overall_diameter_label.setText("Overall outer diameter: — mm")

    def _add_conductor_row(self) -> None:
        if not self._layers_table or self._conductor_row_index is not None:
            return

        row = self._layers_table.rowCount()
        self._layers_table.insertRow(row)
        self._conductor_row_index = row

        name_label = QLabel("Conductor", self._layers_table)
        name_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._layers_table.setCellWidget(row, 0, name_label)
        material_combo = QComboBox(self._layers_table)
        self._populate_material_combo(material_combo, (MaterialClassification.CONDUCTIVE,))
        self._layers_table.setCellWidget(row, 1, material_combo)

        thickness_spin = QDoubleSpinBox(self._layers_table)
        thickness_spin.setRange(0.01, 100.0)
        thickness_spin.setDecimals(2)
        thickness_spin.setValue(self._DEFAULT_CONDUCTOR_DIAMETER / 2.0)
        self._layers_table.setCellWidget(row, 2, thickness_spin)

        rho_e_spin = QDoubleSpinBox(self._layers_table)
        rho_e_spin.setRange(0.0, 5.0)
        rho_e_spin.setDecimals(5)
        rho_e_spin.setSingleStep(0.0001)
        self._layers_table.setCellWidget(row, 3, rho_e_spin)

        rho_th_spin = QDoubleSpinBox(self._layers_table)
        rho_th_spin.setRange(0.0, 200.0)
        rho_th_spin.setDecimals(2)
        rho_th_spin.setSingleStep(0.05)
        self._layers_table.setCellWidget(row, 4, rho_th_spin)

        filling_spin = QDoubleSpinBox(self._layers_table)
        filling_spin.setRange(0.0, 1.0)
        filling_spin.setDecimals(3)
        filling_spin.setSingleStep(0.01)
        filling_spin.setValue(self._DEFAULT_CONDUCTOR_FILLING)
        self._layers_table.setCellWidget(row, 5, filling_spin)

        resistance_label = QLabel("—", self._layers_table)
        resistance_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._layers_table.setCellWidget(row, 6, resistance_label)

        outer_label = QLabel("—", self._layers_table)
        outer_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._layers_table.setCellWidget(row, 7, outer_label)

        self._conductor_control = ConductorControl(
            material_combo=material_combo,
            thickness_spin=thickness_spin,
            rho_e_spin=rho_e_spin,
            rho_th_spin=rho_th_spin,
            filling_spin=filling_spin,
            resistance_label=resistance_label,
            outer_diameter_label=outer_label,
        )
        self._apply_conductor_material(set_values=True)
