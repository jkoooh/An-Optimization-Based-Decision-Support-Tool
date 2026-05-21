# External libraries
import math
import gurobipy as gp
from gurobipy import GRB
import pandas as pd
from pathlib import Path
import pickle
import requests
import re
import time
import random
import numpy as np
import os

# Track full script runtime
start = time.perf_counter()

# Project modules
from Ports.routing import load_or_build_clusters
from Comtrade_API_call.comtradeDataProcessing import build_comtrade_df

# -------------------- Helper Utilities --------------------

# UN Comtrade reference for reporter ISO-alpha3 mapping.
url = "https://comtradeapi.un.org/files/v1/app/reference/Reporters.json"
CACHE_PATH = Path("Data") / "reporters_cache.pkl"

def _load_countries_cache():
    """
    Load countries cache if it exists.
    """
    if CACHE_PATH.exists():
        try:
            with CACHE_PATH.open("rb") as f:
                return pickle.load(f)
        except Exception:
            return None
    return None

def _save_countries_cache(countries_dict):
    """
    Save countries cache to a pickle file.
    """
    try:
        CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        with CACHE_PATH.open("wb") as f:
            pickle.dump(countries_dict, f)
    except Exception:
        pass

# Load or fetch countries data
countries = _load_countries_cache()
if countries is None:
    response = requests.get(url)
    data = response.json()["results"]

    # Build dictionary
    countries = {
        item["text"]: {
            "id": item["id"],
            "country": item["text"],
            "tag": item["reporterCodeIsoAlpha3"],
        }
        for item in data
    }
    _save_countries_cache(countries)

# Build a case-insensitive lookup for faster access
_country_to_reporter = {name.strip().lower(): info["tag"] for name, info in countries.items()}

def get_reporter_code(country: str) -> str:
    """
    Return reporterCodeIsoAlpha3 for a given country name.
    Prints an error and raises ValueError if the country is not found.
    """
    key = country.strip().lower()
    reporter = _country_to_reporter.get(key)
    if reporter is None:
        print(f"Error: Country '{country}' not found. Check spelling in ports data vs comtrade.")
        raise ValueError(f"Country not found: {country}")
    return reporter

# -------------------- Data Loading and Preprocessing --------------------

def load_commodities(path_c: str, sheet: str) -> pd.DataFrame:
    """
    Load HS4 data from Excel and build HS code column.
    """
    df = pd.read_excel(path_c, sheet_name=sheet)
    df["HS4"] = df["HS4"].astype("string")
    return df


def load_clusters(path: str) -> pd.DataFrame:
    """
    Load clusters and normalize country names.
    """
    cache = Path(path)
    if not cache.exists():
        raise FileNotFoundError(f"Missing clusters file: {path}")
    with cache.open("rb") as f:
        df = pickle.load(f)
    df["country"] = df["country"].replace({"Vietnam": "Viet Nam"})
    df["country"] = df["country"].replace({"Hong Kong SAR": "China, Hong Kong SAR"})
    df["country"] = df["country"].replace({"Republic of Congo": "Dem. Rep. of the Congo"})
    df["country"] = df["country"].replace({"Democratic Republic of the Congo": "Dem. Rep. of the Congo"})
    df["country"] = df["country"].replace({"Solomon Islands": "Solomon Isds"})
    df["country"] = df["country"].replace({"Tanzania": "United Rep. of Tanzania"})
    df["country"] = df["country"].replace({"United States": "USA"})
    df["country"] = df["country"].replace({"Moldova": "Rep. of Moldova"})
    df["country"] = df["country"].replace({"Korea": "Rep. of Korea"})
    df["country"] = df["country"].replace({"The Netherlands": "Netherlands"})
    df["country"] = df["country"].replace({"Dominican Republic": "Dominican Rep."})
    return df


def build_wash_data(k_nb):
    """
    Load washing matrix and build wash steps/sets.
    """
    wash_path = Path("Data") / "cleaningMatrix.csv"
    wash_df = pd.read_csv(wash_path, index_col=0)
    wash_df.index = wash_df.index.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(4)
    wash_df.columns = wash_df.columns.astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(4)
    # Ensure all non-ballast HS4 codes are present in the washing matrix
    missing_wash = [k for k in k_nb if k not in wash_df.index]
    if missing_wash:
        raise ValueError(f"cleaningMatrix.csv mangler HS4-koder: {missing_wash}")
    # Build washing steps and infeasible sets
    W_I_DAYS = 1000
    WASH_BALLAST_FACTOR = 0.5
    T_W = {}
    T_WB = {}
    W_I = set()
    for k_prev in k_nb:
        for k_next in k_nb:
            val = wash_df.loc[k_prev, k_next]
            if pd.isna(val):
                W_I.add((k_prev, k_next))
                continue
            val = float(val)
            if val >= W_I_DAYS:
                W_I.add((k_prev, k_next))
            else:
                w = val*2  # Convert days to half-days
                T_W[(k_prev, k_next)] = w
                T_WB[(k_prev, k_next)] = w * (1.0 + WASH_BALLAST_FACTOR)
    return T_W, T_WB, W_I


def load_or_build_D_pq(route_lengths_path: Path, dpq_cache: Path) -> dict:
    """
    Load or build D_pq with caching.
    """
    def _dpq_cache_fresh() -> bool:
        # Cache is valid if it exists and is newer than the CSV
        if not dpq_cache.exists():
            return False
        return dpq_cache.stat().st_mtime >= route_lengths_path.stat().st_mtime

    if _dpq_cache_fresh():
        # Use cached distances if available
        with dpq_cache.open("rb") as f:
            return pickle.load(f)

    # Read route lengths from CSV
    route_lengths_df = pd.read_csv(route_lengths_path)
    route_lengths_df.columns = [c.strip().lower() for c in route_lengths_df.columns]
    # Wide matrix: first column is origin (cluster_id), remaining columns are destination ports
    if "cluster_id" not in route_lengths_df.columns:
        raise ValueError(
            "route_lengths.csv must have 'cluster_id' as first column "
            "and destination ports as remaining columns."
        )
    origin_col = "cluster_id"
    dest_cols = [c for c in route_lengths_df.columns if c != origin_col]
    D_pq = {}
    cols = list(route_lengths_df.columns)
    origin_idx = cols.index(origin_col)
    dest_idxs = [(q, cols.index(q)) for q in dest_cols]
    # Build a dict of distances keyed by (origin, destination)
    for row in route_lengths_df.itertuples(index=False, name=None):
        p = str(row[origin_idx])
        for q, idx in dest_idxs:
            value = row[idx]
            if pd.isna(value):
                continue
            D_pq[(p, q)] = float(value)

    # Save to cache for faster next run
    dpq_cache.parent.mkdir(parents=True, exist_ok=True)
    with dpq_cache.open("wb") as f:
        pickle.dump(D_pq, f, protocol=pickle.HIGHEST_PROTOCOL)
    return D_pq


