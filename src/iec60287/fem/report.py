from __future__ import annotations

import csv
import json
import math
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, Optional, Sequence

from iec60287.fem.analyzer import CableFemResult
from iec60287.fem.mesh_builder import MeshBuildOutput, MeshCableDefinition


@dataclass
class ReportPaths:
    base_dir: Path
    heatmap_path: Optional[Path]
    field_csv_path: Path
    summary_path: Path


def generate_report(  # noqa: D401 - simple wrapper
    mesh_output: MeshBuildOutput,
    result: CableFemResult,
    *,
    root_dir: Path,
) -> ReportPaths:
    """
    Create a FEM analysis report (temperature field CSV, summary JSON, and optional heatmap).
    """

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    report_dir = root_dir / f"fem_report_{timestamp}"
    report_dir.mkdir(parents=True, exist_ok=True)

    field_csv = report_dir / "temperature_field.csv"
    summary_json = report_dir / "summary.json"
    heatmap_path: Optional[Path] = None

    _write_temperature_csv(mesh_output, result, field_csv)
    _write_summary(mesh_output.cables, result, summary_json)
    heatmap_path = _write_heatmap(mesh_output, result, report_dir)

    return ReportPaths(
        base_dir=report_dir,
        heatmap_path=heatmap_path,
        field_csv_path=field_csv,
        summary_path=summary_json,
    )


def _write_temperature_csv(
    mesh_output: MeshBuildOutput,
    result: CableFemResult,
    csv_path: Path,
) -> None:
    with csv_path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["x_mm", "y_mm", "temperature_c"])
        y_nodes = mesh_output.mesh.y_nodes_mm
        x_nodes = mesh_output.mesh.x_nodes_mm
        temperatures = result.temperatures_c
        for j, y in enumerate(y_nodes):
            row = temperatures[j]
            for i, x in enumerate(x_nodes):
                writer.writerow([f"{x:.6f}", f"{y:.6f}", f"{row[i]:.6f}"])


def _write_summary(
    cables: Sequence[MeshCableDefinition],
    result: CableFemResult,
    summary_path: Path,
) -> None:
    temps_by_label: Dict[str, Dict[str, float]] = {
        entry.label: {
            "max_temp_c": entry.max_temp_c,
            "average_temp_c": entry.average_temp_c,
        }
        for entry in result.cable_temperatures
    }
    heat_map = {
        cable.label: heat
        for cable, heat in zip(cables, result.heat_w_per_m)
    }

    cables_summary = []
    for cable in cables:
        entry = temps_by_label.get(cable.label)
        summary = {
            "label": cable.label,
            "centre_x_mm": cable.centre_x_mm,
            "centre_y_mm": cable.centre_y_mm,
            "overall_radius_mm": cable.overall_radius_mm,
            "nominal_current_a": cable.nominal_current_a,
            "heat_w_per_m": heat_map.get(cable.label),
        }
        if entry:
            summary.update(entry)
        cables_summary.append(summary)

    payload = {
        "max_field_temp_c": result.max_temp_c,
        "min_field_temp_c": result.min_temp_c,
        "converged": result.converged,
        "iterations": result.iterations,
        "cables": cables_summary,
    }
    summary_path.write_text(json.dumps(payload, indent=2))


def _write_heatmap(
    mesh_output: MeshBuildOutput,
    result: CableFemResult,
    report_dir: Path,
) -> Optional[Path]:
    try:
        import matplotlib.pyplot as plt
        from matplotlib import patches
    except ModuleNotFoundError:
        return None

    heatmap_path = report_dir / "heatmap.png"
    figure, axis = plt.subplots(figsize=(7, 6), constrained_layout=True)
    mesh = mesh_output.mesh
    x_nodes = mesh.x_nodes_mm
    y_nodes = mesh.y_nodes_mm
    temps = result.temperatures_c

    colour_plot = axis.pcolormesh(
        x_nodes,
        y_nodes,
        temps,
        shading="auto",
        cmap="inferno",
    )
    figure.colorbar(colour_plot, ax=axis, label="Temperature (°C)")

    for cable in mesh_output.cables:
        circle = patches.Circle(
            (cable.centre_x_mm, cable.centre_y_mm),
            radius=cable.overall_radius_mm,
            edgecolor="cyan",
            facecolor="none",
            linewidth=1.0,
        )
        axis.add_patch(circle)
        axis.text(
            cable.centre_x_mm,
            cable.centre_y_mm,
            cable.label,
            color="cyan",
            ha="center",
            va="center",
            fontsize=8,
        )

    axis.set_xlabel("x (mm)")
    axis.set_ylabel("y (mm)")
    axis.set_title("FEM Temperature Field")
    axis.set_aspect("equal", adjustable="box")

    figure.savefig(heatmap_path, dpi=200)
    plt.close(figure)
    return heatmap_path
