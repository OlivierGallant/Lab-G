from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QPointF

from iec60287.gui.placement_scene import PlacementScene, TrenchLayer, TrenchLayerKind
from iec60287.model import (
    CablePhase,
    CableSystem,
    CableSystemKind,
    ConductorSpec,
    DuctContactConstants,
    DuctMaterial,
    DuctOccupancy,
    DuctSpecification,
    LayerRole,
    LayerSpec,
    Material,
    MaterialClassification,
    MultiCoreCable,
    SheathBonding,
    SingleCoreArrangement,
)
from iec60287.model import materials as material_catalog
from iec60287.model.cable_system import STANDARD_DUCT_MATERIALS

_MATERIAL_CATALOG = {material.name: material for material in material_catalog.all_materials()}
_DUCT_MATERIAL_CATALOG = {material.name: material for material in STANDARD_DUCT_MATERIALS}


def save_scene_configuration(scene: PlacementScene, path: Path) -> None:
    """Persist the current scene layout to ``path`` in JSON format."""
    payload = _scene_to_payload(scene)
    path.write_text(json.dumps(payload, indent=2))


def load_scene_configuration(scene: PlacementScene, path: Path) -> None:
    """Load a previously saved layout from ``path`` into ``scene``."""
    data = json.loads(path.read_text())
    _apply_scene_payload(scene, data)


# ---------------------------------------------------------------------------
# Serialisation helpers


def _scene_to_payload(scene: PlacementScene) -> Dict[str, Any]:
    layers = [
        {
            "name": layer.name,
            "kind": layer.kind.value,
            "thickness_mm": layer.thickness_mm,
            "thermal_resistivity_k_m_per_w": layer.thermal_resistivity_k_m_per_w,
        }
        for layer in scene.config.layers
    ]
    systems = [
        {
            "position": {"x": float(item.pos().x()), "y": float(item.pos().y())},
            "system": _cable_system_to_payload(item.system),
        }
        for item in scene.system_items()
    ]
    return {
        "scene": {
            "trench_width_mm": scene.config.trench_width_mm,
            "trench_depth_mm": scene.config.trench_depth_mm,
            "surface_level_y": scene.config.surface_level_y,
            "layers": layers,
        },
        "cable_systems": systems,
    }


def _apply_scene_payload(scene: PlacementScene, payload: Dict[str, Any]) -> None:
    scene.clear_temperature_overlay()
    scene.clear_systems()

    scene_data = payload.get("scene", {})
    if scene_data:
        scene.update_trench_geometry(
            width_mm=scene_data.get("trench_width_mm"),
            depth_mm=scene_data.get("trench_depth_mm"),
            surface_level_y=scene_data.get("surface_level_y"),
        )
        layer_payloads = scene_data.get("layers")
        if isinstance(layer_payloads, list):
            layers: List[TrenchLayer] = []
            for entry in layer_payloads:
                try:
                    layer = TrenchLayer(
                        name=str(entry["name"]),
                        kind=TrenchLayerKind(entry["kind"]),
                        thickness_mm=float(entry["thickness_mm"]),
                        thermal_resistivity_k_m_per_w=float(entry["thermal_resistivity_k_m_per_w"]),
                    )
                except (KeyError, ValueError, TypeError) as exc:  # pragma: no cover - defensive
                    raise ValueError(f"Invalid trench layer entry: {entry}") from exc
                layers.append(layer)
            scene.update_trench_layers(layers)

    systems_payload = payload.get("cable_systems", [])
    if not isinstance(systems_payload, list):
        raise ValueError("Cable systems payload must be an iterable.")

    for entry in systems_payload:
        system_data = entry.get("system")
        if not isinstance(system_data, dict):
            raise ValueError(f"Invalid cable system entry: {entry}")
        system = _cable_system_from_payload(system_data)
        position_payload = entry.get("position", {})
        x = float(position_payload.get("x", 0.0))
        y = float(position_payload.get("y", scene.config.surface_level_y))
        scene.add_system(system, QPointF(x, y), adjust_position=False)

    # Ensure subsequent auto-generated systems continue numbering after load.
    scene._cable_count = max(scene._cable_count, len(scene.system_items()))  # type: ignore[attr-defined]
    scene.clearSelection()
    scene.update()


