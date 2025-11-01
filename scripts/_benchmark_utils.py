from __future__ import annotations

import math
from typing import Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import QPointF

from iec60287.gui.placement_scene import SceneConfig, TrenchLayer, TrenchLayerKind
from iec60287.model import (
    CablePhase,
    CableSystem,
    CableSystemKind,
    ConductorSpec,
    DuctSpecification,
    LayerRole,
    LayerSpec,
    SingleCoreArrangement,
    materials as material_catalog,
)


def make_generic_phase() -> CablePhase:
    """Return a pre-configured 240 mm² copper single-core phase."""

    copper = material_catalog.COPPER
    semicon = material_catalog.SEMI_CONDUCTOR
    xlpe = material_catalog.XLPE
    pvc = material_catalog.PVC
    serving = material_catalog.PE_SERVING

    conductor_diameter_mm = 17.6
    conductor_area_mm2 = 240.0
    cross_section_area_mm2 = math.pi * (conductor_diameter_mm / 2.0) ** 2
    filling_grade = min(max(conductor_area_mm2 / cross_section_area_mm2, 0.0), 1.0)

    conductor = ConductorSpec(
        area_mm2=conductor_area_mm2,
        diameter_mm=conductor_diameter_mm,
        material=copper,
        filling_grade=filling_grade,
    )

    layers = [
        LayerSpec(role=LayerRole.INNER_SCREEN, thickness_mm=1.2, material=semicon),
        LayerSpec(role=LayerRole.INSULATION, thickness_mm=5.5, material=xlpe),
        LayerSpec(role=LayerRole.OUTER_SCREEN, thickness_mm=1.2, material=semicon),
        LayerSpec(role=LayerRole.SHEATH, thickness_mm=2.5, material=pvc),
        LayerSpec(role=LayerRole.SERVING, thickness_mm=1.2, material=serving),
    ]

    return CablePhase(name="Phase", conductor=conductor, layers=layers)


def make_cable_system(
    name: str,
    *,
    phase: Optional[CablePhase] = None,
    arrangement: SingleCoreArrangement = SingleCoreArrangement.TREFOIL,
    spacing_mm: Optional[float] = None,
    duct: Optional[DuctSpecification] = None,
) -> CableSystem:
    phase = phase or make_generic_phase()
    spacing = spacing_mm or phase.overall_diameter_mm()
    return CableSystem(
        name=name,
        kind=CableSystemKind.SINGLE_CORE,
        phase_spacing_mm=spacing,
        arrangement=arrangement,
        single_core_phase=phase,
        duct=duct,
    )


def make_scene_config(
    *,
    depth_mm: float = 1200.0,
    soil_resistivity_k_m_per_w: float = 1.5,
    surface_level_y: float = 0.0,
) -> SceneConfig:
    return SceneConfig(
        trench_depth_mm=depth_mm,
        surface_level_y=surface_level_y,
        layers=[
            TrenchLayer(
                name="Benchmark Soil",
                kind=TrenchLayerKind.GROUND,
                thickness_mm=depth_mm,
                thermal_resistivity_k_m_per_w=soil_resistivity_k_m_per_w,
            )
        ],
    )


class BenchmarkItem:
    """Minimal stand-in for CableSystemItem used by benchmark scripts."""

    def __init__(self, system: CableSystem, position_mm: Tuple[float, float]) -> None:
        self.system = system
        self._pos = QPointF(float(position_mm[0]), float(position_mm[1]))

    def pos(self) -> QPointF:
        return QPointF(self._pos)


class BenchmarkScene:
    """Stub scene providing the interface required by calculators and FEM code."""

    def __init__(self, config: SceneConfig, items: Iterable[BenchmarkItem]) -> None:
        self.config = config
        self._items: List[BenchmarkItem] = list(items)

    def system_items(self) -> List[BenchmarkItem]:
        return list(self._items)


def make_benchmark_scene(
    systems: Sequence[CableSystem],
    *,
    config: Optional[SceneConfig] = None,
    axis_depth_mm: Optional[float] = None,
    lateral_spacing_mm: float = 300.0,
) -> BenchmarkScene:
    config = config or make_scene_config()
    axis_y = config.surface_level_y + (axis_depth_mm if axis_depth_mm is not None else config.trench_depth_mm / 2.0)
    items = []
    for index, system in enumerate(systems):
        x = index * lateral_spacing_mm
        items.append(BenchmarkItem(system, (x, axis_y)))
    return BenchmarkScene(config, items)

