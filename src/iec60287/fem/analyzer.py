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
    from scipy.sparse.linalg import LinearOperator, cg, factorized, spilu, splu, spsolve
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
    top_flux_w_per_m: float
    side_flux_w_per_m: float
    bottom_flux_w_per_m: float


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
        surface_convection_w_per_m2k: float = 8.0,
        progress_callback: Optional[Callable[[float], None]] = None,
        simplified_constant_rho: bool = False,
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
        stiffness_matrix = asm(_diffusion_form, basis, k=conductivity_field).tocsr()

        total_nodes = nx * ny
        robin_load = np.zeros(total_nodes, dtype=float)
        if surface_convection_w_per_m2k > 0.0:
            convection = float(surface_convection_w_per_m2k)
            x_nodes_m = x_nodes_mm / 1000.0
            stiffness_matrix = stiffness_matrix.tolil()
            for i in range(nx - 1):
                edge_length = abs(x_nodes_m[i + 1] - x_nodes_m[i])
                if edge_length <= 0.0:
                    continue
                left_node = i * ny
                right_node = (i + 1) * ny
                coeff = convection * edge_length / 6.0
                stiffness_matrix[left_node, left_node] += 2.0 * coeff
                stiffness_matrix[right_node, right_node] += 2.0 * coeff
                stiffness_matrix[left_node, right_node] += coeff
                stiffness_matrix[right_node, left_node] += coeff
                load = convection * ambient_temp_c * edge_length / 2.0
                robin_load[left_node] += load
                robin_load[right_node] += load
            stiffness_matrix = stiffness_matrix.tocsr()
        else:
            stiffness_matrix = stiffness_matrix.tocsr()

        stiffness_csc = stiffness_matrix.tocsc()

        direct_solver: Optional[Callable[[NDArray[np.float64]], NDArray[np.float64]]] = None
        if (
            self._prefer_direct
            and "factorized" in globals()
            and stiffness_matrix.shape[0] <= self._direct_threshold
        ):
            try:
                factor = factorized(stiffness_csc)
            except Exception:
                factor = None
            if factor is not None:
                direct_solver = factor
            else:
                try:
                    lu = splu(stiffness_csc)
                except Exception:
                    lu = None
                if lu is not None:
                    def _lu_solve(vector: NDArray[np.float64]) -> NDArray[np.float64]:
                        return lu.solve(vector)
                    direct_solver = _lu_solve
                else:
                    if self._prefer_direct:
                        def _spsolve_solver(vector: NDArray[np.float64]) -> NDArray[np.float64]:
                            return spsolve(stiffness_csc, vector)
                        direct_solver = _spsolve_solver

        preconditioner: Optional[LinearOperator] = None
        if direct_solver is None:
            try:
                ilu = spilu(stiffness_csc, drop_tol=1e-3, fill_factor=6)
            except Exception:  # pragma: no cover - preconditioner is optional
                ilu = None
            if ilu is not None:
                def _ilu_solve(vector: NDArray[np.float64]) -> NDArray[np.float64]:
                    return ilu.solve(vector)
                preconditioner = LinearOperator(
                    stiffness_matrix.shape,
                    matvec=_ilu_solve,
                    dtype=stiffness_matrix.dtype,
                )

        prior_solution: Optional[NDArray[np.float64]] = None
        total_iterations = 0
        outer_converged = False
        solver_converged = True
        temperatures_grid = np.full((ny, nx), ambient_temp_c, dtype=float)
        cable_temperatures: Sequence[CableTemperature] = ()

        total_outer = 1 if simplified_constant_rho else max(self._max_outer_iterations, 1)
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
            load_vector = np.asarray(asm(_heat_source_form, basis, q=heat_field), dtype=float)
            rhs = load_vector + robin_load

            if direct_solver is not None:
                solution = np.asarray(direct_solver(rhs), dtype=float)
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

                solution, info = cg(
                    stiffness_matrix,
                    rhs,
                    **cg_kwargs,
                )
                solution = np.asarray(solution, dtype=float)

                if info < 0:
                    raise RuntimeError(f"Conjugate gradient solver failed with info={info}.")

                solve_converged = info == 0
                iterations_this = counter.count
                if not solve_converged and stiffness_matrix.nnz and stiffness_matrix.shape[0]:
                    # Fallback to a direct solve when CG stalls; guarantees progress
                    solution = np.asarray(spsolve(stiffness_csc, rhs), dtype=float)
                    solve_converged = True
                    iterations_this = (
                        counter.count
                        if counter.count and counter.count < self._max_iterations
                        else 1
                    )
                if iterations_this == 0:
                    iterations_this = 1 if solve_converged else self._max_iterations

            solver_converged = solver_converged and solve_converged
            total_iterations += iterations_this
            prior_solution = solution

            if solution.size != total_nodes:
                raise RuntimeError("Solver returned an unexpected solution vector length.")

            temperatures_grid = solution.reshape((nx, ny)).T
            cable_temperatures = _summarise_cable_temperatures(
                temperatures_grid.tolist(),
                conductor_index,
                [load.definition for load in loads],
            )

            updated = False
            if not simplified_constant_rho:
                updated = _update_heat_values(
                    heat_values,
                    loads,
                    cable_temperatures,
                    self._heat_tolerance,
                )
            if progress_callback:
                progress_callback((outer_index + 1) / total_outer)

            if simplified_constant_rho or not updated:
                outer_converged = True
                break

        if progress_callback:
            progress_callback(1.0)

        max_temp = float(np.max(temperatures_grid))
        min_temp = float(np.min(temperatures_grid))
        top_flux, side_flux, bottom_flux = _compute_boundary_fluxes(
            x_nodes_mm,
            y_nodes_mm,
            temperatures_grid,
            conductivity_cells,
            surface_convection_w_per_m2k,
            ambient_temp_c,
        )

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
            top_flux_w_per_m=top_flux,
            side_flux_w_per_m=side_flux,
            bottom_flux_w_per_m=bottom_flux,
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


