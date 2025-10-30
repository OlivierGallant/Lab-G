from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from iec60287.gui.items import CableSystemItem
from iec60287.gui.placement_scene import PlacementScene, TrenchLayer
from iec60287.model import (
    CablePhase,
    CableSystem,
    CableSystemKind,
    LayerRole,
    LayerSpec,
)

_DEFAULT_LAYER_RESISTIVITY = 1.0
_MIN_RESISTIVITY = 1e-5


@dataclass
class CableLayerRegion:
    """Radial segment within a cable phase."""

    name: str
    outer_radius_mm: float
    thermal_resistivity_k_m_per_w: float


@dataclass
class MeshCableDefinition:
    """Description of a single cable phase for FEM meshing."""

    label: str
    centre_x_mm: float
    centre_y_mm: float
    layers: List[CableLayerRegion]
    conductor_area_mm2: float
    conductor_resistivity_ohm_mm2_per_m: Optional[float]
    conductor_temp_coefficient_per_c: float
    nominal_current_a: Optional[float]

    @property
    def conductor_radius_mm(self) -> float:
        if not self.layers:
            return 0.0
        return self.layers[0].outer_radius_mm

    @property
    def overall_radius_mm(self) -> float:
        if not self.layers:
            return 0.0
        return self.layers[-1].outer_radius_mm


@dataclass
class StructuredMesh:
    """Structured grid used by the FEM solver."""

    x_nodes_mm: List[float]
    y_nodes_mm: List[float]
    thermal_resistivity_k_m_per_w: List[List[float]]
    conductor_index: List[List[int]]


@dataclass
class MeshBuildOutput:
    mesh: StructuredMesh
    cables: List[MeshCableDefinition]


def build_structured_mesh(
    scene: PlacementScene,
    *,
    grid_step_mm: float,
    padding_mm: float,
    default_resistivity_k_m_per_w: float = 1.0,
) -> MeshBuildOutput:
    """Generate a structured mesh reflecting the scene and trench configuration."""
    grid_step_mm = max(grid_step_mm, 5.0)
    padding_mm = max(padding_mm, 50.0)

    cables = _collect_cables(scene)

    x_nodes_mm, y_nodes_mm = _build_domain(scene, cables, grid_step_mm, padding_mm)
    if len(x_nodes_mm) < 2 or len(y_nodes_mm) < 2:
        raise ValueError("FEM mesh domain is degenerate; adjust grid spacing or padding.")

    base_resistivity = _build_base_resistivity(
        scene.config.layers,
        x_nodes_mm,
        y_nodes_mm,
        scene.config.surface_level_y,
        default_resistivity_k_m_per_w,
    )
    conductor_index = [[-1 for _ in range(len(x_nodes_mm) - 1)] for _ in range(len(y_nodes_mm) - 1)]

    for idx, cable in enumerate(cables):
        _apply_cable_regions(cable, x_nodes_mm, y_nodes_mm, base_resistivity, conductor_index, idx)

    mesh = StructuredMesh(
        x_nodes_mm=x_nodes_mm,
        y_nodes_mm=y_nodes_mm,
        thermal_resistivity_k_m_per_w=base_resistivity,
        conductor_index=conductor_index,
    )
    return MeshBuildOutput(mesh=mesh, cables=cables)


def _collect_cables(scene: PlacementScene) -> List[MeshCableDefinition]:
    phase_labels = getattr(CableSystemItem, "_PHASE_LABELS", ("A", "B", "C"))
    cables: List[MeshCableDefinition] = []
    for item in scene.system_items():
        system = item.system
        positions = _phase_centres(system, item)
        for index, (centre_x, centre_y) in enumerate(positions):
            label_suffix = phase_labels[index % len(phase_labels)] if len(positions) > 1 else ""
            label = f"{system.name} {label_suffix}".strip()
            definition = _build_cable_definition(system, label, centre_x, centre_y)
            if definition:
                cables.append(definition)
    return cables


def _phase_centres(system: CableSystem, item: CableSystemItem) -> List[Tuple[float, float]]:
    offsets = list(system.phase_offsets_mm()) or [(0.0, 0.0)]
    origin = item.pos()
    centres: List[Tuple[float, float]] = []
    for offset in offsets:
        centres.append((origin.x() + offset[0], origin.y() + offset[1]))
    return centres


