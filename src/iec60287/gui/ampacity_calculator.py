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
from iec60287.gui.placement_scene import PlacementScene, TrenchLayer
from iec60287.model import CableSystem, CableSystemKind, LayerRole


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
        t4 = self._soil_resistance(instance, population, issues)

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

    def _soil_resistance(
        self,
        instance: CablePhaseInstance,
        population: Sequence[CablePhaseInstance],
        issues: List[str],
    ) -> Optional[float]:
        config = self._scene.config
        surface_y = config.surface_level_y
        _, centre_y = instance.position_mm
        depth_mm = max(centre_y - surface_y, 0.0)

        outer_diameter_mm = instance.outer_diameter_mm or self._outer_diameter_mm(instance.system)
        if outer_diameter_mm is None or outer_diameter_mm <= 0.0:
            issues.append("Outer diameter required for T4.")
            return None

        soil_layer = self._soil_layer_for_depth(config.layers, depth_mm)
        resistivity = soil_layer.thermal_resistivity_k_m_per_w if soil_layer else None
        if resistivity is None or resistivity <= 0.0:
            issues.append("Thermal resistivity for soil layer unavailable.")
            return None

        if depth_mm <= 0.0:
            issues.append("Cable axis must be below surface for T4.")
            return None

        u = (2.0 * depth_mm) / outer_diameter_mm
        if u <= 1.0:
            issues.append("Depth-to-diameter ratio too small for T4.")
            return None

        if u > 10.0:
            base_term = 2.0 * u
        else:
            base_term = u + math.sqrt(u * u - 1.0)

        mutual_factor = self._mutual_factor(instance, population, surface_y, issues)
        log_arg = base_term * mutual_factor

        if log_arg <= 0.0:
            issues.append("Invalid logarithm argument for T4.")
            return None

        return (resistivity / (2.0 * math.pi)) * math.log(log_arg)

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
    ) -> float:
        xp, yp = target.position_mm
        mirror_y = (2.0 * surface_level_y) - yp
        product = 1.0

        for other in population:
            if other is target:
                continue
            xo, yo = other.position_mm
            direct = math.hypot(xp - xo, yp - yo)
            if direct <= 0.0:
                issues.append(f"Mutual spacing undefined between {target.label} and {other.label}.")
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
