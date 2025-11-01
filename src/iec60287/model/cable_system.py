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
    electrical_resistivity_override_ohm_mm2_per_m: Optional[float] = None
    thermal_resistivity_override_k_m_per_w: Optional[float] = None
    filling_grade: Optional[float] = None

    def electrical_resistivity(self) -> Optional[float]:
        if self.electrical_resistivity_override_ohm_mm2_per_m is not None:
            return self.electrical_resistivity_override_ohm_mm2_per_m
        return self.material.electrical_resistivity_ohm_mm2_per_m

    def thermal_resistivity(self) -> Optional[float]:
        if self.thermal_resistivity_override_k_m_per_w is not None:
            return self.thermal_resistivity_override_k_m_per_w
        return self.material.thermal_resistivity_k_m_per_w


@dataclass
class ConductorSpec:
    """Basic conductor dimensions and properties."""

    area_mm2: float
    diameter_mm: float
    material: Material
    electrical_resistivity_override_ohm_mm2_per_m: Optional[float] = None
    thermal_resistivity_override_k_m_per_w: Optional[float] = None
    filling_grade: Optional[float] = None

    def electrical_resistivity(self) -> Optional[float]:
        if self.electrical_resistivity_override_ohm_mm2_per_m is not None:
            return self.electrical_resistivity_override_ohm_mm2_per_m
        return self.material.electrical_resistivity_ohm_mm2_per_m

    def thermal_resistivity(self) -> Optional[float]:
        if self.thermal_resistivity_override_k_m_per_w is not None:
            return self.thermal_resistivity_override_k_m_per_w
        return self.material.thermal_resistivity_k_m_per_w


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


class DuctOccupancy(Enum):
    """Supported ways of assigning phases to ducts/pipes."""

    SINGLE_PHASE_PER_DUCT = "single_phase_per_duct"
    THREE_PHASES_PER_DUCT = "three_phases_per_duct"

    def phases_in_single_duct(self) -> int:
        if self is DuctOccupancy.THREE_PHASES_PER_DUCT:
            return 3
        return 1


@dataclass(frozen=True)
class DuctContactConstants:
    """Installation-dependent factors for cable-to-duct thermal resistance."""

    u: float
    v: float
    y: float

    def denominator(self, medium_temp_c: float) -> float:
        """Return the multiplier [1 + 0.1 (V + Y θ_m)]."""
        return 0.1 * (self.v + self.y * medium_temp_c)


@dataclass(frozen=True)
class DuctMaterial:
    """Physical properties for duct wall materials."""

    name: str
    thermal_resistivity_k_m_per_w: float
    is_metallic: bool = False
    notes: Optional[str] = None
    contact_defaults: DuctContactConstants = field(
        default_factory=lambda: DuctContactConstants(u=0.086, v=0.60, y=0.0)
    )


@dataclass
class DuctSpecification:
    """Defines a surrounding duct or pipe for a cable system."""

    material: DuctMaterial
    inner_diameter_mm: float
    wall_thickness_mm: float
    occupancy: DuctOccupancy = DuctOccupancy.SINGLE_PHASE_PER_DUCT
    contact_override: Optional[DuctContactConstants] = None
    medium_temperature_c: float = 20.0

    @property
    def outer_diameter_mm(self) -> float:
        return max(self.inner_diameter_mm + 2.0 * self.wall_thickness_mm, 0.0)

    def equivalent_cable_diameter_mm(self, cable_diameter_mm: float) -> float:
        """Return De for T4' based on the occupied cores."""
        if self.occupancy is DuctOccupancy.THREE_PHASES_PER_DUCT:
            return 2.15 * cable_diameter_mm
        return cable_diameter_mm

    def has_valid_geometry(self) -> bool:
        return self.inner_diameter_mm > 0.0 and self.outer_diameter_mm > self.inner_diameter_mm

    def contact_constants(self) -> DuctContactConstants:
        if self.contact_override is not None:
            return self.contact_override
        return self.material.contact_defaults


PLASTIC_CONTACT = DuctContactConstants(u=1.87, v=0.312, y=0.003)
EARTHENWARE_CONTACT = DuctContactConstants(u=1.87, v=0.28, y=0.003)
WATER_FILLED_CONTACT = DuctContactConstants(u=0.1, v=0.03, y=0.001)
GENERIC_CONTACT = DuctContactConstants(u=0.086, v=0.60, y=0.0)

HDPE_DUCT = DuctMaterial(
    name="HDPE",
    thermal_resistivity_k_m_per_w=3.5,
    notes="High-density polyethylene conduit.",
    contact_defaults=PLASTIC_CONTACT,
)
PVC_DUCT = DuctMaterial(
    name="PVC",
    thermal_resistivity_k_m_per_w=5.0,
    notes="Rigid polyvinyl chloride duct.",
    contact_defaults=PLASTIC_CONTACT,
)
CONCRETE_DUCT = DuctMaterial(
    name="Concrete",
    thermal_resistivity_k_m_per_w=1.5,
    notes="Precast concrete duct segment.",
    contact_defaults=EARTHENWARE_CONTACT,
)
EARTHENWARE_DUCT = DuctMaterial(
    name="Earthenware",
    thermal_resistivity_k_m_per_w=2.0,
    notes="Traditional earthenware duct.",
    contact_defaults=EARTHENWARE_CONTACT,
)
WATER_FILLED_DUCT = DuctMaterial(
    name="Water filled",
    thermal_resistivity_k_m_per_w=0.6,
    notes="Duct completely filled with water.",
    contact_defaults=WATER_FILLED_CONTACT,
)
STEEL_DUCT = DuctMaterial(
    name="Steel",
    thermal_resistivity_k_m_per_w=0.0,
    is_metallic=True,
    notes="Galvanised steel pipe; assume negligible wall resistance.",
    contact_defaults=GENERIC_CONTACT,
)

STANDARD_DUCT_MATERIALS: Sequence[DuctMaterial] = (
    HDPE_DUCT,
    PVC_DUCT,
    CONCRETE_DUCT,
    EARTHENWARE_DUCT,
    WATER_FILLED_DUCT,
    STEEL_DUCT,
)


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
    duct: Optional[DuctSpecification] = None

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
                    (0.0, 2.0 * -r / math.sqrt(3.0)),
                    (-spacing / 2.0, r / 3.0),
                    (spacing / 2.0, r / 3.0),
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
            if self.duct:
                if not self.duct.has_valid_geometry():
                    yield "Duct requires positive inner diameter and wall thickness."
                phase = self.single_core_phase
                if phase:
                    cable_diameter = phase.overall_diameter_mm()
                    if self.duct.inner_diameter_mm <= cable_diameter:
                        yield "Duct inner diameter must exceed cable diameter."
                if self.duct.medium_temperature_c < -50.0:
                    yield "Duct medium temperature appears unrealistically low."
        elif self.kind is CableSystemKind.MULTICORE:
            if self.multicore is None:
                yield "Multicore system requires `multicore` cable details."
            if self.phase_spacing_mm <= 0:
                yield "Use the overall diameter as phase spacing for multicore systems."
