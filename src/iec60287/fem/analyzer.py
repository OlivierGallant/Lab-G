from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence

import numpy as np
from numpy.typing import NDArray

from iec60287.fem.mesh_builder import MeshCableDefinition, StructuredMesh

_MIN_RESISTIVITY = 1e-5
_CG_SUPPORTS_TOL = False
_CG_SUPPORTS_RTOL = False
factorized = None  # type: ignore

try:  # pragma: no cover - import guard exercised at runtime
    import inspect

    from skfem import (
        Basis,
        BilinearForm,
        DiscreteField,
        ElementTriP1,
        LinearForm,
        MeshTri,
        asm,
    )
    from skfem.helpers import dot, grad
    from scipy.sparse.linalg import LinearOperator, cg, factorized, spilu
except ModuleNotFoundError as exc:  # pragma: no cover - lazily reported during solve()
    _IMPORT_ERROR: Optional[ModuleNotFoundError] = exc
else:
    _IMPORT_ERROR = None
    _CG_SUPPORTS_TOL = "tol" in inspect.signature(cg).parameters
    _CG_SUPPORTS_RTOL = "rtol" in inspect.signature(cg).parameters


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


@BilinearForm
def _diffusion_form(u, v, w):
    return w["k"] * dot(grad(u), grad(v))


@LinearForm
def _heat_source_form(v, w):
    return w["q"] * v


