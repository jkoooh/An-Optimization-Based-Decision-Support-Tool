"""Optimization results utilities

This module contains helpers to read solution CSVs, build summary
tables and render route maps. The file is organized with clear
section headers and concise docstrings to make the intent easy
to follow without changing any existing logic.

Do not change logic here when adapting or re-using functions; the
calling scripts rely on the exact behavior of the loaders and
exporters.
"""

import math
import pickle
import webbrowser
from pathlib import Path

import folium
from networkx import display
import pandas as pd

from Comtrade_API_call.comtradeDataProcessing import build_comtrade_df
from Optimization.model1N import load_commodities
from Ports.routing import load_or_build_clusters

# Display options for interactive debugging / REPL
pd.set_option("display.max_columns", None)
pd.set_option("display.width", 120)
pd.set_option("display.max_colwidth", 60)

# -------------------- Helpers: Countries & ISO --------------------


def normalize_country_name(country: str) -> str:
    replacements = {
        "Vietnam": "Viet Nam",
        "Hong Kong SAR": "China, Hong Kong SAR",
        "Republic of Congo": "Dem. Rep. of the Congo",
        "Democratic Republic of the Congo": "Dem. Rep. of the Congo",
        "Solomon Islands": "Solomon Isds",
        "Tanzania": "United Rep. of Tanzania",
        "United States": "USA",
        "Moldova": "Rep. of Moldova",
        "Korea": "Rep. of Korea",
        "The Netherlands": "Netherlands",
        "Dominican Republic": "Dominican Rep.",
    }
    return replacements.get(country, country)


def load_country_iso_lookup(cache_path: str = "Data/reporters_cache.pkl") -> dict[str, str]:
    cache = Path(cache_path)
    if not cache.exists():
        return {}

    try:
        with cache.open("rb") as f:
            data = pickle.load(f)
    except Exception:
        return {}

    lookup = {}
    if isinstance(data, dict):
        for country_name, payload in data.items():
            key = normalize_country_name(str(country_name)).strip().lower()
            if isinstance(payload, dict) and "tag" in payload:
                lookup[key] = str(payload["tag"]).upper()
            elif isinstance(payload, str) and len(payload) == 3:
                lookup[key] = payload.upper()
    return lookup


def country_to_iso3(country: str, country_lookup: dict[str, str]) -> str:
    if not country:
        return "UNK"
    country = normalize_country_name(str(country)).strip()
    if len(country) == 3 and country.isalpha() and country.isupper():
        return country
    iso = country_lookup.get(country.lower())
    if iso:
        return iso
    return country[:3].upper() if len(country) >= 3 else "UNK"


def load_variables(path: str) -> tuple[pd.DataFrame, dict[int, bool], dict[int, float]]:
    """
    Load x (legs), c (washing), and q (cargo quantity) variables from a solution file.
    Returns (df, washing_legs, q_by_leg).
    """
    temp = pd.read_csv(path)
    temp = temp[temp["record_type"] == "var"].copy()

    x_temp = temp[temp["name"].str.startswith("x[")].copy()
    x_parts = (
        x_temp["name"]
        .str.removeprefix("x[")
        .str.removesuffix("]")
        .str.split(",", expand=True)
    )
    if x_parts.shape[1] != 4:
        raise ValueError(f"Unexpected x-format in solution file: {x_parts.shape[1]} index(es)")

    df = x_parts.copy()
    df.columns = ["Leg", "Export_port", "Import_port", "Commodity"]
    df["Leg"] = df["Leg"].astype(int)
    df["Time"] = df["Leg"]
    df = df.sort_values("Leg")

    c_temp = temp[temp["name"].str.startswith("c[")].copy()
    c_temp = c_temp[c_temp["value"] == 1.0].copy()
    washing_legs: dict[int, bool] = {}
    if not c_temp.empty:
        c_parts = (
            c_temp["name"]
            .str.removeprefix("c[")
            .str.removesuffix("]")
            .str.split(",", expand=True)
        )
        # Model semantics: c[..., l] activates sea-cleaning on previous leg (l-1),
        # since wash ballast time is enforced on t_S[l-1].
        c_transition_legs = c_parts[2].astype(int)
        washing_legs = {int(leg - 1): True for leg in c_transition_legs if int(leg) > 0}

    q_temp = temp[temp["name"].str.startswith("q[")].copy()
    q_by_leg: dict[int, float] = {}
    if not q_temp.empty:
        q_parts = (
            q_temp["name"]
            .str.removeprefix("q[")
            .str.removesuffix("]")
            .str.split(",", expand=True)
        )
        if q_parts.shape[1] == 4:
            q_temp["leg"] = q_parts[0].astype(int)
            q_temp["value"] = pd.to_numeric(q_temp["value"], errors="coerce")
            q_by_leg = q_temp.groupby("leg")["value"].sum(min_count=1).dropna().to_dict()

    return df, washing_legs, q_by_leg


