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

It opens in your browser (`localhost:8000`). Pick zones or whole countries, a
2030 date range, then click **Run dispatch**. The results page plots our
electricity and hydrogen marginal prices against PLEXOS's own reference
series and reports per-zone validation metrics. An optional toggle enables
the **Hydrogen Producer** (see below), with an editable per-country capacity
table. The app calls the same model code as the CLI.

## Command line

CLI flags:

| Flag | Meaning |
|------|---------|
| `--zones DE00,FR00,…` | subset of zones (default: all zones in the database) |
| `--start-day S --end-day E` | horizon covering days `S..E` inclusive (`(E-S+1)·24` hours); omit both for day 1 |
| `--uc` | enable unit commitment (small MILP; currently min up/down time for 2 DE00 fleets only) |
| `--h2-producer` | enable the per-country Hydrogen Producer (wind+PV+battery+electrolyser+H2 tank+flexible downstream demand) |
| `--out-tag NAME` | write results to `outputs/NAME/` instead of `outputs/` (keep runs side by side) |

Results are written to `outputs/` (or `outputs/NAME/` with `--out-tag`), and a
balance-validation check prints at the end. Each run wipes the existing
`*.csv` in its output folder first, so the folder always reflects exactly the
last run. Multi-day runs build a larger LP (constraints scale with the number
of hours); every storage device must finish the horizon no lower than it
started it.

**Zones default to every zone in the database.** Selecting a subset with
`--zones` automatically reclassifies each border: a line between two selected
zones stays an internal (optimised) link, while a line to a non-selected zone
becomes a fixed cross-border exchange.

## Hydrogen Producer (optional, `--h2-producer`)

A self-contained, per-country "compact unit" — its own wind, PV, battery, and
electrolyser on the power side; its own H2 tank and a flexible downstream H2
demand on the hydrogen side. It keeps its own internal power/H2 balances and
touches the rest of the model only through a net electricity and net
hydrogen exchange with its host zone. Off by default. **None of its sizing
comes from PLEXOS/ENTSO-E data** — capacities are derived per country off a
`config.py` assumption and applied uniformly; treat it as a scenario/what-if
tool, not a calibrated part of the dataset.

It also enforces the RED III industrial renewable-hydrogen quota, met either
by physically matching electrolyser load to onsite wind/PV or by trading
Green Certificates. Full derivation and math spec in `Formulation.md` §18.

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

**Marginal Price (EUR/MWh)** is the zonal price — the dual of the nodal
balance, read straight off the LP solve. Any hour where that dual lands at
the shedding penalty while the zone itself sheds ~0 is treated as
undefined and left blank rather than shown as a misleadingly precise figure.
