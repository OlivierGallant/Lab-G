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
    insulation_thickness_mm: Optional[float]
    layer_thicknesses_mm: List[Tuple[LayerRole, float]]

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
        layers, thicknesses = _build_layers_from_phase(phase)
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
            insulation_thickness_mm=_find_layer_thickness(thicknesses, LayerRole.INSULATION),
            layer_thicknesses_mm=list(thicknesses),
        )

    if system.kind is CableSystemKind.MULTICORE and system.multicore:
        multicore = system.multicore
        phase = multicore.phase
        layers, thicknesses = _build_layers_from_phase(phase)
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
            thicknesses = [(LayerRole.SHEATH, outer_radius)]
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
            insulation_thickness_mm=_find_layer_thickness(thicknesses, LayerRole.INSULATION),
            layer_thicknesses_mm=list(thicknesses),
        )

    return None


def _build_layers_from_phase(phase: CablePhase) -> Tuple[List[CableLayerRegion], List[Tuple[LayerRole, float]]]:
    layers: List[CableLayerRegion] = []
    thicknesses: List[Tuple[LayerRole, float]] = []
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

    previous_radius = conductor_radius
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
        thicknesses.append((layer_spec.role, max(outer_radius - previous_radius, 0.0)))
        previous_radius = outer_radius
    return layers, thicknesses


def _layer_label(layer_spec: LayerSpec) -> str:
    role = layer_spec.role if isinstance(layer_spec.role, LayerRole) else None
    if role is None:
        return "Layer"
    return role.name.replace("_", " ").title()


def _find_layer_thickness(thicknesses: Sequence[Tuple[LayerRole, float]], target: LayerRole) -> Optional[float]:
    for role, thickness in thicknesses:
        if role is target:
            return thickness if thickness > 0.0 else None
    return None


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
        min_x = min(c.centre_x_mm - max(padding_mm, 10.0 * c.overall_radius_mm) for c in cables)
        max_x = max(c.centre_x_mm + max(padding_mm, 10.0 * c.overall_radius_mm) for c in cables)
        min_y = min(c.centre_y_mm - max(padding_mm, 10.0 * c.overall_radius_mm) for c in cables)
        max_y = max(c.centre_y_mm + max(padding_mm, 10.0 * c.overall_radius_mm) for c in cables)
        min_x = min(min_x, -half_trench_width - padding_mm)
        max_x = max(max_x, half_trench_width + padding_mm)
        max_trench_y = surface_y + trench_depth + padding_mm
        max_y = max(max_y, max_trench_y)
        min_y = min(min_y, surface_y - max(padding_mm, 10.0 * max(c.overall_radius_mm for c in cables)))
    else:
        min_x = -half_trench_width - padding_mm
        max_x = half_trench_width + padding_mm
        min_y = surface_y - padding_mm
        max_y = surface_y + trench_depth + padding_mm

    gap_limit = _minimum_gap_spacing(cables)
    growth = 1.2
    far_spacing = max(grid_step_mm, 10.0)

    x_nodes = _generate_axis_nodes(
        cables,
        min_x,
        max_x,
        axis="x",
        growth_factor=growth,
        gap_limit=gap_limit,
        default_far_spacing=far_spacing,
    )

    y_nodes = _generate_axis_nodes(
        cables,
        min_y,
        max_y,
        axis="y",
        growth_factor=growth,
        gap_limit=gap_limit,
        default_far_spacing=far_spacing,
    )

    # Ensure key horizontal interfaces exist
    if surface_y not in y_nodes:
        y_nodes.append(surface_y)
    bottom_trench = surface_y + trench_depth
    if bottom_trench not in y_nodes:
        y_nodes.append(bottom_trench)

    x_nodes = _uniformize_axis_nodes(x_nodes)
    y_nodes = _uniformize_axis_nodes(y_nodes)

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


def _minimum_gap_spacing(cables: Sequence[MeshCableDefinition]) -> Optional[float]:
    min_limit: Optional[float] = None
    for index, first in enumerate(cables):
        for second in cables[index + 1 :]:
            dx = first.centre_x_mm - second.centre_x_mm
            dy = first.centre_y_mm - second.centre_y_mm
            centre_distance = math.hypot(dx, dy)
            separation = centre_distance - (first.overall_radius_mm + second.overall_radius_mm)
            if separation <= 0.0:
                continue
            candidate = separation * 0.15
            if min_limit is None or candidate < min_limit:
                min_limit = candidate
    return min_limit


def _near_field_spacing(cable: MeshCableDefinition, gap_limit: Optional[float]) -> float:
    candidates: List[float] = []
    conductor_diameter = cable.layers[0].outer_radius_mm * 2.0 if cable.layers else 0.0
    if conductor_diameter > 0.0:
        candidates.append(conductor_diameter / 40.0)
        circumference = 2.0 * math.pi * (conductor_diameter / 2.0)
        if circumference > 0.0:
            candidates.append(circumference / 96.0)
    insulation = cable.insulation_thickness_mm
    if insulation and insulation > 0.0:
        candidates.append(insulation / 12.0)
    for role, thickness in cable.layer_thicknesses_mm:
        if thickness <= 0.0:
            continue
        required = 12 if role is LayerRole.INSULATION else 6
        candidates.append(thickness / required)
    if gap_limit is not None and gap_limit > 0.0:
        candidates.append(gap_limit)
    candidates = [value for value in candidates if value > 0.0]
    if not candidates:
        return 5.0
    return max(0.1, min(candidates))


def _soil_spacing(cable: MeshCableDefinition, default_far_spacing: float) -> float:
    diameter = cable.overall_radius_mm * 2.0
    return max(default_far_spacing, diameter * 0.5)


def _generate_axis_nodes(
    cables: Sequence[MeshCableDefinition],
    min_bound: float,
    max_bound: float,
    *,
    axis: str,
    growth_factor: float,
    gap_limit: Optional[float],
    default_far_spacing: float,
) -> List[float]:
    if not cables:
        return [min_bound, max_bound]

    positions: set[float] = {min_bound, max_bound}
    eps = 1e-6
    for cable in cables:
        center = cable.centre_x_mm if axis == "x" else cable.centre_y_mm
        positions.add(center)

        near_spacing = _near_field_spacing(cable, gap_limit)
        far_spacing = max(_soil_spacing(cable, default_far_spacing), near_spacing)

        # positive direction
        step = near_spacing
        distance = near_spacing
        while center + distance < max_bound - eps:
            positions.add(center + distance)
            step = min(step * growth_factor, far_spacing)
            distance += step

        # negative direction
        step = near_spacing
        distance = near_spacing
        while center - distance > min_bound + eps:
            positions.add(center - distance)
            step = min(step * growth_factor, far_spacing)
            distance += step

    return sorted(positions)


def _uniformize_axis_nodes(nodes: Sequence[float]) -> List[float]:
    unique = sorted(set(nodes))
    if len(unique) < 2:
        return list(unique)
    diffs = [unique[i + 1] - unique[i] for i in range(len(unique) - 1)]
    positive_diffs = [diff for diff in diffs if diff > 1e-6]
    if not positive_diffs:
        return list(unique)
    min_step = min(positive_diffs)
    start = unique[0]
    end = unique[-1]
    span = max(end - start, min_step)

    max_cells = 1600
    min_allowed_step = span / max_cells
    step = max(min_step, min_allowed_step)

    steps = max(1, int(round(span / step)))
    step = span / steps if steps > 0 else step

    result = [start + i * step for i in range(steps + 1)]
    result.append(end)
    return sorted(set(result))
