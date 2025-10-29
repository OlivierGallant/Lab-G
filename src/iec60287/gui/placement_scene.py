from __future__ import annotations

from dataclasses import dataclass, field
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
    surface_level_y: float = -300.0


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

    def add_backfill(self, position: Optional[QPointF] = None) -> BackfillItem:
        self._backfill_count += 1
        label = f"Backfill {self._backfill_count}"
        item = BackfillItem(label)
        spawn_pos = position or self._default_backfill_position()
        self._spawn_item(item, spawn_pos)
        return item

    def remove_selected(self) -> None:
        for item in list(self.selectedItems()):
            if isinstance(item, CableSystemItem):
                self._systems.pop(item.system.identifier, None)
            self.removeItem(item)

    def _spawn_item(self, item, position: Optional[QPointF]) -> None:
        pos = position or QPointF(0.0, 0.0)
        item.setPos(pos)
        self.addItem(item)
        if isinstance(item, CableSystemItem):
            item.scene_config = self.config
            best_pos = self._find_available_position(item, item.pos())
            if best_pos != item.pos():
                item.setPos(best_pos)
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

    def _default_cable_position(self) -> QPointF:
        y = self.config.surface_level_y + self.config.trench_depth_mm * 0.5
        return QPointF(0.0, y)

    def _default_backfill_position(self) -> QPointF:
        y = self.config.surface_level_y + self.config.trench_depth_mm * 0.25
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

    def _draw_trench(self, painter: QPainter, rect: QRectF) -> None:
        width = self.config.trench_width_mm
        depth = self.config.trench_depth_mm
        surface_y = self.config.surface_level_y
        trench_rect = QRectF(-width / 2.0, surface_y, width, depth)

        painter.save()
        painter.setPen(QPen(QColor("#795548"), 2.0))
        painter.setBrush(QColor("#d7ccc8"))
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
