from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import matplotlib.pyplot as plt

from .mesh_builder import MeshBuildOutput, MeshCableDefinition, StructuredMesh


def save_mesh_preview(
    mesh: StructuredMesh,
    cables: Sequence[MeshCableDefinition],
    output_path: str | Path,
    *,
    title: str | None = None,
    dpi: int = 200,
) -> Path:
    """
    Render the structured grid to an image for inspection.

    Args:
        mesh: Structured mesh returned by build_structured_mesh.
        cables: Cable definitions used to generate the mesh.
        output_path: Target path for the PNG file.
        title: Optional title to add to the plot.
        dpi: Resolution for the output image.

    Returns:
        Path to the written image.
    """
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    fig, ax = plt.subplots(figsize=(8, 8), dpi=dpi / 25)

    _draw_grid(ax, mesh)
    _draw_cables(ax, cables)

    if title:
        ax.set_title(title)

    ax.set_xlabel("x [mm]")
    ax.set_ylabel("y [mm]")
    ax.set_aspect("equal", adjustable="box")
    ax.set_xlim(min(mesh.x_nodes_mm), max(mesh.x_nodes_mm))
    ax.set_ylim(min(mesh.y_nodes_mm), max(mesh.y_nodes_mm))
    ax.grid(False)
    fig.tight_layout()
    fig.savefig(path, dpi=dpi)
    plt.close(fig)
    return path


def save_mesh_preview_from_build(
    build_output: MeshBuildOutput,
    output_path: str | Path,
    *,
    title: str | None = None,
    dpi: int = 200,
) -> Path:
    """Convenience wrapper that accepts the full mesh build output."""
    return save_mesh_preview(build_output.mesh, build_output.cables, output_path, title=title, dpi=dpi)


def _draw_grid(ax, mesh: StructuredMesh) -> None:
    for x in mesh.x_nodes_mm:
        ax.axvline(x, color="#cccccc", linewidth=0.5, zorder=1)
    for y in mesh.y_nodes_mm:
        ax.axhline(y, color="#cccccc", linewidth=0.5, zorder=1)


def _draw_cables(ax, cables: Iterable[MeshCableDefinition]) -> None:
    for cable in cables:
        circle = plt.Circle(
            (cable.centre_x_mm, cable.centre_y_mm),
            cable.overall_radius_mm,
            fill=False,
            color="#d62728",
            linewidth=1.2,
            zorder=2,
        )
        ax.add_patch(circle)
        ax.text(
            cable.centre_x_mm,
            cable.centre_y_mm,
            cable.label,
            fontsize=8,
            ha="center",
            va="center",
            zorder=3,
        )
