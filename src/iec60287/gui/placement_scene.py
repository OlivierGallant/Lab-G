from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QGraphicsScene

from iec60287.gui.items import BackfillItem, CableItem


@dataclass
class SceneConfig:
    scene_size: float = 2000.0  # mm
    minor_grid: float = 25.0
    major_grid: float = 100.0
    background_colour: QColor = field(default_factory=lambda: QColor("#f8f9fa"))
    minor_grid_colour: QColor = field(default_factory=lambda: QColor("#dee2e6"))
    major_grid_colour: QColor = field(default_factory=lambda: QColor("#adb5bd"))


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

    def add_cable(self, position: Optional[QPointF] = None) -> CableItem:
        self._cable_count += 1
        label = f"Cable {self._cable_count}"
        item = CableItem(label)
        self._spawn_item(item, position)
        return item

    def add_backfill(self, position: Optional[QPointF] = None) -> BackfillItem:
        self._backfill_count += 1
        label = f"Backfill {self._backfill_count}"
        item = BackfillItem(label)
        self._spawn_item(item, position)
        return item

    def remove_selected(self) -> None:
        for item in list(self.selectedItems()):
            self.removeItem(item)

    def _spawn_item(self, item, position: Optional[QPointF]) -> None:
        pos = position or QPointF(0.0, 0.0)
        item.setPos(pos)
        self.addItem(item)
        self.clearSelection()
        item.setSelected(True)
        self.invalidate()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # type: ignore[override]
        painter.fillRect(rect, self.config.background_colour)
        painter.setRenderHint(QPainter.Antialiasing, False)

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
