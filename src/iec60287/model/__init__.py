"""Domain models for IEC 60287 cable system design."""

from .cable_system import (
    CablePhase,
    CableSystem,
    CableSystemKind,
    ConductorSpec,
    DuctContactConstants,
    DuctMaterial,
    DuctOccupancy,
    DuctSpecification,
    STANDARD_DUCT_MATERIALS,
    LayerRole,
    LayerSpec,
    Material,
    MaterialClassification,
    MultiCoreCable,
    SheathBonding,
    SingleCoreArrangement,
)
from . import materials

__all__ = [
    "CablePhase",
    "CableSystem",
    "CableSystemKind",
    "ConductorSpec",
    "DuctContactConstants",
    "DuctMaterial",
    "DuctOccupancy",
    "DuctSpecification",
    "STANDARD_DUCT_MATERIALS",
    "LayerRole",
    "LayerSpec",
    "Material",
    "MaterialClassification",
    "MultiCoreCable",
    "SheathBonding",
    "SingleCoreArrangement",
    "materials",
]
