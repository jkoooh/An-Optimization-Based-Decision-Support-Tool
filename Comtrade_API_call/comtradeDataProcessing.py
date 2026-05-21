"""Comtrade data processing.

This module cleans the HS4 trade dataset and prepares the exact
output format used by the optimization model.
"""

from pathlib import Path

import matplotlib.pylab as plt
import numpy as np
import pandas as pd
import pycountry
import seaborn as sns
from IPython.display import display
from matplotlib.patches import Patch


# Basic display/style settings used in notebook-like workflows.
plt.style.use("ggplot")
pd.set_option("display.max_columns", 200)
pd.set_option("display.max_rows", 500)
pd.set_option("display.max_colwidth", None)


CSV_PATH = Path("Data/HS4database2024.csv")
CSV_PATH_2024 = Path("Data/HS4database2024.csv")


# Load source data once for module-level helper checks.
df_full = pd.read_csv(CSV_PATH)
df_full_2024 = pd.read_csv(CSV_PATH_2024)


# Identify unit issues in source file (kept for quick diagnostics).
u = df_full["qtyUnitAbbr"].astype("string").str.strip()
cmd_na = sorted(df_full.loc[u.isna(), "cmdCode"].astype(str).unique())
cmd_m3 = sorted(df_full.loc[u.eq("mÂ³"), "cmdCode"].astype(str).unique())
cmd_m2 = sorted(df_full.loc[u.eq("mÂ²"), "cmdCode"].astype(str).unique())
cmd_kg = sorted(df_full.loc[u.eq("kg"), "cmdCode"].astype(str).unique())
cmd_u = sorted(df_full.loc[u.eq("u"), "cmdCode"].astype(str).unique())


# Keep rows with non-zero net weight for summary export.
df_cleaned = df_full[df_full["netWgt"].notna() & (df_full["netWgt"] != 0)].copy()
df_cleaned_2024 = df_full_2024[
    df_full_2024["netWgt"].notna() & (df_full_2024["netWgt"] != 0)
].copy()

hs4_summary = (
    df_cleaned.groupby("cmdCode", as_index=True)
    .agg(**{"sum primary value": ("primaryValue", "sum"), "sum netwgt": ("netWgt", "sum")})
)
hs4_summary.to_csv(Path("Data/valuePerTon.csv"), index=True)


# Landlocked countries excluded from maritime routing use-cases.
LANDLOCKED_ISO3 = {
    "AFG", "AND", "ARM", "AUT", "AZE", "BDI", "BFA", "BOL", "BWA", "CAF", "CHE", "CZE",
    "ETH", "HUN", "KAZ", "KGZ", "LAO", "LIE", "LUX", "MKD", "MLI", "MNG", "MWI", "NER",
    "NPL", "PRY", "RWA", "SMR", "SRB", "SVK", "SWZ", "TCD", "TJK", "UGA", "UZB", "VAT",
    "ZMB", "ZWE", "XKX", "SSD", "LSO", "BLR", "BIH",
}


def repairComtradeData():
    """Show inconsistent commodity descriptions across classification versions."""
    df_rep = df_full.copy()

    ref_h6 = df_rep[df_rep["classificationCode"] == "H6"][["cmdCode", "cmdDesc"]].drop_duplicates()
    ref_h6 = ref_h6.rename(columns={"cmdDesc": "cmdDesc_H6"})

    merged = df_rep.merge(ref_h6, on="cmdCode", how="left")
    merged["cmdDesc_match_H6"] = merged["cmdDesc"] == merged["cmdDesc_H6"]

    inconsistencies = merged[merged["cmdDesc_match_H6"] == False]
    inconsistent_summary = (
        inconsistencies.groupby(["cmdCode", "classificationCode"])[["cmdDesc", "cmdDesc_H6"]]
        .agg(lambda x: ", ".join(sorted(set(x))))
        .reset_index()
    )
    display(inconsistent_summary)


def removeColumns(df_full: pd.DataFrame) -> pd.DataFrame:
    """Drop columns that are constant, redundant, or out of scope."""
    row_list = df_full.iloc[0].tolist()
    column_drop = []
    i = 0

    for key in df_full.columns:
        count = (df_full[key] == row_list[i]).sum()
        i += 1
        if count == df_full.shape[0]:
            column_drop.append(key)

    # Keep only primary trade value and core quantity fields.
    column_drop.append("cifvalue")
    column_drop.append("isQtyEstimated")
    column_drop.append("isNetWgtEstimated")
    column_drop.append("altQty")
    column_drop.append("altQtyUnitCode")
    column_drop.append("altQtyUnitAbbr")
    column_drop.append("fobvalue")

    df = df_full.drop(column_drop, axis=1).copy()
    return df


