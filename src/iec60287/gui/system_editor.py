from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QScrollArea,
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
    enable: Optional[QCheckBox]
    material_combo: QComboBox
    thickness_spin: QDoubleSpinBox
    info_label: QLabel


class CableSystemEditor(QWidget):
    """Form for editing properties of the selected cable system."""

    _ROLE_DEFAULT_THICKNESS = {
        LayerRole.INNER_SCREEN: 1.2,
        LayerRole.OUTER_SCREEN: 1.2,
        LayerRole.INSULATION: 5.0,
        LayerRole.SHEATH: 2.5,
        LayerRole.SERVING: 1.0,
    }

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
        self._nominal_current_spin = QDoubleSpinBox(self)

        self._conductor_area_spin = QDoubleSpinBox(self)
        self._conductor_diameter_spin = QDoubleSpinBox(self)
        self._conductor_material_combo = QComboBox(self)
        self._conductor_info_label = QLabel(self)

        self._layer_controls: Dict[LayerRole, LayerControl] = {}

        self._scroll_area = QScrollArea(self)
        self._scroll_area.setWidgetResizable(True)
        self._form_container = QWidget()
        self._scroll_area.setWidget(self._form_container)
        self._build_ui()
        self._wire_signals()
        self._update_visibility()

    def set_item(self, item: Optional[CableSystemItem]) -> None:
        """Attach the editor to a cable system item."""
        self._current_item = item
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

        self._nominal_current_spin.setRange(0.0, 5000.0)
        self._nominal_current_spin.setDecimals(1)
        self._nominal_current_spin.setSuffix(" A")
        general_form.addRow("Nominal current", self._nominal_current_spin)

        phase_container = QVBoxLayout()
        phase_container.setContentsMargins(0, 0, 0, 0)
        phase_container.setSpacing(12)

        conductor_group = QGroupBox("Conductor", self._form_container)
        conductor_form = QFormLayout(conductor_group)
        conductor_form.setLabelAlignment(Qt.AlignLeft)

        self._conductor_area_spin.setRange(1.0, 3000.0)
        self._conductor_area_spin.setDecimals(1)
        self._conductor_area_spin.setSuffix(" mm²")
        conductor_form.addRow("Area", self._conductor_area_spin)

        self._conductor_diameter_spin.setRange(1.0, 100.0)
        self._conductor_diameter_spin.setDecimals(2)
        self._conductor_diameter_spin.setSuffix(" mm")
        conductor_form.addRow("Diameter", self._conductor_diameter_spin)

        self._populate_material_combo(
            self._conductor_material_combo,
            (MaterialClassification.CONDUCTIVE,),
        )
        conductor_form.addRow("Material", self._conductor_material_combo)

        self._conductor_info_label.setWordWrap(True)
        conductor_form.addRow("Info", self._conductor_info_label)

        layers_group = QGroupBox("Radial layers", self._form_container)
        layers_layout = QVBoxLayout(layers_group)
        layers_layout.setContentsMargins(6, 6, 6, 6)
        layers_layout.setSpacing(12)

        layers_layout.addWidget(
            self._make_layer_group(
                title="Inner Screen",
                role=LayerRole.INNER_SCREEN,
                allowed_classes=(MaterialClassification.SEMICONDUCTIVE,),
                optional=True,
            )
        )
        layers_layout.addWidget(
            self._make_layer_group(
                title="Insulation",
                role=LayerRole.INSULATION,
                allowed_classes=(MaterialClassification.INSULATING,),
                optional=False,
            )
        )
        layers_layout.addWidget(
            self._make_layer_group(
                title="Outer Screen",
                role=LayerRole.OUTER_SCREEN,
                allowed_classes=(MaterialClassification.SEMICONDUCTIVE,),
                optional=True,
            )
        )
        layers_layout.addWidget(
            self._make_layer_group(
                title="Sheath",
                role=LayerRole.SHEATH,
                allowed_classes=(MaterialClassification.PROTECTIVE,),
                optional=True,
            )
        )
        layers_layout.addWidget(
            self._make_layer_group(
                title="Serving / Jacket",
                role=LayerRole.SERVING,
                allowed_classes=(MaterialClassification.PROTECTIVE,),
                optional=True,
            )
        )
        layers_layout.addStretch()

        phase_container.addWidget(conductor_group)
        phase_container.addWidget(layers_group)

        phase_wrapper = QGroupBox("Single-core phase", self._form_container)
        phase_wrapper.setLayout(phase_container)

        form_layout.addWidget(general_group)
        form_layout.addWidget(phase_wrapper)
        form_layout.addStretch()

    def _make_layer_group(
        self,
        title: str,
        role: LayerRole,
        allowed_classes: Sequence[MaterialClassification],
        optional: bool,
    ) -> QGroupBox:
        group = QGroupBox(title, self._form_container)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft)

        enable_checkbox: Optional[QCheckBox] = None
        if optional:
            enable_checkbox = QCheckBox("Enabled", group)
            enable_checkbox.setChecked(True)
            form.addRow(enable_checkbox)

        material_combo = QComboBox(group)
        self._populate_material_combo(material_combo, allowed_classes)

        thickness_spin = QDoubleSpinBox(group)
        thickness_spin.setRange(0.0, 50.0)
        thickness_spin.setDecimals(2)
        thickness_spin.setSuffix(" mm")
        thickness_spin.setValue(self._ROLE_DEFAULT_THICKNESS.get(role, 1.0))

        form.addRow("Material", material_combo)
        form.addRow("Thickness", thickness_spin)

        info_label = QLabel(group)
        info_label.setWordWrap(True)
        form.addRow("Info", info_label)

        control = LayerControl(
            role=role,
            allowed_classes=allowed_classes,
            enable=enable_checkbox,
            material_combo=material_combo,
            thickness_spin=thickness_spin,
            info_label=info_label,
        )
        self._layer_controls[role] = control
        return group

    def _wire_signals(self) -> None:
        self._name_edit.editingFinished.connect(self._apply_general_changes)
        self._arrangement_combo.currentIndexChanged.connect(self._apply_general_changes)
        self._phase_spacing_spin.valueChanged.connect(self._apply_general_changes)
        self._nominal_voltage_spin.valueChanged.connect(self._apply_general_changes)
        self._nominal_current_spin.valueChanged.connect(self._apply_general_changes)

        self._conductor_area_spin.valueChanged.connect(self._apply_conductor_changes)
        self._conductor_diameter_spin.valueChanged.connect(self._apply_conductor_changes)
        self._conductor_material_combo.currentIndexChanged.connect(self._apply_conductor_changes)

        for role, control in self._layer_controls.items():
            if control.enable:
                control.enable.toggled.connect(lambda checked, r=role: self._handle_layer_enable(r, checked))
            control.material_combo.currentIndexChanged.connect(lambda _, r=role: self._handle_layer_changed(r))
            control.thickness_spin.valueChanged.connect(lambda _, r=role: self._handle_layer_changed(r))

    def _populate_fields(self, system: Optional[CableSystem]) -> None:
        self._updating = True
        if system is None:
            self._name_edit.clear()
            self._phase_spacing_spin.setValue(1.0)
            self._nominal_voltage_spin.setValue(0.0)
            self._nominal_current_spin.setValue(0.0)
            self._conductor_area_spin.setValue(1.0)
            self._conductor_diameter_spin.setValue(1.0)
            self._conductor_material_combo.setCurrentIndex(0)
            self._update_conductor_info()
            for control in self._layer_controls.values():
                if control.enable:
                    control.enable.blockSignals(True)
                    control.enable.setChecked(False)
                    control.enable.blockSignals(False)
                control.thickness_spin.setValue(self._ROLE_DEFAULT_THICKNESS.get(control.role, 1.0))
                control.material_combo.setCurrentIndex(0)
                enabled = control.enable.isChecked() if control.enable else True
                self._refresh_layer_enabled_state(control, enabled)
                self._update_material_info(control)
            self._updating = False
            return

        self._name_edit.setText(system.name)
        index = self._arrangement_combo.findData(system.arrangement)
        self._arrangement_combo.setCurrentIndex(index if index >= 0 else 0)

        self._phase_spacing_spin.setValue(max(system.phase_spacing_mm, 1.0))
        self._nominal_voltage_spin.setValue(system.nominal_voltage_kv or 0.0)
        self._nominal_current_spin.setValue(system.nominal_current_a or 0.0)

        phase = self._single_core_phase(system)
        if phase:
            self._conductor_area_spin.setValue(phase.conductor.area_mm2)
            self._conductor_diameter_spin.setValue(phase.conductor.diameter_mm)
            self._set_combo_to_material(self._conductor_material_combo, phase.conductor.material)
            self._update_conductor_info()

            for role, control in self._layer_controls.items():
                existing = phase.get_layer(role)
                enabled = existing is not None if control.enable else True
                self._refresh_layer_enabled_state(control, enabled)
                if control.enable:
                    control.enable.blockSignals(True)
                    control.enable.setChecked(enabled)
                    control.enable.blockSignals(False)
                if existing:
                    control.thickness_spin.blockSignals(True)
                    control.thickness_spin.setValue(existing.thickness_mm)
                    control.thickness_spin.blockSignals(False)
                    self._set_combo_to_material(control.material_combo, existing.material)
                else:
                    control.thickness_spin.blockSignals(True)
                    control.thickness_spin.setValue(self._ROLE_DEFAULT_THICKNESS.get(role, 1.0))
                    control.thickness_spin.blockSignals(False)
                    control.material_combo.blockSignals(True)
                    control.material_combo.setCurrentIndex(0)
                    control.material_combo.blockSignals(False)
                self._update_material_info(control)
        else:
            self._conductor_area_spin.setValue(1.0)
            self._conductor_diameter_spin.setValue(1.0)
            self._conductor_material_combo.setCurrentIndex(0)
            self._update_conductor_info()
            for control in self._layer_controls.values():
                enabled = control.enable.isChecked() if control.enable else True
                self._refresh_layer_enabled_state(control, enabled)
                self._update_material_info(control)

        self._updating = False

    def _populate_material_combo(
        self,
        combo: QComboBox,
        classifications: Sequence[MaterialClassification],
    ) -> None:
        combo.blockSignals(True)
        combo.clear()
        for material in material_catalog.materials_for_classifications(classifications):
            combo.addItem(material.name, material)
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
        if not found and combo.count() > 0:
            combo.setCurrentIndex(0)
        combo.blockSignals(False)

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
        current = self._nominal_current_spin.value()
        system.nominal_voltage_kv = voltage or None
        system.nominal_current_a = current or None

        self._current_item.update_system(system)

    def _apply_conductor_changes(self) -> None:
        if self._updating or not self._current_item:
            return

        system = self._current_item.system
        phase = self._single_core_phase(system)
        if not phase:
            return

        material = self._conductor_material_combo.currentData()
        if not isinstance(material, Material):
            return

        phase.conductor = ConductorSpec(
            area_mm2=self._conductor_area_spin.value(),
            diameter_mm=self._conductor_diameter_spin.value(),
            material=material,
        )
        self._update_conductor_info()
        self._current_item.update_system(system)

    def _handle_layer_enable(self, role: LayerRole, checked: bool) -> None:
        control = self._layer_controls[role]
        self._refresh_layer_enabled_state(control, checked)
        self._update_material_info(control)
        self._sync_phase_layers()

    def _handle_layer_changed(self, role: LayerRole) -> None:
        control = self._layer_controls[role]
        self._update_material_info(control)
        self._sync_phase_layers()

    def _refresh_layer_enabled_state(self, control: LayerControl, enabled: bool) -> None:
        if control.enable:
            control.material_combo.setEnabled(enabled)
            control.thickness_spin.setEnabled(enabled)
        else:
            control.material_combo.setEnabled(True)
            control.thickness_spin.setEnabled(True)
        if not enabled:
            control.info_label.setText("Disabled")

    def _sync_phase_layers(self) -> None:
        if self._updating or not self._current_item:
            return

        system = self._current_item.system
        phase = self._single_core_phase(system)
        if not phase:
            return

        for role, control in self._layer_controls.items():
            enabled = control.enable.isChecked() if control.enable else True
            if not enabled:
                phase.set_layer(role, None)
                continue

            material = control.material_combo.currentData()
            if not isinstance(material, Material):
                phase.set_layer(role, None)
                continue

            thickness = control.thickness_spin.value()
            if thickness <= 0.0:
                phase.set_layer(role, None)
                continue

            phase.set_layer(role, LayerSpec(role=role, thickness_mm=thickness, material=material))

        self._current_item.update_system(system)

    def _material_summary(self, material: Optional[Material]) -> str:
        if not isinstance(material, Material):
            return "Type: —"

        parts = [f"Type: {material.classification.name.title()}"]
        if material.electrical_resistivity_ohm_mm2_per_m:
            parts.append(f"ρe {material.electrical_resistivity_ohm_mm2_per_m:.4f} Ω·mm²/m")
        if material.thermal_resistivity_k_m_per_w:
            parts.append(f"ρth {material.thermal_resistivity_k_m_per_w:.2f} K·m/W")
        if material.temp_coefficient_per_c:
            parts.append(f"α {material.temp_coefficient_per_c:.4f}/°C")
        if material.max_operating_temp_c:
            parts.append(f"Tmax {material.max_operating_temp_c:.0f} °C")
        return " | ".join(parts)

    def _update_material_info(self, control: LayerControl) -> None:
        enabled = control.enable.isChecked() if control.enable else True
        if not enabled:
            control.info_label.setText("Disabled")
            return
        material = control.material_combo.currentData()
        control.info_label.setText(self._material_summary(material))

    def _update_conductor_info(self) -> None:
        material = self._conductor_material_combo.currentData()
        self._conductor_info_label.setText(self._material_summary(material))

    def _single_core_phase(self, system: CableSystem) -> Optional[CablePhase]:
        if system.kind is not CableSystemKind.SINGLE_CORE:
            return None
        return system.single_core_phase

    def _update_visibility(self) -> None:
        has_item = self._current_item is not None
        self._placeholder.setVisible(not has_item)
        self._form_container.setVisible(has_item)
        self._form_container.setEnabled(has_item)