def load_trade_data(path: str, horizon_scale: float):
    """
    Load trade pairs and bilateral trade caps (tons over model horizon).
    """
    df = pd.read_csv(path)
    required_cols = {"reporterISO", "partnerISO", "cmdCode"}
    if not required_cols.issubset(df.columns):
        # Handle accidental merge marker line at top of CSV.
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            first_line = f.readline().strip()
        if first_line.startswith("<<<<<<<"):
            df = pd.read_csv(path, skiprows=1)
    if not required_cols.issubset(df.columns):
        raise ValueError(
            f"Missing required columns in {path}. Expected at least {sorted(required_cols)}."
        )

    df["cmdCode"] = df["cmdCode"].astype(str).str.zfill(4)
    export_pairs = set(zip(df["reporterISO"], df["cmdCode"]))
    import_pairs = set(zip(df["partnerISO"], df["cmdCode"]))
    trade_pairs = set(
        zip(df["reporterISO"], 
            df["partnerISO"], 
            df["cmdCode"]
        )
    )

    # Build bilateral caps from annual netWgt and scale to optimization horizon.
    trade_caps = {}
    if "netWgt" in df.columns:
        caps = df[["reporterISO", "partnerISO", "cmdCode", "netWgt"]].copy()
        caps["netWgt"] = pd.to_numeric(caps["netWgt"], errors="coerce").fillna(0.0)
        caps = caps[caps["netWgt"] > 0].copy()
        caps = caps.groupby(["reporterISO", "partnerISO", "cmdCode"], as_index=False)["netWgt"].sum()
        caps["cap_tons"] = (caps["netWgt"] / 1000.0) * float(horizon_scale)
        trade_caps = {
            (r, p, k): float(cap_tons)
            for r, p, k, cap_tons in caps[
                ["reporterISO", "partnerISO", "cmdCode", "cap_tons"]
            ].itertuples(index=False, name=None)
            if cap_tons > 0
        }
    return export_pairs, import_pairs, trade_pairs, trade_caps


def load_port_handling_rates(path: str, sheet=0):
    """
    Load port-level loading/discharge rates (tons/day) from Excel.
    Expected columns: port_id, load_tpd, discharge_tpd
    """
    rate_path = Path(path)
    if not rate_path.exists():
        raise FileNotFoundError(f"Missing handling-rate file: {path}")
    df = pd.read_excel(rate_path, sheet_name=sheet)
    required = {"port_id", "load_tpd", "discharge_tpd"}
    if not required.issubset(df.columns):
        missing = sorted(required - set(df.columns))
        raise ValueError(f"Missing columns in {path}: {missing}")

    def normalize_port_rate_id(value) -> str:
        s = str(value).strip()
        if not s:
            return s
        if s.startswith("portid") or s.startswith("port"):
            return s
        return f"port{s}"

    df["port_id"] = df["port_id"].map(normalize_port_rate_id)
    df["load_tpd"] = pd.to_numeric(df["load_tpd"], errors="coerce")
    df["discharge_tpd"] = pd.to_numeric(df["discharge_tpd"], errors="coerce")
    df = df.dropna(subset=["port_id", "load_tpd", "discharge_tpd"])
    load_rate_by_port = dict(zip(df["port_id"], df["load_tpd"].astype(float)))
    discharge_rate_by_port = dict(zip(df["port_id"], df["discharge_tpd"].astype(float)))
    return load_rate_by_port, discharge_rate_by_port

# -------------------- Global Model Parameters --------------------

# PARAM: Maximum number of legs
N = 5

# PARAM: Time horizon (half-days)
T_MAX = 120  # 365 days

# PARAM: Fuel cost per day in ballast
C_FB = 13874.89  # USD/day

# PARAM: Fuel cost per day fully laden
C_FL = 15534.35  # USD/day

# PARAM: Fuel consumption per day in ballast (tons/day)
F_FB = 30.1  # tons/day

# PARAM: Fuel consumption per day fully laden (tons/day)
F_FL = 33.7  # tons/day

# PARAM: CO2 factor (tons CO2 per ton fuel)
CO2_FACTOR = 3.15  # tons CO2 per ton fuel

#PARAM: Daily cleaning cost per commodity swap (USD/day)
C_CL = 1000 # USD/day

# Port fee curve as function of port time (half-days).
def C_P(t_p_half_days):
    """
    Port fee as a function of time in port (half-days).
    Regression analysis based on https://www.mpa.gov.sg/finance-e-services/tariff-fees-and-charges/ocean-going-vessels.
    Approximate GT of kamsarmax: 43545
    """
    return (2.303 * t_p_half_days / 2 + 2.83) * 43545/100

# PARAM: Vessel speed in knots
U = 12  # knots

# PARAM: Deadweight tonnage
DWT = 82000

# PARAM: Minimum non-ballast lift per leg (tons)
Q_MIN = 10000.0

# PARAM: Country handling rate table (Excel)
PORT_RATE_PATH = "Data/port_handling_rates.xlsx"
# 0 means first sheet in the workbook (pandas sheet index)
PORT_RATE_SHEET = 0

# PARAM: Max runtime per iteration (seconds)
T_LIM = 10000000

# PARAM: Ballast "HS4" code
K_B = "7777"

# PARAM: OPEX per day (USD/day)
OPEX_tank = 7151.25
OPEX_bulk = 5663.75
OPEX_combi = OPEX_tank

# Distance between ports p and q
route_lengths_path = Path("Data") / "route_lengths.csv"
cache_dir = Path("Data") / "cache"
dpq_cache = cache_dir / "route_lengths.pkl"
# PARAM: Load or compute D_pq
D_pq = load_or_build_D_pq(route_lengths_path, dpq_cache)

# PARAM: Time to t_S between ports p and q (half days), precomputed for fast lookup
tau_pq = {(p, q): ((dist / U) / 24) * 2 for (p, q), dist in D_pq.items()}


# Read relevant HS4 codes
path_c = "Data/cmd_groups.xlsx"
sheet = "cmdList"
commodities = load_commodities(path_c, sheet)

# -------------------- Vessel Type (Tank/Bulk) --------------------

# Tanker commodity approximation (HS4, post-cleaning/post-merge codes).
# Bulk is defined as all non-ballast HS4 not in the tanker profile.
TANK_HS4_CODES = {
    "1507", "1511", "1512", "1513", "1514",
    "2710",
    "2815",
    "3826",
}
BOTH = {

}

# PARAM: CII rating thresholds (gCO2/ton-NM) for each rating level (A to E)
CII_BOUNDARIES_BULK = {"A": 3.59, "B": 3.92, "C": 4.42, "D": 4.92, "E": 1000}
CII_BOUNDARIES_TANK = {"A": 4.33, "B": 4.91, "C": 5.70, "D": 6.76, "E": 1000}
CII_BOUNDARIES_COMBI = {"A": 3.92, "B": 4.32, "C": 4.77, "D": 5.13, "E": 1000}