def _cable_system_to_payload(system: CableSystem) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "name": system.name,
        "kind": system.kind.value,
        "phase_spacing_mm": system.phase_spacing_mm,
        "identifier": system.identifier,
        "nominal_current_a": system.nominal_current_a,
        "nominal_voltage_kv": system.nominal_voltage_kv,
    }
    if system.arrangement:
        payload["arrangement"] = system.arrangement.value
    if system.kind is CableSystemKind.SINGLE_CORE and system.single_core_phase:
        payload["single_core_phase"] = _cable_phase_to_payload(system.single_core_phase)
    if system.kind is CableSystemKind.MULTICORE and system.multicore:
        payload["multicore"] = _multicore_to_payload(system.multicore)
    if system.duct:
        payload["duct"] = _duct_to_payload(system.duct)
    return payload


def _cable_system_from_payload(payload: Dict[str, Any]) -> CableSystem:
    try:
        kind = CableSystemKind(payload["kind"])
        name = str(payload["name"])
        phase_spacing = float(payload.get("phase_spacing_mm", 0.0))
    except (KeyError, ValueError, TypeError) as exc:
        raise ValueError(f"Invalid cable system payload: {payload}") from exc

    identifier = payload.get("identifier")
    arrangement = payload.get("arrangement")
    single_core_phase = payload.get("single_core_phase")
    multicore_payload = payload.get("multicore")
    duct_payload = payload.get("duct")

    system = CableSystem(
        name=name,
        kind=kind,
        phase_spacing_mm=phase_spacing,
        arrangement=SingleCoreArrangement(arrangement) if arrangement else None,
        nominal_current_a=_maybe_float(payload.get("nominal_current_a")),
        nominal_voltage_kv=_maybe_float(payload.get("nominal_voltage_kv")),
    )

    if identifier:
        system.identifier = str(identifier)

    if kind is CableSystemKind.SINGLE_CORE:
        if single_core_phase:
            system.single_core_phase = _cable_phase_from_payload(single_core_phase)
    elif kind is CableSystemKind.MULTICORE:
        if multicore_payload:
            system.multicore = _multicore_from_payload(multicore_payload)
    if duct_payload:
        system.duct = _duct_from_payload(duct_payload)
    if not system.identifier:
        system.identifier = uuid.uuid4().hex
    return system


def _cable_phase_to_payload(phase: CablePhase) -> Dict[str, Any]:
    return {
        "name": phase.name,
        "conductor": _conductor_to_payload(phase.conductor),
        "layers": [_layer_to_payload(layer) for layer in phase.layers],
        "rated_voltage_kv": phase.rated_voltage_kv,
    }


def _cable_phase_from_payload(payload: Dict[str, Any]) -> CablePhase:
    try:
        conductor_payload = payload["conductor"]
    except KeyError as exc:
        raise ValueError(f"Phase payload missing conductor: {payload}") from exc
    conductor = _conductor_from_payload(conductor_payload)
    layers_payload = payload.get("layers", [])
    layers = [_layer_from_payload(entry) for entry in layers_payload]
    phase = CablePhase(
        name=str(payload.get("name", "Phase")),
        conductor=conductor,
        layers=layers,
        rated_voltage_kv=_maybe_float(payload.get("rated_voltage_kv")),
    )
    return phase


def _conductor_to_payload(conductor: ConductorSpec) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "area_mm2": conductor.area_mm2,
        "diameter_mm": conductor.diameter_mm,
        "material": _material_to_payload(conductor.material),
    }
    if conductor.electrical_resistivity_override_ohm_mm2_per_m is not None:
        payload["electrical_resistivity_override_ohm_mm2_per_m"] = (
            conductor.electrical_resistivity_override_ohm_mm2_per_m
        )
    if conductor.thermal_resistivity_override_k_m_per_w is not None:
        payload["thermal_resistivity_override_k_m_per_w"] = (
            conductor.thermal_resistivity_override_k_m_per_w
        )
    if conductor.filling_grade is not None:
        payload["filling_grade"] = conductor.filling_grade
    return payload


