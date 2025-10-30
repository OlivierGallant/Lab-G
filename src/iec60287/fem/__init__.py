"""
Finite element style thermal analysis utilities for cable layouts.

This module currently provides a light-weight 2D steady-state solver that
approximates IEC cable arrangements using a structured grid and Gauss-Seidel
iterations.  It is intentionally simple so it can run without external
dependencies, while still giving users an intuitive comparison point against
the IEC 60287 analytical results.
"""

from .analyzer import CableFemAnalyzer, CableFemResult, CableTemperature
from .mesh_builder import (
    MeshBuildOutput,
    MeshCableDefinition,
    StructuredMesh,
    build_structured_mesh,
)

__all__ = [
    "CableFemAnalyzer",
    "CableFemResult",
    "CableTemperature",
    "MeshBuildOutput",
    "MeshCableDefinition",
    "StructuredMesh",
    "build_structured_mesh",
]