class CableFemAnalyzer:
    """Finite element solver leveraging scikit-fem for steady-state conduction."""

    def __init__(
        self,
        *,
        max_iterations: int = 5000,
        tolerance_c: float = 1e-3,
        max_outer_iterations: int = 6,
        heat_tolerance_w_per_m: float = 1e-3,
        prefer_direct_solver: bool = True,
        direct_solver_threshold: int = 125_000,
    ) -> None:
        self._max_iterations = max(1, max_iterations)
        self._tolerance = max(tolerance_c, 1e-9)
        self._max_outer_iterations = max(1, max_outer_iterations)
        self._heat_tolerance = max(heat_tolerance_w_per_m, 0.0)
        self._prefer_direct = prefer_direct_solver
        self._direct_threshold = max(1, direct_solver_threshold)

    def solve(
        self,
        mesh: StructuredMesh,
        loads: Sequence[CableLoad],
        *,
        ambient_temp_c: float,
        progress_callback: Optional[Callable[[float], None]] = None,
    ) -> CableFemResult:
        if _IMPORT_ERROR is not None:
            raise ModuleNotFoundError(
                "CableFemAnalyzer requires scikit-fem and SciPy. "
                "Install them with `pip install scikit-fem scipy`."
            ) from _IMPORT_ERROR

        x_nodes_mm = np.asarray(mesh.x_nodes_mm, dtype=float)
        y_nodes_mm = np.asarray(mesh.y_nodes_mm, dtype=float)
        ny = y_nodes_mm.size
        nx = x_nodes_mm.size

        if ny < 2 or nx < 2:
            raise ValueError("FEM mesh must contain at least a 2x2 grid of nodes.")

        cell_resistivity = np.asarray(mesh.thermal_resistivity_k_m_per_w, dtype=float)
        conductor_index = mesh.conductor_index
        if cell_resistivity.shape != (ny - 1, nx - 1):
            raise ValueError("Thermal resistivity array dimensions do not match the mesh.")
        if len(conductor_index) != ny - 1 or any(len(row) != nx - 1 for row in conductor_index):
            raise ValueError("Conductor index array dimensions do not match the mesh.")

        dx_values = np.diff(x_nodes_mm)
        dy_values = np.diff(y_nodes_mm)
        if np.any(dx_values <= 0.0) or np.any(dy_values <= 0.0):
            raise ValueError("Mesh nodes must increase monotonically.")

        conductivity_cells = 1.0 / np.maximum(cell_resistivity, _MIN_RESISTIVITY)

        heat_values = [max(load.heat_w_per_m, 0.0) for load in loads]
        heat_cells: List[List[float]] = [
            [0.0 for _ in range(nx - 1)] for _ in range(ny - 1)
        ]

        mesh_tri, triangle_to_cell = _build_triangular_mesh(x_nodes_mm, y_nodes_mm)
        basis = Basis(mesh_tri, ElementTriP1())
        nqp = basis.X.shape[-1]

        tri_conductivity = conductivity_cells.reshape(-1)[triangle_to_cell]
        conductivity_field = DiscreteField(
            np.broadcast_to(tri_conductivity[:, None], (mesh_tri.t.shape[1], nqp))
        )
        stiffness_full = asm(_diffusion_form, basis, k=conductivity_field).tocsr()

        total_nodes = nx * ny
        boundary_nodes = _boundary_vertex_indices(nx, ny)
        ambient_vector = np.full(boundary_nodes.size, ambient_temp_c, dtype=float)

        internal_mask = np.ones(total_nodes, dtype=bool)
        internal_mask[boundary_nodes] = False
        internal_nodes = np.nonzero(internal_mask)[0]

        if internal_nodes.size == 0:
            raise ValueError("FEM mesh does not contain any interior nodes to solve for.")

        stiffness_internal = stiffness_full[internal_nodes][:, internal_nodes].tocsr()
        stiffness_ib = stiffness_full[internal_nodes][:, boundary_nodes].tocsr()

        direct_solver: Optional[Callable[[NDArray[np.float64]], NDArray[np.float64]]] = None
        if (
            self._prefer_direct
            and "factorized" in globals()
            and stiffness_internal.shape[0] <= self._direct_threshold
        ):
            try:
                factor = factorized(stiffness_internal.tocsc())
            except Exception:
                factor = None
            if factor is not None:
                direct_solver = factor

        preconditioner: Optional[LinearOperator] = None
        if direct_solver is None:
            try:
                ilu = spilu(stiffness_internal.tocsc(), drop_tol=1e-3, fill_factor=6)
            except Exception:  # pragma: no cover - preconditioner is optional
                ilu = None
            if ilu is not None:
                def _ilu_solve(vector: NDArray[np.float64]) -> NDArray[np.float64]:
                    return ilu.solve(vector)
                preconditioner = LinearOperator(
                    stiffness_internal.shape,
                    matvec=_ilu_solve,
                    dtype=stiffness_internal.dtype,
                )

        prior_solution: Optional[NDArray[np.float64]] = None
        total_iterations = 0
        outer_converged = False
        solver_converged = True
        temperatures_grid = np.full((ny, nx), ambient_temp_c, dtype=float)
        cable_temperatures: Sequence[CableTemperature] = ()

        total_outer = max(self._max_outer_iterations, 1)
        if progress_callback:
            progress_callback(0.0)

        for outer_index in range(total_outer):
            if progress_callback:
                progress_callback(outer_index / total_outer)

            _populate_heat_cells(
                heat_cells,
                conductor_index,
                heat_values,
                dx_values,
                dy_values,
            )

            tri_heat = np.asarray(heat_cells, dtype=float).reshape(-1)[triangle_to_cell]
            heat_field = DiscreteField(
                np.broadcast_to(tri_heat[:, None], (mesh_tri.t.shape[1], nqp))
            )
            load_vector = asm(_heat_source_form, basis, q=heat_field)
            rhs = load_vector[internal_nodes] - stiffness_ib.dot(ambient_vector)

            if direct_solver is not None:
                solution_internal = direct_solver(rhs)
                info = 0
                iterations_this = 1
                solve_converged = True
            else:
                counter = _IterationCounter()
                cg_kwargs: dict[str, object] = {
                    "maxiter": self._max_iterations,
                    "callback": counter,
                }
                if prior_solution is not None:
                    cg_kwargs["x0"] = prior_solution
                if _CG_SUPPORTS_TOL:
                    cg_kwargs["tol"] = self._tolerance
                elif _CG_SUPPORTS_RTOL:
                    cg_kwargs["rtol"] = self._tolerance
                    cg_kwargs.setdefault("atol", 0.0)
                else:  # pragma: no cover - defensive branch for future SciPy changes
                    raise RuntimeError("Unsupported SciPy conjugate gradient signature.")

                if preconditioner is not None:
                    cg_kwargs["M"] = preconditioner

                solution_internal, info = cg(
                    stiffness_internal,
                    rhs,
                    **cg_kwargs,
                )

                if info < 0:
                    raise RuntimeError(f"Conjugate gradient solver failed with info={info}.")

                solve_converged = info == 0
                iterations_this = counter.count
                if iterations_this == 0:
                    iterations_this = 1 if solve_converged else self._max_iterations

            solver_converged = solver_converged and solve_converged
            total_iterations += iterations_this
            prior_solution = solution_internal

            full_solution = np.full(total_nodes, ambient_temp_c, dtype=float)
            full_solution[internal_nodes] = solution_internal
            temperatures_grid = full_solution.reshape((nx, ny)).T

            cable_temperatures = _summarise_cable_temperatures(
                temperatures_grid.tolist(),
                conductor_index,
                [load.definition for load in loads],
            )

            updated = _update_heat_values(
                heat_values,
                loads,
                cable_temperatures,
                self._heat_tolerance,
            )
            if progress_callback:
                progress_callback((outer_index + 1) / total_outer)

            if not updated:
                outer_converged = True
                break

        if progress_callback:
            progress_callback(1.0)

        max_temp = float(np.max(temperatures_grid))
        min_temp = float(np.min(temperatures_grid))

        return CableFemResult(
            grid_x_mm=tuple(map(float, x_nodes_mm)),
            grid_y_mm=tuple(map(float, y_nodes_mm)),
            temperatures_c=temperatures_grid.tolist(),
            max_temp_c=max_temp,
            min_temp_c=min_temp,
            cable_temperatures=cable_temperatures,
            iterations=total_iterations,
            converged=solver_converged and outer_converged,
            heat_w_per_m=tuple(float(value) for value in heat_values),
        )