# PARAM: Earnings per commodity per NM (map HS code -> earnings)
CMD_RATE_PATH = "Data/commodity_rates_random_2024.csv"
R_k_matrix = pd.read_csv(CMD_RATE_PATH)
R_k_matrix["Day"] = pd.to_numeric(R_k_matrix["Day"], errors="coerce").astype("Int64")
R_k_matrix = R_k_matrix.set_index("Day", drop=True)

def build_leg_rate_matrix_for_window(
    rates_by_day_df: pd.DataFrame,
    day_start: int,
    day_end: int,
    n_legs: int,
) -> pd.DataFrame:
    """
    Build leg-level rate matrix for a day window.
    - Input index must be Day (1..365), columns are HS4.
    - Output index is leg 0..n_legs-1, columns are HS4.
    """
    if day_start > day_end:
        raise ValueError(f"Invalid day window: {day_start}>{day_end}")

    window_df = rates_by_day_df.loc[day_start:day_end].copy()
    if window_df.empty:
        raise ValueError(f"No rate rows in window [{day_start}, {day_end}]")

    hs4_cols = [c for c in window_df.columns if str(c).isdigit()]
    if not hs4_cols:
        raise ValueError("Rate matrix has no HS4 columns.")

    index_groups = np.array_split(window_df.index.to_numpy(), n_legs)
    rows = []
    for idx in index_groups:
        if len(idx) == 0:
            rows.append(window_df.iloc[-1][hs4_cols])
        else:
            rows.append(window_df.loc[idx, hs4_cols].mean())

    result = pd.DataFrame(rows, columns=hs4_cols)
    result.index = range(n_legs)
    return result
        

# Load or build port clusters
cache_path: str = "Data/clusters.pkl"
clusters = load_clusters(cache_path)

# Load port-level handling rates (tons/day) from Excel.
load_rate_by_port, discharge_rate_by_port = load_port_handling_rates(
    PORT_RATE_PATH,
    PORT_RATE_SHEET,
)

# -------------------- Base Sets --------------------

# -------------------- Commodities K --------------------

# Set of commodities
K = commodities["HS4"].dropna().tolist()
# Add ballast code to commodity list
K.append(K_B)

# Set of non-ballast commodities (exclude ballast code)
K_nb = [k for k in K if k != K_B]

# -------------------- Washing --------------------

# Build washing steps and infeasible sets
T_W, T_WB, W_I = build_wash_data(K_nb)

# -------------------- Ports P --------------------

# Set of ports: one port per country from country_port_centroids
P = [
    "port691",   # Angola
    "port184",   # Argentina
    "port280",   # Australia
    "port1203",  # Bahrain
    "port890",   # Bangladesh
    "port57",    # Belgium
    "port506",   # Brazil
    "port774",   # Brunei Darussalam
    "port193",   # Bulgaria
    "port535",   # Cambodia
    "port2100",  # Cameroon
    "port1350",  # Canada
    "port1219",  # Canada
    "port1053",  # Chile
    "port2029",  # China
    "port183",   # Colombia
    "port919",   # Republic of Congo
    "port1052",  # Costa Rica
    "port1218",  # Croatia
    "port223",   # Cuba
    "port633",   # Cyprus
    "port4",     # Côte d'Ivoire
    "port717",   # Democratic Republic of the Congo
    "port1204",  # Denmark
    "port1158",  # Dominican Republic
    "port2032",  # Ecuador
    "port321",   # Egypt
    "port6",     # El Salvador
    "port781",   # Estonia
    "port1015",  # Finland
    "port990",   # France
    "port985",   # France
    "port2138",  # Gabon
    "port2139",  # Georgia
    "port1401",  # Germany
    "port1255",  # Ghana
    "port75",    # Greece
    "port1159",  # Guatemala
    "port390",   # Guyana
    "port474",   # Hong Kong SAR
    "port1036",  # Honduras
    "port329",   # Iceland
    "port907",   # India
    "port309",   # Indonesia
    "port108",   # Iran
    "port1341",  # Iraq
    "port353",   # Ireland
    "port2016",  # Israel
    "port1318",  # Italy
    "port572",   # Jamaica
    "port785",   # Japan
    "port19",    # Jordan
    "port757",   # Kenya
    "port1065",  # Korea
    "port25",    # Kuwait
    "port1100",  # Latvia
    "port2187",  # Lebanon
    "port110",   # Libya
    "port579",   # Lithuania
    "port55",    # Madagascar
    "port603",   # Malaysia
    "port1349",  # Malta
    "port831",   # Mauritania
    "port723",   # Mexico
    "port1358",  # Mexico
    "port2005",  # Moldova
    "port1265",  # Morocco
    "port137",   # Mozambique
    "port1086",  # Myanmar
    "port1381",  # Namibia
    "port1299",  # New Zealand
    "port155",   # Nigeria
    "port864",   # Norway
    "port988",   # Oman
    "port543",   # Pakistan
    "port1026",  # Peru
    "port125",   # Philippines
    "port381",   # Poland
    "port653",   # Portugal
    "port1090",  # Qatar
    "port260",   # Romania
    "port833",   # Russian Federation
    "port1369",  # Russian Federation
    "port1150",  # Russian Federation
    "port1091",  # Saudi Arabia
    "port272",   # Senegal
    "port1201",  # Singapore
    "port592",   # Slovenia
    "port475",   # Solomon Islands
    "port311",   # South Africa
    "port31",    # Spain
    "port678",   # Sri Lanka
    "port2225",  # Sudan
    "port1228",  # Sweden
    "port278",   # Tanzania
    "port111",   # Thailand
    "port1114",  # The Netherlands
    "port917",   # Trinidad and Tobago
    "port2047",  # Tunisia
    "port504",   # Türkiye
    "port843",   # Ukraine
    "port362",   # United Arab Emirates
    "port659",   # United Kingdom
    "port664",   # United States
    "port815",   # United States
    "port764",   # Uruguay
    "port473",   # Vietnam
]

P_test = [
    "port184",   # Argentina
    "port280",   # Australia
    "port2029",  # China
    "port474",   # Hong Kong SAR
    "port907",   # India
    "port309",   # Indonesia
    "port785",   # Japan
    "port125",   # Philippines
    "port1201",  # Singapore
    "port111",   # Thailand
    "port473",   # Vietnam
]


# Keep full clusters; per-run filtering happens inside optimize_model.

# Leg index set
L = list(range(N))

# Build export/import/trade sets and annual bilateral trade caps.
# Load annual trade caps; each rolling window scales these locally.
HORIZON_SCALE = 1.0
export_pairs, import_pairs, trade_pairs, Q_trade_annual = load_trade_data(
    "Data/cleanedHS4database2024.csv",
    HORIZON_SCALE,
)
# Valid arcs/triplets are built per run inside optimize_model.

