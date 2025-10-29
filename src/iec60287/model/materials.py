from __future__ import annotations

from typing import Iterable, List, Sequence

from .cable_system import Material, MaterialClassification

COPPER = Material(
    name="Copper",
    classification=MaterialClassification.CONDUCTIVE,
    electrical_resistivity_ohm_mm2_per_m=0.017241,
    thermal_resistivity_k_m_per_w=0.00,
    temp_coefficient_per_c=0.00393,
    max_operating_temp_c=90.0,
)

ALUMINIUM = Material(
    name="Aluminium",
    classification=MaterialClassification.CONDUCTIVE,
    electrical_resistivity_ohm_mm2_per_m=0.028264,
    thermal_resistivity_k_m_per_w=0.00,
    temp_coefficient_per_c=0.00403,
    max_operating_temp_c=90.0,
)

SEMI_CONDUCTOR = Material(
    name="Semi-Conductive Compound",
    classification=MaterialClassification.SEMICONDUCTIVE,
    electrical_resistivity_ohm_mm2_per_m=0.250,
    thermal_resistivity_k_m_per_w=3.50,
    max_operating_temp_c=90.0,
    notes="Traditional carbon-loaded XLPE screen.",
)

XLPE = Material(
    name="XLPE",
    classification=MaterialClassification.INSULATING,
    thermal_resistivity_k_m_per_w=3.50,
    max_operating_temp_c=90.0,
)

EPR = Material(
    name="EPR",
    classification=MaterialClassification.INSULATING,
    thermal_resistivity_k_m_per_w=3.70,
    max_operating_temp_c=90.0,
)

PVC = Material(
    name="PVC",
    classification=MaterialClassification.PROTECTIVE,
    thermal_resistivity_k_m_per_w=5.00,
    max_operating_temp_c=70.0,
)

PE_SERVING = Material(
    name="PE Serving",
    classification=MaterialClassification.PROTECTIVE,
    thermal_resistivity_k_m_per_w=3.30,
    max_operating_temp_c=70.0,
)

LEAD_SHEATH = Material(
    name="Lead Sheath",
    classification=MaterialClassification.PROTECTIVE,
    thermal_resistivity_k_m_per_w=19.00,
    electrical_resistivity_ohm_mm2_per_m=0.208,
    max_operating_temp_c=70.0,
)

STEEL_ARMOUR = Material(
    name="Steel Wire Armour",
    classification=MaterialClassification.PROTECTIVE,
    thermal_resistivity_k_m_per_w=12.0,
    electrical_resistivity_ohm_mm2_per_m=0.15,
    notes="Galvanised steel armour wires.",
)

MATERIALS: List[Material] = [
    COPPER,
    ALUMINIUM,
    SEMI_CONDUCTOR,
    XLPE,
    EPR,
    PVC,
    PE_SERVING,
    LEAD_SHEATH,
    STEEL_ARMOUR,
]

_MATERIAL_LOOKUP = {material.name: material for material in MATERIALS}


def all_materials() -> Sequence[Material]:
    return list(MATERIALS)


def find_material(name: str) -> Material | None:
    return _MATERIAL_LOOKUP.get(name)


def materials_for_classifications(classifications: Iterable[MaterialClassification]) -> List[Material]:
    allowed = set(classifications)
    return [material for material in MATERIALS if material.classification in allowed]