def _update_heat_values(
    heat_values: List[float],
    loads: Sequence[CableLoad],
    cable_temperatures: Sequence[CableTemperature],
    tolerance_w_per_m: float,
) -> bool:
    updated = False
    temp_map = {entry.label: entry for entry in cable_temperatures}
    for idx, load in enumerate(loads):
        if not load.auto_update:
            continue
        definition = load.definition
        current = definition.nominal_current_a
        if current is None or current <= 0.0:
            continue
        temp_info = temp_map.get(definition.label)
        if not temp_info:
            continue
        resistance = _temperature_dependent_resistance(definition, temp_info.max_temp_c)
        if resistance is None:
            continue
        new_heat = max(current**2 * resistance, 0.0)
        if abs(new_heat - heat_values[idx]) > tolerance_w_per_m:
            heat_values[idx] = new_heat
            updated = True
    return updated


def _build_triangular_mesh(
    x_nodes_mm: NDArray[np.float64],
    y_nodes_mm: NDArray[np.float64],
) -> tuple[MeshTri, NDArray[np.int64]]:
    x = np.asarray(x_nodes_mm, dtype=float) / 1000.0
    y = np.asarray(y_nodes_mm, dtype=float) / 1000.0

    mesh = MeshTri.init_tensor(x, y)

    nx = x_nodes_mm.size - 1
    ny = y_nodes_mm.size - 1
    num_cells = nx * ny

    tri_nodes = mesh.t  # (3, ntri)
    centroids = mesh.p[:, tri_nodes].mean(axis=1)
    x_centroids_mm = centroids[0] * 1000.0
    y_centroids_mm = centroids[1] * 1000.0

    cell_x = np.searchsorted(x_nodes_mm[1:], x_centroids_mm, side="right")
    cell_y = np.searchsorted(y_nodes_mm[1:], y_centroids_mm, side="right")
    cell_x = np.clip(cell_x, 0, nx - 1)
    cell_y = np.clip(cell_y, 0, ny - 1)

    triangle_to_cell = (cell_y * nx + cell_x).astype(np.int64)
    if triangle_to_cell.size != mesh.t.shape[1]:
        raise RuntimeError("Inconsistent triangle-to-cell mapping generated.")
    if np.any(triangle_to_cell < 0) or np.any(triangle_to_cell >= num_cells):
        raise RuntimeError("Triangle-to-cell mapping produced invalid indices.")
    return mesh, triangle_to_cell


def _boundary_vertex_indices(nx: int, ny: int) -> NDArray[np.int64]:
    indices: set[int] = set()
    last_x = nx - 1
    last_y = ny - 1

    # Left and right boundaries: x index 0 and last_x
    for y in range(ny):
        indices.add(y)  # x = 0
        indices.add(last_x * ny + y)  # x = last_x

    # Bottom and top boundaries: y index 0 and last_y
    for x in range(nx):
        indices.add(x * ny)  # y = 0
        indices.add(x * ny + last_y)  # y = last_y

    return np.array(sorted(indices), dtype=np.int64)


class _IterationCounter:
    __slots__ = ("count",)

    def __init__(self) -> None:
        self.count = 0

    def __call__(self, _vector: NDArray[np.float64]) -> None:
        self.count += 1


def _conductor_area(
    conductor_index: Sequence[Sequence[int]],
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
    conductor_index: Sequence[Sequence[int]],
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


def _summarise_cable_temperatures(
    temperatures: Sequence[Sequence[float]],
    conductor_index: Sequence[Sequence[int]],
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
