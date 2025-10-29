from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import QGraphicsObject


class BaseGraphicsItem(QGraphicsObject):
    """Shared behaviour for placement items."""

    def __init__(self, label: str, *, z_value: float = 0.0) -> None:
        super().__init__()
        self._label = label
        self.setFlags(
            QGraphicsObject.ItemIsMovable
            | QGraphicsObject.ItemIsSelectable
            | QGraphicsObject.ItemSendsGeometryChanges
        )
        self.setAcceptHoverEvents(True)
        self.setZValue(z_value)
        self._hovered: bool = False

    def label(self) -> str:
        return self._label

    def rename(self, label: str) -> None:
        self._label = label
        self.update()

    def hoverEnterEvent(self, _) -> None:  # type: ignore[override]
        self._hovered = True
        self.update()

    def hoverLeaveEvent(self, _) -> None:  # type: ignore[override]
        self._hovered = False
        self.update()

    def _pen(self, base_colour: QColor) -> QPen:
        colour = QColor(base_colour)
        if self.isSelected():
            colour = colour.lighter(140)
        elif self._hovered:
            colour = colour.lighter(120)

        pen = QPen(colour, 2.0)
        pen.setCosmetic(True)
        return pen


class CableItem(BaseGraphicsItem):
    """Circular item representing a single cable or phase."""

    def __init__(
        self,
        label: str,
        *,
        radius: float = 20.0,
        colour: Optional[QColor] = None,
    ) -> None:
        super().__init__(label, z_value=10.0)
        self.radius = radius
        self.colour = colour or QColor("#2b8a3e")

    def boundingRect(self) -> QRectF:  # type: ignore[override]
        padding = 3.0
        size = (self.radius * 2) + padding * 2
        return QRectF(-size / 2, -size / 2, size, size)

    def paint(self, painter: QPainter, _, __) -> None:  # type: ignore[override]
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(self._pen(self.colour.darker(150)))
        painter.setBrush(self.colour)
        diameter = self.radius * 2
        painter.drawEllipse(QPointF(0, 0), self.radius, self.radius)

        font = QFont()
        font.setPointSizeF(9)
        painter.setFont(font)
        painter.setPen(Qt.white)
        label_rect = QRectF(-(diameter / 2), -(diameter / 2), diameter, diameter)
        painter.drawText(label_rect, Qt.AlignCenter, self.label())


class BackfillItem(BaseGraphicsItem):
    """Rectangular item representing a backfill region."""

    def __init__(
        self,
        label: str,
        *,
        width: float = 120.0,
        height: float = 80.0,
        colour: Optional[QColor] = None,
    ) -> None:
        super().__init__(label, z_value=1.0)
        self.width = width
        self.height = height
        self.colour = colour or QColor("#adb5bd")

    def boundingRect(self) -> QRectF:  # type: ignore[override]
        padding = 4.0
        return QRectF(
            -(self.width / 2) - padding,
            -(self.height / 2) - padding,
            self.width + padding * 2,
            self.height + padding * 2,
        )

    def paint(self, painter: QPainter, _, __) -> None:  # type: ignore[override]
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setPen(self._pen(self.colour.darker(180)))
        painter.setBrush(self.colour)
        rect = QRectF(-(self.width / 2), -(self.height / 2), self.width, self.height)
        painter.drawRoundedRect(rect, 6.0, 6.0)

        font = QFont()
        font.setPointSizeF(9)
        painter.setFont(font)
        painter.setPen(Qt.black)
        painter.drawText(rect, Qt.AlignCenter, self.label())
