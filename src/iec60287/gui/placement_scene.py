from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsScene

from iec60287.gui.items import BackfillItem, CableSystemItem
from iec60287.model import (
    CablePhase,
    CableSystem,
    CableSystemKind,
    ConductorSpec,
    LayerRole,
    LayerSpec,
    SingleCoreArrangement,
)
from iec60287.model import materials as material_catalog


class TrenchLayerKind(Enum):
    GROUND = "ground"
    BACKFILL = "backfill"
    CONCRETE = "concrete"
    AIR = "air"
    CUSTOM = "custom"


@dataclass
class TrenchLayer:
    name: str
    kind: TrenchLayerKind
    thickness_mm: float
    thermal_resistivity_k_m_per_w: float


def default_trench_layers() -> List[TrenchLayer]:
    return [
        TrenchLayer(
            name="Native Soil",
            kind=TrenchLayerKind.GROUND,
            thickness_mm=1200,
            thermal_resistivity_k_m_per_w=1.25,
        ),
    ]


@dataclass
class SceneConfig:
    scene_size: float = 2000.0  # mm
    minor_grid: float = 25.0
    major_grid: float = 100.0
    background_colour: QColor = field(default_factory=lambda: QColor("#f8f9fa"))
    minor_grid_colour: QColor = field(default_factory=lambda: QColor("#dee2e6"))
    major_grid_colour: QColor = field(default_factory=lambda: QColor("#adb5bd"))
    trench_width_mm: float = 1200.0
    trench_depth_mm: float = 1200.0
    surface_level_y: float = 0.0
    layers: List[TrenchLayer] = field(default_factory=default_trench_layers)


