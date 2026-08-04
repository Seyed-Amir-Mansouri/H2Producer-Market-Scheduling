"""Views for the CORE electricity + hydrogen dispatch web UI.

Lets the user pick zones (grouped by country -- picking a country's zone
auto-expands to its siblings via RunConfig itself, see economic_dispatch.
config._expand_to_countries), a 2030 calendar date range, run the dispatch
model, and see the resulting marginal prices plotted against PLEXOS's own
reference prices with the same validation metrics used throughout this
project's development.
"""
from __future__ import annotations

import json
from datetime import date

import numpy as np
import pandas as pd
import plotly.graph_objects as go
from django.shortcuts import render

from economic_dispatch.config import (
    RunConfig, discover_zones, DEFAULT_MARGINAL_PRICE_ELEC_DB, DEFAULT_MARGINAL_PRICE_H2_DB,
)
from economic_dispatch import pipeline, report, model

SCARCITY_THRESHOLD = 2000.0

_COUNTRY_NAMES = {
    "AT": "Austria", "BE": "Belgium", "CZ": "Czechia", "DE": "Germany",
    "FR": "France", "HR": "Croatia", "HU": "Hungary", "LU": "Luxembourg",
    "NL": "Netherlands", "PL": "Poland", "RO": "Romania", "SI": "Slovenia",
    "SK": "Slovakia",
}

H2_PRODUCER_ASSET_KEYS = [
    {"key": "electrolyser", "label": "Electrolyser (MW)", "config_field": "h2_producer_electrolyser_mw_overrides"},
    {"key": "wind", "label": "Wind (MW)", "config_field": "h2_producer_wind_mw_overrides"},
    {"key": "pv", "label": "PV (MW)", "config_field": "h2_producer_pv_mw_overrides"},
    {"key": "battery", "label": "Battery (MW)", "config_field": "h2_producer_battery_mw_overrides"},
    {"key": "tank", "label": "H2 tank (MW)", "config_field": "h2_producer_tank_mw_overrides"},
]


def _h2_producer_country_defaults() -> dict[str, dict]:
    """Every country that can get a Hydrogen Producer, at its model-derived
    default sizing (Formulation.md S18.2) -- independent of the current zone
    selection or ``enable_h2_producer`` (``model._h2_producer_sizing`` reads
    the full zone database regardless), so this can be computed once and
    reused for every page render. Sorted by country name -- the order the
    web UI's table fills in as zones get checked. Keyed by country code;
    each value has ``name``, ``zone`` (attachment zone), and one float per
    asset key above."""
    cfg = RunConfig()
    sizing = model._h2_producer_sizing(cfg)
    main_zones = model._h2_main_zones(cfg)
    countries = sorted(sizing, key=lambda c: _COUNTRY_NAMES.get(c, c))
    return {
        c: {
            "name": _COUNTRY_NAMES.get(c, c),
            "zone": main_zones.get(c, ""),
            "electrolyser": sizing[c]["electrolyser_mw"],
            "wind": sizing[c]["wind_mw"],
            "pv": sizing[c]["pv_mw"],
            "battery": sizing[c]["battery_mw"],
            "tank": sizing[c]["tank_mw"],
        }
        for c in countries
    }


def _parse_h2_producer_table(post, countries: list[str]) -> tuple[dict[str, dict], dict[str, dict], list[str]]:
    """POSTed per-country Hydrogen Producer cells (``h2p_{asset}_{country}``)
    -> ``(overrides, submitted, errors)``:

    * ``overrides`` -- ``{config_field: {country: mw}}``, only the cells that
      were both filled in and valid, ready to splat as ``RunConfig`` kwargs.
    * ``submitted`` -- ``{country: {asset_key: raw_string}}`` for every
      NON-BLANK cell regardless of validity, so an error redisplay can put
      exactly what the user typed back in the box instead of silently
      reverting to the default.
    * ``errors`` -- one message per cell that was filled in but didn't parse.

    A blank cell is not an error -- it just means "use this country's own
    derived value for this asset"."""
    overrides: dict[str, dict] = {f["config_field"]: {} for f in H2_PRODUCER_ASSET_KEYS}
    submitted: dict[str, dict] = {}
    errors: list[str] = []
    defaults = _h2_producer_country_defaults()
    for c in countries:
        country_name = defaults.get(c, {}).get("name", c)
        for f in H2_PRODUCER_ASSET_KEYS:
            raw = post.get(f"h2p_{f['key']}_{c}", "").strip()
            if not raw:
                continue
            submitted.setdefault(c, {})[f["key"]] = raw
            try:
                overrides[f["config_field"]][c] = float(raw)
            except ValueError:
                errors.append(f"Hydrogen Producer — {country_name} {f['label']}: enter a number, "
                              f"or leave it blank to use the model's own sizing (got {raw!r}).")
    return overrides, submitted, errors