def _compute_boundary_fluxes(
    x_nodes_mm: NDArray[np.float64],
    y_nodes_mm: NDArray[np.float64],
    temperatures_grid: NDArray[np.float64],
    conductivity_cells: NDArray[np.float64],
    surface_convection_w_per_m2k: float,
    ambient_temp_c: float,
) -> tuple[float, float, float]:
    """Return the heat flux (W/m) through the top, combined sides, and bottom boundaries."""
    if (
        x_nodes_mm.size < 2
        or y_nodes_mm.size < 2
        or temperatures_grid.shape[0] != y_nodes_mm.size
        or temperatures_grid.shape[1] != x_nodes_mm.size
    ):
        return 0.0, 0.0, 0.0

    temps = np.asarray(temperatures_grid, dtype=float)
    x_nodes_m = np.asarray(x_nodes_mm, dtype=float) / 1000.0
    y_nodes_m = np.asarray(y_nodes_mm, dtype=float) / 1000.0
    dx_segments = np.diff(x_nodes_m)
    dy_segments = np.diff(y_nodes_m)
    top_flux = 0.0
    side_flux_left = 0.0
    side_flux_right = 0.0
    bottom_flux = 0.0

    if dx_segments.size and dy_segments.size:
        # Top boundary: convection or conduction depending on configured h.
        if surface_convection_w_per_m2k > 0.0:
            h = float(surface_convection_w_per_m2k)
            temp_top_left = temps[0, :-1]
            temp_top_right = temps[0, 1:]
            avg_temp = 0.5 * (temp_top_left + temp_top_right)
            top_flux = float(np.sum(h * (avg_temp - ambient_temp_c) * dx_segments))
        else:
            if dy_segments.size >= 1:
                dy = dy_segments[0]
                for i, dx in enumerate(dx_segments):
                    t_boundary_avg = 0.5 * (temps[0, i] + temps[0, i + 1])
                    t_inside_avg = 0.5 * (temps[1, i] + temps[1, i + 1])
                    k = float(conductivity_cells[0, i])
                    top_flux += k * (t_inside_avg - t_boundary_avg) / dy * dx

        # Left boundary (x = min)
        dx_left = x_nodes_m[1] - x_nodes_m[0]
        if dx_left > 0.0:
            for j, dy in enumerate(dy_segments):
                t_boundary_avg = 0.5 * (temps[j, 0] + temps[j + 1, 0])
                t_inside_avg = 0.5 * (temps[j, 1] + temps[j + 1, 1])
                k = float(conductivity_cells[j, 0])
                side_flux_left += k * (t_inside_avg - t_boundary_avg) / dx_left * dy

        # Right boundary (x = max)
        dx_right = x_nodes_m[-1] - x_nodes_m[-2]
        if dx_right > 0.0:
            last_col = conductivity_cells.shape[1] - 1
            for j, dy in enumerate(dy_segments):
                t_boundary_avg = 0.5 * (temps[j, -1] + temps[j + 1, -1])
                t_inside_avg = 0.5 * (temps[j, -2] + temps[j + 1, -2])
                k = float(conductivity_cells[j, last_col])
                side_flux_right += -k * (t_boundary_avg - t_inside_avg) / dx_right * dy

        # Bottom boundary (y = max)
        dy_bottom = y_nodes_m[-1] - y_nodes_m[-2]
        if dy_bottom > 0.0:
            last_row = conductivity_cells.shape[0] - 1
            for i, dx in enumerate(dx_segments):
                t_boundary_avg = 0.5 * (temps[-1, i] + temps[-1, i + 1])
                t_inside_avg = 0.5 * (temps[-2, i] + temps[-2, i + 1])
                k = float(conductivity_cells[last_row, i])
                bottom_flux += -k * (t_boundary_avg - t_inside_avg) / dy_bottom * dx

    side_flux = side_flux_left + side_flux_right
    return top_flux, side_flux, bottom_flux


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
