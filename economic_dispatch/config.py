"""Run configuration and tunable assumptions.

Everything a user might reasonably want to change lives here so the model code
stays free of magic numbers. Values flagged "ASSUMPTION" are documented in the
README and are the ones to revisit if results look off.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ALL_ZONES = [
    "AT00", "BE00", "BEOF", "CZ00", "DE00", "DEKF", "FR00", "FR15", "HR00",
    "HU00", "LUB1", "LUF1", "LUG1", "LUV1", "NL00", "NLLL", "PL00",
    "RO00", "SI00", "SK00",
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = PROJECT_ROOT / "XLSXs"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "outputs"
DEFAULT_EXPORTS_DIR = PROJECT_ROOT / "inputs"
DEFAULT_ZONES_DB = DEFAULT_EXPORTS_DIR / "zones_2030.parquet"
DEFAULT_NETWORKS_DB = DEFAULT_EXPORTS_DIR / "networks_2030.parquet"
DEFAULT_H2_REF = DEFAULT_EXPORTS_DIR / "ReferenceGrid_Hydrogen.xlsx"
DEFAULT_PLEXOS_REF = DEFAULT_DATA_DIR / "MMStandardOutputFile_NT2030_Plexos_CY2009_2.5_v40.xlsx"
DEFAULT_MARGINAL_PRICE_ELEC_DB = DEFAULT_EXPORTS_DIR / "marginal_price_electricity_2030.parquet"
DEFAULT_MARGINAL_PRICE_H2_DB = DEFAULT_EXPORTS_DIR / "marginal_price_hydrogen_2030.parquet"

HOURS_PER_DAY = 24
HOURS_PER_YEAR = 8736


_ZONE_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{2,3}$")
_EXCLUDE_ZONES = {"NL6H", "PL00E", "PL00I"}


def discover_zones_from_xlsx(data_dir=DEFAULT_DATA_DIR) -> list[str]:
    """Zone codes = every ``*.xlsx`` in ``data_dir`` whose name matches a zone code.

    Returns them sorted for reproducibility. Excel lock files (``~$*``),
    ``Networks.xlsx``, non-zone workbooks, and ``_EXCLUDE_ZONES`` are skipped.
    Empty list if the folder can't be read. Only used by build_db.py to
    build zones_2030.parquet in the first place -- discover_zones() below
    reads that database directly at runtime, so normal use never needs the
    raw XLSXs/ folder at all.
    """
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        return []
    return sorted(
        p.stem for p in data_dir.glob("*.xlsx")
        if _ZONE_RE.match(p.stem) and p.stem not in _EXCLUDE_ZONES
        and not p.name.startswith("~$")
    )


@lru_cache(maxsize=8)
def discover_zones(zones_db=DEFAULT_ZONES_DB, data_dir=DEFAULT_DATA_DIR) -> list[str]:
    zones_db = Path(zones_db)
    if zones_db.exists():
        import pandas as pd
        return sorted(pd.read_parquet(zones_db, columns=["zone"])["zone"].unique().tolist())
    return discover_zones_from_xlsx(data_dir)


def _expand_to_countries(zones: list[str], zones_db) -> list[str]:
    all_zones = discover_zones(zones_db)
    countries = {z[:2] for z in zones}
    return sorted(set(zones) | {z for z in all_zones if z[:2] in countries})


@dataclass
class RunConfig:
    zones: list[str] = field(default_factory=lambda: list(ALL_ZONES))
    start_day: int = 1
    end_day: int = 1
    data_dir: Path = DEFAULT_DATA_DIR
    output_dir: Path = DEFAULT_OUTPUT_DIR
    exports_dir: Path = DEFAULT_EXPORTS_DIR
    zones_db: Path = DEFAULT_ZONES_DB
    networks_db: Path = DEFAULT_NETWORKS_DB
    out_tag: str | None = None

    enable_h2_storage: bool = True
    cyclic_storage: bool = True
    enable_uc: bool = False
    subtract_dsr_implicit: bool = False
    electricity_only: bool = False
    enable_h2_producer: bool = False

    fuel_per_thermal: bool = True
    co2_per_thermal: bool = True
    default_efficiency: float = 0.5
    voll_eur_per_mwh: float = 3_000.0
    h2_terminal_price: float = 150.0
    dump_penalty_eur_per_mwh: float = 0.0
    storage_op_cost_eur_per_mwh: float = 0.01

    initial_soc_fraction: float = 0.5
    ramp_scale: float = 1.0
    default_pump_efficiency: float = 0.8
    default_closed_ps_efficiency: float = 0.75
    h2_storage_hours: float = 168.0
    h2_storage_efficiency: float = 1.0
    default_hydro_efficiency: float = 1.0

    # Hydrogen Producer (enable_h2_producer): a self-contained, per-country
    # wind+PV+battery+electrolyser+H2-tank+flexible-demand unit (model.py
    # _build_h2_producer, Formulation.md S18). None of this has PLEXOS or
    # ENTSO-E source data. A few values are still flat ASSUMPTIONs applied
    # uniformly to every country (efficiencies, connection caps, the first
    # things to revisit for a real scenario); downstream demand, electrolyser
    # capacity, wind/PV capacity, battery, and H2 tank are all DERIVED per
    # country instead, see the blocks below.
    #
    # Wind + PV nameplate capacity: combined = this fraction of the assigned
    # electrolyser's own MW rating (NOT its H2-equivalent output -- a simple
    # installed-capacity ratio, consistent with how downstream demand is
    # sized off electrolyser capacity below), split so wind is
    # h2_producer_wind_to_pv_ratio times PV, then each rounded independently
    # to the nearest multiple of h2_producer_renewable_capacity_step_mw
    # (floored at one step, never 0 -- "wind and pv should generate" implies
    # both are always present). See model._h2_producer_sizing,
    # Formulation.md S18.2. The step defaults to 2.5 MW, not 5: at this
    # project's electrolyser scale (5-40 MW), 30% combined is only 1.5-12
    # MW, too narrow a range for a 5 MW step to resolve (every country
    # collapsed to the same 5/5 MW floor) -- 2.5 MW does resolve real
    # differentiation across the electrolyser range (verified: 8 of 13
    # countries floor to 2.5/2.5 MW, BE/FR reach wind=5.0, PL/NL reach
    # pv=wind=5.0, DE reaches pv=5.0/wind=7.5).
    h2_producer_renewable_pct_of_electrolyser_mw: float = 0.30
    h2_producer_wind_to_pv_ratio: float = 1.3
    h2_producer_renewable_capacity_step_mw: float = 2.5
    h2_producer_electrolyser_efficiency: float = 0.68
    # Battery and H2 tank power/energy are DERIVED off electrolyser capacity
    # too, using "usual" sizing conventions for a compact P2X unit rather
    # than a single flat number for every country (model._h2_producer_sizing,
    # Formulation.md S18.2):
    #   battery_mw = pct * electrolyser_mw               (short-duration
    #     power smoothing/arbitrage -- 25% is a typical sizing fraction for
    #     a battery paired with an electrolyser of this size)
    #   battery_mwh = battery_mw * duration_hours         (2h is a standard
    #     Li-ion grid-battery duration)
    #   tank_mw = pct * (electrolyser_mw * efficiency)    (H2 charge/discharge
    #     rate, sized off DELIVERABLE H2 since the tank stores hydrogen, not
    #     electricity -- 50% lets it buffer roughly half of peak production)
    #   tank_mwh = tank_mw * duration_hours               (24h = "one day of
    #     autonomy", a common onsite H2 buffer target)
    # Both powers are rounded to the nearest h2_producer_battery_tank_step_mw
    # (floored at one step), same convention as wind/PV.
    h2_producer_battery_pct_of_electrolyser_mw: float = 0.25
    h2_producer_battery_duration_hours: float = 2.0
    h2_producer_tank_pct_of_electrolyser_h2: float = 0.50
    h2_producer_tank_duration_hours: float = 24.0
    h2_producer_battery_tank_step_mw: float = 2.5
    h2_producer_battery_efficiency: float = 0.92
    h2_producer_tank_efficiency: float = 1.0
    h2_producer_grid_connection_mw: float = 40.0
    h2_producer_h2_connection_mw: float = 20.0
    # Electrolyser capacity is DERIVED, not flat, per model._h2_producer_sizing
    # (Formulation.md S18.2): picked per country from the list below by rank
    # against that country's OWN total Hydrogen Demand Profile (full-year,
    # every zone of that country, selection/horizon-independent -- same
    # stability principle as model._h2_main_zones) -- countries sorted by that
    # reference load ascending are zipped against this list sorted ascending,
    # so the smallest-load country gets the smallest capacity and so on. The
    # list must have one entry per country with a Producer (13 by default); a
    # mismatch pads with the largest value (too few) or keeps the largest N
    # (too many).
    h2_producer_electrolyser_capacities_mw: list[float] = field(
        default_factory=lambda: [5, 5, 10, 10, 15, 15, 20, 20, 25, 25, 30, 35, 40])
    # Downstream demand is flat and defined directly off the assigned
    # electrolyser's own H2-equivalent capacity (electrolyser_mw * efficiency)
    # -- NOT off national H2 load -- so it can never need rescaling to fit
    # (Formulation.md S18.6): baseline = this fraction of that capacity, every
    # hour; the flexible band is +/- h2_producer_demand_flex_pct of that SAME
    # capacity (not of the baseline itself). Defaults span demand from 60% to
    # 100% of deliverable capacity (0.8 +/- 0.2).
    h2_producer_downstream_demand_pct_of_electrolyser_capacity: float = 0.80
    h2_producer_demand_flex_pct: float = 0.2
    # RED III Art. 22a industrial RFNBO target: >=42% of the hydrogen used in
    # industry must be renewable by 2030 (Formulation.md S18.7). Applied per
    # country to the Producer's own downstream demand. 0.0 disables it.
    h2_producer_renewable_h2_quota: float = 0.42
    # Green Certificates (Guarantees of Origin): buying one lets grid-imported
    # electricity count toward the quota above WITHOUT the hourly onsite-
    # generation matching e_ren normally requires -- an unbundled, book-and-
    # claim purchase, priced per MWh certified (Formulation.md S18.7).
    h2_producer_gc_price_eur_per_mwh: float = 5.0

    # Direct, per-country capacity overrides -- each dict is {country_code:
    # mw}, empty by default, which leaves the DERIVED sizing above completely
    # alone. A country present in one of these dicts gets that ONE asset
    # fixed at exactly the given MW (MWh for battery/tank = mw * the existing
    # h2_producer_battery_duration_hours / h2_producer_tank_duration_hours,
    # unchanged) instead of its derived value; a country absent from a dict
    # keeps that asset's normal derived value. Independent per asset AND per
    # country -- overriding DE's wind doesn't touch DE's PV/battery/tank, the
    # electrolyser ranking, or any other country's wind. Exists for the web
    # UI's per-country capacity table (model._h2_producer_sizing); the CLI
    # has no flag for these, only RunConfig/the web UI.
    h2_producer_electrolyser_mw_overrides: dict[str, float] = field(default_factory=dict)
    h2_producer_wind_mw_overrides: dict[str, float] = field(default_factory=dict)
    h2_producer_pv_mw_overrides: dict[str, float] = field(default_factory=dict)
    h2_producer_battery_mw_overrides: dict[str, float] = field(default_factory=dict)
    h2_producer_tank_mw_overrides: dict[str, float] = field(default_factory=dict)

    solver_name: str = "highs"
    mip_rel_gap: float = 1e-4

    def __post_init__(self) -> None:
        self.zones = _expand_to_countries(self.zones, self.zones_db)

    def resolved_output_dir(self) -> Path:
        """Output folder for this run: outputs/ or outputs/<out_tag>/ if tagged."""
        base = Path(self.output_dir)
        return base / self.out_tag if self.out_tag else base

    def hour_slice(self) -> tuple[int, int]:
        """Return (start_row, end_row) 0-based half-open into the 8736-hour year.

        Covers the inclusive day range [start_day, end_day], i.e.
        ``num_days() * 24`` hours.
        """
        start = (self.start_day - 1) * HOURS_PER_DAY
        end = self.end_day * HOURS_PER_DAY
        return start, end

    def num_days(self) -> int:
        return self.end_day - self.start_day + 1

    def month_index(self) -> int:
        """Approx calendar month (0-based) of the first day, for must-run selection.

        The dataset year is 364 days (52 weeks); we map to 12 equal ~30.33-day
        months purely to index the 12-value must-run lists. For a multi-day
        horizon the first day's month is used for the whole run.
        """
        day0 = self.start_day - 1
        return min(11, int(day0 / (364 / 12)))
