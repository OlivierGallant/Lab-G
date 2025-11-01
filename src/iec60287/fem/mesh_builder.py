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
    DuctOccupancy,
    DuctSpecification,
    LayerRole,
    LayerSpec,
)

_DEFAULT_LAYER_RESISTIVITY = 1.0
_MIN_RESISTIVITY = 1e-5
_AIR_GAP_RESISTIVITY = 25.0


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
    surface_level_y: float


@dataclass
class MeshBuildOutput:
    mesh: StructuredMesh
    cables: List[MeshCableDefinition]


@dataclass
class MeshDuctDefinition:
    centre_x_mm: float
    centre_y_mm: float
    inner_radius_mm: float
    outer_radius_mm: float
    fill_resistivity_k_m_per_w: float
    wall_resistivity_k_m_per_w: float


def build_structured_mesh(
    scene: PlacementScene,
    *,
    grid_step_mm: float,
    padding_mm: float,
    max_growth_ratio: float = 0.5,
    default_resistivity_k_m_per_w: float = 1.0,
) -> MeshBuildOutput:
    """
    Generate a structured mesh reflecting the scene and trench configuration.

    Args:
        scene: Active placement scene.
        grid_step_mm: Minimum mesh spacing near conductors.
        padding_mm: Extra distance added around the outermost cables.
        max_growth_ratio: Multiplier limiting how quickly element size may expand
            as distance from cables or material interfaces increases. A value of
            0.5 allows cell widths up to 0.5 * distance from the nearest relevant
            object (cables, layer boundaries, surface) while never dropping below
            `grid_step_mm`.
        default_resistivity_k_m_per_w: Fallback soil resistivity when none is provided.
    """
    grid_step_mm = max(grid_step_mm, 5.0)
    padding_mm = max(padding_mm, 50.0)
    max_growth_ratio = max(max_growth_ratio, 0.0)

    cables = _collect_cables(scene)
    ducts = _collect_ducts(scene)

    x_nodes_mm, y_nodes_mm = _build_domain(scene, cables, grid_step_mm, padding_mm)
    if len(x_nodes_mm) < 2 or len(y_nodes_mm) < 2:
        raise ValueError("FEM mesh domain is degenerate; adjust grid spacing or padding.")

    x_relevant = [c.centre_x_mm for c in cables]
    for cable in cables:
        radius = cable.overall_radius_mm
        if radius > 0.0:
            x_relevant.extend((cable.centre_x_mm - radius, cable.centre_x_mm + radius))
    x_relevant.extend(duct.centre_x_mm for duct in ducts)
    for duct in ducts:
        x_relevant.extend((duct.centre_x_mm - duct.outer_radius_mm, duct.centre_x_mm + duct.outer_radius_mm))

    y_relevant = _relevant_y_positions(scene, cables)
    for cable in cables:
        radius = cable.overall_radius_mm
        if radius > 0.0:
            y_relevant.extend((cable.centre_y_mm - radius, cable.centre_y_mm + radius))
    for duct in ducts:
        y_relevant.extend(
            (
                duct.centre_y_mm - duct.outer_radius_mm,
                duct.centre_y_mm - duct.inner_radius_mm,
                duct.centre_y_mm + duct.inner_radius_mm,
                duct.centre_y_mm + duct.outer_radius_mm,
            )
        )

    x_nodes_mm = _enforce_spacing_growth(
        x_nodes_mm,
        relevant_positions=x_relevant,
        growth_ratio=max_growth_ratio,
        min_step=grid_step_mm,
    )
    x_nodes_mm = _uniformize_axis_nodes(x_nodes_mm)
    y_nodes_mm = _enforce_spacing_growth(
        y_nodes_mm,
        relevant_positions=y_relevant,
        growth_ratio=max_growth_ratio,
        min_step=grid_step_mm,
    )
    y_nodes_mm = _uniformize_axis_nodes(y_nodes_mm)

    base_resistivity = _build_base_resistivity(
        scene.config.layers,
        x_nodes_mm,
        y_nodes_mm,
        scene.config.surface_level_y,
        default_resistivity_k_m_per_w,
    )
    conductor_index = [[-1 for _ in range(len(x_nodes_mm) - 1)] for _ in range(len(y_nodes_mm) - 1)]

    if ducts:
        _apply_duct_regions(ducts, x_nodes_mm, y_nodes_mm, base_resistivity)

    for idx, cable in enumerate(cables):
        _apply_cable_regions(cable, x_nodes_mm, y_nodes_mm, base_resistivity, conductor_index, idx)

    mesh = StructuredMesh(
        x_nodes_mm=x_nodes_mm,
        y_nodes_mm=y_nodes_mm,
        thermal_resistivity_k_m_per_w=base_resistivity,
        conductor_index=conductor_index,
        surface_level_y=scene.config.surface_level_y,
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


def _collect_ducts(scene: PlacementScene) -> List[MeshDuctDefinition]:
    ducts: List[MeshDuctDefinition] = []
    for item in scene.system_items():
        system = item.system
        duct = system.duct
        if duct is None or not duct.has_valid_geometry():
            continue
        inner_radius = max(duct.inner_diameter_mm * 0.5, 0.0)
        outer_radius = max(duct.outer_diameter_mm * 0.5, inner_radius)
        wall_res = max(duct.material.thermal_resistivity_k_m_per_w, _MIN_RESISTIVITY)
        fill_res = max(_duct_fill_resistivity(duct), _MIN_RESISTIVITY)

        offsets = list(system.phase_offsets_mm()) or [(0.0, 0.0)]
        centres = [(item.pos().x() + dx, item.pos().y() + dy) for dx, dy in offsets]

        if duct.occupancy is DuctOccupancy.THREE_PHASES_PER_DUCT:
            avg_x = sum(cx for cx, _ in centres) / len(centres)
            avg_y = sum(cy for _, cy in centres) / len(centres)
            ducts.append(
                MeshDuctDefinition(
                    centre_x_mm=avg_x,
                    centre_y_mm=avg_y,
                    inner_radius_mm=inner_radius,
                    outer_radius_mm=outer_radius,
                    fill_resistivity_k_m_per_w=fill_res,
                    wall_resistivity_k_m_per_w=wall_res,
                )
            )
        else:
            for centre_x_mm, centre_y_mm in centres:
                ducts.append(
                    MeshDuctDefinition(
                        centre_x_mm=centre_x_mm,
                        centre_y_mm=centre_y_mm,
                        inner_radius_mm=inner_radius,
                        outer_radius_mm=outer_radius,
                        fill_resistivity_k_m_per_w=fill_res,
                        wall_resistivity_k_m_per_w=wall_res,
                    )
                )
    return ducts


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
        cable_min_x = min(c.centre_x_mm - c.overall_radius_mm for c in cables)
        cable_max_x = max(c.centre_x_mm + c.overall_radius_mm for c in cables)
        max_radius = max((c.overall_radius_mm for c in cables), default=0.0)
        lateral_margin = max(padding_mm, 10.0 * max_radius)

        min_x = cable_min_x - lateral_margin
        max_x = cable_max_x + lateral_margin
        min_x = min(min_x, -half_trench_width - padding_mm)
        max_x = max(max_x, half_trench_width + padding_mm)

        vertical_margin = max(padding_mm, 10.0 * max_radius)
        cable_bottom = max(c.centre_y_mm + vertical_margin for c in cables)
        domain_width = max(max_x - min_x, grid_step_mm * 4.0)
        trench_span = scene.config.trench_width_mm + 2.0 * padding_mm
        target_depth = max(
            scene.config.trench_depth_mm + padding_mm,
            trench_span,
            domain_width,
        )
        min_y = surface_y
        max_y = max(cable_bottom, surface_y + target_depth)
    else:
        min_x = -half_trench_width - padding_mm
        max_x = half_trench_width + padding_mm
        min_y = surface_y
        max_y = surface_y + max(
            trench_depth + padding_mm,
            scene.config.trench_width_mm + 2.0 * padding_mm,
        )

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


def _apply_duct_regions(
    ducts: Sequence[MeshDuctDefinition],
    x_nodes_mm: Sequence[float],
    y_nodes_mm: Sequence[float],
    resistivity: List[List[float]],
) -> None:
    if not ducts:
        return

    x_centres = _cell_centres(x_nodes_mm)
    y_centres = _cell_centres(y_nodes_mm)

    for duct in ducts:
        inner_radius = max(duct.inner_radius_mm, 0.0)
        outer_radius = max(duct.outer_radius_mm, inner_radius)
        fill_res = max(duct.fill_resistivity_k_m_per_w, _MIN_RESISTIVITY)
        wall_res = max(duct.wall_resistivity_k_m_per_w, _MIN_RESISTIVITY)

        min_x = duct.centre_x_mm - outer_radius
        max_x = duct.centre_x_mm + outer_radius
        min_y = duct.centre_y_mm - outer_radius
        max_y = duct.centre_y_mm + outer_radius

        x_indices = [i for i, cx in enumerate(x_centres) if min_x - 1e-6 <= cx <= max_x + 1e-6]
        y_indices = [j for j, cy in enumerate(y_centres) if min_y - 1e-6 <= cy <= max_y + 1e-6]

        for j in y_indices:
            cy = y_centres[j]
            for i in x_indices:
                cx = x_centres[i]
                distance = math.hypot(cx - duct.centre_x_mm, cy - duct.centre_y_mm)
                half_width = 0.5 * (x_nodes_mm[i + 1] - x_nodes_mm[i])
                half_height = 0.5 * (y_nodes_mm[j + 1] - y_nodes_mm[j])
                half_diag = math.hypot(half_width, half_height)
                if distance > outer_radius + half_diag:
                    continue
                if distance <= inner_radius + half_diag:
                    resistivity[j][i] = fill_res
                else:
                    resistivity[j][i] = wall_res


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
        far_growth_factor = max(growth_factor * 1.3, growth_factor + 0.2)
        far_limit_multiplier = 6.0
        near_extent = max(4.0 * cable.overall_radius_mm, 150.0)
        far_spacing_cap = max(far_spacing * far_limit_multiplier, far_spacing)

        # positive direction
        step = near_spacing
        distance = near_spacing
        while center + distance < max_bound - eps:
            positions.add(center + distance)
            if distance < near_extent:
                step = min(step * growth_factor, far_spacing)
            else:
                step = min(step * far_growth_factor, far_spacing_cap)
            distance += step

        # negative direction
        step = near_spacing
        distance = near_spacing
        while center - distance > min_bound + eps:
            positions.add(center - distance)
            if distance < near_extent:
                step = min(step * growth_factor, far_spacing)
            else:
                step = min(step * far_growth_factor, far_spacing_cap)
            distance += step

    return sorted(positions)


def _uniformize_axis_nodes(nodes: Sequence[float]) -> List[float]:
    if not nodes:
        return []

    sorted_nodes = sorted(nodes)
    result: List[float] = [sorted_nodes[0]]

    # Merge nodes that are effectively identical due to floating-point noise
    tolerance = 1e-4
    for value in sorted_nodes[1:]:
        if value - result[-1] > tolerance:
            result.append(value)
        else:
            # Preserve the extremal value when collapsing near-duplicates
            result[-1] = max(result[-1], value)

    if result[-1] != sorted_nodes[-1]:
        result[-1] = sorted_nodes[-1]

    return result


def _relevant_y_positions(
    scene: PlacementScene,
    cables: Sequence[MeshCableDefinition],
) -> List[float]:
    positions: List[float] = [scene.config.surface_level_y]
    cumulative = scene.config.surface_level_y
    for layer in scene.config.layers:
        thickness = max(layer.thickness_mm, 0.0)
        cumulative += thickness
        positions.append(cumulative)
    positions.extend(c.centre_y_mm for c in cables)
    return positions


def _enforce_spacing_growth(
    nodes: Sequence[float],
    *,
    relevant_positions: Sequence[float],
    growth_ratio: float,
    min_step: float,
) -> List[float]:
    if len(nodes) < 2:
        return list(nodes)
    if growth_ratio <= 0.0 or not relevant_positions:
        return list(nodes)

    current_nodes = sorted(set(nodes))
    relevant = set(relevant_positions)
    relevant.add(current_nodes[0])
    relevant.add(current_nodes[-1])
    relevant = sorted(relevant)
    if not relevant:
        return current_nodes

    min_step = max(min_step, 1e-6)
    tolerance = 1e-9
    changed = True
    while changed:
        changed = False
        new_nodes: List[float] = [current_nodes[0]]
        for a, b in zip(current_nodes, current_nodes[1:]):
            interval = b - a
            if interval <= 0.0:
                continue
            midpoint = 0.5 * (a + b)
            dist = min(abs(midpoint - ref) for ref in relevant)
            allowed = max(min_step, dist * growth_ratio)
            if interval <= allowed * (1.0 + tolerance):
                new_nodes.append(b)
                continue
            segments = max(1, int(math.ceil(interval / allowed)))
            step = interval / segments
            for idx in range(1, segments + 1):
                new_nodes.append(a + step * idx)
            changed = True
        current_nodes = new_nodes
    return current_nodes


def _duct_fill_resistivity(duct: DuctSpecification) -> float:
    name = duct.material.name.lower() if duct.material.name else ""
    if "water" in name:
        return duct.material.thermal_resistivity_k_m_per_w
    if duct.material.is_metallic:
        return 1.0
    return _AIR_GAP_RESISTIVITY