def removeRows(df: pd.DataFrame) -> pd.DataFrame:
    """Apply commodity and minimum-volume filters."""
    df = df.copy()
    blocked_hs4 = {
        "1401", "1404", "2705", "2709", "2714", "2715", "2706", "2707", "2833", "2836",
        "2840", "3801", "3802", "3816", "3825", "3901", "3902", "3903", "4001",
    }
    df = df[~df["cmdCode"].astype(str).str.zfill(4).isin(blocked_hs4)].copy()
    df = df[df["netWgt"] >= 250000 * 1000].copy()
    return df


def remove_reps(df: pd.DataFrame) -> pd.DataFrame:
    """Keep reporters that cumulatively cover 99.9% of trade value."""
    reporter_values = df.groupby("reporterDesc")["primaryValue"].sum().sort_values(ascending=False)
    cum_share = reporter_values.cumsum() / reporter_values.sum()
    reporter_df = pd.DataFrame({"trade_value": reporter_values, "cum_share": cum_share})
    keep_reporters = reporter_df[reporter_df["cum_share"] <= 0.999].index
    return df[df["reporterDesc"].isin(keep_reporters)].copy()


def merge_hs4_ranges(
    df: pd.DataFrame,
    ranges,
    cmd_col: str = "cmdCode",
    desc_map=None,
    desc_col: str = "cmdDesc",
) -> pd.DataFrame:
    """Merge HS4 intervals into placeholder codes and re-aggregate measures."""
    df = df.copy()
    cmd_str = df[cmd_col].astype(str).str.zfill(4)
    cmd_int = cmd_str.astype(int)
    new_cmd = cmd_str.copy()

    for start, end, label in ranges:
        start_i = int(start)
        end_i = int(end)
        mask = (cmd_int >= start_i) & (cmd_int <= end_i)
        new_cmd = new_cmd.where(~mask, str(label))

    df[cmd_col] = new_cmd

    if desc_map and desc_col in df.columns:
        for _, _, label in ranges:
            label_str = str(label)
            if label_str in desc_map:
                df.loc[df[cmd_col].astype(str) == label_str, desc_col] = desc_map[label_str]

    measure_cols = [c for c in ["primaryValue", "netWgt", "qty"] if c in df.columns]
    if not measure_cols:
        return df

    group_cols = [c for c in df.columns if c not in measure_cols]
    return df.groupby(group_cols, as_index=False, dropna=False)[measure_cols].sum()