def _country_groups() -> list[dict]:
    """[{"name": "Austria (AT)", "code": "AT", "zones": ["AT00"]},
    {"name": "Germany (DE)", "code": "DE", "zones": ["DE00", "DEKF"]}, ...],
    sorted by country name -- one checkbox group per CORE country."""
    groups: dict[str, list[str]] = {}
    for z in discover_zones():
        groups.setdefault(z[:2], []).append(z)
    return [
        {"name": f"{_COUNTRY_NAMES.get(c, c)} ({c})", "code": c,
         "map_path": f"dispatcher/country-maps/{c.lower()}.svg", "zones": sorted(zs)}
        for c, zs in sorted(groups.items(), key=lambda kv: _COUNTRY_NAMES.get(kv[0], kv[0]))
    ]


def _day_of_year_2030(d: date) -> int:
    """Calendar date -> 1-based day-of-year, clamped to the dataset's
    364-day modelled year (52 weeks) -- Dec 31 of a real 365-day year maps
    to day 364, the last modelled day."""
    return min(d.timetuple().tm_yday, 364)


def _h2_producer_context(submitted: dict | None = None) -> dict:
    """Context shared by both the fresh form (``index``) and an error
    redisplay (``run_dispatch``): the asset-key spec, and the per-country
    defaults table as JSON for the page's own JS to render/extend as zone
    checkboxes are (un)checked. ``submitted`` -- ``{country: {asset_key:
    raw_string}}`` -- lets an error redisplay put back exactly what the user
    typed instead of silently reverting every cell to its default."""
    return {
        "h2_producer_asset_keys": H2_PRODUCER_ASSET_KEYS,
        "h2_producer_defaults_json": json.dumps(_h2_producer_country_defaults()),
        "h2_producer_submitted_json": json.dumps(submitted or {}),
    }


def index(request):
    return render(request, "dispatcher/index.html", {
        "country_groups": _country_groups(), "year": 2030,
        **_h2_producer_context(),
    })


def run_dispatch(request):
    if request.method != "POST":
        return index(request)

    requested_zones = request.POST.getlist("zones")
    start_date_str = request.POST.get("start_date", "")
    end_date_str = request.POST.get("end_date", "")
    enable_h2_producer = bool(request.POST.get("enable_h2_producer"))

    errors: list[str] = []
    if not requested_zones:
        errors.append("Select at least one zone or country.")

    start_day = end_day = None
    try:
        start_d = date.fromisoformat(start_date_str)
        end_d = date.fromisoformat(end_date_str)
        if start_d.year != 2030 or end_d.year != 2030:
            errors.append("Dates must be in 2030 (the modelled year).")
        elif end_d < start_d:
            errors.append("End date must be on or after the start date.")
        else:
            start_day = _day_of_year_2030(start_d)
            end_day = _day_of_year_2030(end_d)
    except (TypeError, ValueError):
        errors.append("Enter valid start/end dates.")

    h2p_overrides, h2p_submitted, h2p_errors = _parse_h2_producer_table(
        request.POST, list(_h2_producer_country_defaults()))
    if enable_h2_producer:
        errors += h2p_errors

    if errors:
        return render(request, "dispatcher/index.html", {
            "country_groups": _country_groups(), "year": 2030, "errors": errors,
            "selected_zones": requested_zones, "start_date": start_date_str, "end_date": end_date_str,
            "enable_h2_producer": enable_h2_producer,
            **_h2_producer_context(h2p_submitted),
        })

    h2p_kwargs = h2p_overrides if enable_h2_producer else {}
    cfg = RunConfig(zones=sorted(requested_zones), start_day=start_day, end_day=end_day,
                    enable_uc=False, subtract_dsr_implicit=True,
                    enable_h2_producer=enable_h2_producer, **h2p_kwargs)
    build = pipeline.solve_scenario(cfg)

    plexos_price_e = pd.read_parquet(DEFAULT_MARGINAL_PRICE_ELEC_DB)
    plexos_price_h = pd.read_parquet(DEFAULT_MARGINAL_PRICE_H2_DB)
    h0, h1 = cfg.hour_slice()
    hours = np.arange(h0, h1)

    auto_added = sorted(set(cfg.zones) - set(requested_zones))

    zone_results = []
    for z in cfg.zones:
        ours_e = build.price_e.sel(zone=z).to_numpy()
        px_e = plexos_price_e[z].to_numpy()[h0:h1] if z in plexos_price_e.columns else None
        shed_e = build.model.solution["shed_e"].sel(zone=z).to_numpy()

        hz = f"{z[:2]}_H2"
        ours_h = build.price_h.sel(zone=z).to_numpy()
        px_h = plexos_price_h[hz].to_numpy()[h0:h1] if hz in plexos_price_h.columns else None
        shed_h = build.model.solution["shed_h"].sel(zone=z).to_numpy()

        zone_results.append(dict(
            zone=z,
            metrics_e=_metrics(ours_e, px_e, shed_e),
            metrics_h=_metrics(ours_h, px_h, shed_h),
            plot_e=_plot_prices(hours, ours_e, px_e, f"{z} — Electricity price (EUR/MWh)", "EUR/MWh"),
            plot_h=_plot_prices(hours, ours_h, px_h, f"{z} — Hydrogen price (EUR/MWhH2)", "EUR/MWhH2"),
        ))

    h2_producer_assets = None
    h2_producer_summary = None
    if enable_h2_producer:
        prod = build.h2_producer
        h2_producer_assets = [
            {"country": c, **{k: row[k] for k in
                ("zone", "electrolyser_mw", "wind_mw", "pv_mw", "wind_donor_zone", "pv_donor_zone",
                 "battery_mw", "battery_mwh", "tank_mw", "tank_mwh")}}
            for c, row in prod.iterrows()
        ] if not prod.empty else []
        s = report.summary(build)
        h2_producer_summary = {
            "renewable_h2_share": s.get("h2_producer_renewable_h2_share"),
            "gc_purchased_mwh": s.get("h2_producer_gc_purchased_mwh"),
            "gc_sold_mwh": s.get("h2_producer_gc_sold_mwh"),
        }

    return render(request, "dispatcher/results.html", {
        "country_groups": _country_groups(),
        "year": 2030,
        "requested_zones": requested_zones,
        "auto_added": auto_added,
        "zones": cfg.zones,
        "start_date": start_date_str,
        "end_date": end_date_str,
        "start_day": start_day,
        "end_day": end_day,
        "zone_results": zone_results,
        "enable_h2_producer": enable_h2_producer,
        "h2_producer_assets": h2_producer_assets,
        "h2_producer_summary": h2_producer_summary,
    })


