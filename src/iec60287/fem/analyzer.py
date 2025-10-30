from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple

from iec60287.fem.mesh_builder import MeshCableDefinition, StructuredMesh

_MIN_RESISTIVITY = 1e-5


@dataclass
class CableTemperature:
    label: str
    max_temp_c: float
    average_temp_c: float


@dataclass
class CableFemResult:
    """Computed thermal field for the analysed cable arrangement."""

    grid_x_mm: Sequence[float]
    grid_y_mm: Sequence[float]
    temperatures_c: Sequence[Sequence[float]]
    max_temp_c: float
    min_temp_c: float
    cable_temperatures: Sequence[CableTemperature]
    iterations: int
    converged: bool
    heat_w_per_m: Sequence[float]


@dataclass
class CableLoad:
    definition: MeshCableDefinition
    heat_w_per_m: float
    auto_update: bool


class CableFemAnalyzer:
    """Finite-difference solver operating on structured meshes."""

    def __init__(
        self,
        *,
        max_iterations: int = 5000,
        tolerance_c: float = 1e-3,
        max_outer_iterations: int = 6,
        heat_tolerance_w_per_m: float = 1e-3,
    ) -> None:
        self._max_iterations = max(1, max_iterations)
        self._tolerance = max(tolerance_c, 1e-6)
        self._max_outer_iterations = max(1, max_outer_iterations)
        self._heat_tolerance = max(heat_tolerance_w_per_m, 0.0)

    def solve(
        self,
        mesh: StructuredMesh,
        loads: Sequence[CableLoad],
        *,
        ambient_temp_c: float,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> CableFemResult:
        x_nodes = list(mesh.x_nodes_mm)
        y_nodes = list(mesh.y_nodes_mm)
        ny = len(y_nodes)
        nx = len(x_nodes)

        if ny < 2 or nx < 2:
            raise ValueError("FEM mesh must contain at least a 2x2 grid of nodes.")

        cell_resistivity = mesh.thermal_resistivity_k_m_per_w
        conductor_index = mesh.conductor_index
        if len(cell_resistivity) != ny - 1 or any(len(row) != nx - 1 for row in cell_resistivity):
            raise ValueError("Thermal resistivity array dimensions do not match the mesh.")
        if len(conductor_index) != ny - 1 or any(len(row) != nx - 1 for row in conductor_index):
            raise ValueError("Conductor index array dimensions do not match the mesh.")

        dx_values = [x_nodes[i + 1] - x_nodes[i] for i in range(nx - 1)]
        dy_values = [y_nodes[j + 1] - y_nodes[j] for j in range(ny - 1)]
        if not dx_values or not dy_values:
            raise ValueError("Invalid mesh spacing.")

        temperatures = [
            [ambient_temp_c for _ in range(nx)] for _ in range(ny)
        ]

        heat_cells = [[0.0 for _ in range(nx - 1)] for _ in range(ny - 1)]
        conductivity_cells = [
            [1.0 / max(cell_resistivity[j][i], _MIN_RESISTIVITY) for i in range(nx - 1)]
            for j in range(ny - 1)
        ]

        heat_values = [max(load.heat_w_per_m, 0.0) for load in loads]

        total_iterations = 0
        converged = False
        cable_temperatures: List[CableTemperature] = []

        total_outer = max(self._max_outer_iterations, 1)
        if progress_callback:
            progress_callback(0.0)

        for outer in range(self._max_outer_iterations):
            inner_callback: Optional[Callable[[float], None]] = None
            if progress_callback:
                def _inner_progress_factory(base_index: int) -> Callable[[float], None]:
                    def _inner(fraction: float) -> None:
                        clamped = max(0.0, min(1.0, fraction))
                        progress_callback(min(1.0, (base_index + clamped) / total_outer))
                    return _inner

                inner_callback = _inner_progress_factory(outer)

            _populate_heat_cells(heat_cells, conductor_index, heat_values, dx_values, dy_values)
            iterations_run = _iterate_temperature(
                temperatures,
                ambient_temp_c,
                heat_cells,
                conductivity_cells,
                x_nodes,
                y_nodes,
                self._max_iterations,
                self._tolerance,
                inner_callback,
            )
            total_iterations += iterations_run
            if iterations_run < self._max_iterations:
                converged = True

            cable_temperatures = _summarise_cable_temperatures(
                temperatures,
                conductor_index,
                [load.definition for load in loads],
            )

            updated = False
            heat_map = {load.definition.label: heat_values[idx] for idx, load in enumerate(loads)}
            temp_map = {temp.label: temp for temp in cable_temperatures}
            for idx, load in enumerate(loads):
                if not load.auto_update:
                    continue
                definition = load.definition
                current = definition.nominal_current_a
                if current is None or current <= 0.0:
                    continue
                temperature_info = temp_map.get(definition.label)
                if not temperature_info:
                    continue
                resistance = _temperature_dependent_resistance(
                    definition,
                    temperature_info.max_temp_c,
                )
                if resistance is None:
                    continue
                new_heat = max(current ** 2 * resistance, 0.0)
                if abs(new_heat - heat_values[idx]) > self._heat_tolerance:
                    updated = True
                    heat_values[idx] = new_heat
            if not updated:
                break
        if progress_callback:
            progress_callback(1.0)

        max_temp = max((temp for row in temperatures for temp in row), default=ambient_temp_c)
        min_temp = min((temp for row in temperatures for temp in row), default=ambient_temp_c)

        return CableFemResult(
            grid_x_mm=x_nodes,
            grid_y_mm=y_nodes,
            temperatures_c=temperatures,
            max_temp_c=max_temp,
            min_temp_c=min_temp,
            cable_temperatures=cable_temperatures,
            iterations=total_iterations,
            converged=converged,
            heat_w_per_m=heat_values,
        )


def _conductor_area(
    conductor_index: List[List[int]],
    target_index: int,
    dx_values: Sequence[float],
    dy_values: Sequence[float],
) -> float:
    area = 0.0
    for j, row in enumerate(conductor_index):
        if j >= len(dy_values):
            continue
        dy = dy_values[j] / 1000.0
        for i, value in enumerate(row):
            if i >= len(dx_values):
                break
            if value == target_index:
                area += (dx_values[i] / 1000.0) * dy
    return area


def _populate_heat_cells(
    heat_cells: List[List[float]],
    conductor_index: List[List[int]],
    heat_values: Sequence[float],
    dx_values: Sequence[float],
    dy_values: Sequence[float],
) -> None:
    ny = len(heat_cells)
    nx = len(heat_cells[0]) if heat_cells else 0
    for j in range(ny):
        row = heat_cells[j]
        for i in range(nx):
            row[i] = 0.0

    for idx, heat in enumerate(heat_values):
        if heat <= 0.0:
            continue
        area_m2 = _conductor_area(conductor_index, idx, dx_values, dy_values)
        if area_m2 <= 0.0:
            continue
        heat_density = heat / area_m2  # W/m^3
        for j in range(ny):
            index_row = conductor_index[j]
            heat_row = heat_cells[j]
            for i in range(nx):
                if index_row[i] == idx:
                    heat_row[i] += heat_density


def _temperature_dependent_resistance(
    definition: MeshCableDefinition,
    temperature_c: float,
) -> Optional[float]:
    area = max(definition.conductor_area_mm2, 0.0)
    resistivity = definition.conductor_resistivity_ohm_mm2_per_m
    if area <= 0.0 or resistivity is None or resistivity <= 0.0:
        return None
    alpha = definition.conductor_temp_coefficient_per_c or 0.0
    rho_theta = resistivity * (1.0 + alpha * (temperature_c - 20.0))
    resistance = rho_theta / area
    return resistance if resistance > 0.0 else None


def _average_heat_density(
    heat_cells: Sequence[Sequence[float]],
    j: int,
    i: int,
) -> float:
    samples: List[float] = []
    ny = len(heat_cells)
    nx = len(heat_cells[0]) if ny else 0
    positions = (
        (j - 1, i - 1),
        (j - 1, i),
        (j, i - 1),
        (j, i),
    )
    for row, col in positions:
        if 0 <= row < ny and 0 <= col < nx:
            samples.append(heat_cells[row][col])
    if not samples:
        return 0.0
    return sum(samples) / len(samples)


def _face_conductivity(
    conductivity_cells: Sequence[Sequence[float]],
    j: int,
    i: int,
    direction: str,
) -> float:
    ny = len(conductivity_cells)
    nx = len(conductivity_cells[0]) if ny else 0
    vals: List[float] = []
    if direction == "east":
        positions = ((j - 1, i), (j, i))
    elif direction == "west":
        positions = ((j - 1, i - 1), (j, i - 1))
    elif direction == "north":
        positions = ((j - 1, i - 1), (j - 1, i))
    elif direction == "south":
        positions = ((j, i - 1), (j, i))
    else:
        positions = ()
    for row, col in positions:
        if 0 <= row < ny and 0 <= col < nx:
            vals.append(conductivity_cells[row][col])
    if not vals:
        return 0.0
    return sum(vals) / len(vals)


def _summarise_cable_temperatures(
    temperatures: List[List[float]],
    conductor_index: List[List[int]],
    cable_definitions: Sequence[MeshCableDefinition],
) -> List[CableTemperature]:
    ny = len(conductor_index)
    nx = len(conductor_index[0]) if conductor_index else 0
    summary: List[List[float]] = [[] for _ in cable_definitions]

    for j in range(ny):
        for i in range(nx):
            idx = conductor_index[j][i]
            if idx < 0 or idx >= len(summary):
                continue
            cell_temp = (
                temperatures[j][i]
                + temperatures[j][i + 1]
                + temperatures[j + 1][i]
                + temperatures[j + 1][i + 1]
            ) * 0.25
            summary[idx].append(cell_temp)

    results: List[CableTemperature] = []
    for idx, temps in enumerate(summary):
        if not temps:
            continue
        label = cable_definitions[idx].label
        results.append(
            CableTemperature(
                label=label,
                max_temp_c=max(temps),
                average_temp_c=sum(temps) / len(temps),
            )
        )
    return results


def _iterate_temperature(
    temperatures: List[List[float]],
    ambient_temp_c: float,
    heat_cells: List[List[float]],
    conductivity_cells: List[List[float]],
    x_nodes: Sequence[float],
    y_nodes: Sequence[float],
    max_iterations: int,
    tolerance_c: float,
    progress_callback: Optional[Callable[[float], None]] = None,
) -> int:
    ny = len(temperatures)
    nx = len(temperatures[0]) if temperatures else 0

    # Reset boundaries to ambient
    for j in range(ny):
        temperatures[j][0] = ambient_temp_c
        temperatures[j][nx - 1] = ambient_temp_c
    for i in range(nx):
        temperatures[0][i] = ambient_temp_c
        temperatures[ny - 1][i] = ambient_temp_c

    if progress_callback:
        progress_callback(0.0)

    iterations_run = max_iterations
    for iteration in range(1, max_iterations + 1):
        max_delta = 0.0
        for j in range(1, ny - 1):
            row = temperatures[j]
            row_above = temperatures[j - 1]
            row_below = temperatures[j + 1]
            for i in range(1, nx - 1):
                dx_w = x_nodes[i] - x_nodes[i - 1]
                dx_e = x_nodes[i + 1] - x_nodes[i]
                dy_s = y_nodes[j] - y_nodes[j - 1]
                dy_n = y_nodes[j + 1] - y_nodes[j]

                dy_avg = 0.5 * (dy_n + dy_s)
                dx_avg = 0.5 * (dx_e + dx_w)

                k_w = _face_conductivity(conductivity_cells, j, i, "west")
                k_e = _face_conductivity(conductivity_cells, j, i, "east")
                k_s = _face_conductivity(conductivity_cells, j, i, "south")
                k_n = _face_conductivity(conductivity_cells, j, i, "north")

                a_w = k_w * dy_avg / max(dx_w, 1e-9)
                a_e = k_e * dy_avg / max(dx_e, 1e-9)
                a_s = k_s * dx_avg / max(dy_s, 1e-9)
                a_n = k_n * dx_avg / max(dy_n, 1e-9)

                q_avg = _average_heat_density(heat_cells, j, i)
                volume = (dx_avg / 1000.0) * (dy_avg / 1000.0)
                source = q_avg * volume

                denominator = a_w + a_e + a_s + a_n
                if denominator <= 0.0:
                    new_temp = ambient_temp_c
                else:
                    new_temp = (
                        a_w * row[i - 1]
                        + a_e * row[i + 1]
                        + a_s * row_below[i]
                        + a_n * row_above[i]
                        + source
                    ) / denominator
                delta = abs(new_temp - row[i])
                if delta > max_delta:
                    max_delta = delta
                row[i] = new_temp
        if progress_callback:
            progress_callback(iteration / max_iterations)
        if max_delta < tolerance_c:
            iterations_run = iteration
            break
    else:
        iterations_run = max_iterations

    return iterations_run
