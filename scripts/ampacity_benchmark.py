#!/usr/bin/env python3

"""Ad-hoc benchmark script that prints IEC 60287 thermal terms for two setups.

The script does not depend on the GUI running: it creates lightweight stand-ins
for the placement scene and feeds them through the existing ampacity calculator
logic. Use it to sanity-check the raw T1–T4 and resistance values for a generic
240 mm² CU single-core system, once bare in soil and once installed in an HDPE
duct.
"""

from __future__ import annotations

from typing import List

from PySide6.QtWidgets import QApplication

from iec60287.gui.ampacity_calculator import (
    AmpacityResult,
    CableAmpacityCalculator,
    CalculatorParams,
)
from iec60287.model import CableSystem, DuctOccupancy, DuctSpecification
from iec60287.model.cable_system import HDPE_DUCT
from scripts._benchmark_utils import make_benchmark_scene, make_cable_system, make_scene_config


# --------------------------------------------------------------------------- helpers


def evaluate_system(system: CableSystem) -> List[AmpacityResult]:
    config = make_scene_config()
    axis_depth_mm = config.trench_depth_mm / 2.0
    scene = make_benchmark_scene([system], config=config, axis_depth_mm=axis_depth_mm, lateral_spacing_mm=0.0)

    calculator = CableAmpacityCalculator(scene)

    params = CalculatorParams(
        ambient_temp_c=calculator._ambient_spin.value(),
        conductor_temp_c=calculator._conductor_spin.value(),
        dielectric_loss_w_per_m=calculator._dielectric_spin.value(),
        sheath_loss_factor=calculator._sheath_spin.value(),
        armour_loss_factor=calculator._armour_spin.value(),
        loaded_conductors=calculator._loaded_spin.value(),
    )

    instances = calculator._collect_cable_instances()
    return [calculator._compute_result(instance, params, instances) for instance in instances]


def print_results(label: str, results: List[AmpacityResult]) -> None:
    print(f"\n=== {label} ===")
    for result in results:
        print(f"{result.label} at {result.position_mm} mm:")
        print(f"  R  = {result.conductor_resistance_ohm_per_m:.6f} Ω/m")
        print(f"  T1 = {result.t1 if result.t1 is not None else float('nan'):.6f} K·m/W")
        print(f"  T2 = {result.t2 if result.t2 is not None else float('nan'):.6f} K·m/W")
        print(f"  T3 = {result.t3 if result.t3 is not None else float('nan'):.6f} K·m/W")
        print(f"  T4 = {result.t4 if result.t4 is not None else float('nan'):.6f} K·m/W")
        if result.ampacity_a is not None:
            print(f"  I  = {result.ampacity_a:.2f} A")
        if result.issues:
            for issue in result.issues:
                print(f"    ! {issue}")


def main() -> None:
    app = QApplication([])

    bare_system = make_cable_system("Bare Trefoil")

    duct_system = make_cable_system(
        "Trefoil in HDPE duct",
        duct=DuctSpecification(
            material=HDPE_DUCT,
            inner_diameter_mm=150.0,  # assume 160 mm OD with 5 mm wall
            wall_thickness_mm=5.0,
            occupancy=DuctOccupancy.THREE_PHASES_PER_DUCT,
            medium_temperature_c=20.0,
        ),
    )

    print_results("Bare soil installation", evaluate_system(bare_system))
    print_results("HDPE duct installation", evaluate_system(duct_system))

    app.quit()


if __name__ == "__main__":
    main()
