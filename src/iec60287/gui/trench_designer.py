from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional

from PySide6.QtCore import QPointF, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from iec60287.gui.items import CableSystemItem
from iec60287.gui.placement_scene import (
    PlacementScene,
    TrenchLayer,
    TrenchLayerKind,
    default_trench_layers,
)


@dataclass
class LayerWidgetBundle:
    kind_combo: QComboBox
    thickness_spin: QDoubleSpinBox
    resistivity_spin: QDoubleSpinBox


class TrenchDesigner(QWidget):
    """Docked editor for configuring trench composition and cable positioning."""

    _KIND_LABELS = {
        TrenchLayerKind.GROUND: "Ground",
        TrenchLayerKind.BACKFILL: "Backfill",
        TrenchLayerKind.CONCRETE: "Concrete",
        TrenchLayerKind.AIR: "Air / Void",
        TrenchLayerKind.CUSTOM: "Custom",
    }

    def __init__(self, scene: PlacementScene, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._scene = scene
        self._updating = False
        self._connected_item: Optional[CableSystemItem] = None
        self._layer_widgets: List[LayerWidgetBundle] = []

        self._width_spin = QDoubleSpinBox(self)
        self._depth_spin = QDoubleSpinBox(self)
        self._surface_spin = QDoubleSpinBox(self)

        self._layers_table = QTableWidget(self)
        self._add_layer_button = QPushButton("Add Layer", self)
        self._remove_layer_button = QPushButton("Remove Layer", self)
        self._reset_layers_button = QPushButton("Reset Defaults", self)

        self._system_list = QListWidget(self)
        self._pos_x_spin = QDoubleSpinBox(self)
        self._pos_y_spin = QDoubleSpinBox(self)
        self._selected_label = QLabel("No cable selected", self)

        self._build_ui()
        self._wire_signals()
        self.refresh_all()

    def refresh_all(self) -> None:
        """Refresh trench parameters, layers, and system list from the scene."""
        self._updating = True
        config = self._scene.config
        self._width_spin.setValue(config.trench_width_mm)
        self._depth_spin.setValue(config.trench_depth_mm)
        self._surface_spin.setValue(config.surface_level_y)

        self._layers_table.blockSignals(True)
        self._layers_table.setRowCount(0)
        self._layer_widgets.clear()
        for layer in config.layers:
            self._append_layer_row(layer)
        self._layers_table.blockSignals(False)

        self.refresh_systems()
        self._updating = False

    def refresh_systems(self) -> None:
        """Refresh the list of cable systems displayed in the designer."""
        selected_id = None
        if self._connected_item:
            selected_id = self._connected_item.system.identifier

        self._system_list.blockSignals(True)
        self._system_list.clear()
        for item in self._scene.system_items():
            entry = QListWidgetItem(item.system.name)
            entry.setData(Qt.UserRole, item)
            self._system_list.addItem(entry)
            if item.system.identifier == selected_id:
                entry.setSelected(True)
        self._system_list.blockSignals(False)
        self._update_position_fields()

    def set_selected_item(self, item: Optional[CableSystemItem]) -> None:
        """Update the designer to reflect the currently selected cable system."""
        if self._connected_item:
            try:
                self._connected_item.positionChanged.disconnect(self._handle_item_position_changed)
            except (RuntimeError, TypeError):
                pass
            self._connected_item = None

        self._connected_item = item
        if item:
            item.positionChanged.connect(self._handle_item_position_changed)
            self._select_item_in_list(item)
        else:
            self._system_list.clearSelection()

        self._update_position_fields()

    # --------------------------------------------------------------------- UI
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)

        layout.addLayout(self._build_trench_row())
        layout.addLayout(self._build_system_row())
        layout.addStretch()

    def _build_trench_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self._build_general_group())
        row.addWidget(self._build_layers_group())
        return row

    def _build_system_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(6)
        row.addWidget(self._build_system_list_group())
        row.addWidget(self._build_position_group())
        return row

    def _build_general_group(self) -> QGroupBox:
        group = QGroupBox("Trench Geometry", self)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft)

        self._width_spin.setRange(100.0, 25000.0)
        self._width_spin.setDecimals(1)
        self._width_spin.setSuffix(" mm")
        self._width_spin.setValue(1200.0)

        self._depth_spin.setRange(100.0, 25000.0)
        self._depth_spin.setDecimals(1)
        self._depth_spin.setSuffix(" mm")
        self._depth_spin.setValue(1200.0)

        self._surface_spin.setRange(-5000.0, 1000.0)
        self._surface_spin.setDecimals(1)
        self._surface_spin.setSuffix(" mm")
        self._surface_spin.setValue(0.0)

        form.addRow("Width", self._width_spin)
        form.addRow("Depth", self._depth_spin)
        form.addRow("Surface level (Y)", self._surface_spin)
        return group

    def _build_layers_group(self) -> QGroupBox:
        group = QGroupBox("Trench Composition", self)
        vlayout = QVBoxLayout(group)
        vlayout.setContentsMargins(6, 6, 6, 6)
        vlayout.setSpacing(6)

        self._layers_table.setColumnCount(4)
        self._layers_table.setHorizontalHeaderLabels(
            ["Name", "Type", "Thickness (mm)", "Thermal ρ (K·m/W)"]
        )
        header = self._layers_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self._layers_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._layers_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self._layers_table.verticalHeader().setVisible(False)

        vlayout.addWidget(self._layers_table)

        button_row = QHBoxLayout()
        button_row.setSpacing(6)
        button_row.addWidget(self._add_layer_button)
        button_row.addWidget(self._remove_layer_button)
        button_row.addWidget(self._reset_layers_button)
        button_row.addStretch()
        vlayout.addLayout(button_row)
        return group

    def _build_system_list_group(self) -> QGroupBox:
        group = QGroupBox("Cable Systems", self)
        layout = QVBoxLayout(group)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        self._system_list.setSelectionMode(QAbstractItemView.SingleSelection)
        layout.addWidget(self._system_list)
        return group

    def _build_position_group(self) -> QGroupBox:
        group = QGroupBox("Cable Positioning", self)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft)

        self._pos_x_spin.setRange(-10000.0, 10000.0)
        self._pos_x_spin.setDecimals(1)
        self._pos_x_spin.setSuffix(" mm")

        self._pos_y_spin.setRange(-10000.0, 25000.0)
        self._pos_y_spin.setDecimals(1)
        self._pos_y_spin.setSuffix(" mm")

        form.addRow("Selected", self._selected_label)
        form.addRow("X position", self._pos_x_spin)
        form.addRow("Y position", self._pos_y_spin)
        return group

    # ---------------------------------------------------------------- signals
    def _wire_signals(self) -> None:
        self._width_spin.valueChanged.connect(self._handle_general_change)
        self._depth_spin.valueChanged.connect(self._handle_general_change)
        self._surface_spin.valueChanged.connect(self._handle_general_change)

        self._layers_table.itemChanged.connect(self._handle_layer_table_change)
        self._add_layer_button.clicked.connect(self._handle_add_layer)
        self._remove_layer_button.clicked.connect(self._handle_remove_layer)
        self._reset_layers_button.clicked.connect(self._handle_reset_layers)

        self._system_list.itemSelectionChanged.connect(self._handle_system_selection_changed)
        self._pos_x_spin.valueChanged.connect(self._handle_position_spin_changed)
        self._pos_y_spin.valueChanged.connect(self._handle_position_spin_changed)

    # --------------------------------------------------------- general config
    def _handle_general_change(self) -> None:
        if self._updating:
            return
        self._scene.update_trench_geometry(
            width_mm=self._width_spin.value(),
            depth_mm=self._depth_spin.value(),
            surface_level_y=self._surface_spin.value(),
        )

    # ----------------------------------------------------------- layer config
    def _append_layer_row(self, layer: Optional[TrenchLayer] = None) -> None:
        previous_block_state = self._layers_table.blockSignals(True)
        previous_updating = self._updating
        self._updating = True
        row = self._layers_table.rowCount()
        self._layers_table.insertRow(row)

        name_item = QTableWidgetItem(layer.name if layer else "Layer")
        self._layers_table.setItem(row, 0, name_item)

        combo = QComboBox(self._layers_table)
        for kind, label in self._KIND_LABELS.items():
            combo.addItem(label, kind)
        if layer:
            index = combo.findData(layer.kind)
            combo.setCurrentIndex(index if index >= 0 else 0)
        combo.currentIndexChanged.connect(self._handle_layer_widget_change)
        self._layers_table.setCellWidget(row, 1, combo)

        thickness = QDoubleSpinBox(self._layers_table)
        thickness.setRange(0.0, 25000.0)
        thickness.setDecimals(1)
        thickness.setSuffix(" mm")
        thickness.setValue(layer.thickness_mm if layer else 0.0)
        thickness.valueChanged.connect(self._handle_layer_widget_change)
        self._layers_table.setCellWidget(row, 2, thickness)

        resistivity = QDoubleSpinBox(self._layers_table)
        resistivity.setRange(0.10, 5.0)
        resistivity.setDecimals(3)
        resistivity.setSingleStep(0.05)
        resistivity.setValue(layer.thermal_resistivity_k_m_per_w if layer else 1.50)
        resistivity.valueChanged.connect(self._handle_layer_widget_change)
        self._layers_table.setCellWidget(row, 3, resistivity)

        self._layer_widgets.append(
            LayerWidgetBundle(
                kind_combo=combo,
                thickness_spin=thickness,
                resistivity_spin=resistivity,
            )
        )
        self._updating = previous_updating
        self._layers_table.blockSignals(previous_block_state)

    def _handle_add_layer(self) -> None:
        self._append_layer_row()
        self._apply_layers()

    def _handle_remove_layer(self) -> None:
        row = self._layers_table.currentRow()
        if row < 0:
            return
        self._layers_table.blockSignals(True)
        self._layers_table.removeRow(row)
        self._layers_table.blockSignals(False)
        if 0 <= row < len(self._layer_widgets):
            self._layer_widgets.pop(row)
        self._apply_layers()

    def _handle_reset_layers(self) -> None:
        layers = default_trench_layers()
        self._updating = True
        self._layers_table.blockSignals(True)
        self._layers_table.setRowCount(0)
        self._layer_widgets.clear()
        for layer in layers:
            self._append_layer_row(layer)
        self._layers_table.blockSignals(False)
        self._updating = False
        self._apply_layers()

    def _handle_layer_widget_change(self) -> None:
        if self._updating:
            return
        self._apply_layers()

    def _handle_layer_table_change(self, _: QTableWidgetItem) -> None:
        if self._updating:
            return
        self._apply_layers()

    def _apply_layers(self) -> None:
        layers: List[TrenchLayer] = []
        for row in range(self._layers_table.rowCount()):
            name_item = self._layers_table.item(row, 0)
            name = name_item.text().strip() if name_item else "Layer"
            widgets = self._layer_widgets[row]
            kind_data = widgets.kind_combo.currentData()
            kind = kind_data if isinstance(kind_data, TrenchLayerKind) else TrenchLayerKind.CUSTOM
            thickness = widgets.thickness_spin.value()
            resistivity = widgets.resistivity_spin.value()
            layers.append(
                TrenchLayer(
                    name=name or "Layer",
                    kind=kind,
                    thickness_mm=thickness,
                    thermal_resistivity_k_m_per_w=resistivity,
                )
            )
        self._scene.update_trench_layers(layers)

    # --------------------------------------------------- system positioning
    def _handle_system_selection_changed(self) -> None:
        if self._updating:
            return
        items = self._system_list.selectedItems()
        if not items:
            self.set_selected_item(None)
            return
        item = items[0].data(Qt.UserRole)
        if isinstance(item, CableSystemItem):
            item.setSelected(True)

    def _handle_item_position_changed(self, pos: QPointF) -> None:
        self._update_position_fields(pos)

    def _handle_position_spin_changed(self) -> None:
        if self._updating or not self._connected_item:
            return
        target = QPointF(self._pos_x_spin.value(), self._pos_y_spin.value())
        if target != self._connected_item.pos():
            self._connected_item.setPos(target)
            self._connected_item.ensure_valid_position()

    def _select_item_in_list(self, target: CableSystemItem) -> None:
        self._system_list.blockSignals(True)
        found = False
        for index in range(self._system_list.count()):
            list_item = self._system_list.item(index)
            scene_item = list_item.data(Qt.UserRole)
            match = isinstance(scene_item, CableSystemItem) and scene_item is target
            list_item.setSelected(match)
            if match:
                found = True
                self._system_list.scrollToItem(list_item)
        if not found:
            self._system_list.clearSelection()
        self._system_list.blockSignals(False)

    def _update_position_fields(self, pos: Optional[QPointF] = None) -> None:
        if not self._connected_item:
            self._updating = True
            self._selected_label.setText("No cable selected")
            self._pos_x_spin.setValue(0.0)
            self._pos_y_spin.setValue(0.0)
            self._pos_x_spin.setEnabled(False)
            self._pos_y_spin.setEnabled(False)
            self._updating = False
            return

        point = pos if pos is not None else self._connected_item.pos()
        self._updating = True
        self._selected_label.setText(self._connected_item.system.name)
        self._pos_x_spin.setEnabled(True)
        self._pos_y_spin.setEnabled(True)
        self._pos_x_spin.setValue(point.x())
        self._pos_y_spin.setValue(point.y())
        self._updating = False