def _metrics(ours: np.ndarray, plexos: np.ndarray | None, shed: np.ndarray) -> dict | None:
    """corr_excl/rmse_excl/mean_diff/corr_all (scarcity-hour filter at
    SCARCITY_THRESHOLD) plus real shedding hours -- the same battery of
    validation metrics used throughout this project's PLEXOS comparisons."""
    real_shed_hours = int((shed > 1e-9).sum())
    if plexos is None:
        return dict(corr_excl=None, rmse_excl=None, mean_diff=None, corr_all=None,
                   n_hi_ours=None, n_hi_plexos=None, real_shed_hours=real_shed_hours)
    ns = (ours < SCARCITY_THRESHOLD) & (plexos < SCARCITY_THRESHOLD)
    n = int(ns.sum())
    return dict(
        corr_excl=float(np.corrcoef(ours[ns], plexos[ns])[0, 1]) if n > 1 else None,
        rmse_excl=float(np.sqrt(np.mean((ours[ns] - plexos[ns]) ** 2))) if n > 1 else None,
        mean_diff=float(np.mean(ours[ns] - plexos[ns])) if n > 1 else None,
        corr_all=float(np.corrcoef(ours, plexos)[0, 1]) if len(ours) > 1 else None,
        n_hi_ours=int((ours >= SCARCITY_THRESHOLD).sum()),
        n_hi_plexos=int((plexos >= SCARCITY_THRESHOLD).sum()),
        real_shed_hours=real_shed_hours,
    )


def _plot_prices(hours: np.ndarray, ours: np.ndarray, plexos: np.ndarray | None, title: str,
                 yaxis_title: str = "EUR/MWh") -> str:
    """Interactive two-line Plotly chart: blue = ours, red = PLEXOS. Returns
    an HTML <div> fragment for direct embedding (plotly.js itself is loaded
    once via CDN in the base template, not repeated per chart)."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=hours, y=ours, mode="lines", name="Ours",
                             line=dict(color="#2563eb", width=1.8)))
    if plexos is not None:
        fig.add_trace(go.Scatter(x=hours, y=plexos, mode="lines", name="PLEXOS",
                                 line=dict(color="#dc2626", width=1.5, dash="dot")))
    fig.update_layout(
        title=title, xaxis_title="Hour of year", yaxis_title=yaxis_title,
        template="plotly_white", height=320, autosize=True,
        margin=dict(l=55, r=20, t=45, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False,
                       config={"displaylogo": False, "responsive": True})