def build_comtrade_df() -> pd.DataFrame:
    """Build cleaned Comtrade dataframe for optimization input."""
    df = pd.read_csv(CSV_PATH).copy()
    df_2024 = pd.read_csv(CSV_PATH_2024).copy()

    valid_iso3 = [c.alpha_3 for c in pycountry.countries]
    df = df[df["reporterISO"].isin(valid_iso3)].copy()
    df = df[df["partnerISO"].isin(valid_iso3)].copy()
    df_2024 = df_2024[df_2024["reporterISO"].isin(valid_iso3)].copy()
    df_2024 = df_2024[df_2024["partnerISO"].isin(valid_iso3)].copy()

    df = df[~df["partnerISO"].isin(LANDLOCKED_ISO3)].copy()
    df = df[~df["reporterISO"].isin(LANDLOCKED_ISO3)].copy()
    df_2024 = df_2024[~df_2024["partnerISO"].isin(LANDLOCKED_ISO3)].copy()
    df_2024 = df_2024[~df_2024["reporterISO"].isin(LANDLOCKED_ISO3)].copy()

    # Keep reporter countries that are net exporters per HS4.
    exp = (
        df.groupby(["reporterISO", "cmdCode"], as_index=False)["primaryValue"]
        .sum()
        .rename(columns={"reporterISO": "iso", "primaryValue": "exp_value"})
    )
    imp = (
        df.groupby(["partnerISO", "cmdCode"], as_index=False)["primaryValue"]
        .sum()
        .rename(columns={"partnerISO": "iso", "primaryValue": "imp_value"})
    )
    exp_2024 = (
        df_2024.groupby(["reporterISO", "cmdCode"], as_index=False)["primaryValue"]
        .sum()
        .rename(columns={"reporterISO": "iso", "primaryValue": "exp_value"})
    )
    imp_2024 = (
        df_2024.groupby(["partnerISO", "cmdCode"], as_index=False)["primaryValue"]
        .sum()
        .rename(columns={"partnerISO": "iso", "primaryValue": "imp_value"})
    )

    net = exp.merge(imp, on=["iso", "cmdCode"], how="left")
    net["imp_value"] = net["imp_value"].fillna(0)
    net_exporters = net[net["exp_value"] - net["imp_value"] > 0][["iso", "cmdCode"]]
    df = df.merge(net_exporters, left_on=["reporterISO", "cmdCode"], right_on=["iso", "cmdCode"], how="inner")
    df = df.drop(columns=["iso"])

    net_2024 = exp_2024.merge(imp_2024, on=["iso", "cmdCode"], how="left")
    net_2024["imp_value"] = net_2024["imp_value"].fillna(0)
    net_exporters_2024 = net_2024[net_2024["exp_value"] - net_2024["imp_value"] > 0][["iso", "cmdCode"]]
    df_2024 = df_2024.merge(
        net_exporters_2024,
        left_on=["reporterISO", "cmdCode"],
        right_on=["iso", "cmdCode"],
        how="inner",
    )
    df_2024 = df_2024.drop(columns=["iso"])

    # Keep partner countries that are net importers per HS4.
    net_importers = net[net["imp_value"] - net["exp_value"] > 0][["iso", "cmdCode"]]
    df = df.merge(net_importers, left_on=["partnerISO", "cmdCode"], right_on=["iso", "cmdCode"], how="inner")
    df = df.drop(columns=["iso"])

    net_importers_2024 = net_2024[net_2024["imp_value"] - net_2024["exp_value"] > 0][["iso", "cmdCode"]]
    df_2024 = df_2024.merge(
        net_importers_2024,
        left_on=["partnerISO", "cmdCode"],
        right_on=["iso", "cmdCode"],
        how="inner",
    )
    df_2024 = df_2024.drop(columns=["iso"])

    df = removeRows(df)
    df_2024 = removeRows(df_2024)

    df = removeColumns(df)
    df_2024 = removeColumns(df_2024)

    # Merge HS4 ranges to reduce commodity count in optimization.
    hs4_merge_ranges = [
        (2301, 2308, "2301"),
        (2618, 2619, "2618"),
        (2620, 2621, "2620"),
        (2701, 2704, "2701"),
        (2706, 2707, "2706"),
        (2714, 2715, "2714"),
        (3101, 3105, "3101"),
        (3801, 3816, "3801"),
        (3901, 3903, "3901"),
        (4701, 4707, "4701"),
        (7206, 7229, "7206"),
        (7301, 7309, "7301"),
        (7608, 7610, "7608"),
    ]
    hs4_desc_map = {
        "2301": "Food industry residues (HS4 2301â€“2308)",
        "2618": "Slags (HS4 2618â€“2619)",
        "2620": "Ashes (HS4 2620â€“2621)",
        "2701": "Coals (HS4 2701â€“2704)",
        "2706": "Tar (HS4 2706â€“2707)",
        "2714": "Asphalts (HS4 2714)",
        "3105": "Fertilizers (HS4 3101â€“3105)",
        "3801": "Misc chemical products, Powder (HS4 3801â€“3816)",
        "3901": "Polymers (HS4 3901â€“3903)",
        "4701": "Pulp of wood (HS4 4701â€“4707)",
        "7206": "Iron and steel products (HS4 7206â€“7229)",
        "7301": "Articles of iron and steel (HS4 7301â€“7309)",
        "7608": "Aluminium products (HS4 7608â€“7610)",
    }

    print(f"Unique HS4 codes before merging: {df['cmdCode'].unique()}")
    df = merge_hs4_ranges(
        df,
        hs4_merge_ranges,
        cmd_col="cmdCode",
        desc_map=hs4_desc_map,
        desc_col="cmdDesc",
    )

    return df


if __name__ == "__main__":
    df = build_comtrade_df()
    df.to_csv(Path("Data/cleanedHS4database2024.csv"), index=False)

    unique_hs4_codes = sorted(
        df["cmdCode"].astype(str).str.zfill(4).unique().tolist()
    )
    print("Unique HS4 codes in cleaned database:", unique_hs4_codes)
    print(df.shape)
