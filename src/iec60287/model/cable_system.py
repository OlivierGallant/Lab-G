from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Iterable, List, Optional, Sequence


class CableSystemKind(Enum):
    """High level grouping for a three-phase system."""

    SINGLE_CORE = "single_core"
    MULTICORE = "multicore"


class SingleCoreArrangement(Enum):
    """Allowed geometrical placement for single-core systems."""

    FLAT = "flat"
    TREFOIL = "trefoil"


class SheathBonding(Enum):
    """Simplified bonding options that influence IEC 60287 losses."""

    SINGLE_POINT = "single_point"
    BOTH_ENDS = "both_ends"
    CROSS = "cross"
    NONE = "none"


class MaterialClassification(Enum):
    """Electrical/thermal behaviour of a material."""

    CONDUCTIVE = "conductive"
    SEMICONDUCTIVE = "semiconductive"
    INSULATING = "insulating"
    PROTECTIVE = "protective"


class LayerRole(Enum):
    """Canonical positions for radial layers around a conductor."""

    INNER_SCREEN = "inner_screen"
    INSULATION = "insulation"
    OUTER_SCREEN = "outer_screen"
    SHEATH = "sheath"
    ARMOUR = "armour"
    SERVING = "serving"
    JACKET = "jacket"

    def order_index(self) -> int:
        order = {
            LayerRole.INNER_SCREEN: 10,
            LayerRole.INSULATION: 20,
            LayerRole.OUTER_SCREEN: 30,
            LayerRole.SHEATH: 40,
            LayerRole.ARMOUR: 50,
            LayerRole.SERVING: 60,
            LayerRole.JACKET: 70,
        }
        return order[self]


@dataclass(frozen=True)
class Material:
    """Physical properties describing either conducting or insulating media."""

    name: str
    classification: MaterialClassification
    electrical_resistivity_ohm_mm2_per_m: Optional[float] = None
    thermal_resistivity_k_m_per_w: Optional[float] = None
    temp_coefficient_per_c: Optional[float] = None
    max_operating_temp_c: Optional[float] = None
    notes: Optional[str] = None

    def is_conductive(self) -> bool:
        return self.classification in (
            MaterialClassification.CONDUCTIVE,
            MaterialClassification.SEMICONDUCTIVE,
        )


@dataclass
class LayerSpec:
    """Layer wrapped around the conductor/core."""

    role: LayerRole
    thickness_mm: float
    material: Material


@dataclass
class ConductorSpec:
    """Basic conductor dimensions and properties."""

    area_mm2: float
    diameter_mm: float
    material: Material


@dataclass
class CablePhase:
    """One phase of a cable system (single-core or a core inside a multicore)."""

    name: str
    conductor: ConductorSpec
    layers: List[LayerSpec] = field(default_factory=list)
    rated_voltage_kv: Optional[float] = None

    def get_layer(self, role: LayerRole) -> Optional[LayerSpec]:
        for layer in self.layers:
            if layer.role is role:
                return layer
        return None

    def set_layer(self, role: LayerRole, layer: Optional[LayerSpec]) -> None:
        existing_index: Optional[int] = None
        for idx, existing in enumerate(self.layers):
            if existing.role is role:
                existing_index = idx
                break

        if layer is None:
            if existing_index is not None:
                self.layers.pop(existing_index)
            return

        if layer.role is not role:
            raise ValueError("Layer role mismatch when assigning layer to phase.")

        if existing_index is not None:
            self.layers[existing_index] = layer
            return

        insert_at = len(self.layers)
        for idx, existing in enumerate(self.layers):
            if existing.role.order_index() > role.order_index():
                insert_at = idx
                break
        self.layers.insert(insert_at, layer)

    def radial_profile_mm(self) -> List[tuple[LayerSpec, float, float]]:
        """Return [(layer, inner_radius_mm, outer_radius_mm)] for drawing/calculations."""
        profile: List[tuple[LayerSpec, float, float]] = []
        radius = self.conductor.diameter_mm / 2.0
        for layer in self.layers:
            inner = radius
            radius += layer.thickness_mm
            profile.append((layer, inner, radius))
        return profile

    def overall_diameter_mm(self) -> float:
        """Return the finished phase diameter including outer jacket."""
        radius = self.conductor.diameter_mm / 2.0
        for layer in self.layers:
            radius += layer.thickness_mm
        return radius * 2.0


@dataclass
class MultiCoreCable:
    """Represents the constructed cable for the multicore case."""

    outer_diameter_mm: float
    phase: CablePhase
    armour: Optional[LayerSpec] = None
    bedding: Optional[LayerSpec] = None
    sheath_bonding: SheathBonding = SheathBonding.BOTH_ENDS


@dataclass
class CableSystem:
    """Canonical representation of a three-phase cable system ready for placement."""

    name: str
    kind: CableSystemKind
    phase_spacing_mm: float
    identifier: str = field(default_factory=lambda: uuid.uuid4().hex)
    arrangement: Optional[SingleCoreArrangement] = None
    single_core_phase: Optional[CablePhase] = None
    multicore: Optional[MultiCoreCable] = None
    nominal_current_a: Optional[float] = None
    nominal_voltage_kv: Optional[float] = None

    def phase_diameters_mm(self) -> Sequence[float]:
        """Return the relevant phase diameters for drawing or checks."""
        if self.kind is CableSystemKind.SINGLE_CORE:
            if not self.single_core_phase:
                return ()
            diameter = self.single_core_phase.overall_diameter_mm()
            return (diameter, diameter, diameter)
        if self.kind is CableSystemKind.MULTICORE:
            if not self.multicore:
                return ()
            return (self.multicore.outer_diameter_mm,)
        return ()

    def phase_offsets_mm(self) -> Sequence[tuple[float, float]]:
        """Return relative centre positions for each phase in the system."""
        if self.kind is CableSystemKind.SINGLE_CORE:
            arrangement = self.arrangement or SingleCoreArrangement.FLAT
            spacing = self.phase_spacing_mm
            if arrangement is SingleCoreArrangement.FLAT:
                offsets = (-spacing, 0.0), (0.0, 0.0), (spacing, 0.0)
                return offsets
            if arrangement is SingleCoreArrangement.TREFOIL:
                # Equilateral triangle with side length equal to spacing.
                r = spacing / math.sqrt(3.0)
                return (
                    (0.0, 2.0 * r / 3.0),
                    (-spacing / 2.0, -r / 3.0),
                    (spacing / 2.0, -r / 3.0),
                )
        elif self.kind is CableSystemKind.MULTICORE:
            return ((0.0, 0.0),)
        return ()

    def validate(self) -> Iterable[str]:
        """Yield human readable validation issues."""
        if self.kind is CableSystemKind.SINGLE_CORE:
            if self.single_core_phase is None:
                yield "Single-core system requires `single_core_phase` details."
            if self.phase_spacing_mm <= 0:
                yield "Phase spacing must be positive for single-core systems."
            if self.arrangement is None:
                yield "Single-core system must specify a placement arrangement."
            phase = self.single_core_phase
            if phase:
                if phase.get_layer(LayerRole.INSULATION) is None:
                    yield "Single-core phase requires an insulation layer."
                if not phase.conductor.material.is_conductive():
                    yield "Conductor material must be conductive."
        elif self.kind is CableSystemKind.MULTICORE:
            if self.multicore is None:
                yield "Multicore system requires `multicore` cable details."
            if self.phase_spacing_mm <= 0:
                yield "Use the overall diameter as phase spacing for multicore systems."