class PlacementScene(QGraphicsScene):
    """Scene hosting draggable cable and backfill items."""

    def __init__(self, config: Optional[SceneConfig] = None) -> None:
        self.config = config or SceneConfig()
        half = self.config.scene_size / 2.0
        rect = QRectF(-half, -half, self.config.scene_size, self.config.scene_size)
        super().__init__(rect)
        self.setItemIndexMethod(QGraphicsScene.NoIndex)

        self._cable_count = 0
        self._backfill_count = 0
        self._systems: Dict[str, CableSystemItem] = {}

    def add_cable(self, position: Optional[QPointF] = None) -> CableSystemItem:
        self._cable_count += 1
        system = self._build_default_single_core_system(self._cable_count)
        item = CableSystemItem(system)
        self._systems[system.identifier] = item
        spawn_pos = position or self._default_cable_position()
        self._spawn_item(item, spawn_pos)
        return item

    def remove_selected(self) -> None:
        for item in list(self.selectedItems()):
            if isinstance(item, CableSystemItem):
                self._systems.pop(item.system.identifier, None)
            self.removeItem(item)
        self.invalidate()

    def _spawn_item(self, item, position: Optional[QPointF]) -> None:
        pos = position or QPointF(0.0, 0.0)
        item.setPos(pos)
        self.addItem(item)
        if isinstance(item, CableSystemItem):
            item.scene_config = self.config
            best_pos = self._find_available_position(item, item.pos())
            if best_pos != item.pos():
                item.setPos(best_pos)
            self._systems[item.system.identifier] = item
        self.clearSelection()
        item.setSelected(True)
        self.invalidate()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # type: ignore[override]
        painter.fillRect(rect, self.config.background_colour)
        painter.setRenderHint(QPainter.Antialiasing, False)

        self._draw_trench(painter, rect)

        def draw_grid(step: float, colour: QColor) -> None:
            left = int(rect.left() // step - 1)
            right = int(rect.right() // step + 1)
            top = int(rect.top() // step - 1)
            bottom = int(rect.bottom() // step + 1)

            pen = QPen(colour, 0.0)
            pen.setCosmetic(True)
            painter.setPen(pen)

            for x in range(left, right + 1):
                painter.drawLine(x * step, top * step, x * step, bottom * step)
            for y in range(top, bottom + 1):
                painter.drawLine(left * step, y * step, right * step, y * step)

        draw_grid(self.config.minor_grid, self.config.minor_grid_colour)
        draw_grid(self.config.major_grid, self.config.major_grid_colour)

    def systems(self) -> List[CableSystem]:
        """Return the cable system data present in the scene."""
        return [item.system for item in self._systems.values()]

    def system_items(self) -> List[CableSystemItem]:
        return list(self._systems.values())

    def update_trench_geometry(
        self,
        *,
        width_mm: Optional[float] = None,
        depth_mm: Optional[float] = None,
        surface_level_y: Optional[float] = None,
    ) -> None:
        if width_mm is not None and width_mm > 0.0:
            self.config.trench_width_mm = width_mm
        if depth_mm is not None and depth_mm > 0.0:
            self.config.trench_depth_mm = depth_mm
        if surface_level_y is not None:
            self.config.surface_level_y = surface_level_y
        self.refresh_after_config_change()

    def update_trench_layers(self, layers: List[TrenchLayer]) -> None:
        self.config.layers = list(layers)
        self.refresh_after_config_change()

    def refresh_after_config_change(self) -> None:
        for item in self._systems.values():
            item.scene_config = self.config
            item.ensure_valid_position()
            item.update()
        self.invalidate()
        self.update()

    def _default_cable_position(self) -> QPointF:
        y = self.config.surface_level_y + self.config.trench_depth_mm * 0.5
        return QPointF(0.0, y)

    def _build_default_single_core_system(self, index: int) -> CableSystem:
        """Generate a starter single-core system suitable for quick prototyping."""
        name = f"Cable System {index}"
        copper = material_catalog.COPPER
        semicon = material_catalog.SEMI_CONDUCTOR
        xlpe = material_catalog.XLPE
        pvc = material_catalog.PVC
        serving = material_catalog.PE_SERVING

        conductor = ConductorSpec(
            area_mm2=240.0,
            diameter_mm=17.6,
            material=copper,
        )
        layers = [
            LayerSpec(role=LayerRole.INNER_SCREEN, thickness_mm=1.2, material=semicon),
            LayerSpec(role=LayerRole.INSULATION, thickness_mm=5.5, material=xlpe),
            LayerSpec(role=LayerRole.OUTER_SCREEN, thickness_mm=1.2, material=semicon),
            LayerSpec(role=LayerRole.SHEATH, thickness_mm=2.5, material=pvc),
            LayerSpec(role=LayerRole.SERVING, thickness_mm=1.2, material=serving),
        ]

        phase = CablePhase(
            name="Phase",
            conductor=conductor,
            layers=layers,
        )

        return CableSystem(
            name=name,
            kind=CableSystemKind.SINGLE_CORE,
            phase_spacing_mm=150.0,
            arrangement=SingleCoreArrangement.FLAT,
            single_core_phase=phase,
            nominal_voltage_kv=11.0,
        )

    def _layer_colour(self, layer: TrenchLayer) -> QColor:
        base_colours = {
            TrenchLayerKind.GROUND: QColor("#c0a080"),
            TrenchLayerKind.BACKFILL: QColor("#ffe066"),
            TrenchLayerKind.CONCRETE: QColor("#adb5bd"),
            TrenchLayerKind.AIR: QColor("#d0ebff"),
            TrenchLayerKind.CUSTOM: QColor("#ced4da"),
        }
        return base_colours.get(layer.kind, QColor("#ced4da"))

    def _draw_trench(self, painter: QPainter, rect: QRectF) -> None:
        width = self.config.trench_width_mm
        depth = self.config.trench_depth_mm
        surface_y = self.config.surface_level_y
        trench_rect = QRectF(-width / 2.0, surface_y, width, depth)

        painter.save()
        current_y = surface_y
        remaining = depth
        for layer in self.config.layers:
            thickness = max(layer.thickness_mm, 0.0)
            if thickness <= 0.0 or remaining <= 0.0:
                continue
            layer_height = min(thickness, remaining)
            colour = self._layer_colour(layer)
            painter.setPen(QPen(colour.darker(140), 1.0))
            painter.setBrush(colour)
            painter.drawRect(QRectF(-width / 2.0, current_y, width, layer_height))
            current_y += layer_height
            remaining -= layer_height

        if remaining > 0.0:
            fallback_colour = QColor("#d7ccc8")
            painter.setPen(QPen(fallback_colour.darker(140), 1.0))
            painter.setBrush(fallback_colour)
            painter.drawRect(QRectF(-width / 2.0, current_y, width, remaining))

        painter.setPen(QPen(QColor("#795548"), 2.0))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(trench_rect)

        surface_pen = QPen(QColor("#5d4037"), 3.0)
        surface_pen.setCosmetic(True)
        painter.setPen(surface_pen)
        painter.drawLine(trench_rect.left() - width * 0.2, surface_y, trench_rect.right() + width * 0.2, surface_y)
        painter.restore()

    def _find_available_position(self, item: CableSystemItem, start: QPointF) -> QPointF:
        if item.position_is_allowed(start):
            return start

        step = max(self.config.minor_grid, 10.0)
        max_level = 40
        for level in range(1, max_level + 1):
            min_offset = -level
            max_offset = level
            for dx in range(min_offset, max_offset + 1):
                for dy in range(min_offset, max_offset + 1):
                    if abs(dx) != level and abs(dy) != level:
                        continue
                    candidate = QPointF(start.x() + dx * step, start.y() + dy * step)
                    if item.position_is_allowed(candidate):
                        return candidate
        return start
