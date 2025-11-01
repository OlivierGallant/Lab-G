from __future__ import annotations

from pathlib import Path
from typing import Iterable, Sequence

import matplotlib.pyplot as plt
import matplotlib.tri as mtri
import numpy as np

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

    _draw_mesh(ax, mesh)
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


def _draw_mesh(ax, mesh: StructuredMesh) -> None:
    triangulation = _triangulate_mesh(mesh)
    if triangulation is None:
        for x in mesh.x_nodes_mm:
            ax.axvline(x, color="#cccccc", linewidth=0.5, zorder=1)
        for y in mesh.y_nodes_mm:
            ax.axhline(y, color="#cccccc", linewidth=0.5, zorder=1)
        return
    ax.triplot(
        triangulation,
        color="#b5b5b5",
        linewidth=0.5,
        zorder=1,
    )


def _draw_cables(ax, cables: Iterable[MeshCableDefinition]) -> None:
    handles: list[plt.Circle] = []
    labels_seen: set[str] = set()
    for cable in cables:
        label = cable.label or "Cable"
        circle = plt.Circle(
            (cable.centre_x_mm, cable.centre_y_mm),
            cable.overall_radius_mm,
            fill=False,
            color="#d62728",
            linewidth=1.2,
            zorder=2,
            label=label,
        )
        ax.add_patch(circle)
        if label not in labels_seen:
            handles.append(circle)
            labels_seen.add(label)
    if handles:
        ax.legend(handles=handles, loc="upper right", fontsize=8, frameon=True)


def _triangulate_mesh(mesh: StructuredMesh) -> mtri.Triangulation | None:
    x_nodes = np.asarray(mesh.x_nodes_mm, dtype=float)
    y_nodes = np.asarray(mesh.y_nodes_mm, dtype=float)
    if x_nodes.size < 2 or y_nodes.size < 2:
        return None

    xx, yy = np.meshgrid(x_nodes, y_nodes, indexing="xy")
    points_x = xx.ravel()
    points_y = yy.ravel()

    nx = x_nodes.size
    ny = y_nodes.size
    triangles = np.empty(((nx - 1) * (ny - 1) * 2, 3), dtype=int)

    idx = 0
    for j in range(ny - 1):
        for i in range(nx - 1):
            v0 = j * nx + i
            v1 = v0 + 1
            v2 = (j + 1) * nx + i
            v3 = v2 + 1
            triangles[idx] = (v0, v1, v3)
            triangles[idx + 1] = (v0, v3, v2)
            idx += 2

    return mtri.Triangulation(points_x, points_y, triangles)
