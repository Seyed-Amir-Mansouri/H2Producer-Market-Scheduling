<p align="center">
  <img src="Power-Hydrogen%20Co-Dispatch%20Overview.png" alt="Power-Hydrogen Co-Dispatch Overview" width="760">
</p>

A **linear-programming economic dispatch** over the **Central-European CORE
region** — the database covers 20 ENTSO-E bidding zones (NL6H excluded;
PL00E/PL00I replaced by direct PL00-CZ00/DE00/SK00 links in `Networks.xlsx`)
of the 13 CORE Capacity-Calculation-Region countries (AT, BE, CZ, DE, FR, HR,
HU, LU, NL, PL, RO, SI, SK) — coupling two energy carriers, **electricity**
and **hydrogen**,
using [linopy](https://linopy.readthedocs.io) + the open-source **HiGHS** solver.
The horizon is a whole number of days (default one day = 24 hours; use
`--start-day/--end-day` for multi-day runs). All data is the **ENTSO-E TYNDP
National Trends 2030 (NT2030)** scenario, supplied as the parquet databases in
`inputs/`.

## Setup

Requires **Python 3.12+**. From the project folder, create and activate a virtual
environment, then install the dependencies:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1          # PowerShell   (CMD: .venv\Scripts\activate.bat)
pip install -r requirements.txt
```

Activating the environment puts `python` and `pip` on your PATH in
that terminal, so the commands below work as written. To use them from **any**
terminal without activating first, add the environment's `Scripts` folder to the
Windows **PATH** environment variable (Settings → *Edit the system environment
variables* → *Environment Variables* → select *Path* → *New*), then restart the
terminal.

## Quick start

```bash
python run_dispatch.py                                              # all zones, day 1
python run_dispatch.py --zones DE00,FR00 --start-day 10 --end-day 10  # a single day
python run_dispatch.py --start-day 10 --end-day 16                  # a 7-day horizon
python run_dispatch.py --uc
```

Everything the model needs is in the **`inputs/` NT2030 databases** — zone data,
network topology + prices, and cross-border flows — so a fresh clone runs
straight away.

## Web UI

A [Django](https://www.djangoproject.com) app wraps the model — run it locally
after installing the requirements:

```bash
python webui/manage.py runserver
```

It opens in your browser (`localhost:8000`). Pick zones or whole countries
(checking one zone of a country auto-includes its siblings), a 2030 date
range, then click **Run dispatch**. The results page plots our electricity and
hydrogen marginal prices against PLEXOS's own reference series and reports
per-zone validation metrics (correlation, RMSE, mean difference, real shedding
hours). The app calls the same model code as the CLI.

A toggle switch in front of **Hydrogen Producer** (off by default, mirrors CLI
`--h2-producer`) reveals a table with one row per country that has at least
one zone checked above — added/removed live as you (un)check zones — and one
editable column per asset: **Electrolyser, Wind, PV, Battery, H2 tank** (all
MW). Each cell starts pre-filled with that country's own model-derived
default (see "Hydrogen Producer" below) and can be edited freely; leave a
cell blank instead and that one asset falls back to the model's own sizing
for that country. Every cell is independent — editing DE's Wind doesn't touch
DE's PV/Battery/Tank, the electrolyser ranking, or any other country's
values. Battery/tank MWh still comes from that MW times the model's own
duration defaults (2h / 24h). Submitting with the toggle off ignores the
table entirely (bad input there won't block an otherwise-valid run). When
it's on, the results page adds a Hydrogen Producer panel: the realized RED
III renewable-H2 share and Green Certificates bought/sold, plus a per-country
table of the resolved asset capacities (electrolyser, wind, PV + donor
zones, battery, H2 tank) for that run — this is where you can see any edits
actually take effect.

## Command line

CLI flags:

| Flag | Meaning |
|------|---------|
| `--zones DE00,FR00,…` | subset of zones (default: all zones in the database) |
| `--start-day S --end-day E` | horizon covering days `S..E` inclusive (`(E-S+1)·24` hours); omit both for day 1 |
| `--uc` | enable unit commitment (small MILP; currently min up/down time for 2 DE00 fleets only) |
| `--h2-producer` | enable the per-country Hydrogen Producer (wind+PV+battery+electrolyser+H2 tank+flexible downstream demand) |
| `--out-tag NAME` | write results to `outputs/NAME/` instead of `outputs/` (keep runs side by side) |

Results are written to `outputs/` and a balance-validation check prints at the
end. Each run **wipes the existing `*.csv` in its output folder first** (clean
slate), so the folder always reflects exactly the last run — no stale files
linger from a previous run with different options. By default all runs share the
one `outputs/` folder, so a run overwrites the previous one's results. To keep
runs side by side, pass **`--out-tag NAME`**, which writes to `outputs/NAME/`
(the clean-slate wipe is scoped to that subfolder, so tagged and untagged runs
don't clobber each other). Example:

```bash
python run_dispatch.py --start-day 1   --end-day 1   --out-tag winter_day
python run_dispatch.py --start-day 200 --end-day 200 --out-tag summer_day
# -> outputs/winter_day/  and  outputs/summer_day/  side by side
```

Multi-day runs build a larger LP (constraints scale with the number of hours).
Every storage device must finish the horizon **no lower than it started it** —
`soc[last hour] ≥ soc[first hour]` (a full storage cycle over the run) — so a
run cannot look good merely by draining the reservoirs it began with. Must-run
uses the first day's month.

**Zones default to every zone in the database.** Selecting a subset with
`--zones` automatically reclassifies each border: a line between two selected
zones stays an internal (optimised) link, while a line to a non-selected zone
becomes a fixed cross-border exchange. Requesting a zone absent from the
database is a clear error.

## Hydrogen Producer (optional, `--h2-producer`)

A self-contained, per-country "compact unit" — its own wind, PV, battery, and
electrolyser on the power side; its own H2 tank and a flexible downstream H2
demand on the hydrogen side (one per qualifying country, attached to that
country's main H2 zone). It keeps its own internal power/H2 balances and
touches the rest of the model only through a net electricity exchange and a
net hydrogen exchange with its host zone, so import/export is directly
visible in the output columns above. Off by default. **None of its sizing
comes from PLEXOS/ENTSO-E data** — most capacities are a flat `config.py`
assumption (`h2_producer_*`) applied uniformly to every country; treat it as
a scenario/what-if tool, not a calibrated part of the dataset.

**Electrolyser, wind, PV, battery, and H2 tank capacity are the exception —
all derived per country**, not flat. Electrolyser: picked from
`h2_producer_electrolyser_capacities_mw` (default
`[5,5,10,10,15,15,20,20,25,25,30,35,40]` MW — one entry per country, 13 by
default): countries are ranked by their own national H2 load and zipped
against the list sorted ascending, so the smallest-load country gets the
smallest capacity and the largest gets the largest — every listed value gets
used.

Everything else scales off that assigned electrolyser capacity, each
rounded to the nearest step (floored at one step, never 0):
- **Wind + PV**: combined = `h2_producer_renewable_pct_of_electrolyser_mw`
  (default 30%) of electrolyser MW, split so wind is
  `h2_producer_wind_to_pv_ratio` (default 1.3x) bigger than PV, rounded to
  `h2_producer_renewable_capacity_step_mw` (default **2.5 MW** — 5 MW was
  too coarse to resolve anything at this project's 5–40 MW electrolyser
  scale; every country collapsed to the same 5/5 MW floor).
- **Battery**: power = `h2_producer_battery_pct_of_electrolyser_mw` (default
  25%) of electrolyser MW, energy = power × `h2_producer_battery_duration_hours`
  (default 2h, a standard Li-ion grid-battery duration).
- **H2 tank**: power = `h2_producer_tank_pct_of_electrolyser_h2` (default
  50%) of the electrolyser's *deliverable H2* (not raw electric MW — the
  tank stores hydrogen), energy = power × `h2_producer_tank_duration_hours`
  (default 24h, "one day of autonomy"). Battery and tank power both round
  to `h2_producer_battery_tank_step_mw` (default 2.5 MW).

Verified spread: the 8 smallest-electrolyser countries floor to
wind=pv=2.5 MW, battery=2.5 MW/5 MWh (or 5/10 for CZ/HU), tank scaling
similarly; Germany (the largest, 40 MW electrolyser) reaches wind=7.5/pv=5.0
MW, battery=10 MW/20 MWh, tank=12.5 MW/300 MWh.

**Wind/PV weather data isn't always the attachment zone's own.** 3 of the 13
countries' main H2 zones (BEOF, LUB1, NLLL) are H2-hub nodes with zero
installed wind/solar capacity and an all-zero weather profile of their own —
so even with a nonzero MW rating assigned, those Producers generated *zero*
wind/PV every hour. Fixed by falling back, independently for wind and PV, to
the same-country zone with real data and the largest installed capacity for
that tech (checked against the full stored year for stability): BE now uses
BE00's weather, LU uses LUG1's, NL uses NL00's. Only the weather source
changes — the Producer still nets its electricity/H2 exchange at the main H2
zone. See `wind_donor_zone`/`pv_donor_zone` in `nodes_h2_producer.csv`.

**That profile is then normalized to its own historical peak.** Whichever
zone gets selected (host or donor), its raw capacity-factor profile is
divided by its own maximum value over the full stored year, so the profile's
peak becomes exactly 1.0. These NT2030 profiles never actually reach 1.0
even at their single best hour of the year (e.g. DE00 wind tops out at 0.83,
NL00 solar at 0.44) — without normalizing, an assigned wind/PV MW rating
could never be reached even under the best weather the year has to offer.

**Downstream demand is flat, and defined directly off that assigned
capacity** — `h2_producer_downstream_demand_pct_of_electrolyser_capacity`
(default 80%) of what the electrolyser can actually deliver
(`electrolyser_mw * h2_producer_electrolyser_efficiency`), the same every
hour, so it can never exceed what the electrolyser could supply and never
needs rescaling. The flex band (`h2_producer_demand_flex_pct`, default 20%)
is sized off that same deliverable capacity too, not off the baseline itself
— with the defaults, demand ranges from exactly 60% to 100% of what the
electrolyser can deliver. See `outputs/inputs/nodes_h2_producer.csv` for the
resolved capacity and demand per country.

Its downstream demand is also subject to the **RED III industrial
renewable-hydrogen quota** (`h2_producer_renewable_h2_quota`, default
`0.42`): at least 42% of the H2 it consumes, over the whole run, must be
renewable. Two ways to meet it: physically matching electrolyser load to its
own onsite wind/PV hour-by-hour, or trading **Green Certificates**
(`h2_producer_gc_price_eur_per_mwh`, default EUR 5/MWh, same price both
ways) — unbundled, non-hourly-matched purchases/sales, like a real
Guarantee-of-Origin market:
- **Buy** lets grid-sourced electrolyser load count too, capped by how much
  load wasn't already matched onsite.
- **Sell** separately monetizes onsite wind/PV generation that wasn't needed
  for this country's own compliance — its own cap, and it doesn't reduce
  this country's own quota math (a real generator sells the certificate for
  surplus generation on top of selling the power itself).

Because the onsite-matching route ties compliance to whatever weather a
short horizon happens to see, a single unlucky low-wind/low-solar day used
to be **infeasible** with defaults; buying certificates fixes that (the same
day now solves). At the cheap default certificate price, cost-minimization
actually prefers trading certificates over dedicating onsite generation —
and buys even *more* once selling is available too, since every MWh kept
out of the electrolyser now earns two revenue streams (export **and** its
certificate) — a deliberate, realistic reflection of a known critique of
unbundled Guarantee-of-Origin schemes; raise the price to push the model
back toward physical matching. Set the quota to `0.0` to disable the whole
mechanism for short exploratory runs. `report.summary()` reports the
realized share as `h2_producer_renewable_h2_share`, and the certificates
traded as `h2_producer_gc_purchased_mwh` / `h2_producer_gc_sold_mwh`.

Full math spec in `Formulation.md` §18 (quota + GC: §18.7).

## Hourly per-technology balance (PLEXOS-style)

Each run writes exactly two CSVs to `outputs/` — wide tables in the style of the
market model's hourly per-technology output (electricity and hydrogen), with a
two-level column header `(zone, category)` and one row per hour:

| File | Per-zone categories |
|------|---------------------|
| `hourly_balance_elec.csv` | each generation technology (MW), plus Storage discharge / charge, Electrolyser load, Net line import, External exchange, Load shedding, Dumped/curtailed, Demand, H2 Producer wind/pv/battery/electrolyser load/grid exchange *(with `--h2-producer`)*, **Marginal Price (EUR/MWh)** |
| `hourly_balance_h2.csv` | Electrolyser production, Terminal import, SMR production, Net pipeline import, External exchange, H2 storage discharge / charge, Load shedding, Dumped/curtailed, H2 plant consumption, Demand, H2 Producer electrolyser production/tank discharge/tank charge/downstream demand/pipeline exchange *(with `--h2-producer`)*, **Marginal Price (EUR/MWh)** |

Signs are chosen so supply is `+` and consumption `-`, so the energy (MW)
categories of each row sum to ~0 (the nodal balance holds).

**Marginal Price (EUR/MWh)** is the zonal price — the dual of the nodal balance.
Since the dispatch is a pure LP, this dual comes straight from the single solve
(no re-solve needed), so prices are always reported. Any hour where that dual
lands at (≥99% of) the shedding penalty `voll_eur_per_mwh` while that zone's own
`Load shedding` is ~0 for that same hour is treated as undefined and left blank
(`report.py`'s masking check compares price against shed **zone-by-zone,
hour-by-hour** — it doesn't check whether the zone is otherwise empty). The most
common case is a genuinely empty node (no demand, generation, or lines), whose
degenerate dual pins at the penalty by construction. But the same blanking also
fires on real, active zones: an hour immediately adjacent to a real shedding
event elsewhere in the coupled system can inherit a dual pinned at the penalty
through a binding cross-border/pipeline link, even though that particular
zone didn't shed *that* hour — the price number in that case reflects the
penalty, not an economically meaningful clearing price, so it's blanked rather
than shown as a misleadingly precise figure.