# -------------------- Model Helpers --------------------

def filename_part(value: str) -> str:
    """
    Sanitize a string for safe use in filenames.
    """
    return re.sub(r"[^A-Za-z0-9_-]+", "_", str(value)).strip("_")


def check_port_prefix(cluster_ids) -> str:
    """
    Infer whether port IDs are "portidXXXX" or "portXXXX" based on clusters.
    """
    try:
        if cluster_ids.astype(str).str.startswith("portid").any():
            return "portid"
    except Exception:
        pass
    return "port"


def format_port_id(port_id, prefix: str) -> str:
    """
    Format a port id with the expected prefix.
    """
    s = str(port_id)
    if s.startswith("portid") or s.startswith("port"):
        return s
    return f"{prefix}{s}"


def save_optimization_results(
    model,
    output_root,
    runtime_seconds=None,
    P_1=None,
    P_1_country=None,
    n_legs=None,
    iter_id=None,
):
    """
    Save active variables, runtime and objective to a single CSV file.
    """
    out_dir = Path(output_root)
    out_dir.mkdir(parents=True, exist_ok=True)
    # Prepare rows for DataFrame
    has_solution = model.SolCount > 0
    sense = "MAX" if model.ModelSense == GRB.MAXIMIZE else "MIN"
    rows = [
        {
            "record_type": "objective",
            "name": "objective",
            "value": model.objVal if has_solution else None,
            "sense": sense,
            "vtype": None,
            "obj_coef": None,
            "route_nm": None,
        }
    ]
    # Add runtime if provided
    if runtime_seconds is not None:
        rows.append(
            {
                "record_type": "runtime_seconds",
                "name": "optimize_model",
                "value": float(runtime_seconds),
                "sense": None,
                "vtype": None,
                "obj_coef": None,
                "route_nm": None,
            }
        )
    # Add variable values if a solution exists
    if has_solution:
        tol = 1e-6
        for v in model.getVars():
            if abs(v.X) > tol:
                route_nm = None
                if v.VarName.startswith("x[") and v.VarName.endswith("]"):
                    parts = [p.strip() for p in v.VarName[2:-1].split(",")]
                    if len(parts) >= 4:
                        p_from = parts[1]
                        p_to = parts[2]
                        route_nm = D_pq.get((p_from, p_to))
                rows.append(
                    {
                        "record_type": "var",
                        "name": v.VarName,
                        "value": v.X,
                        "sense": None,
                        "vtype": v.VType,
                        "obj_coef": v.Obj,
                        "route_nm": route_nm,
                    }
                )
    # Construct filename
    filename_parts = [
        "solution",
        filename_part(P_1) if P_1 is not None else None,
        filename_part(P_1_country) if P_1_country is not None else None,
        filename_part(n_legs) if n_legs is not None else None,
        f"iter{int(iter_id):03d}" if iter_id is not None else None,
    ]
    filename_parts = [p for p in filename_parts if p]
    base_name = "_".join(filename_parts) or "solution"

    # Ensure unique filename
    out_file = out_dir / f"{base_name}.csv"
    if out_file.exists():
        i = 1
        while True:
            candidate = out_dir / f"{base_name}_{i}.csv"
            if not candidate.exists():
                out_file = candidate
                break
            i += 1
    # Save to CSV
    pd.DataFrame(rows).to_csv(out_file, index=False)

    return out_file


def _method_label(method_value):
    """
    Return readable label for Gurobi Method parameter.
    """
    mapping = {
        -1: "auto (Gurobi velger selv)",
        0: "primal simplex",
        1: "dual simplex",
        2: "barrier",
        3: "concurrent (parallel kjøring av flere metoder)",
        4: "deterministic concurrent (parallell, deterministisk)",
    }
    return mapping.get(method_value, str(method_value))


def _safe_optimize(model):
    """
    Optimize model and keep incumbent if Gurobi stops with OOM (error 10001).
    Returns True if OOM was raised, otherwise False.
    """
    try:
        model.optimize()
        return False
    except gp.GurobiError as e:
        if e.errno == 10001:
            print("Gurobi OOM (10001): fortsetter med incumbent hvis tilgjengelig.")
            return True
        raise

def combine_solution_files(solution_files, out_path):
    """
    Combine multiple solution CSVs into one year-level file.
    """
    parts = []
    for path in solution_files:
        if path is None:
            continue
        p = Path(path)
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["source_file"] = p.name
        parts.append(df)
    if not parts:
        return None
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pd.concat(parts, ignore_index=True).to_csv(out_path, index=False)
    return out_path