# -------------------- Solution File Loaders --------------------


def plot_routes(
    df: pd.DataFrame,
    washing_legs: dict[int, bool],
    q_by_leg: dict[int, float],
    out_html: str = "jebsen_routes.html",
):
    """
    Plot routes on a Folium map with extended tooltip and legend (bottom left).
    """
    comtrade_df = build_comtrade_df()
    clusters = load_or_build_clusters()
    cmds = load_commodities("Data/cmd_groups.xlsx", "cmdList")
    country_lookup = load_country_iso_lookup()

    with open("Data/routes.pkl", "rb") as f:
        routes_file = pickle.load(f)

    cmd_lookup = (
        comtrade_df[["cmdCode", "cmdDesc"]]
        .dropna()
        .drop_duplicates(subset=["cmdCode"])
        .assign(cmdCode=lambda d: d["cmdCode"].astype(str))
        .set_index("cmdCode")["cmdDesc"]
        .to_dict()
    )
    cmd_lookup["7777"] = "Ballasting"

    segment_lookup = (
        cmds[["HS4", "Segment"]]
        .dropna()
        .assign(HS4=lambda d: d["HS4"].astype(str))
        .drop_duplicates(subset=["HS4"])
        .set_index("HS4")["Segment"]
        .to_dict()
    )
    segment_lookup["7777"] = "Ballast"

    port_country = (
        clusters[["Cluster_id", "country"]]
        .dropna()
        .drop_duplicates(subset=["Cluster_id"])
        .set_index("Cluster_id")["country"]
        .to_dict()
    )

    routes = []
    for _, row in df.iterrows():
        leg = int(row["Leg"])
        export_p = row["Export_port"]
        import_p = row["Import_port"]
        hs4 = str(row["Commodity"])
        route = routes_file.get((export_p, import_p))
        if route is None:
            continue

        commodity_desc = cmd_lookup.get(hs4, "Unknown commodity")
        if hs4 == "2710":
            commodity_desc = "Petroleum products"
        elif hs4 == "2709":
            commodity_desc = "Crude oil"
        elif hs4 == "2523":
            commodity_desc = "Cement"
        elif hs4 == "2517":
            commodity_desc = "Pebbles, gravel, crushed stone"
        elif hs4 == "3101":
            commodity_desc = "Fertilizers"
        elif hs4 == "2501":
            commodity_desc = "Salt"
        elif hs4 == "4407":
            commodity_desc = "Wood sawn or chipped lengthwise"
        elif hs4 == "1507":
            commodity_desc = "Soya-bean oil and its fractions"
        elif hs4 == "1511":
            commodity_desc = "Palm oil and its fractions"
        elif hs4 == "2713":
            commodity_desc = "Petroleum coke"
        elif hs4 == "7204":
            commodity_desc = "Ferrous waste and scrap"
        elif hs4 == "1513":
            commodity_desc = "Coconut/palm kernel/babassu oil and their fractions"
        elif hs4 == "1512":
            commodity_desc = "Sunflower seed/safflower/cotton-seed oil and their fractions"
        elif hs4 == "3826":
            commodity_desc = "Biodiesel"
        elif hs4 == "1514":
            commodity_desc = "Rape/colza/mustard oil and their fraction"
        elif hs4 == "4401":
            commodity_desc = "Fuel wood"
        elif hs4 == "4403":
            commodity_desc = "Wood in the rough"

        export_country = port_country.get(export_p, "Unknown")
        import_country = port_country.get(import_p, "Unknown")
        export_iso = country_to_iso3(export_country, country_lookup)
        import_iso = country_to_iso3(import_country, country_lookup)

        segment = segment_lookup.get(hs4, "Unknown")
        if segment == "Tanker":
            color = "red"
            leg_type = "Tanker"
        elif segment == "Bulk":
            color = "blue"
            leg_type = "Bulk"

        elif hs4 == "7777":
            if washing_legs.get(leg, False):
                color = "green"
                leg_type = "Cleaning"
            else:
                color = "black"
                leg_type = "Ballast"
        else:
            color = "green"
            leg_type = str(segment)

        if hs4 == "7777" and washing_legs.get(leg, False):
            commodity_desc = "Cleaning"

        q_val = q_by_leg.get(leg)
        q_text = f"{q_val:.0f} t" if q_val is not None else "-"
        detail_text = f"Leg {leg}: {export_iso}-{import_iso} / {commodity_desc} / {q_text}"

        routes.append(
            {
                "leg": leg,
                "hs4": hs4,
                "coords": route.geometry["coordinates"],
                "color": color,
                "leg_type": leg_type,
                "detail_text": detail_text,
                "dash": "5, 5" if hs4 == "7777" else None,
            }
        )

    routes.sort(key=lambda r: r["leg"])
    m = folium.Map(
        location=[0, 0],
        zoom_start=2,
        tiles="cartodb positron",
        zoom_snap=0.01,
        zoom_delta=0.1,
    )

    def offset_coords(coords, offset_m):
        if not coords or abs(offset_m) < 1e-6:
            return coords
        out = []
        for i, (lon, lat) in enumerate(coords):
            lon1, lat1 = coords[i - 1] if i > 0 else coords[i]
            lon2, lat2 = coords[i + 1] if i < len(coords) - 1 else coords[i]
            lat_avg = (lat1 + lat2) / 2.0
            coslat = max(0.1, math.cos(math.radians(lat_avg)))
            dx = (lon2 - lon1) * coslat
            dy = lat2 - lat1
            norm = math.hypot(dx, dy)
            if norm == 0:
                out.append([lon, lat])
                continue

            nx = -dy / norm
            ny = dx / norm
            dlat = (offset_m * ny) / 111320.0
            dlon = (offset_m * nx) / (111320.0 * coslat)
            out.append([lon + dlon, lat + dlat])
        return out

    def wrap_lon(lon):
        return ((lon + 180.0) % 360.0) - 180.0

    def split_on_dateline(coords):
        """
        Split a lon/lat LineString into segments when crossing +/-180 deg longitude.
        """
        if len(coords) < 2:
            return [coords] if coords else []

        wrapped = [[wrap_lon(lon), lat] for lon, lat in coords]
        segments = []
        current = [wrapped[0]]

        for idx in range(1, len(wrapped)):
            lon1, lat1 = wrapped[idx - 1]
            lon2, lat2 = wrapped[idx]
            delta = lon2 - lon1

            if abs(delta) <= 180.0:
                current.append([lon2, lat2])
                continue

            if delta > 180.0:
                lon2_unwrapped = lon2 - 360.0
                cross_lon = -180.0
                next_lon = 180.0
            else:
                lon2_unwrapped = lon2 + 360.0
                cross_lon = 180.0
                next_lon = -180.0

            denom = lon2_unwrapped - lon1
            if abs(denom) < 1e-12:
                current.append([lon2, lat2])
                continue

            t = (cross_lon - lon1) / denom
            t = min(max(t, 0.0), 1.0)
            lat_cross = lat1 + t * (lat2 - lat1)

            current.append([cross_lon, lat_cross])
            if len(current) >= 2:
                segments.append(current)

            current = [[next_lon, lat_cross], [lon2, lat2]]

        if len(current) >= 2:
            segments.append(current)

        return segments

    total_routes = len(routes)
    step_m = 10000.0
    max_offset_m = 20000.0
    legend_items = []

    for i, route in enumerate(routes):
        offset_m = (i - (total_routes - 1) / 2.0) * step_m if total_routes > 1 else 0.0
        offset_m = max(-max_offset_m, min(max_offset_m, offset_m))
        coords = offset_coords(route["coords"], offset_m)
        segments = split_on_dateline(coords)
        if not segments:
            continue

        geometry = (
            {"type": "LineString", "coordinates": segments[0]}
            if len(segments) == 1
            else {"type": "MultiLineString", "coordinates": segments}
        )

        feature = {
            "type": "Feature",
            "geometry": geometry,
            "properties": {
                "color": route["color"],
                "detail_line": route["detail_text"],
                "is_ballast": route["hs4"] == "7777",
            },
        }

        folium.GeoJson(
            data=feature,
            style_function=lambda f: {
                "color": f["properties"]["color"],
                "weight": 1,
                "opacity": 0.95,
                "dashArray": "5, 5" if f["properties"]["is_ballast"] else None,
            },
            highlight_function=lambda f: {
                "color": f["properties"]["color"],
                "weight": 4,
                "opacity": 0.95,
                "dashArray": "5, 5" if f["properties"]["is_ballast"] else None,
            },
            tooltip=folium.GeoJsonTooltip(
                fields=["detail_line"],
                localize=True,
                labels=False,
                sticky=False,
            ),
        ).add_to(m)

        legend_items.append(
            (
                route["leg"],
                f"<div style='margin-bottom:6px;'>"
                f"<div><svg width='20' height='3' style='margin-right: 5px; vertical-align:middle;'>"
                f"<line x1='0' y1='1.5' x2='20' y2='1.5' stroke='{route['color']}' "
                f"stroke-dasharray='{route['dash'] or ''}'/></svg>"
                f"{route['detail_text']}</div>"
                f"</div>"
            )
        )

    legend_items.sort(key=lambda x: x[0])
    legend_html = f"""
    <div style="
        position: fixed;
        bottom: 30px;
        left: 30px;
        z-index: 9999;
        background: white;
        padding: 10px 12px;
        border: 1px solid #999;
        border-radius: 4px;
        font-size: 12px;
        box-shadow: 0 1px 4px rgba(0,0,0,0.2);
        max-height: 320px;
        overflow-y: auto;
        overflow-x: auto;
        white-space: nowrap;
    ">
      <div style="font-weight: 600; margin-bottom: 6px;">Legs</div>
      {"".join(item[1] for item in legend_items)}
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    m.save(out_html)
    webbrowser.open(out_html)


# -------------------- Visualization / Plotting --------------------


def build_rolling120_case_table(
    base_dir: str = "Optimization/Solutions/Jebsen/rolling120",
    dwt: float = 82000.0,
) -> pd.DataFrame:
    """
    Build one summary row per case folder in rolling120.

    Each row aggregates all `solution_*_iter*.csv` files in the folder.
    """
    rows: list[dict[str, int | str]] = []
    base_path = Path(base_dir)

    def case_sort_key(case_name: str) -> tuple[str, int]:
        parts = case_name.rsplit("_", 1)
        if len(parts) == 2 and parts[1] in {"A", "B", "C"}:
            stem, suffix = parts
        else:
            stem, suffix = case_name, ""
        suffix_order = {"": 0, "C": 1, "B": 2, "A": 3}
        return stem, suffix_order.get(suffix, 9)

    case_dirs = [p for p in base_path.iterdir() if p.is_dir()]
    for case_dir in sorted(case_dirs, key=lambda p: case_sort_key(p.name)):
        if not case_dir.is_dir():
            continue

        iter_files = sorted(case_dir.glob("solution_*_iter*.csv"))
        if not iter_files:
            continue

        case_frames = [pd.read_csv(file) for file in iter_files]
        case_df = pd.concat(case_frames, ignore_index=True)
        var_df = case_df[case_df["record_type"] == "var"].copy()
        var_df["value_num"] = pd.to_numeric(var_df["value"], errors="coerce")

        x_df = var_df[var_df["name"].str.startswith("x[")].copy()
        x_df = x_df[x_df["value_num"] > 0.5]
        x_df["commodity"] = x_df["name"].str.extract(r"^x\[[^,]+,[^,]+,[^,]+,([^\]]+)\]$")
        x_df["route_nm_num"] = pd.to_numeric(x_df["route_nm"], errors="coerce").fillna(0.0)

        distance_laden = x_df.loc[x_df["commodity"] != "7777", "route_nm_num"].sum()
        distance_ballast = x_df.loc[x_df["commodity"] == "7777", "route_nm_num"].sum()

        sailing_time = var_df.loc[var_df["name"].str.startswith("t_S["), "value_num"].sum()
        q_values = var_df.loc[var_df["name"].str.startswith("q["), "value_num"]
        tonnes_avg = q_values.mean() if not q_values.empty else 0.0
        capacity_utilization = (tonnes_avg / dwt * 100.0) if dwt > 0 else 0.0
        objective_values = pd.to_numeric(
            case_df.loc[
                (case_df["record_type"] == "objective") & (case_df["name"] == "objective"),
                "value",
            ],
            errors="coerce",
        )
        objective_sum = objective_values.sum()

        rows.append(
            {
                "Case": case_dir.name,
                "Sailing time": int(round(float(sailing_time), 0)),
                "Distance laden": int(round(float(distance_laden), 0)),
                "Distance Ballast": int(round(float(distance_ballast), 0)),
                "Tonnes avg": int(round(float(tonnes_avg), 0)),
                "Capacity utilization": int(round(float(capacity_utilization), 0)),
                "Objective function": int(round(float(objective_sum), 0)),
            }
        )

    return pd.DataFrame(rows)


# -------------------- Rolling120 Summary Builders --------------------


def export_rolling120_case_table(
    output_csv: str = "Optimization/Solutions/Jebsen/rolling120/rolling120_case_table.csv",
) -> pd.DataFrame:
    """
    Build and store the rolling120 summary table as CSV.
    """
    table = build_rolling120_case_table()
    table.to_csv(output_csv, index=False)
    return table


def export_rolling120_case_table_latex(
    output_tex: str = "Optimization/Solutions/Jebsen/rolling120/rolling120_case_table.tex",
    caption: str = "Jebsen rolling120 summary table",
    label: str = "tab:jebsen_rolling120_summary",
) -> str:
    """
    Export rolling120 summary table to LaTeX longtable in thesis style.
    """
    table = build_rolling120_case_table()

    lines = [
        "{\\footnotesize \\setstretch{1.0}",
        "\\begin{longtable}{C{2.0cm} C{2.0cm} C{2.4cm} C{2.4cm} C{1.8cm} C{2.6cm} C{2.8cm}}",
        f"\\caption{{{caption}}}",
        f"\\label{{{label}}} \\\\",
        "",
        "\\toprule",
        "Case&Sailing time&Distance laden&Distance Ballast&Tonnes avg&Capacity utilization&Objective function\\\\",
        "\\midrule",
        "\\endfirsthead",
        "",
        "\\toprule",
        "Case&Sailing time&Distance laden&Distance Ballast&Tonnes avg&Capacity utilization&Objective function\\\\",
        "\\midrule",
        "\\endhead",
        "",
        "\\midrule",
        "\\multicolumn{7}{r}{\\emph{Continued on next page}} \\\\",
        "\\endfoot",
        "",
        "\\bottomrule",
        "\\endlastfoot",
        "",
    ]

    for row in table.itertuples(index=False):
        lines.append(
            f"{row[0]} & {row[1]} & {row[2]} & {row[3]} & {row[4]} & {row[5]}\\% & {row[6]} \\\\"
        )

    lines.extend(["", "\\end{longtable}", "}"])
    latex_text = "\n".join(lines)

    output_path = Path(output_tex)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(latex_text, encoding="utf-8")
    return latex_text


if __name__ == "__main__":
    """
    Convenient runner for interactive use.

    This runner attempts to call `Optimization.Solutions.run()` if the
    `Solutions` module is available. It does not hard-code solution
    filenames so it is safe to leave in the repository. No logic in the
    module is changed by this runner.

    How to use with files produced by `model_finished.py`:
    - Place the resulting solution CSVs under a folder pattern such as
      `Optimization/Solutions/<CaseName>/solution_*.csv`.
    - If you want to visualize a single solution manually, call:

        df, washing_legs, q_by_leg = load_variables('path/to/solution.csv')
        plot_routes(df, washing_legs, q_by_leg, out_html='jebsen_routes.html')

    The automated runner below will only run if `Optimization.Solutions`
    can be imported and exposes a `run()` function.
    """

    try:
        from Optimization import Solutions  # optional integration point

        if hasattr(Solutions, "run"):
            Solutions.run()
        else:
            print("Imported Optimization.Solutions but no `run()` found.")
    except Exception as exc:  # pragma: no cover - optional runtime
        print("Could not run Optimization.Solutions.run():", exc)
        print(
            "To visualize a single solution produced by `model_finished.py`,"
            " call `load_variables(path)` and `plot_routes(...)` as described above."
        )