def _build_cable_definition(
    system: CableSystem,
    label: str,
    centre_x_mm: float,
    centre_y_mm: float,
) -> Optional[MeshCableDefinition]:
    if system.kind is CableSystemKind.SINGLE_CORE and system.single_core_phase:
        phase = system.single_core_phase
        layers = _build_layers_from_phase(phase)
        if not layers:
            return None
        conductor = phase.conductor
        return MeshCableDefinition(
            label=label,
            centre_x_mm=centre_x_mm,
            centre_y_mm=centre_y_mm,
            layers=layers,
            conductor_area_mm2=conductor.area_mm2,
            conductor_resistivity_ohm_mm2_per_m=conductor.electrical_resistivity(),
            conductor_temp_coefficient_per_c=conductor.material.temp_coefficient_per_c or 0.0,
            nominal_current_a=system.nominal_current_a,
        )

    if system.kind is CableSystemKind.MULTICORE and system.multicore:
        multicore = system.multicore
        phase = multicore.phase
        layers = _build_layers_from_phase(phase)
        outer_radius = multicore.outer_diameter_mm / 2.0
        if not layers:
            # Fallback to a single region covering the cable
            layers = [
                CableLayerRegion(
                    name="Cable",
                    outer_radius_mm=outer_radius,
                    thermal_resistivity_k_m_per_w=_DEFAULT_LAYER_RESISTIVITY,
                )
            ]
        if layers[-1].outer_radius_mm < outer_radius:
            layers[-1].outer_radius_mm = outer_radius
        conductor = phase.conductor
        return MeshCableDefinition(
            label=label,
            centre_x_mm=centre_x_mm,
            centre_y_mm=centre_y_mm,
            layers=layers,
            conductor_area_mm2=conductor.area_mm2,
            conductor_resistivity_ohm_mm2_per_m=conductor.electrical_resistivity(),
            conductor_temp_coefficient_per_c=conductor.material.temp_coefficient_per_c or 0.0,
            nominal_current_a=system.nominal_current_a,
        )

    return None


def _build_layers_from_phase(phase: CablePhase) -> List[CableLayerRegion]:
    layers: List[CableLayerRegion] = []
    conductor = phase.conductor
    conductor_radius = conductor.diameter_mm / 2.0
    conductor_res = _clamped_resistivity(conductor.thermal_resistivity() or conductor.material.thermal_resistivity_k_m_per_w)
    if conductor_res is None:
        conductor_res = _MIN_RESISTIVITY
    layers.append(
        CableLayerRegion(
            name="Conductor",
            outer_radius_mm=conductor_radius,
            thermal_resistivity_k_m_per_w=conductor_res,
        )
    )

    for layer_spec, _inner_radius, outer_radius in phase.radial_profile_mm():
        res = _clamped_resistivity(layer_spec.thermal_resistivity())
        if res is None:
            res = _clamped_resistivity(layer_spec.material.thermal_resistivity_k_m_per_w)
        if res is None:
            res = _DEFAULT_LAYER_RESISTIVITY
        layers.append(
            CableLayerRegion(
                name=_layer_label(layer_spec),
                outer_radius_mm=outer_radius,
                thermal_resistivity_k_m_per_w=res,
            )
        )
    return layers


def _layer_label(layer_spec: LayerSpec) -> str:
    role = layer_spec.role if isinstance(layer_spec.role, LayerRole) else None
    if role is None:
        return "Layer"
    return role.name.replace("_", " ").title()


def _build_domain(
    scene: PlacementScene,
    cables: Sequence[MeshCableDefinition],
    grid_step_mm: float,
    padding_mm: float,
) -> Tuple[List[float], List[float]]:
    half_trench_width = max(scene.config.trench_width_mm / 2.0, 10.0)
    surface_y = scene.config.surface_level_y
    trench_depth = max(scene.config.trench_depth_mm, 10.0)

    if cables:
        min_cable_x = min(cable.centre_x_mm - cable.overall_radius_mm for cable in cables)
        max_cable_x = max(cable.centre_x_mm + cable.overall_radius_mm for cable in cables)
        min_x = min(-half_trench_width, min_cable_x) - padding_mm
        max_x = max(half_trench_width, max_cable_x) + padding_mm
        max_cable_y = max(cable.centre_y_mm + cable.overall_radius_mm for cable in cables)
    else:
        min_x = -half_trench_width - padding_mm
        max_x = half_trench_width + padding_mm
        max_cable_y = surface_y + trench_depth

    min_y = surface_y - padding_mm
    max_y = max(max_cable_y, surface_y + trench_depth) + padding_mm

    x_nodes: List[float] = []
    value = min_x
    while value < max_x:
        x_nodes.append(value)
        value += grid_step_mm
    if not x_nodes or x_nodes[-1] < max_x:
        x_nodes.append(max_x)

    y_nodes: List[float] = []
    value = min_y
    while value < max_y:
        y_nodes.append(value)
        value += grid_step_mm
    if not y_nodes or y_nodes[-1] < max_y:
        y_nodes.append(max_y)

    return x_nodes, y_nodes


