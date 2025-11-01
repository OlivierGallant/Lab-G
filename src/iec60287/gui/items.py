from __future__ import annotations

from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QGraphicsItem, QGraphicsObject
from iec60287.model import (
    CableSystem,
    CableSystemKind,
    DuctOccupancy,
    LayerRole,
    LayerSpec,
    MaterialClassification,
)

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


class CableSystemItem(BaseGraphicsItem):
    """Composite graphics item representing a three-phase cable system."""

    positionChanged = Signal(QPointF)

    _PHASE_LABELS = ("A", "B", "C")
    _PHASE_COLOURS = (
        QColor("#1864ab"),
        QColor("#c2255c"),
        QColor("#2b8a3e"),
    )
    _DUCT_COLOUR = QColor("#5f5f5f")
    ROLE_COLOURS: Dict[LayerRole, QColor] = {
        LayerRole.INNER_SCREEN: QColor("#495057"),
        LayerRole.OUTER_SCREEN: QColor("#343a40"),
        LayerRole.INSULATION: QColor("#f6b026"),
        LayerRole.SHEATH: QColor("#748ffc"),
        LayerRole.ARMOUR: QColor("#868e96"),
        LayerRole.SERVING: QColor("#d0b36f"),
        LayerRole.JACKET: QColor("#51cf66"),
    }
    CLASSIFICATION_COLOURS: Dict[MaterialClassification, QColor] = {
        MaterialClassification.CONDUCTIVE: QColor("#ad4f2f"),
        MaterialClassification.SEMICONDUCTIVE: QColor("#495057"),
        MaterialClassification.INSULATING: QColor("#f6b026"),
        MaterialClassification.PROTECTIVE: QColor("#748ffc"),
    }

    def __init__(self, system: CableSystem) -> None:
        super().__init__(system.name, z_value=10.0)
        self.system = system
        self._phase_geometry: List[Tuple[QPointF, float]] = []
        self._phase_profiles: List[dict] = []
        self._label_rect = QRectF()
        self._cached_rect = QRectF()
        self.scene_config = None
        self._update_geometry_cache()

    def rename(self, label: str) -> None:
        """Rename both the graphics item and underlying system."""
        self.system.name = label
        super().rename(label)

    def update_system(self, system: CableSystem) -> None:
        """Replace the underlying cable system details."""
        self.prepareGeometryChange()
        self.system = system
        super().rename(system.name)
        self._update_geometry_cache()
        self.ensure_valid_position()
        self.update()

    def boundingRect(self) -> QRectF:  # type: ignore[override]
        return QRectF(self._cached_rect)

    def paint(self, painter: QPainter, _, __) -> None:  # type: ignore[override]
        painter.setRenderHint(QPainter.Antialiasing, True)
        if not self._phase_profiles:
            # Fall back to a placeholder ring when geometry is invalid.
            placeholder_colour = QColor("#868e96")
            painter.setPen(self._pen(placeholder_colour))
            painter.setBrush(Qt.NoBrush)
            painter.drawEllipse(QPointF(0, 0), 30.0, 30.0)
            painter.setPen(Qt.black)
            painter.drawText(self._label_rect, Qt.AlignCenter, self.label())
            return

        for index, profile in enumerate(self._phase_profiles):
            centre: QPointF = profile["centre"]
            conductor_radius: float = profile["conductor_radius"]
            layers: List[tuple[LayerSpec, float, float]] = profile["layers"]

            if profile.get("draw_duct"):
                duct_outer = profile.get("duct_outer_radius") or 0.0
                if duct_outer > 0.0:
                    duct_inner = profile.get("duct_inner_radius") or 0.0
                    duct_colour = QColor(self._DUCT_COLOUR)
                    fill_colour = QColor(duct_colour)
                    fill_colour.setAlpha(30)
                    duct_centre = profile.get("duct_center")
                    if not isinstance(duct_centre, QPointF):
                        duct_centre = centre
                    painter.setPen(self._pen(duct_colour))
                    painter.setBrush(fill_colour)
                    painter.drawEllipse(duct_centre, duct_outer, duct_outer)
                    painter.setBrush(Qt.NoBrush)
                    if duct_inner > 0.0:
                        inner_pen = QPen(duct_colour.darker(140), 1.0, Qt.DotLine)
                        inner_pen.setCosmetic(True)
                        painter.setPen(inner_pen)
                        painter.drawEllipse(duct_centre, duct_inner, duct_inner)
                        painter.setPen(self._pen(duct_colour))

            # Draw layers from outermost to innermost.
            for layer, _inner, outer in reversed(layers):
                colour = self._colour_for_layer(layer)
                painter.setPen(self._pen(colour.darker(160)))
                painter.setBrush(colour)
                painter.drawEllipse(centre, outer, outer)

            # Draw the conductor core last.
            conductor_colour = self._PHASE_COLOURS[index % len(self._PHASE_COLOURS)]
            painter.setPen(self._pen(conductor_colour.darker(180)))
            painter.setBrush(conductor_colour)
            painter.drawEllipse(centre, conductor_radius, conductor_radius)

            label = self._PHASE_LABELS[index % len(self._PHASE_LABELS)]
            font = QFont()
            font.setPointSizeF(9)
            painter.setFont(font)
            painter.setPen(Qt.white)
            label_rect = QRectF(
                centre.x() - conductor_radius,
                centre.y() - conductor_radius,
                conductor_radius * 2.0,
                conductor_radius * 2.0,
            )
            painter.drawText(label_rect, Qt.AlignCenter, label)

        # Draw system label above the phases.
        font = QFont()
        font.setPointSizeF(9)
        painter.setFont(font)
        painter.setPen(Qt.black)
        painter.drawText(self._label_rect, Qt.AlignCenter, self.label())

        if self.isSelected():
            outline = QPen(QColor("#495057"), 1.5, Qt.DashLine)
            outline.setCosmetic(True)
            painter.setPen(outline)
            painter.setBrush(Qt.NoBrush)
            padding = 6.0
            rect = QRectF(self._cached_rect)
            rect.adjust(padding, padding, -padding, -padding)
            painter.drawRect(rect)

    def _update_geometry_cache(self) -> None:
        self._phase_geometry = []
        self._phase_profiles = []

        offsets = list(self.system.phase_offsets_mm()) or [(0.0, 0.0)]

        if self.system.kind is CableSystemKind.SINGLE_CORE and self.system.single_core_phase:
            phase = self.system.single_core_phase
            conductor_radius = phase.conductor.diameter_mm / 2.0
            radial_profile = phase.radial_profile_mm()
            outer_radius = radial_profile[-1][2] if radial_profile else conductor_radius

            duct = self.system.duct if self.system.duct and self.system.duct.has_valid_geometry() else None
            duct_outer_radius = (duct.outer_diameter_mm / 2.0) if duct else None
            duct_inner_radius = (duct.inner_diameter_mm / 2.0) if duct else None
            shared_duct = bool(duct and duct.occupancy is DuctOccupancy.THREE_PHASES_PER_DUCT)
            duct_centre = None
            if shared_duct and offsets:
                avg_x = sum(offset[0] for offset in offsets) / len(offsets)
                avg_y = sum(offset[1] for offset in offsets) / len(offsets)
                duct_centre = QPointF(avg_x, avg_y)

            for index, offset in enumerate(offsets):
                centre = QPointF(*offset)
                envelope_diameter = outer_radius * 2.0
                draw_duct = False
                if duct:
                    if not shared_duct and duct_outer_radius and duct_outer_radius > 0.0:
                        envelope_diameter = max(envelope_diameter, duct_outer_radius * 2.0)
                    draw_duct = (not shared_duct) or index == 0
                self._phase_geometry.append((centre, envelope_diameter))
                layers_copy = [(layer, inner, outer) for layer, inner, outer in radial_profile]
                self._phase_profiles.append(
                    {
                        "centre": centre,
                        "conductor_radius": conductor_radius,
                        "layers": layers_copy,
                        "outer_radius": outer_radius,
                        "duct_outer_radius": duct_outer_radius,
                        "duct_inner_radius": duct_inner_radius,
                        "draw_duct": draw_duct,
                        "duct_center": duct_centre if shared_duct else centre,
                    }
                )

            if shared_duct and duct_outer_radius and duct_outer_radius > 0.0 and duct_centre is not None:
                self._phase_geometry.append((duct_centre, duct_outer_radius * 2.0))
        elif self.system.kind is CableSystemKind.MULTICORE and self.system.multicore:
            multicore = self.system.multicore
            diameter = multicore.outer_diameter_mm
            outer_radius = diameter / 2.0
            for offset in offsets:
                centre = QPointF(*offset)
                self._phase_geometry.append((centre, diameter))
                self._phase_profiles.append(
                    {
                        "centre": centre,
                        "conductor_radius": outer_radius,
                        "layers": [],
                        "outer_radius": outer_radius,
                    }
                )

        geometry = self._phase_geometry
        if geometry:
            min_x = min(centre.x() - diameter / 2.0 for centre, diameter in geometry)
            max_x = max(centre.x() + diameter / 2.0 for centre, diameter in geometry)
            min_y = min(centre.y() - diameter / 2.0 for centre, diameter in geometry)
            max_y = max(centre.y() + diameter / 2.0 for centre, diameter in geometry)
            width = max_x - min_x
            height = max_y - min_y
            padding = 16.0
            label_height = 24.0
            rect_left = min_x - padding
            rect_top = min_y - (padding + label_height)
            rect_width = width + (padding * 2)
            rect_height = height + (padding * 2) + label_height
            self._cached_rect = QRectF(rect_left, rect_top, rect_width, rect_height)
            self._label_rect = QRectF(rect_left, rect_top, rect_width, label_height)
        else:
            # Reserve space for placeholder visuals.
            size = 80.0
            label_height = 24.0
            self._cached_rect = QRectF(-(size / 2.0), -(size / 2.0) - label_height, size, size + label_height)
            self._label_rect = QRectF(
                self._cached_rect.left(),
                self._cached_rect.top(),
                self._cached_rect.width(),
                label_height,
            )

    def _colour_for_layer(self, layer: LayerSpec) -> QColor:
        if layer.role in self.ROLE_COLOURS:
            return QColor(self.ROLE_COLOURS[layer.role])
        return QColor(self.CLASSIFICATION_COLOURS.get(layer.material.classification, QColor("#adb5bd")))

    def shape(self) -> QPainterPath:  # type: ignore[override]
        path = QPainterPath()
        if not self._phase_geometry:
            path.addEllipse(QPointF(0.0, 0.0), 1.0, 1.0)
            return path
        for centre, diameter in self._phase_geometry:
            radius = diameter / 2.0
            path.addEllipse(centre, radius, radius)
        return path

    def ensure_valid_position(self) -> None:
        if not self.scene():
            return
        if self.position_is_allowed(self.pos()):
            return

        finder = getattr(self.scene(), "_find_available_position", None)
        if callable(finder):
            new_pos = finder(self, self.pos())
            if new_pos != self.pos():
                self.setPos(new_pos)
            return

        # Fallback radial search using local logic.
        step = 25.0
        max_level = 40
        start = self.pos()
        for level in range(1, max_level + 1):
            for dx in range(-level, level + 1):
                for dy in range(-level, level + 1):
                    if abs(dx) != level and abs(dy) != level:
                        continue
                    candidate = QPointF(start.x() + dx * step, start.y() + dy * step)
                    if self.position_is_allowed(candidate):
                        self.setPos(candidate)
                        return
    def itemChange(self, change: QGraphicsItem.GraphicsItemChange, value):
        if change == QGraphicsItem.ItemPositionChange and self.scene():
            if isinstance(value, QPointF):
                if not self.position_is_allowed(value):
                    return self.pos()
        result = super().itemChange(change, value)
        if change == QGraphicsItem.ItemPositionHasChanged:
            self.positionChanged.emit(self.pos())
        return result

    def position_is_allowed(self, pos: QPointF) -> bool:
        geometry = self._phase_world_geometry(pos)
        if not geometry:
            return True
        if not self._within_trench(geometry):
            return False
        for other in self._other_system_items():
            for centre, diameter in geometry:
                for other_centre, other_diameter in other._phase_world_geometry():
                    if self._circles_overlap(centre, diameter, other_centre, other_diameter):
                        return False
        return True

    def _phase_world_geometry(self, pos: Optional[QPointF] = None) -> List[Tuple[QPointF, float]]:
        base = pos or self.pos()
        return [(QPointF(base.x() + centre.x(), base.y() + centre.y()), diameter) for centre, diameter in self._phase_geometry]

    def _other_system_items(self) -> List["CableSystemItem"]:
        if not self.scene():
            return []
        items: List[CableSystemItem] = []
        for item in self.scene().items():
            if isinstance(item, CableSystemItem) and item is not self:
                items.append(item)
        return items

    def _circles_overlap(
        self,
        c1: QPointF,
        d1: float,
        c2: QPointF,
        d2: float,
        clearance: float = 0.5,
    ) -> bool:
        radius_sum = (d1 + d2) / 2.0 + clearance
        dx = c1.x() - c2.x()
        dy = c1.y() - c2.y()
        return dx * dx + dy * dy < radius_sum * radius_sum

    def _within_trench(self, geometry: List[Tuple[QPointF, float]]) -> bool:
        if not getattr(self, "scene_config", None):
            return True
        config = self.scene_config
        left = -config.trench_width_mm / 2.0
        right = config.trench_width_mm / 2.0
        top = config.surface_level_y
        bottom = top + config.trench_depth_mm
        for centre, diameter in geometry:
            radius = diameter / 2.0
            if centre.x() - radius < left or centre.x() + radius > right:
                return False
            if centre.y() - radius < top or centre.y() + radius > bottom:
                return False
        return True


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
