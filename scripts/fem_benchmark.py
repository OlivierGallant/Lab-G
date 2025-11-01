#!/usr/bin/env python3

"""Standalone FEM benchmark for the IEC 60287 toolchain.

Builds a simple trefoil installation (bare soil and HDPE duct variants), runs
the structured mesh generator and finite-difference solver, and prints key
metrics so the results can be tracked outside the GUI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List

from pathlib import Path
import sys

from PySide6.QtWidgets import QApplication
import matplotlib.pyplot as plt
import matplotlib.image as mpimg

from iec60287.fem.analyzer import CableFemAnalyzer, CableLoad
from iec60287.fem.mesh_builder import MeshBuildOutput, build_structured_mesh
from iec60287.model import CableSystem, DuctOccupancy, DuctSpecification
from iec60287.model.cable_system import HDPE_DUCT
from iec60287.fem.mesh_preview import save_mesh_preview

if __package__:
    from ._benchmark_utils import make_benchmark_scene, make_cable_system, make_scene_config
else:  # Allow execution via `python fem_benchmark.py`
    script_dir = Path(__file__).resolve().parent
    if str(script_dir) not in sys.path:
        sys.path.insert(0, str(script_dir))
    from _benchmark_utils import make_benchmark_scene, make_cable_system, make_scene_config  # type: ignore  # noqa: E402


@dataclass
class Scenario:
    name: str
    system: CableSystem
    load_current_a: float = 400.0


def _heat_for_cable(definition, current_a: float, reference_temp_c: float = 90.0) -> float:
    rho = definition.conductor_resistivity_ohm_mm2_per_m
    area = max(definition.conductor_area_mm2, 1e-9)
    if rho is None or area <= 0.0 or current_a <= 0.0:
        return 0.0
    alpha = definition.conductor_temp_coefficient_per_c
    rho_theta = rho * (1.0 + alpha * (reference_temp_c - 20.0))
    resistance = rho_theta / area
    return (current_a ** 2) * resistance


def build_loads(mesh_output: MeshBuildOutput, current_a: float) -> List[CableLoad]:
    return [
        CableLoad(
            definition=definition,
            heat_w_per_m=_heat_for_cable(definition, current_a),
            auto_update=False,
        )
        for definition in mesh_output.cables
    ]


def run_scenario(scenario: Scenario) -> None:
    config = make_scene_config()
    axis_depth_mm = config.trench_depth_mm / 2.0
    scene = make_benchmark_scene([scenario.system], config=config, axis_depth_mm=axis_depth_mm, lateral_spacing_mm=0.0)

    mesh_output = build_structured_mesh(
        scene,
        grid_step_mm=25.0,
        padding_mm=300.0,
        default_resistivity_k_m_per_w=config.layers[0].thermal_resistivity_k_m_per_w,
    )
    loads = build_loads(mesh_output, scenario.load_current_a)

    analyzer = CableFemAnalyzer(max_iterations=4000, tolerance_c=1e-4)
    result = analyzer.solve(
        mesh_output.mesh,
        loads,
        ambient_temp_c=20.0,
    )

    preview_path = save_mesh_preview(
        mesh_output.mesh,
        mesh_output.cables,
        Path.cwd() / f"fem_mesh_{scenario.name.replace(' ', '_').lower()}.png",
        title=f"Mesh preview: {scenario.name}",
        dpi=150,
    )
    print(f"Mesh preview saved to {preview_path}")
    image = mpimg.imread(preview_path)
    plt.figure(figsize=(8, 8))
    plt.imshow(image)
    plt.axis("off")
    plt.title(f"Mesh preview: {scenario.name}")
    plt.show()

    print(f"\n=== {scenario.name} ===")
    print(f"Grid: {len(result.grid_x_mm)} x {len(result.grid_y_mm)} nodes, iterations: {result.iterations}, converged={result.converged}")
    for idx, load in enumerate(loads):
        label = load.definition.label
        heat = load.heat_w_per_m
        print(f"  Load {idx+1}: {label} -> {heat:.2f} W/m")
    for temp in result.cable_temperatures:
        print(f"  {temp.label}: avg={temp.average_temp_c:.2f} °C, max={temp.max_temp_c:.2f} °C")
    print(f"  Field extremes: min={result.min_temp_c:.2f} °C, max={result.max_temp_c:.2f} °C")


def main() -> None:
    app = QApplication([])

    bare = Scenario(
        name="Bare trefoil in soil",
        system=make_cable_system("Bare Trefoil"),
    )

    duct = Scenario(
        name="Trefoil in 160 mm HDPE duct",
        system=make_cable_system(
            "Trefoil in HDPE duct",
            duct=DuctSpecification(
                material=HDPE_DUCT,
                inner_diameter_mm=150.0,
                wall_thickness_mm=5.0,
                occupancy=DuctOccupancy.THREE_PHASES_PER_DUCT,
                medium_temperature_c=20.0,
            ),
        ),
    )

    for scenario in (bare, duct):
        run_scenario(scenario)

    app.quit()


if __name__ == "__main__":
    main()
