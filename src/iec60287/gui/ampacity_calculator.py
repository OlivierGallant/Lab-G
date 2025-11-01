from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from iec60287.gui.items import CableSystemItem
from iec60287.gui.placement_scene import PlacementScene, TrenchLayer, TrenchLayerKind
from iec60287.model import (
    CableSystem,
    CableSystemKind,
    DuctSpecification,
    LayerRole,
    SingleCoreArrangement,
)


@dataclass
class CalculatorParams:
    ambient_temp_c: float
    conductor_temp_c: float
    dielectric_loss_w_per_m: float
    sheath_loss_factor: float
    armour_loss_factor: float
    loaded_conductors: int


@dataclass
class AmpacityResult:
    label: str
    system: CableSystem
    position_mm: Tuple[float, float]
    conductor_resistance_ohm_per_m: Optional[float]
    t1: Optional[float]
    t2: Optional[float]
    t3: Optional[float]
    t4: Optional[float]
    ampacity_a: Optional[float]
    issues: List[str]


@dataclass
class CablePhaseInstance:
    item: CableSystemItem
    system: CableSystem
    phase_index: int
    label: str
    position_mm: Tuple[float, float]
    outer_diameter_mm: Optional[float]


class CableAmpacityCalculator(QWidget):
    """Display IEC 60287 ampacity estimates for the scene's cable systems."""

    _TABLE_HEADERS = [
        "Cable",
        "R (Ω/m)",
        "T1 (K·m/W)",
        "T2",
        "T3",
        "T4",
        "Ampacity (A)",
        "Notes",
    ]

    def __init__(self, scene: PlacementScene, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._scene = scene

        self._ambient_spin = QDoubleSpinBox(self)
        self._conductor_spin = QDoubleSpinBox(self)
        self._dielectric_spin = QDoubleSpinBox(self)
        self._sheath_spin = QDoubleSpinBox(self)
        self._armour_spin = QDoubleSpinBox(self)
        self._loaded_spin = QSpinBox(self)

        self._results_table = QTableWidget(self)
        self._footer_label = QLabel(self)

        self._build_ui()
        self._wire_signals()
        self.refresh_from_scene()

    # ------------------------------------------------------------------ setup
    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(10)

        layout.addWidget(self._build_parameters_group())
        layout.addWidget(self._build_results_table())
        layout.addWidget(self._footer_label)

        self._footer_label.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self._footer_label.setWordWrap(True)
        self._footer_label.setText(
            "Ampacity uses IEC 60287-1-1 steady-state equations. "
            "Assumes coaxial heat flow and uniform soil resistivity."
        )

    def _build_parameters_group(self) -> QGroupBox:
        group = QGroupBox("Calculation Parameters", self)
        form = QFormLayout(group)
        form.setLabelAlignment(Qt.AlignLeft)
        form.setFormAlignment(Qt.AlignLeft)
        form.setSpacing(6)

        self._ambient_spin.setRange(-50.0, 100.0)
        self._ambient_spin.setDecimals(1)
        self._ambient_spin.setSuffix(" °C")
        self._ambient_spin.setValue(20.0)

        self._conductor_spin.setRange(30.0, 120.0)
        self._conductor_spin.setDecimals(1)
        self._conductor_spin.setSuffix(" °C")
        self._conductor_spin.setValue(90.0)

        self._dielectric_spin.setRange(0.0, 50.0)
        self._dielectric_spin.setDecimals(3)
        self._dielectric_spin.setSuffix(" W/m")
        self._dielectric_spin.setSingleStep(0.10)
        self._dielectric_spin.setValue(0.000)

        self._sheath_spin.setRange(0.0, 2.0)
        self._sheath_spin.setDecimals(3)
        self._sheath_spin.setSingleStep(0.05)
        self._sheath_spin.setValue(0.000)

        self._armour_spin.setRange(0.0, 2.0)
        self._armour_spin.setDecimals(3)
        self._armour_spin.setSingleStep(0.05)
        self._armour_spin.setValue(0.000)

        self._loaded_spin.setRange(1, 4)
        self._loaded_spin.setValue(3)

        form.addRow("Ambient temperature", self._ambient_spin)
        form.addRow("Conductor temperature", self._conductor_spin)
        form.addRow("Dielectric loss Wd", self._dielectric_spin)
        form.addRow("Sheath loss factor λ₁", self._sheath_spin)
        form.addRow("Armour loss factor λ₂", self._armour_spin)
        form.addRow("Loaded conductors n", self._loaded_spin)
        return group

    def _build_results_table(self) -> QTableWidget:
        table = self._results_table
        table.setColumnCount(len(self._TABLE_HEADERS))
        table.setHorizontalHeaderLabels(self._TABLE_HEADERS)
        table.verticalHeader().setVisible(False)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionMode(QTableWidget.NoSelection)
        table.setFocusPolicy(Qt.NoFocus)
        table.horizontalHeader().setStretchLastSection(True)
        table.horizontalHeader().setDefaultSectionSize(130)
        return table

    def _wire_signals(self) -> None:
        for spin in (
            self._ambient_spin,
            self._conductor_spin,
            self._dielectric_spin,
            self._sheath_spin,
            self._armour_spin,
        ):
            spin.valueChanged.connect(self.refresh_from_scene)
        self._loaded_spin.valueChanged.connect(self.refresh_from_scene)

    # ---------------------------------------------------------------- refresh
    def refresh_from_scene(self) -> None:
        params = CalculatorParams(
            ambient_temp_c=self._ambient_spin.value(),
            conductor_temp_c=self._conductor_spin.value(),
            dielectric_loss_w_per_m=self._dielectric_spin.value(),
            sheath_loss_factor=self._sheath_spin.value(),
            armour_loss_factor=self._armour_spin.value(),
            loaded_conductors=self._loaded_spin.value(),
        )
        instances = self._collect_cable_instances()
        results = [self._compute_result(instance, params, instances) for instance in instances]
        self._populate_table(results)

    def _collect_cable_instances(self) -> List[CablePhaseInstance]:
        instances: List[CablePhaseInstance] = []
        phase_labels = getattr(CableSystemItem, "_PHASE_LABELS", ("A", "B", "C"))

        for item in self._scene.system_items():
            system = item.system
            offsets = list(system.phase_offsets_mm()) or [(0.0, 0.0)]

            if system.kind is CableSystemKind.SINGLE_CORE and system.single_core_phase:
                outer_diameter = system.single_core_phase.overall_diameter_mm()
            else:
                outer_diameter = self._outer_diameter_mm(system)

            base_pos = item.pos()
            for index, offset in enumerate(offsets):
                centre_x = base_pos.x() + offset[0]
                centre_y = base_pos.y() + offset[1]
                label_suffix = phase_labels[index % len(phase_labels)] if offsets else "—"
                label = f"{system.name} · Phase {label_suffix}" if len(offsets) > 1 else system.name
                instances.append(
                    CablePhaseInstance(
                        item=item,
                        system=system,
                        phase_index=index,
                        label=label,
                        position_mm=(centre_x, centre_y),
                        outer_diameter_mm=outer_diameter,
                    )
                )

        return instances

    # ---------------------------------------------------------------- compute
    def _compute_result(
        self,
        instance: CablePhaseInstance,
        params: CalculatorParams,
        population: Sequence[CablePhaseInstance],
    ) -> AmpacityResult:
        system = instance.system
        issues: List[str] = []

        resistance = self._conductor_resistance(system, params.conductor_temp_c, issues)
        t1, t2, t3 = self._radial_resistances(system, issues)
        t4 = self._external_resistance(instance, population, params, issues)

        ampacity = None
        delta_theta = params.conductor_temp_c - params.ambient_temp_c
        if delta_theta <= 0.0:
            issues.append("Conductor temperature must exceed ambient.")

        valid_inputs = (
            resistance is not None
            and resistance > 0.0
            and t1 is not None
            and t1 > 0.0
            and t3 is not None
            and t3 >= 0.0
            and t4 is not None
            and t4 > 0.0
        )
        n = max(params.loaded_conductors, 1)

        if valid_inputs and delta_theta > 0.0:
            wd_term = params.dielectric_loss_w_per_m * (
                0.5 * t1 + n * ((t2 or 0.0) + (t3 or 0.0) + (t4 or 0.0))
            )

            numerator = delta_theta - wd_term
            denominator = 0.0
            r_value = resistance or 0.0
            t2_term = (t2 or 0.0)
            t3_t4 = (t3 or 0.0) + (t4 or 0.0)

            denominator += r_value * (t1 or 0.0)
            denominator += n * r_value * (1.0 + params.sheath_loss_factor) * t2_term
            denominator += n * r_value * (1.0 + params.sheath_loss_factor + params.armour_loss_factor) * t3_t4

            if numerator <= 0.0:
                issues.append("Dielectric losses exceed allowable temperature rise.")
            elif denominator <= 0.0:
                issues.append("Invalid denominator for current calculation.")
            else:
                ampacity = math.sqrt(numerator / denominator)
        else:
            if resistance is None:
                issues.append("Conductor resistance unavailable.")
            if t1 is None:
                issues.append("Insulation resistance T1 unavailable.")
            if t3 is None:
                issues.append("Outer cable resistance T3 unavailable.")
            if t4 is None:
                issues.append("Soil resistance T4 unavailable.")

        pos = instance.position_mm
        return AmpacityResult(
            label=instance.label,
            system=system,
            position_mm=pos,
            conductor_resistance_ohm_per_m=resistance,
            t1=t1,
            t2=t2,
            t3=t3,
            t4=t4,
            ampacity_a=ampacity,
            issues=issues,
        )

    # ------------------------------------------------------------- diagnostics
    def _conductor_resistance(
        self,
        system: CableSystem,
        conductor_temp_c: float,
        issues: List[str],
    ) -> Optional[float]:
        if system.kind is not CableSystemKind.SINGLE_CORE or not system.single_core_phase:
            issues.append("Ampacity calculator currently supports single-core systems only.")
            return None

        phase = system.single_core_phase
        conductor = phase.conductor
        area = max(conductor.area_mm2, 0.0)
        if area <= 0.0:
            issues.append("Conductor area must be positive.")
            return None

        resistivity = conductor.electrical_resistivity()
        if resistivity is None:
            issues.append("Conductor electrical resistivity unavailable.")
            return None

        alpha = conductor.material.temp_coefficient_per_c or 0.0
        rho_theta = resistivity * (1.0 + alpha * (conductor_temp_c - 20.0))
        resistance = rho_theta / area  # Ω/m
        if resistance <= 0.0:
            issues.append("Computed conductor resistance is non-positive.")
            return None
        return resistance

    def _radial_resistances(
        self,
        system: CableSystem,
        issues: List[str],
    ) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        if system.kind is not CableSystemKind.SINGLE_CORE or not system.single_core_phase:
            return (None, None, None)

        phase = system.single_core_phase
        profile = phase.radial_profile_mm()
        if not profile:
            issues.append("Cable phase layers unavailable.")
            return (None, None, None)

        sheath_index = self._find_layer_index(profile, LayerRole.SHEATH)
        armour_index = self._find_layer_index(profile, LayerRole.ARMOUR)

        layers_before_sheath = profile[:sheath_index] if sheath_index is not None else profile
        t1 = self._sum_layer_resistances(layers_before_sheath, issues, label="T1")

        if sheath_index is None:
            # No sheath present: only internal resistance is modelled.
            t2 = 0.0
            t3_layers: Sequence[Tuple[object, float, float]] = ()
        elif armour_index is None:
            # Sheath present but no armour: T3 gathers everything from sheath outward.
            t2 = 0.0
            t3_layers = profile[sheath_index:]
        else:
            t2_layers = profile[sheath_index:armour_index]
            t3_layers = profile[armour_index:]
            t2 = self._sum_layer_resistances(t2_layers, issues, label="T2")

        t3 = self._sum_layer_resistances(t3_layers, issues, label="T3")
        return (t1, t2, t3)

    def _external_resistance(
        self,
        instance: CablePhaseInstance,
        population: Sequence[CablePhaseInstance],
        params: CalculatorParams,
        issues: List[str],
    ) -> Optional[float]:
        config = self._scene.config
        surface_y = config.surface_level_y
        _, centre_y = instance.position_mm
        depth_mm = max(centre_y - surface_y, 0.0)

        soil_layer = self._soil_layer_for_depth(config.layers, depth_mm)
        resistivity = soil_layer.thermal_resistivity_k_m_per_w if soil_layer else None
        if resistivity is None or resistivity <= 0.0:
            issues.append("Thermal resistivity for soil layer unavailable.")
            return None

        system = instance.system
        duct = system.duct if isinstance(system.duct, DuctSpecification) else None
        environment = self._determine_environment(system, depth_mm, soil_layer)

        if environment == "AIR":
            return self._t4_air(
                instance=instance,
                depth_mm=depth_mm,
                issues=issues,
            )
        if environment == "DUCT" and duct:
            return self._t4_duct(
                instance=instance,
                system=system,
                duct=duct,
                soil_resistivity=resistivity,
                depth_mm=depth_mm,
                population=population,
                surface_level_y=surface_y,
                params=params,
                issues=issues,
            )
        if environment == "CONCRETE_DUCT" and duct:
            concrete_result = self._t4_concrete_duct(
                instance=instance,
                system=system,
                duct=duct,
                soil_layer=soil_layer,
                depth_mm=depth_mm,
                population=population,
                surface_level_y=surface_y,
                params=params,
                issues=issues,
            )
            if concrete_result is not None:
                return concrete_result
            # Fall back to standard duct model if concrete-specific data is incomplete.
            return self._t4_duct(
                instance=instance,
                system=system,
                duct=duct,
                soil_resistivity=resistivity,
                depth_mm=depth_mm,
                population=population,
                surface_level_y=surface_y,
                params=params,
                issues=issues,
            )
        if environment == "TROUGH":
            return self._t4_trough(
                instance=instance,
                soil_layer=soil_layer,
                depth_mm=depth_mm,
                population=population,
                surface_level_y=surface_y,
                issues=issues,
            )

        outer_diameter_mm = instance.outer_diameter_mm or self._outer_diameter_mm(system)
        if outer_diameter_mm is None or outer_diameter_mm <= 0.0:
            issues.append("Outer diameter required for T4.")
            return None
        if depth_mm <= 0.0:
            issues.append("Cable axis must be below surface for T4.")
            return None

        return self._t4_direct_buried(
            instance=instance,
            population=population,
            surface_level_y=surface_y,
            outer_diameter_mm=outer_diameter_mm,
            depth_mm=depth_mm,
            soil_resistivity=resistivity,
            issues=issues,
        )

    def _determine_environment(
        self,
        system: CableSystem,
        depth_mm: float,
        soil_layer: Optional[TrenchLayer],
    ) -> str:
        if depth_mm <= 0.0:
            return "AIR"

        if system.duct and system.duct.has_valid_geometry():
            if soil_layer and soil_layer.kind is TrenchLayerKind.CONCRETE:
                return "CONCRETE_DUCT"
            return "DUCT"

        if soil_layer and soil_layer.kind is TrenchLayerKind.AIR:
            return "AIR"

        if soil_layer and soil_layer.kind is TrenchLayerKind.CONCRETE:
            return "TROUGH"

        return "SOIL"

    def _t4_direct_buried(
        self,
        instance: CablePhaseInstance,
        population: Sequence[CablePhaseInstance],
        surface_level_y: float,
        outer_diameter_mm: float,
        depth_mm: float,
        soil_resistivity: float,
        issues: List[str],
    ) -> Optional[float]:
        if soil_resistivity <= 0.0:
            issues.append("Thermal resistivity for soil layer unavailable.")
            return None

        u = (2.0 * depth_mm) / outer_diameter_mm
        if u <= 1.0:
            issues.append("Depth-to-diameter ratio too small for T4.")
            return None

        group_term = self._grouped_direct_burial_resistance(
            instance.system,
            soil_resistivity,
            u,
            outer_diameter_mm,
            issues,
        )
        if group_term is not None:
            mutual_factor = self._mutual_factor(
                instance,
                population,
                surface_level_y,
                issues,
                skip_system=instance.system,
            )
            if mutual_factor <= 0.0:
                issues.append("Invalid logarithm argument for T4.")
                return None
            if mutual_factor == 1.0:
                return group_term
            return group_term + (soil_resistivity / (2.0 * math.pi)) * math.log(mutual_factor)

        return self._buried_medium_resistance(
            outer_diameter_mm=outer_diameter_mm,
            depth_mm=depth_mm,
            soil_resistivity=soil_resistivity,
            instance=instance,
            population=population,
            surface_level_y=surface_level_y,
            issues=issues,
        )

    def _grouped_direct_burial_resistance(
        self,
        system: CableSystem,
        soil_resistivity: float,
        u: float,
        outer_diameter_mm: float,
        issues: List[str],
    ) -> Optional[float]:
        if system.kind is not CableSystemKind.SINGLE_CORE:
            return None

        arrangement = system.arrangement or SingleCoreArrangement.FLAT
        phase_count = len(system.phase_offsets_mm()) or 1
        sheath_type = self._sheath_category(system)

        offsets = system.phase_offsets_mm()
        min_spacing = None
        if offsets and len(offsets) > 1 and outer_diameter_mm > 0.0:
            min_spacing = min(
                math.hypot(x1 - x2, y1 - y2)
                for idx, (x1, y1) in enumerate(offsets)
                for x2, y2 in offsets[idx + 1 :]
            )
            touching_threshold = outer_diameter_mm * 1.05
            if min_spacing is not None and min_spacing > touching_threshold:
                return None

        if arrangement is SingleCoreArrangement.TREFOIL and phase_count == 3:
            if u <= 0.0:
                issues.append("Invalid burial ratio for trefoil grouping.")
                return None
            if sheath_type == "metallic":
                if 2.0 * u <= 0.0:
                    issues.append("Invalid logarithm argument for trefoil metallic T4.")
                    return None
                return (soil_resistivity / (1.5 * math.pi)) * math.log(2.0 * u) - 0.63 * soil_resistivity
            # Treat part-metallic as metallic for conservatism.
            if u <= 0.0:
                issues.append("Invalid burial ratio for trefoil grouping.")
                return None
            return (soil_resistivity / (2.0 * math.pi)) * (math.log(2.0 * u) + math.log(u))

        if arrangement is SingleCoreArrangement.FLAT:
            if phase_count == 3:
                if u < 5.0:
                    return None
                if sheath_type == "metallic":
                    argument = 0.475 * math.log(2 * u) - 0.346
                else:
                    argument = 0.475 * math.log(2 * u) - 0.142
                if argument <= 0.0:
                    issues.append("Invalid logarithm argument for flat grouping T4.")
                    return None
                return soil_resistivity * argument
            if phase_count == 2:
                if u < 5.0:
                    return None
                if sheath_type == "metallic":
                    argument = 1.5 * u - 0.45
                else:
                    argument = 0.95 * u - 0.29
                if argument <= 0.0:
                    issues.append("Invalid logarithm argument for flat grouping T4.")
                    return None
                return (soil_resistivity / math.pi) * math.log(argument)

        return None

    def _sheath_category(self, system: CableSystem) -> str:
        phase = system.single_core_phase if system.kind is CableSystemKind.SINGLE_CORE else None
        if not phase:
            return "non_metallic"

        def _is_metallic(layer_role: LayerRole) -> bool:
            layer = phase.get_layer(layer_role)
            if not layer or not layer.material:
                return False
            resistivity = layer.material.electrical_resistivity_ohm_mm2_per_m
            return resistivity is not None and resistivity > 0.0 and resistivity < 1.0

        if _is_metallic(LayerRole.SHEATH) or _is_metallic(LayerRole.ARMOUR):
            return "metallic"
        return "non_metallic"

    def _buried_medium_resistance(
        self,
        outer_diameter_mm: float,
        depth_mm: float,
        soil_resistivity: float,
        instance: CablePhaseInstance,
        population: Sequence[CablePhaseInstance],
        surface_level_y: float,
        issues: List[str],
        skip_system: Optional[CableSystem] = None,
    ) -> Optional[float]:
        if depth_mm <= 0.0:
            issues.append("Cable axis must be below surface for T4.")
            return None

        if outer_diameter_mm <= 0.0:
            issues.append("Outer diameter required for T4.")
            return None

        u = (2.0 * depth_mm) / outer_diameter_mm
        if u <= 1.0:
            issues.append("Depth-to-diameter ratio too small for T4.")
            return None

        if u > 10.0:
            base_term = 2.0 * u
        else:
            base_term = u + math.sqrt(u * u - 1.0)

        mutual_factor = self._mutual_factor(instance, population, surface_level_y, issues, skip_system=skip_system)
        log_arg = base_term * mutual_factor

        if log_arg <= 0.0:
            issues.append("Invalid logarithm argument for T4.")
            return None

        return (soil_resistivity / (2.0 * math.pi)) * math.log(log_arg)

    def _t4_air(
        self,
        instance: CablePhaseInstance,
        depth_mm: float,
        issues: List[str],
    ) -> Optional[float]:
        issues.append("Air installation thermal resistance requires additional ambient data.")
        if depth_mm > 0.0:
            issues.append("Air branch selected but cable is below surface.")
        return None

    def _t4_trough(
        self,
        instance: CablePhaseInstance,
        soil_layer: Optional[TrenchLayer],
        depth_mm: float,
        population: Sequence[CablePhaseInstance],
        surface_level_y: float,
        issues: List[str],
    ) -> Optional[float]:
        if soil_layer is None:
            issues.append("Trough installation lacks surrounding material data.")
            return None
        if soil_layer.kind is TrenchLayerKind.CONCRETE:
            issues.append("Concrete trough modelling not yet implemented; treating as direct burial.")
            resistivity = soil_layer.thermal_resistivity_k_m_per_w
            outer_diameter_mm = instance.outer_diameter_mm or self._outer_diameter_mm(instance.system)
            if outer_diameter_mm is None or outer_diameter_mm <= 0.0:
                issues.append("Outer diameter required for trough model.")
                return None
            return self._buried_medium_resistance(
                outer_diameter_mm=outer_diameter_mm,
                depth_mm=depth_mm,
                soil_resistivity=resistivity,
                instance=instance,
                population=population,
                surface_level_y=surface_level_y,
                issues=issues,
            )
        issues.append("Open trough installations not supported; assuming ambient air cooling.")
        return None

    def _t4_duct(
        self,
        instance: CablePhaseInstance,
        system: CableSystem,
        duct: DuctSpecification,
        soil_resistivity: float,
        depth_mm: float,
        population: Sequence[CablePhaseInstance],
        surface_level_y: float,
        params: CalculatorParams,
        issues: List[str],
    ) -> Optional[float]:
        if depth_mm <= 0.0:
            issues.append("Cable axis must be below surface for T4.")
            return None

        phase = system.single_core_phase
        if system.kind is not CableSystemKind.SINGLE_CORE or not phase:
            issues.append("Duct modelling currently supports single-core systems only.")
            return None

        cable_diameter_mm = phase.overall_diameter_mm()
        if cable_diameter_mm <= 0.0:
            issues.append("Cable diameter required for duct thermal model.")
            return None

        equivalent_diameter_mm = duct.equivalent_cable_diameter_mm(cable_diameter_mm)
        equivalent_diameter_m = equivalent_diameter_mm / 1000.0
        if equivalent_diameter_m <= 0.0:
            issues.append("Equivalent cable diameter for duct must be positive.")
            return None

        contact = duct.contact_constants()
        if contact.u <= 0.0:
            issues.append("Duct contact constant U must be positive.")
            return None

        medium_temp_c = duct.medium_temperature_c if duct.medium_temperature_c is not None else params.ambient_temp_c
        denominator = contact.denominator(medium_temp_c)
        if denominator <= 0.0:
            issues.append("Duct contact denominator is non-positive.")
            return None

        t4_prime = contact.u / (1 + denominator * equivalent_diameter_m)

        inner_m = duct.inner_diameter_mm / 1000.0
        outer_m = duct.outer_diameter_mm / 1000.0
        if inner_m <= 0.0 or outer_m <= inner_m:
            issues.append("Duct geometry invalid for wall thermal resistance.")
            return None

        t4_double_prime = 0.0
        rho_t = duct.material.thermal_resistivity_k_m_per_w
        if not duct.material.is_metallic:
            if rho_t is None or rho_t <= 0.0:
                issues.append("Thermal resistivity for duct material unavailable.")
                return None
            t4_double_prime = (rho_t / (2.0 * math.pi)) * math.log(outer_m / inner_m)

        t4_triple_prime = self._buried_medium_resistance(
            outer_diameter_mm=duct.outer_diameter_mm,
            depth_mm=depth_mm,
            soil_resistivity=soil_resistivity,
            instance=instance,
            population=population,
            surface_level_y=surface_level_y,
            issues=issues,
        )
        if t4_triple_prime is None:
            return None

        return t4_prime + t4_double_prime + t4_triple_prime

    def _t4_concrete_duct(
        self,
        instance: CablePhaseInstance,
        system: CableSystem,
        duct: DuctSpecification,
        soil_layer: Optional[TrenchLayer],
        depth_mm: float,
        population: Sequence[CablePhaseInstance],
        surface_level_y: float,
        params: CalculatorParams,
        issues: List[str],
    ) -> Optional[float]:
        if not soil_layer or soil_layer.kind is not TrenchLayerKind.CONCRETE:
            return None

        rho_c = soil_layer.thermal_resistivity_k_m_per_w
        if rho_c is None or rho_c <= 0.0:
            issues.append("Concrete layer requires thermal resistivity.")
            return None

        surrounding_soil = self._find_adjacent_soil_layer(soil_layer)
        rho_e = surrounding_soil.thermal_resistivity_k_m_per_w if surrounding_soil else rho_c

        phase = system.single_core_phase
        if not phase:
            issues.append("Concrete duct bank requires single-core phase definition.")
            return None

        cable_diameter_mm = phase.overall_diameter_mm()
        equivalent_diameter_m = duct.equivalent_cable_diameter_mm(cable_diameter_mm) / 1000.0
        if equivalent_diameter_m <= 0.0:
            issues.append("Equivalent cable diameter for duct must be positive.")
            return None

        contact = duct.contact_constants()
        medium_temp_c = duct.medium_temperature_c if duct.medium_temperature_c is not None else params.ambient_temp_c
        denominator = contact.denominator(medium_temp_c)
        if denominator <= 0.0:
            issues.append("Duct contact denominator is non-positive.")
            return None
        t4_prime = contact.u / (denominator * equivalent_diameter_m)

        duct_wall_term = 0.0
        rho_t = duct.material.thermal_resistivity_k_m_per_w
        if not duct.material.is_metallic:
            if rho_t is None or rho_t <= 0.0:
                issues.append("Thermal resistivity for duct material unavailable.")
                return None
            inner_m = duct.inner_diameter_mm / 1000.0
            outer_m = duct.outer_diameter_mm / 1000.0
            if inner_m <= 0.0 or outer_m <= inner_m:
                issues.append("Duct geometry invalid for wall thermal resistance.")
                return None
            duct_wall_term = (rho_t / (2.0 * math.pi)) * math.log(outer_m / inner_m)

        x_m = self._scene.config.trench_width_mm / 1000.0
        y_m = max(soil_layer.thickness_mm, 0.0) / 1000.0
        if x_m <= 0.0 or y_m <= 0.0:
            issues.append("Concrete duct bank dimensions invalid.")
            return None
        if y_m / x_m >= 3.0:
            issues.append("Concrete duct bank aspect ratio exceeds IEC correlation limit.")
            return None

        r_b = self._equivalent_bank_radius(x_m, y_m)
        if r_b <= 0.0:
            issues.append("Equivalent duct bank radius invalid.")
            return None

        L_g_m = depth_mm / 1000.0
        u_b = L_g_m / r_b
        if u_b <= 1.0:
            issues.append("Concrete duct bank burial ratio too small.")
            return None

        sqrt_term = math.sqrt(u_b * u_b - 1.0)
        t4_concrete = (rho_c / (2.0 * math.pi)) * math.log(u_b + sqrt_term)

        n_loaded = max(len(system.phase_offsets_mm()), 1)
        delta_t4 = (n_loaded / (2.0 * math.pi)) * (rho_e - rho_c) * math.log(u_b + sqrt_term)

        mutual_factor = self._mutual_factor(
            instance,
            population,
            surface_level_y,
            issues,
            skip_system=instance.system,
        )
        if mutual_factor <= 0.0:
            issues.append("Invalid logarithm argument for concrete duct mutual factor.")
            return None

        adjustment = 0.0
        if mutual_factor != 1.0:
            adjustment = (rho_e / (2.0 * math.pi)) * math.log(mutual_factor)

        return t4_prime + duct_wall_term + t4_concrete + delta_t4 + adjustment

    def _find_adjacent_soil_layer(self, target: TrenchLayer) -> Optional[TrenchLayer]:
        layers = self._scene.config.layers
        try:
            index = layers.index(target)
        except ValueError:
            return None

        for offset in (1, -1):
            neighbour_index = index + offset
            if 0 <= neighbour_index < len(layers):
                candidate = layers[neighbour_index]
                if candidate.kind is not TrenchLayerKind.CONCRETE and candidate.thermal_resistivity_k_m_per_w > 0.0:
                    return candidate
        return None

    @staticmethod
    def _equivalent_bank_radius(x_m: float, y_m: float) -> float:
        ratio = x_m / y_m if y_m != 0 else 0.0
        if y_m == 0.0 or ratio <= 0.0:
            return -1.0
        term = 0.5 * (ratio) * (4.0 / math.pi - ratio) * math.log(1.0 + (y_m * y_m) / (x_m * x_m))
        return math.exp(term + math.log(x_m / 2.0))

    @staticmethod
    def _outer_diameter_mm(system: CableSystem) -> Optional[float]:
        if system.kind is CableSystemKind.SINGLE_CORE and system.single_core_phase:
            return system.single_core_phase.overall_diameter_mm()
        if system.kind is CableSystemKind.MULTICORE and system.multicore:
            return system.multicore.outer_diameter_mm
        return None

    @staticmethod
    def _soil_layer_for_depth(layers: Sequence[TrenchLayer], depth_mm: float) -> Optional[TrenchLayer]:
        remaining = depth_mm
        for layer in layers:
            thickness = max(layer.thickness_mm, 0.0)
            if remaining <= thickness:
                return layer
            remaining -= thickness
        return layers[-1] if layers else None

    def _mutual_factor(
        self,
        target: CablePhaseInstance,
        population: Sequence[CablePhaseInstance],
        surface_level_y: float,
        issues: List[str],
        skip_system: Optional[CableSystem] = None,
    ) -> float:
        xp, yp = target.position_mm
        mirror_y = (2.0 * surface_level_y) - yp
        product = 1.0

        for other in population:
            if other is target:
                continue
            if skip_system is not None and other.system is skip_system:
                continue
            xo, yo = other.position_mm
            direct = math.hypot(xp - xo, yp - yo)
            if direct <= 0.0:
                # Co-located phases share conduits; ignore their mutual image contribution.
                continue
            image_distance = math.hypot(xp - xo, mirror_y - yo)
            if image_distance <= 0.0:
                issues.append(f"Image distance invalid between {target.label} and {other.label}.")
                continue
            product *= image_distance / direct

        return product

    @staticmethod
    def _find_layer_index(
        profile: Sequence[Tuple[object, float, float]],
        role: LayerRole,
    ) -> Optional[int]:
        for idx, (layer, _inner, _outer) in enumerate(profile):
            if isinstance(layer, tuple):
                continue
            if getattr(layer, "role", None) is role:
                return idx
        return None

    @staticmethod
    def _sum_layer_resistances(
        layers: Sequence[Tuple[object, float, float]],
        issues: List[str],
        label: str,
    ) -> Optional[float]:
        if not layers:
            return 0.0

        total = 0.0
        for spec, inner_radius, outer_radius in layers:
            material = getattr(spec, "material", None)
            rho = None
            if hasattr(spec, "thermal_resistivity"):
                rho = spec.thermal_resistivity()
            if rho is None and material is not None:
                rho = getattr(material, "thermal_resistivity_k_m_per_w", None)

            if rho is None or rho <= 0.0:
                issues.append(f"{label}: Missing thermal resistivity for {getattr(spec, 'role', 'layer')}.")
                return None
            if outer_radius <= inner_radius:
                continue

            inner_m = (inner_radius / 1000.0) if inner_radius > 0 else 1e-9
            outer_m = outer_radius / 1000.0
            if outer_m <= inner_m:
                continue
            total += (rho / (2.0 * math.pi)) * math.log(outer_m / inner_m)

        return total

    # ----------------------------------------------------------- table output
    def _populate_table(self, results: Iterable[AmpacityResult]) -> None:
        rows = list(results)
        table = self._results_table
        table.setRowCount(len(rows))

        for row, result in enumerate(rows):
            self._set_table_item(row, 0, result.label)
            self._set_table_item(row, 1, self._format_value(result.conductor_resistance_ohm_per_m, precision=5))
            self._set_table_item(row, 2, self._format_value(result.t1))
            self._set_table_item(row, 3, self._format_value(result.t2))
            self._set_table_item(row, 4, self._format_value(result.t3))
            self._set_table_item(row, 5, self._format_value(result.t4))
            self._set_table_item(row, 6, self._format_value(result.ampacity_a, precision=1))
            notes = "; ".join(dict.fromkeys(result.issues))
            self._set_table_item(row, 7, notes if notes else "—")

        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)

    @staticmethod
    def _format_value(value: Optional[float], precision: int = 3) -> str:
        if value is None:
            return "—"
        return f"{value:.{precision}f}"

    def _set_table_item(self, row: int, column: int, text: str) -> None:
        item = QTableWidgetItem(text)
        item.setFlags(Qt.ItemIsEnabled)
        self._results_table.setItem(row, column, item)