def optimize_model(
    segment,
    CII_rating,
    P_override,
    iter_id,
    output_root,
    day_start=1,
    day_end=365,
    start_port=None,
):
    try:
        model_start = time.perf_counter()
        # Stage 1: Resolve run-specific ports, time window, and country mappings.
        P_run = list(P_override) if P_override is not None else list(P)
        if not P_run:
            raise ValueError("P list is empty.")
        if Q_MIN > DWT:
            raise ValueError("Q_MIN can not be greater than DWT.")
        day_start = int(day_start)
        day_end = int(day_end)
        if day_start < 1 or day_end > 365 or day_start > day_end:
            raise ValueError(f"Invalid rolling window [{day_start}, {day_end}]")
        t_max_run = int(2 * (day_end - day_start + 1))
        window_scale = (day_end - day_start + 1) / 365.0
        Q_trade = {k: v * window_scale for k, v in Q_trade_annual.items()}

        port_prefix = check_port_prefix(clusters["Cluster_id"])
        P_run = [format_port_id(pid, port_prefix) for pid in P_run]

        # Keep only selected ports for this run.
        clusters_run = clusters[clusters["Cluster_id"].isin(P_run)].copy()
        missing_ports = [p for p in P_run if p not in set(clusters_run["Cluster_id"])]
        if missing_ports:
            raise ValueError(f"Missing ports in clusters: {missing_ports}")
        port_country = clusters_run.set_index("Cluster_id")["country"].to_dict()
        port_iso = {p: get_reporter_code(port_country[p]) for p in P_run}

        # Build run-specific loading and discharge rates.
        load_rate_port = {}
        discharge_rate_port = {}
        missing_load_ports = set()
        missing_discharge_ports = set()
        for p in P_run:
            if p not in load_rate_by_port:
                missing_load_ports.add(p)
                continue
            if p not in discharge_rate_by_port:
                missing_discharge_ports.add(p)
                continue
            load_rate = float(load_rate_by_port[p])
            discharge_rate = float(discharge_rate_by_port[p])
            if load_rate <= 0 or discharge_rate <= 0:
                raise ValueError(f"Handling rates must be > 0 for port {p}.")
            load_rate_port[p] = load_rate
            discharge_rate_port[p] = discharge_rate
        if missing_load_ports or missing_discharge_ports:
            raise ValueError(
                "Missing handling rates for ports. "
                f"load_missing={sorted(missing_load_ports)}, "
                f"discharge_missing={sorted(missing_discharge_ports)}"
            )

        # Group available ports by country.
        country_ports = {}
        for p, c in port_country.items():
            country_ports.setdefault(c, []).append(p)

        # Stage 2: Build candidate arcs.
        # For each country pair, keep the shortest feasible port-to-port arc.
        valid_arcs = []
        countries = list(country_ports.keys())
        for c_from in countries:
            for c_to in countries:
                if c_from == c_to:
                    continue
                best = None
                for p in country_ports[c_from]:
                    for q in country_ports[c_to]:
                        if p == q:
                            continue
                        if (p, q) not in D_pq:
                            continue
                        if tau_pq.get((p, q), 0) <= 0:
                            continue
                        dist = D_pq[(p, q)]
                        if best is None or dist < best[0]:
                            best = (dist, p, q)
                if best is not None:
                    _, p_best, q_best = best
                    valid_arcs.append((p_best, q_best))
        
        # Always include ballast arcs for feasible port pairs.
        ballast_triplets = [
            (p, q, K_B)
            for p in P_run
            for q in P_run
            if p != q and (p, q) in D_pq and tau_pq[(p, q)] > 0
        ]

        segment_key = str(segment).strip().lower()
        if segment_key == "tank":
            allowed_non_ballast = {
                k for k in K if k != K_B and (k in TANK_HS4_CODES or k in BOTH)
            }
            CII_boundary = CII_BOUNDARIES_TANK.get(CII_rating)
            OPEX = OPEX_tank
        elif segment_key == "bulk":
            allowed_non_ballast = {
                k for k in K if k != K_B and k not in TANK_HS4_CODES
            }
            CII_boundary = CII_BOUNDARIES_BULK.get(CII_rating)
            OPEX = OPEX_bulk
        elif segment_key == "combi":
            allowed_non_ballast = {k for k in K if k != K_B}
            CII_boundary = CII_BOUNDARIES_COMBI.get(CII_rating)
            OPEX = OPEX_combi
        else:
            raise ValueError(
                f"Unknown segment '{segment}'. Expected one of: Tank, Bulk, Combi."
            )

        if CII_boundary is None:
            raise ValueError(
                f"Unknown CII_rating '{CII_rating}' for segment '{segment}'."
            )

        # Keep non-ballast arcs only if trade data allows the country pair and commodity.
        non_ballast_triplets = []
        for p, q in valid_arcs:
            p_iso = port_iso[p]
            q_iso = port_iso[q]
            for k in allowed_non_ballast:
                if (p_iso, q_iso, k) in trade_pairs:
                    non_ballast_triplets.append((p, q, k))

        # Combine ballast and non-ballast arcs.
        print(f"Total ballast legs: {len(ballast_triplets)}, non-ballast legs: {len(non_ballast_triplets)}")
        A = ballast_triplets + non_ballast_triplets
        # SET: Sets of valid in-arcs and out-arcs to all ports
        A_in = {r: [(p, k) for (p, r2, k) in A if r2 == r] for r in P_run}
        A_out = {r: [(q, k) for (r2, q, k) in A if r2 == r] for r in P_run}

        # Pre-group non-ballast arcs by bilateral trade line.
        arcs_by_trade_line = {}
        for p, q, k in non_ballast_triplets:
            key = (port_iso[p], port_iso[q], k)
            arcs_by_trade_line.setdefault(key, []).append((p, q))
        missing_trade_caps = [key for key in arcs_by_trade_line if key not in Q_trade]
        if missing_trade_caps:
            raise ValueError(
                "Missing bilateral trade caps for run keys. "
                f"missing_trade_caps={missing_trade_caps[:10]}"
            )

        # Use only commodities that appear in the run-specific arc set.
        unique_k = {k for (_, _, k) in A}
        K_nb = unique_k - {K_B}
        # Build a new optimization model instance.
        model = gp.Model("model2N")

        # -------------------- Decision Variables --------------------
        # Cargo tons (q) is continuous and linked to binary arc usage (x).

        # 1 if leg l uses arc (p,q) with commodity k.
        x_keys = [(l, p, q, k) for l in L for (p, q, k) in A]
        # (E.32) Arc-selection variable domain.
        x = model.addVars(x_keys, vtype=GRB.BINARY, name="x")
        # 1 if leg l is used.
        # (E.33) Leg-activation variable domain.
        y = model.addVars(L, vtype=GRB.BINARY, name="y")
        # Tons loaded on leg l for non-ballast commodity arcs.
        q_keys = [(l, p, q_port, k) for l in L for (p, q_port, k) in non_ballast_triplets]
        # (E.39) Load variable domain.
        q = model.addVars(q_keys, vtype=GRB.CONTINUOUS, lb=0.0, ub=DWT, name="q")

        # Time when leg l starts.
        # (E.34) Time variable domain.
        t = model.addVars(L, vtype=GRB.CONTINUOUS, lb=0.0, name="t")
        # Sailing time per leg (half-days).
        # (E.34) Time variable domain.
        t_S = model.addVars(L, vtype=GRB.CONTINUOUS, lb=0.0, name="t_S")
        # Extra variable for ballast cleaning done at sea.
        # (E.36) Ballast-cleaning time-difference domain.
        t_bx = model.addVars(L, vtype=GRB.CONTINUOUS, lb=0.0, name="t_bx")
        # Total time in port per leg (half-days).
        # (E.34) Time variable domain.
        t_p = model.addVars(L, vtype=GRB.CONTINUOUS, lb=0.0, name="t_p")
        # Explicit additive decomposition of time in port.
        # (E.40) Loading-time domain.
        t_load = model.addVars(L, vtype=GRB.CONTINUOUS, lb=0.0, name="t_load")
        # (E.41) Discharge-time domain.
        t_disch = model.addVars(L, vtype=GRB.CONTINUOUS, lb=0.0, name="t_disch")
        # (E.35) In-port cleaning-time domain.
        t_wash_port = model.addVars(L, vtype=GRB.CONTINUOUS, lb=0.0, name="t_wash_port")

        # Last non-ballast cargo memory per leg.
        # (E.37) Commodity-memory variable domain.
        k_l = model.addVars(K_nb, L, vtype=GRB.BINARY, name="k_l")

        # Washing mode: 1 means wash is done while ballast sailing.
        c_keys = [
            (k_prev, k_next, l)
            for l in L[1:]
            for k_prev in K_nb
            for k_next in K_nb
        ]
        # (E.38) Cleaning-mode variable domain.
        c = model.addVars(c_keys, vtype=GRB.BINARY, name="c")
 
        # -------------------- Objective Function --------------------
        # Maximize route profit: revenue minus fuel, port, cleaning, and OPEX costs.
        R_k = build_leg_rate_matrix_for_window(R_k_matrix, day_start, day_end, N)
        revenue = gp.quicksum(
            q[l, p, q_port, k]/DWT * tau_pq[p, q_port]/2 * R_k.at[l, k]
            for (l, p, q_port, k) in q_keys
        )

        fuel_base = gp.quicksum(
            tau_pq[p, q_port]/2 * C_FB * x[l, p, q_port, k]
            for (l, p, q_port, k) in x_keys
        )
        fuel_load_premium = gp.quicksum(
            tau_pq[p, q_port]/2 * (C_FL - C_FB) * (q[l, p, q_port, k] / DWT)
            for (l, p, q_port, k) in q_keys
        )
        port_cost = gp.quicksum(
            C_P(t_p[l]) 
            for l in L if l > 0
            )
        cleaning_cost = gp.quicksum(
            C_CL/2 * (t_wash_port[l] + t_bx[l])
            for l in L if l > 0
        )
        opex = gp.quicksum(OPEX * (t_S[l]/2 + t_p[l]/2) for l in L)

        # (E.1) Route-profit objective.
        model.setObjective(
            revenue -opex-fuel_base - fuel_load_premium - port_cost - cleaning_cost,
            GRB.MAXIMIZE,
        )

        # -------------------- Constraints --------------------

        fuel_consumption_ballast = gp.quicksum(
            tau_pq[p, q_port]/2 * F_FB * x[l, p, q_port, k]
            for (l, p, q_port, k) in x_keys
        )
        fuel_consumption_premium = gp.quicksum(
            tau_pq[p, q_port]/2 * (F_FL - F_FB) * (q[l, p, q_port, k] / DWT)
            for (l, p, q_port, k) in q_keys
        )    
        distance_travelled = gp.quicksum(
            D_pq[p, q_port] * x[l, p, q_port, k]
            for (l, p, q_port, k) in x_keys
        )

        # (E.31) CII emissions-intensity constraint.
        model.addConstr(
            (fuel_consumption_ballast + fuel_consumption_premium)*CO2_FACTOR <= CII_boundary*distance_travelled*DWT/10**6
        )

        # Route structure: one arc per used leg, no holes, and max used legs.
        for l in L:
            # (E.2) One arc is selected for each used leg.
            model.addConstr(
                gp.quicksum(x[l, p, q, k] for (p, q, k) in A) == y[l],
                name=f"one_leg[{l}]",
            )
        # (E.3) Maximum number of used legs.
        model.addConstr(
            gp.quicksum(y[l] for l in L) <= N,
            name="max_used_legs",
        )
        for l in L[:-1]:
            # (E.4) No gaps in used legs.
            model.addConstr(
                y[l] >= y[l + 1],
                name=f"no_holes[{l}]",
            )

        # Link loaded tons to binary non-ballast arc selection.
        for l, p, q_port, k in q_keys:
            # (E.24) Upper bound on loaded tons when arc is selected.
            model.addConstr(
                q[l, p, q_port, k] <= DWT * x[l, p, q_port, k],
                name=f"q_upper[{l},{p},{q_port},{k}]",
            )
            # (E.25) Lower bound on loaded tons when arc is selected.
            model.addConstr(
                q[l, p, q_port, k] >= Q_MIN * x[l, p, q_port, k],
                name=f"q_lower[{l},{p},{q_port},{k}]",
            )

        for (p_from, p_to, k) in A:
            if k != K_B:
                cap_tons = float(
                    Q_trade[
                        get_reporter_code(port_country[p_from]),
                        get_reporter_code(port_country[p_to]),
                        k,
                    ]
                )
                # (E.26) Bilateral trade-cap constraint.
                model.addConstr(
                    gp.quicksum(
                        q[l, p_from, p_to, k] for l in L
                    ) <= cap_tons,
                    name=f"trade_cap[{p_from},{p_to},{k}]",
                )

        # Flow continuity between consecutive legs.
        A_in = {r: [(p, k) for (p, r2, k) in A if r2 == r] for r in P_run}
        A_out = {r: [(q, k) for (r2, q, k) in A if r2 == r] for r in P_run}
        for l in L[1:]:
            for r in P_run:
                in_prev = gp.quicksum(x[l - 1, p, r, k] for (p, k) in A_in[r])
                out_cur = gp.quicksum(x[l, r, q, k] for (q, k) in A_out[r])
                # (E.5) Flow balance upper side.
                model.addConstr(
                    in_prev - out_cur <= 1 - y[l],
                    name=f"flow_in_out_ub[{l},{r}]",
                )
                # (E.6) Flow balance lower side.
                model.addConstr(
                    out_cur - in_prev <= 1 - y[l],
                    name=f"flow_in_out_lb[{l},{r}]",
                )

        # Sailing-time accounting, including optional ballast-cleaning slack.
        for l in L:
            base_time = gp.quicksum(
                tau_pq[p, q] * x[l, p, q, k] for (p, q, k) in A
            )
            dep_ballast = gp.quicksum(
                x[l, p, q, K_B]
                for (p, q, k) in A
                if k == K_B
            )
            # (E.7) Sailing-time definition.
            model.addConstr(
                t_S[l] == base_time + t_bx[l],
                name=f"sailing_time[{l}]",
            )
            # (E.11) Bound extra sea-time to ballast legs.
            model.addConstr(
                t_bx[l] <= t_max_run * dep_ballast,
                name=f"ballast_time_upper[{l}]",
            )

        # Port-time decomposition: load + discharge + cleaning-in-port.
        for l in L:
            # (E.27) Loading-time definition.
            model.addConstr(
                t_load[l]
                == gp.quicksum(
                    (2.0 / load_rate_port[p]) * q[l, p, q_port, k]
                    for (ll, p, q_port, k) in q_keys
                    if ll == l
                ),
                name=f"load_time[{l}]",
            )
            if l == 0:
                # (E.29) No discharge in first port call.
                model.addConstr(
                    t_disch[l] == 0.0,
                    name="discharge_time[0]",
                )
            else:
                # (E.28) Discharge-time definition.
                model.addConstr(
                    t_disch[l]
                    == gp.quicksum(
                        (2.0 / discharge_rate_port[q_port]) * q[l - 1, p, q_port, k]
                        for (p, q_port, k) in non_ballast_triplets
                    ),
                    name=f"discharge_time[{l}]",
                )
            # (E.30) Port-time balance.
            model.addConstr(
                t_p[l] == t_load[l] + t_disch[l] + t_wash_port[l],
                name=f"port_time_balance[{l}]",
            )

        # Time linking across legs and total time horizon.
        # (E.10) Start time is fixed to zero.
        model.addConstr(t[0] == 0, name="t0")
        for l in L[1:]:
            # (E.8) Link start times between consecutive legs.
            model.addConstr(
                t[l] == t[l - 1] + t_S[l - 1] + t_p[l],
                name=f"time_link[{l}]",
            )
        # (E.9) Route must finish within horizon.
        model.addConstr(
            t[L[-1]] + t_S[L[-1]] <= t_max_run,
            name="time_horizon",
        )


        for l in L:
            # 1 if any non-ballast cargo is loaded this leg
            dep_any_nb = gp.quicksum(
                x[l, p, q, k] for (p, q, k) in A if k in K_nb
            )
            for k in K_nb:
                # 1 if cargo k is loaded this leg
                dep_nb = gp.quicksum(
                    x[l, p, q, k] for (p, q, k2) in A if k2 == k
                )
                # Update k_l variable
                # (E.13) Activate commodity memory when commodity is loaded.
                model.addConstr(
                    k_l[k, l] >= dep_nb,
                    name=f"k_l_update_lb[{k},{l}]",
                )
                # k_l can not be 1 if dep_nb is 0 (i.e., no cargo k is loaded this leg)
                # (E.14) Keep memory consistent when non-ballast cargo is loaded.
                model.addConstr(
                    k_l[k, l] - dep_nb + dep_any_nb <= 1,
                    name=f"k_l_update_ub[{k},{l}]",
                )

        for l in L[1:]:
            # 1 if any non-ballast cargo is loaded this leg
            dep_any_nb = gp.quicksum(
                x[l, p, q, k] for (p, q, k) in A if k in K_nb
            )
            for k in K_nb:
                # 1 if cargo k is loaded this leg
                dep_nb = gp.quicksum(
                    x[l, p, q, k] for (p, q, k2) in A if k2 == k
                )
                # Persist cargo memory when current leg is ballast.
                # (E.15) Persistence upper bound during ballast transitions.
                model.addConstr(
                    k_l[k, l] - k_l[k, l - 1] - dep_any_nb <= 0,
                    name=f"k_l_persist_ub[{k},{l}]",
                )
                # (E.16) Persistence lower bound during ballast transitions.
                model.addConstr(
                    k_l[k, l] - k_l[k, l - 1] + dep_any_nb >= 0,
                    name=f"k_l_persist_lb[{k},{l}]",
                )

        # Washing logic and wash-time placement (in-port or during ballast sailing).
        for l in L[1:]:
            # 1 if departing ballast previous leg
            dep_ballast_prev = gp.quicksum(
                x[l - 1, p, q, K_B]
                for (p, q, k) in A
                if k == K_B
            )
            for k_prev in K_nb:
                for k_next in K_nb:
                    if (k_prev, k_next) in W_I:
                        # Cannot combine these commodities
                        # (E.17) Prohibit illegal commodity swaps.
                        model.addConstr(
                            gp.quicksum(
                                x[l, p, q, k_next]
                                for (p, q, k) in A
                                if k == k_next
                            )
                            + k_l[k_prev, l - 1]
                            <= 1,
                            name=f"Illegal_swaps[{k_prev},{k_next},{l}]",
                        )
                        continue
                    w = T_W.get((k_prev, k_next), 0)
                    w_b = T_WB.get((k_prev, k_next), 0)
                    if w <= 0:
                        continue
                    # 1 if departing k_next this leg
                    dep_nb = gp.quicksum(
                        x[l, p, q, k_next]
                        for (p, q, k) in A
                        if k == k_next
                    )
                    # c can not be 1 if dep_nb, k_l or dep_ballast_prev is zero
                    wm = c[k_prev, k_next, l]
                    # (E.18) Cleaning mode can only be 1 if next cargo is selected.
                    model.addConstr(
                        wm <= dep_nb,
                        name=f"washmode_next[{k_prev},{k_next},{l}]",
                    )
                    # (E.19) Cleaning mode can only be 1 if previous cargo memory matches.
                    model.addConstr(
                        wm <= k_l[k_prev, l - 1],
                        name=f"washmode_prev[{k_prev},{k_next},{l}]",
                    )
                    # (E.20) Cleaning mode can only be 1 if previous leg is ballast.
                    model.addConstr(
                        wm <= dep_ballast_prev,
                        name=f"washmode_ballast[{k_prev},{k_next},{l}]",
                    )
                    # If ballast wash is selected, enforce enough ballast sailing time.
                    # Otherwise, enforce in-port wash time.
                    # (E.21) In-port cleaning-time lower bound.
                    model.addConstr(
                        t_wash_port[l]
                        >= w * (dep_nb + k_l[k_prev, l - 1] - 1) - t_max_run * wm,
                        name=f"wash_time_port[{k_prev},{k_next},{l}]",
                    )
                    if w_b > 0:
                        # (E.22) Ballast-cleaning sea-time requirement.
                        model.addConstr(
                            t_S[l - 1] >= w_b * wm,
                            name=f"wash_time_ballast[{k_prev},{k_next},{l}]",
                        )

        # Disallow two consecutive ballast legs.
        for l in L[1:]:
            # (E.12) No two consecutive ballast legs.
            model.addConstr(
                gp.quicksum(
                    x[l, p, q, K_B]
                    for (p, q, k) in A if k == K_B
                )
                + gp.quicksum(
                    x[l-1, p, q, K_B]
                    for (p, q, k) in A if k == K_B
                )
                <= 1,
                name=f"no_two_ballast[{l}]",
            )
        # Disallow two consecutive legs with the same commodity.
        for l in L[1:]:
            for k_fix in K:
                # (E.23) No two consecutive legs with the same commodity.
                model.addConstr(
                    gp.quicksum(
                        x[l, p, q, k_arc]
                        for (p, q, k_arc) in A if k_arc == k_fix
                    )
                    + gp.quicksum(
                        x[l-1, p, q, k_arc]
                        for (p, q, k_arc) in A if k_arc == k_fix
                    )
                    <= 1,
                    name=f"no_two_cmd[{l},{k_fix}]",
                )

        if start_port is not None:
            model.addConstr(
                gp.quicksum(
                    x[0, p, q, k]
                    for (p, q, k) in A
                    if p == start_port
                ) == 1,
                name=f"rolling_start_port[{start_port}]",
            )

        # Solver runtime guardrail: store node files in run output folder.
        nodefile_dir = Path(output_root) / "nodefiles"
        nodefile_dir.mkdir(parents=True, exist_ok=True)
        model.Params.NodefileDir = str(nodefile_dir.resolve())

        # Optimize model
        optimize_start = time.perf_counter()
        oom_hit = _safe_optimize(model)

        # Iterative freight-rate heuristic:
        # 1) Solve with leg-mean rates in window
        # 2) Update used legs with exact day rates from solved start times
        # 3) Re-optimize until stable signature/objective
        rate_loop_iters = 5
        rate_obj_tol = 1e-6

        def _rate_signature():
            active_x = frozenset(
                (l, p, q_port, k)
                for (l, p, q_port, k) in x_keys
                if x[l, p, q_port, k].X > 0.5
            )
            start_days = tuple(
                (l, max(day_start, min(day_end, day_start + int(round(t[l].X / 2.0)))))
                for l in L
                if y[l].X > 0.5
            )
            return active_x, start_days

        prev_signature = _rate_signature() if model.SolCount > 0 else None
        prev_obj = float(model.objVal) if model.SolCount > 0 else None

        for rate_iter in range(rate_loop_iters):
            has_incumbent = model.SolCount > 0 and model.Status in [
                GRB.OPTIMAL, GRB.TIME_LIMIT, GRB.SUBOPTIMAL, GRB.INTERRUPTED
            ]
            if not has_incumbent:
                print(f"Stopper rate-loop: ingen løsning tilgjengelig (status={model.Status})")
                break
            if oom_hit:
                print("Stopper rate-loop etter OOM for å bevare incumbent-løsning.")
                break

            used_legs = [l for l in L if y[l].X > 0.5]
            for l in used_legs:
                day_offset = int(round(t[l].X / 2.0))
                day = day_start + day_offset
                day = max(day_start, min(day_end, day))
                R_k.loc[l, :] = R_k_matrix.loc[day, R_k.columns]

            revenue = gp.quicksum(
                q[l, p, q_port, k] / DWT * tau_pq[p, q_port] / 2 * R_k.at[l, k]
                for (l, p, q_port, k) in q_keys
            )
        

            fuel_base = gp.quicksum(
                tau_pq[p, q_port]/2 * C_FB * x[l, p, q_port, k]
                for (l, p, q_port, k) in x_keys
            )
            fuel_load_premium = gp.quicksum(
                tau_pq[p, q_port]/2 * (C_FL - C_FB) * (q[l, p, q_port, k] / DWT)
                for (l, p, q_port, k) in q_keys
            )
            port_cost = gp.quicksum(
                C_P(t_p[l]) 
                for l in L if l > 0
                )
            cleaning_cost = gp.quicksum(
                C_CL/2 * (t_wash_port[l] + t_bx[l])
                for l in L if l > 0
            )
            opex = gp.quicksum(OPEX * (t_S[l]/2 + t_p[l]/2) for l in L)

            model.setObjective(
                revenue -opex-fuel_base - fuel_load_premium - port_cost - cleaning_cost,
                GRB.MAXIMIZE,
            )
            
            if model.SolCount > 0:
                for key in x.keys():
                    x[key].Start = x[key].X
                for l in L:
                    y[l].Start = y[l].X
            oom_hit = _safe_optimize(model) or oom_hit
            if model.SolCount == 0:
                print("Stopper rate-loop: ingen incumbent etter re-optimize.")
                break

            curr_signature = _rate_signature()
            curr_obj = float(model.objVal)
            if (
                prev_signature is not None
                and curr_signature == prev_signature
                and prev_obj is not None
                and abs(curr_obj - prev_obj) <= rate_obj_tol
            ):
                print(
                    f"Rate-loop konvergerte etter iterasjon {rate_iter + 1}: "
                    "uendret x/startdag og objekt."
                )
                break
            prev_signature = curr_signature
            prev_obj = curr_obj
        optimize_end = time.perf_counter()
        print(f"Optimize time: {optimize_end - optimize_start:.2f}s")

        # Save optimization data to CSVs
        model_elapsed = time.perf_counter() - model_start
        out_file = save_optimization_results(
            model,
            runtime_seconds=model_elapsed,
            P_1=None,
            P_1_country=None,
            n_legs=N,
            iter_id=iter_id,
            output_root=output_root,
        )
        print(f"Saved optimization data to: {out_file}")

        # Write IIS if infeasible
        if model.Status == GRB.INFEASIBLE:
            model.computeIIS()
            model.write("model2N.ilp")

        end_port = None
        if model.SolCount > 0:
            used_legs = [l for l in L if y[l].X > 0.5]
            if used_legs:
                last_leg = max(used_legs)
                for (p, q, k) in A:
                    if x[last_leg, p, q, k].X > 0.5:
                        end_port = q
                        break

        # Print the results (only if a solution exists)
        if model.SolCount > 0:
            for v in model.getVars():
                if v.X > 0:
                    print(f"{v.varName}: {v.X}")
            print(f"Optimal Objective Value: {model.objVal}")
        else:
            print("Ingen losning funnet (SolCount = 0).")

        method_used = _method_label(model.Params.Method)
        status_map = {
            GRB.OPTIMAL: "optimal",
            GRB.INFEASIBLE: "infeasible",
            GRB.TIME_LIMIT: "time_limit",
            GRB.SUBOPTIMAL: "suboptimal",
            GRB.INTERRUPTED: "interrupted",
        }
        mem_limit_status = getattr(GRB, "MEM_LIMIT", None)
        if mem_limit_status is not None:
            status_map[mem_limit_status] = "mem_limit"
        status_label = status_map.get(model.Status, f"status_{model.Status}")
        if oom_hit:
            status_label = "oom_with_incumbent" if model.SolCount > 0 else "oom_no_incumbent"

        if model.SolCount > 0:
            return out_file, float(model.objVal), float(model_elapsed), method_used, status_label, end_port
        return out_file, None, float(model_elapsed), method_used, status_label, end_port

    except gp.GurobiError as e:
        print(f"Gurobi Error: {e.errno} - {e.message}")
        return None, None, None, None, "gurobi_error", None

    except AttributeError as e:
        print(f"Attribute Error: {str(e)}")
        return None, None, None, None, "attribute_error", None

if __name__ == "__main__":
    ############################ 120 day period Bulk rolling horizon ############################
    output_root = "Optimization/Solutions"
    windows = [
        (1, 30),
        (32, 60)
    ]
    solution_files = []
    start_port = None
    for quarter_id, (day_start, day_end) in enumerate(windows, start=1):
        out_file, obj, runtime_s, method, status, end_port = optimize_model(
            "Tank",
            CII_rating="E",
            P_override=P,
            iter_id=quarter_id,
            output_root=output_root,
            day_start=day_start,
            day_end=day_end,
            start_port=start_port,
        )
        print(
            f"Quarter {quarter_id}: days={day_start}-{day_end}, "
            f"status={status}, obj={obj}, runtime={runtime_s}, end_port={end_port}"
        )
        solution_files.append(out_file)
        if end_port is None:
            print(f"Stopping rolling horizon after quarter {quarter_id} (no feasible end port).")
            break
        start_port = end_port

    year_file = combine_solution_files(
        solution_files,
        Path(output_root) / "solution_year.csv",
    )
    if year_file is not None:
        print(f"Saved combined year solution to: {year_file}")

    elapsed = time.perf_counter() - start
    print(f"Runtime: {elapsed:.2f} seconds")