def _build_base_resistivity(
    layers: Sequence[TrenchLayer],
    x_nodes_mm: Sequence[float],
    y_nodes_mm: Sequence[float],
    surface_level_y: float,
    default_resistivity_k_m_per_w: float,
) -> List[List[float]]:
    resistivity: List[List[float]] = []
    y_centres = _cell_centres(y_nodes_mm)
    x_centres = _cell_centres(x_nodes_mm)

    for cy in y_centres:
        row: List[float] = []
        depth = cy - surface_level_y
        layer_resistivity = _resistivity_for_depth(
            layers,
            depth,
            default_resistivity_k_m_per_w,
        )
        for _ in x_centres:
            row.append(layer_resistivity)
        resistivity.append(row)
    return resistivity


def _cell_centres(nodes: Sequence[float]) -> List[float]:
    centres: List[float] = []
    for index in range(len(nodes) - 1):
        centres.append((nodes[index] + nodes[index + 1]) * 0.5)
    return centres


def _resistivity_for_depth(
    layers: Sequence[TrenchLayer],
    depth_mm: float,
    default_resistivity_k_m_per_w: float,
) -> float:
    if not layers:
        return max(default_resistivity_k_m_per_w, _MIN_RESISTIVITY)

    if depth_mm < 0.0:
        res = _clamped_resistivity(layers[0].thermal_resistivity_k_m_per_w)
        if res is None:
            res = default_resistivity_k_m_per_w
        return max(res, _MIN_RESISTIVITY)

    remaining = depth_mm
    for layer in layers:
        thickness = max(layer.thickness_mm, 0.0)
        if remaining <= thickness:
            res = _clamped_resistivity(layer.thermal_resistivity_k_m_per_w)
            if res is None:
                res = default_resistivity_k_m_per_w
            return max(res, _MIN_RESISTIVITY)
        remaining -= thickness

    res = _clamped_resistivity(layers[-1].thermal_resistivity_k_m_per_w)
    if res is None:
        res = default_resistivity_k_m_per_w
    return max(res, _MIN_RESISTIVITY)


def _apply_cable_regions(
    cable: MeshCableDefinition,
    x_nodes_mm: Sequence[float],
    y_nodes_mm: Sequence[float],
    resistivity: List[List[float]],
    conductor_index: List[List[int]],
    cable_idx: int,
) -> None:
    if not cable.layers:
        return

    x_centres = _cell_centres(x_nodes_mm)
    y_centres = _cell_centres(y_nodes_mm)

    overall_radius = cable.overall_radius_mm
    conductor_radius = cable.conductor_radius_mm
    if overall_radius <= 0.0:
        return

    min_x = cable.centre_x_mm - overall_radius
    max_x = cable.centre_x_mm + overall_radius
    min_y = cable.centre_y_mm - overall_radius
    max_y = cable.centre_y_mm + overall_radius

    x_indices = [
        i for i, cx in enumerate(x_centres) if min_x - 1e-6 <= cx <= max_x + 1e-6
    ]
    y_indices = [
        j for j, cy in enumerate(y_centres) if min_y - 1e-6 <= cy <= max_y + 1e-6
    ]

    for j in y_indices:
        cy = y_centres[j]
        for i in x_indices:
            cx = x_centres[i]
            distance = math.hypot(cx - cable.centre_x_mm, cy - cable.centre_y_mm)
            half_width = 0.5 * (x_nodes_mm[i + 1] - x_nodes_mm[i])
            half_height = 0.5 * (y_nodes_mm[j + 1] - y_nodes_mm[j])
            half_diag = math.hypot(half_width, half_height)
            if distance > overall_radius + half_diag:
                continue
            region_resistivity = _region_resistivity(cable.layers, distance)
            resistivity[j][i] = region_resistivity
            if distance <= conductor_radius + half_diag:
                conductor_index[j][i] = cable_idx


def _region_resistivity(layers: Sequence[CableLayerRegion], radius_mm: float) -> float:
    for layer in layers:
        if radius_mm <= layer.outer_radius_mm + 1e-9:
            return max(layer.thermal_resistivity_k_m_per_w, _MIN_RESISTIVITY)
    return max(layers[-1].thermal_resistivity_k_m_per_w, _MIN_RESISTIVITY)


def _clamped_resistivity(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if value <= 0.0:
        return _MIN_RESISTIVITY
    return value