def _conductor_from_payload(payload: Dict[str, Any]) -> ConductorSpec:
    material_payload = payload.get("material")
    if not isinstance(material_payload, dict):
        raise ValueError(f"Invalid conductor material payload: {payload}")
    material = _material_from_payload(material_payload)
    return ConductorSpec(
        area_mm2=float(payload.get("area_mm2", 0.0)),
        diameter_mm=float(payload.get("diameter_mm", 0.0)),
        material=material,
        electrical_resistivity_override_ohm_mm2_per_m=_maybe_float(
            payload.get("electrical_resistivity_override_ohm_mm2_per_m")
        ),
        thermal_resistivity_override_k_m_per_w=_maybe_float(
            payload.get("thermal_resistivity_override_k_m_per_w")
        ),
        filling_grade=_maybe_float(payload.get("filling_grade")),
    )


def _layer_to_payload(layer: LayerSpec) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "role": layer.role.value,
        "thickness_mm": layer.thickness_mm,
        "material": _material_to_payload(layer.material),
    }
    if layer.electrical_resistivity_override_ohm_mm2_per_m is not None:
        payload["electrical_resistivity_override_ohm_mm2_per_m"] = (
            layer.electrical_resistivity_override_ohm_mm2_per_m
        )
    if layer.thermal_resistivity_override_k_m_per_w is not None:
        payload["thermal_resistivity_override_k_m_per_w"] = (
            layer.thermal_resistivity_override_k_m_per_w
        )
    if layer.filling_grade is not None:
        payload["filling_grade"] = layer.filling_grade
    return payload


def _layer_from_payload(payload: Dict[str, Any]) -> LayerSpec:
    material_payload = payload.get("material")
    if not isinstance(material_payload, dict):
        raise ValueError(f"Invalid layer material payload: {payload}")
    return LayerSpec(
        role=LayerRole(payload["role"]),
        thickness_mm=float(payload.get("thickness_mm", 0.0)),
        material=_material_from_payload(material_payload),
        electrical_resistivity_override_ohm_mm2_per_m=_maybe_float(
            payload.get("electrical_resistivity_override_ohm_mm2_per_m")
        ),
        thermal_resistivity_override_k_m_per_w=_maybe_float(
            payload.get("thermal_resistivity_override_k_m_per_w")
        ),
        filling_grade=_maybe_float(payload.get("filling_grade")),
    )


def _duct_to_payload(duct: DuctSpecification) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "material": _duct_material_to_payload(duct.material),
        "inner_diameter_mm": duct.inner_diameter_mm,
        "wall_thickness_mm": duct.wall_thickness_mm,
        "occupancy": duct.occupancy.value,
        "medium_temperature_c": duct.medium_temperature_c,
    }
    if duct.contact_override is not None:
        payload["contact_override"] = _contact_to_payload(duct.contact_override)
    return payload


def _duct_from_payload(payload: Dict[str, Any]) -> DuctSpecification:
    material_payload = payload.get("material")
    if not isinstance(material_payload, dict):
        raise ValueError(f"Invalid duct material payload: {payload}")
    material = _duct_material_from_payload(material_payload)
    contact_overrides = payload.get("contact_override")
    return DuctSpecification(
        material=material,
        inner_diameter_mm=float(payload.get("inner_diameter_mm", 0.0)),
        wall_thickness_mm=float(payload.get("wall_thickness_mm", 0.0)),
        occupancy=DuctOccupancy(payload.get("occupancy", DuctOccupancy.SINGLE_PHASE_PER_DUCT.value)),
        contact_override=_contact_from_payload(contact_overrides) if contact_overrides else None,
        medium_temperature_c=float(payload.get("medium_temperature_c", 20.0)),
    )


def _duct_material_to_payload(material: DuctMaterial) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "name": material.name,
        "thermal_resistivity_k_m_per_w": material.thermal_resistivity_k_m_per_w,
        "is_metallic": material.is_metallic,
    }
    if material.notes:
        payload["notes"] = material.notes
    catalog_material = _DUCT_MATERIAL_CATALOG.get(material.name)
    if catalog_material and material == catalog_material:
        payload["catalog"] = True
    else:
        payload["contact_defaults"] = _contact_to_payload(material.contact_defaults)
    return payload


def _duct_material_from_payload(payload: Dict[str, Any]) -> DuctMaterial:
    name = str(payload.get("name", ""))
    if not name:
        raise ValueError(f"Duct material entry missing name: {payload}")
    if payload.get("catalog"):
        catalog = _DUCT_MATERIAL_CATALOG.get(name)
        if catalog is None:
            raise ValueError(f"Unknown duct material '{name}'.")
        return catalog
    defaults_payload = payload.get("contact_defaults")
    contact_defaults = (
        _contact_from_payload(defaults_payload)
        if defaults_payload
        else DuctContactConstants(u=0.086, v=0.60, y=0.0)
    )
    return DuctMaterial(
        name=name,
        thermal_resistivity_k_m_per_w=float(payload.get("thermal_resistivity_k_m_per_w", 0.0)),
        is_metallic=bool(payload.get("is_metallic", False)),
        notes=payload.get("notes"),
        contact_defaults=contact_defaults,
    )


