from __future__ import annotations

import math
from dataclasses import dataclass
from typing import List, Sequence, Tuple

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


class CableFemAnalyzer:
    """Finite-difference solver operating on structured meshes."""

    def __init__(
        self,
        *,
        max_iterations: int = 5000,
        tolerance_c: float = 1e-3,
    ) -> None:
        self._max_iterations = max(1, max_iterations)
        self._tolerance = max(tolerance_c, 1e-6)

    def solve(
        self,
        mesh: StructuredMesh,
        cable_heat_w_per_m: Sequence[float],
        *,
        ambient_temp_c: float,
        cable_definitions: Sequence[MeshCableDefinition],
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
        dx0 = dx_values[0]
        dy0 = dy_values[0]
        if any(abs(dx - dx0) > 1e-6 for dx in dx_values) or any(abs(dy - dy0) > 1e-6 for dy in dy_values):
            raise NotImplementedError("Variable grid spacing is not supported yet.")
        if abs(dx0 - dy0) > 1e-6:
            raise NotImplementedError("Non-square cells are not supported yet.")

        dx_m = dx0 / 1000.0
        dy_m = dy0 / 1000.0

        temperatures = [
            [ambient_temp_c for _ in range(nx)] for _ in range(ny)
        ]

        heat_cells = [[0.0 for _ in range(nx - 1)] for _ in range(ny - 1)]

        cable_heat = list(cable_heat_w_per_m)
        if len(cable_heat) < len(cable_definitions):
            cable_heat.extend([0.0] * (len(cable_definitions) - len(cable_heat)))

        for index, heat in enumerate(cable_heat):
            if heat <= 0.0:
                continue
            area_m2 = _conductor_area(conductor_index, index, dx_m, dy_m)
            if area_m2 <= 0.0:
                continue
            heat_density = heat / area_m2  # W/m^3
            for j in range(ny - 1):
                row = conductor_index[j]
                heat_row = heat_cells[j]
                for i in range(nx - 1):
                    if row[i] == index:
                        heat_row[i] += heat_density

        factor_cells = [
            [
                (dx_m * dx_m) * max(cell_resistivity[j][i], _MIN_RESISTIVITY)
                for i in range(nx - 1)
            ]
            for j in range(ny - 1)
        ]

        converged = False
        iterations_run = self._max_iterations
        for iteration in range(1, self._max_iterations + 1):
            max_delta = 0.0
            for j in range(1, ny - 1):
                row = temperatures[j]
                row_above = temperatures[j - 1]
                row_below = temperatures[j + 1]
                for i in range(1, nx - 1):
                    neighbours = row_above[i] + row_below[i] + row[i - 1] + row[i + 1]
                    local_heat, local_factor = _local_source_and_factor(
                        heat_cells, factor_cells, j, i
                    )
                    new_temp = 0.25 * (neighbours + local_factor * local_heat)
                    delta = abs(new_temp - row[i])
                    if delta > max_delta:
                        max_delta = delta
                    row[i] = new_temp
            if max_delta < self._tolerance:
                converged = True
                iterations_run = iteration
                break
        else:
            iterations_run = self._max_iterations

        cable_temperatures = _summarise_cable_temperatures(
            temperatures,
            conductor_index,
            cable_definitions,
        )

        max_temp = max((temp for row in temperatures for temp in row), default=ambient_temp_c)
        min_temp = min((temp for row in temperatures for temp in row), default=ambient_temp_c)

        return CableFemResult(
            grid_x_mm=x_nodes,
            grid_y_mm=y_nodes,
            temperatures_c=temperatures,
            max_temp_c=max_temp,
            min_temp_c=min_temp,
            cable_temperatures=cable_temperatures,
            iterations=iterations_run,
            converged=converged,
        )


def _conductor_area(conductor_index: List[List[int]], target_index: int, dx_m: float, dy_m: float) -> float:
    count = sum(row.count(target_index) for row in conductor_index)
    return count * dx_m * dy_m


def _local_source_and_factor(
    heat_cells: List[List[float]],
    factor_cells: List[List[float]],
    node_row: int,
    node_col: int,
) -> Tuple[float, float]:
    heat_samples: List[float] = []
    factor_samples: List[float] = []
    j = node_row
    i = node_col

    neighbours = [
        (j - 1, i - 1),
        (j - 1, i),
        (j, i - 1),
        (j, i),
    ]
    max_row = len(heat_cells)
    max_col = len(heat_cells[0]) if heat_cells else 0

    for cell_row, cell_col in neighbours:
        if 0 <= cell_row < max_row and 0 <= cell_col < max_col:
            heat_samples.append(heat_cells[cell_row][cell_col])
            factor_samples.append(factor_cells[cell_row][cell_col])

    if not heat_samples:
        return 0.0, 0.0

    avg_heat = sum(heat_samples) / len(heat_samples)
    avg_factor = sum(factor_samples) / len(factor_samples)
    return avg_heat, avg_factor


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