def _contact_to_payload(constants: DuctContactConstants) -> Dict[str, Any]:
    return {"u": constants.u, "v": constants.v, "y": constants.y}


def _contact_from_payload(payload: Dict[str, Any]) -> DuctContactConstants:
    return DuctContactConstants(
        u=float(payload.get("u", 0.0)),
        v=float(payload.get("v", 0.0)),
        y=float(payload.get("y", 0.0)),
    )


def _multicore_to_payload(multicore: MultiCoreCable) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "outer_diameter_mm": multicore.outer_diameter_mm,
        "phase": _cable_phase_to_payload(multicore.phase),
        "sheath_bonding": multicore.sheath_bonding.value,
    }
    if multicore.armour:
        payload["armour"] = _layer_to_payload(multicore.armour)
    if multicore.bedding:
        payload["bedding"] = _layer_to_payload(multicore.bedding)
    return payload


def _multicore_from_payload(payload: Dict[str, Any]) -> MultiCoreCable:
    phase_payload = payload.get("phase")
    if not isinstance(phase_payload, dict):
        raise ValueError(f"Invalid multicore phase payload: {payload}")
    phase = _cable_phase_from_payload(phase_payload)
    multicore = MultiCoreCable(
        outer_diameter_mm=float(payload.get("outer_diameter_mm", 0.0)),
        phase=phase,
        sheath_bonding=SheathBonding(payload.get("sheath_bonding", SheathBonding.BOTH_ENDS.value)),
    )
    if payload.get("armour"):
        multicore.armour = _layer_from_payload(payload["armour"])
    if payload.get("bedding"):
        multicore.bedding = _layer_from_payload(payload["bedding"])
    return multicore


def _material_to_payload(material: Material) -> Dict[str, Any]:
    payload: Dict[str, Any] = {"name": material.name}
    catalog = _MATERIAL_CATALOG.get(material.name)
    if catalog and material == catalog:
        payload["catalog"] = True
        return payload
    payload["classification"] = material.classification.value
    if material.electrical_resistivity_ohm_mm2_per_m is not None:
        payload["electrical_resistivity_ohm_mm2_per_m"] = material.electrical_resistivity_ohm_mm2_per_m
    if material.thermal_resistivity_k_m_per_w is not None:
        payload["thermal_resistivity_k_m_per_w"] = material.thermal_resistivity_k_m_per_w
    if material.temp_coefficient_per_c is not None:
        payload["temp_coefficient_per_c"] = material.temp_coefficient_per_c
    if material.max_operating_temp_c is not None:
        payload["max_operating_temp_c"] = material.max_operating_temp_c
    if material.notes:
        payload["notes"] = material.notes
    return payload


def _material_from_payload(payload: Dict[str, Any]) -> Material:
    name = str(payload.get("name", ""))
    if not name:
        raise ValueError(f"Material entry missing name: {payload}")
    if payload.get("catalog"):
        catalog = _MATERIAL_CATALOG.get(name)
        if catalog is None:
            raise ValueError(f"Unknown material '{name}'.")
        return catalog
    classification_value = payload.get("classification")
    if classification_value is None:
        catalog = _MATERIAL_CATALOG.get(name)
        if catalog is None:
            raise ValueError(f"Material '{name}' missing classification.")
        return catalog
    classification = MaterialClassification(classification_value)
    return Material(
        name=name,
        classification=classification,
        electrical_resistivity_ohm_mm2_per_m=_maybe_float(payload.get("electrical_resistivity_ohm_mm2_per_m")),
        thermal_resistivity_k_m_per_w=_maybe_float(payload.get("thermal_resistivity_k_m_per_w")),
        temp_coefficient_per_c=_maybe_float(payload.get("temp_coefficient_per_c")),
        max_operating_temp_c=_maybe_float(payload.get("max_operating_temp_c")),
        notes=payload.get("notes"),
    )


def _maybe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
