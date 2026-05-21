import pandas as pd
import numpy as np
import matplotlib.pylab as plt
from matplotlib.patches import Patch
import seaborn as sns
from pathlib import Path
from IPython.display import display
import pycountry 


plt.style.use('ggplot')
pd.set_option('display.max_columns', 200)
pd.set_option('display.max_rows', 500)
pd.set_option('display.max_colwidth', None)

csv_path = Path("Data/HS4database2024.csv")
df_full = pd.read_csv(csv_path)

csv_path_2024 = Path("Data/HS4database2024.csv")
df_full_2024 = pd.read_csv(csv_path)

#print(df_full["reporterDesc"].nunique())

# cmdCodes where qtyUnitAbbr is N/A (missing)
u = df_full["qtyUnitAbbr"].astype("string").str.strip()

cmd_na = sorted(df_full.loc[u.isna(), "cmdCode"].astype(str).unique())
cmd_m3 = sorted(df_full.loc[u.eq("m³"), "cmdCode"].astype(str).unique())
cmd_m2 = sorted(df_full.loc[u.eq("m²"), "cmdCode"].astype(str).unique())
cmd_kg = sorted(df_full.loc[u.eq("kg"), "cmdCode"].astype(str).unique())
cmd_u = sorted(df_full.loc[u.eq("u"), "cmdCode"].astype(str).unique())

#print("N/A cmdCodes:", cmd_na)
#print("m³ cmdCodes:", cmd_m3)
#print("m² cmdCodes:", cmd_m2)
#print("kg cmdCodes:", cmd_kg)

#print("Counts -> N/A:", len(cmd_na), "| m³:", len(cmd_m3), "| m²:", len(cmd_m2), "| kg:", len(cmd_kg))



'''#print(df_full.shape)
display(df_full.head())
df_full.columns'''

#countries = sorted(df_full["reporterDesc"].dropna().unique())
##print(countries)


df_cleaned = df_full[
    df_full["netWgt"].notna()
    & (df_full["netWgt"] != 0)
].copy()
df_cleaned_2024 = df_full_2024[
    df_full_2024["netWgt"].notna()
    & (df_full_2024["netWgt"] != 0)
].copy()

hs4_summary = (
    df_cleaned.groupby("cmdCode", as_index=True)
    .agg(**{"sum primary value": ("primaryValue", "sum"), "sum netwgt": ("netWgt", "sum")})
)
"""hs4_summary["value/ton"] = hs4_summary["sum primary value"] / (hs4_summary["sum netwgt"] / 1000)
hs4_summary["primary value cap"] = hs4_summary["value/ton"] * 400000"""
hs4_summary.to_csv(Path("Data/valuePerTon.csv"), index=True)

def repairComtradeData():
    #print(df_full['classificationCode'].value_counts())

    df_rep = df_full.copy()

    # 1. DF med kun H6 koder og beskrivelser
    ref_h6 = df_rep[df_rep["classificationCode"] == "H6"][["cmdCode", "cmdDesc"]].drop_duplicates()
    ref_h6 = ref_h6.rename(columns={"cmdDesc": "cmdDesc_H6"})

    # 2. Merge H6 ref to df
    merged = df_rep.merge(ref_h6, on="cmdCode", how="left")

    # 3. Check for inconsistencies in commodity descriptions
    merged["cmdDesc_match_H6"] = merged["cmdDesc"] == merged["cmdDesc_H6"]

    # 4. Identify mismatches
    inconsistencies = merged[merged["cmdDesc_match_H6"] == False]

    # 5. Sum up mismatches
    inconsistent_summary = (
    inconsistencies.groupby(["cmdCode", 'classificationCode'])[["cmdDesc", "cmdDesc_H6"]]
    .agg(lambda x: ", ".join(sorted(set(x))))
    .reset_index())

    display(inconsistent_summary)
#repairComtradeData()

def removeColumns(df_full):
    row_list = df_full.iloc[0].tolist()
    column_drop = []
    irreg_list = []
    i=0

    # Iterates to check if the instances in a column is constant
    for key in df_full.columns:
        count = (df_full[key] == row_list[i]).sum()
        i += 1
        if count == df_full.shape[0]:
            column_drop.append(key)
        # Columns containing NA are now appended to irreg_list for further inspection
        elif count == 0:
            irreg_list.append(key)


    """#print(column_drop)
    #print(irreg_list)"""

    #Checking irregularities count = 0
    """count_cif = df_full['cifvalue'].isna().sum()
    #print(count_cif)"""
    column_drop.append('cifvalue') #all but 100 are zero

    #Checking additional columns
    """count_cc = (df_full['classificationCode'] == 'H6' ).sum()
    #print(count_cc)""" #Implies there are some reports from earlier harmonised system revisions

    #legacyEstimationFlag tells us wether weight quantity or both are estimated, making isNetWgtEstimated and isQtytEstimated redundant
    #0: No estimation, 2: Quantity estimation, 4: Net weight estimation, 6: Quantity and net weight estimation
    column_drop.append('isQtyEstimated')
    column_drop.append('isNetWgtEstimated')

    #We are not interested in altQty only qty
    column_drop.append('altQty')
    column_drop.append('altQtyUnitCode')
    column_drop.append('altQtyUnitAbbr')

    #Primary value is the main value used in comtrade aggregate statistics, and represents the official trade value. Therefore we remove Fob value (cif is already removed)
    column_drop.append('fobvalue')
    df = df_full.drop(column_drop, axis=1).copy()

    ##print(df.columns)
    ##print(df.shape)

    return df
#df = removeColumns()

##print(df.shape)
#valid_iso3 = [c.alpha_3 for c in pycountry.countries] #Liste med alle FN godkjente land. 

#df = df[df['reporterISO'].isin(valid_iso3)].copy() #Fjerner aggregater som EUR, S19, A19 etc (dobbeltelling) 
#df = df[df['partnerISO'].isin(valid_iso3)].copy()  #og placeholders for ukjente partnere som X1, XX etc (Ikke meningsfulle)

#landlocked_iso3 = [
    'AFG','AND','ARM','AUT','AZE','BDI','BFA','BOL','BWA','CAF','CHE','CZE','ETH','HUN',
    'KAZ','KGZ','LAO','LIE','LUX','MKD','MLI','MNG','MWI','NER','NPL','PRY','RWA','SMR',
    'SRB','SVK','SWZ','TCD','TJK','UGA','UZB','VAT','ZMB','ZWE','XKX','SSD','LSO', "BLR",
    "BIH"
#] 

##print(df.shape)

#df = df[~df['partnerISO'].isin(landlocked_iso3)].copy()
#df = df[~df['reporterISO'].isin(landlocked_iso3)].copy()

#df.shape

def removeRows(df):
    df = df.copy()
    # Drop selected HS4 codes after further research on tank/bulk compatibility
    blocked_hs4 = {"1401", "1404", "2705", "2709", "2714", "2715", "2706", "2707", "2833", "2836", "2840", "3801", "3802", "3816", "3825", "3901", "3902", "3903", "4001"}
    df = df[~df["cmdCode"].astype(str).str.zfill(4).isin(blocked_hs4)].copy()
    df = df[
        df["netWgt"] >= 250000 * 1000
    ].copy()
    ##print(df.shape)

    return df
#df = removeRows(df)

def remove_reps(df):
    # Antall trade lines per land 
    reporter_counts = df['reporterDesc'].value_counts()
    ##print(reporter_counts)

    # Total handelsverdi per land
    reporter_values = df.groupby('reporterDesc')['primaryValue'].sum().sort_values(ascending=False)
    ##print(reporter_values)

    #Alle reporters
    reporters = df['reporterISO'].unique().tolist()
    ##print(reporters)


    # Generer kumulativ fordeling av verdenshandel
    cum_share = reporter_values.cumsum() / reporter_values.sum()
    ##print(cum_share)
    # Lager df for å se resultatene
    reporter_df = pd.DataFrame({
        'trade_value': reporter_values,
        'cum_share': cum_share
    })
    # Keep only reporters that together cover 99.9% of trade value
    keep_reporters = reporter_df[reporter_df['cum_share'] <= 0.999].index
    df = df[df['reporterDesc'].isin(keep_reporters)].copy()
    ##print(f'Total number of reporters: {len(reporters)}')
    ##print(f"Reporters covering 99.9% of trade: {len(keep_reporters)}")
    return df

#df = remove_reps(df)

def merge_hs4_ranges(df, ranges, cmd_col="cmdCode", desc_map=None, desc_col="cmdDesc"):
    """
    Replace HS4 codes in given ranges with a placeholder cmdCode, then
    aggregate numeric measures so the merged code is a single commodity.
    ranges: list of (start, end, label)
    """
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

    # Sum only measure columns; keep identifiers/dimensions as group keys.
    measure_cols = [c for c in ["primaryValue", "netWgt", "qty"] if c in df.columns]
    if not measure_cols:
        return df
    group_cols = [c for c in df.columns if c not in measure_cols]
    df = df.groupby(group_cols, as_index=False, dropna=False)[measure_cols].sum()
    return df

def build_comtrade_df():
    df_full = pd.read_csv(csv_path)
    df = df_full.copy()
    df_full_2024 = pd.read_csv(csv_path_2024)
    df_2024 = df_full_2024.copy()

    valid_iso3 = [c.alpha_3 for c in pycountry.countries] #Liste med alle FN godkjente land. 
    df = df[df['reporterISO'].isin(valid_iso3)].copy() #Fjerner aggregater som EUR, S19, A19 etc (dobbeltelling) 
    df = df[df['partnerISO'].isin(valid_iso3)].copy()  #og placeholders for ukjente partnere som X1, XX etc (Ikke meningsfulle)
    df_2024 = df_2024[df_2024['reporterISO'].isin(valid_iso3)].copy() #Fjerner aggregater som EUR, S19, A19 etc (dobbeltelling) 
    df_2024 = df_2024[df_2024['partnerISO'].isin(valid_iso3)].copy()  #og placeholders for ukjente partnere som X1, XX etc (Ikke meningsfulle)
    #print(df.shape)
    #print(f"2024 {df_2024.shape}")

    landlocked_iso3 = [
    'AFG','AND','ARM','AUT','AZE','BDI','BFA','BOL','BWA','CAF','CHE','CZE','ETH','HUN',
    'KAZ','KGZ','LAO','LIE','LUX','MKD','MLI','MNG','MWI','NER','NPL','PRY','RWA','SMR',
    'SRB','SVK','SWZ','TCD','TJK','UGA','UZB','VAT','ZMB','ZWE','XKX','SSD','LSO', "BLR",
    "BIH"
    ] 

    df = df[~df['partnerISO'].isin(landlocked_iso3)].copy()
    df = df[~df['reporterISO'].isin(landlocked_iso3)].copy()
    df_2024 = df_2024[~df_2024['partnerISO'].isin(landlocked_iso3)].copy()
    df_2024 = df_2024[~df_2024['reporterISO'].isin(landlocked_iso3)].copy()
    ##print(df.shape)
    #print(df.shape)
    #print(f"2024 {df_2024.shape}")
    # Keep only trades where reporter is a net exporter of the commodity
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
    df_2024 = df_2024.merge(net_exporters_2024, left_on=["reporterISO", "cmdCode"], right_on=["iso", "cmdCode"], how="inner")
    df_2024 = df_2024.drop(columns=["iso"])

    net_importers = net[net["imp_value"] - net["exp_value"] > 0][["iso", "cmdCode"]]
    df = df.merge(net_importers, left_on=["partnerISO", "cmdCode"], right_on=["iso", "cmdCode"], how="inner")
    df = df.drop(columns=["iso"])

    net_importers_2024 = net_2024[net_2024["imp_value"] - net_2024["exp_value"] > 0][["iso", "cmdCode"]]
    df_2024 = df_2024.merge(net_importers_2024, left_on=["partnerISO", "cmdCode"], right_on=["iso", "cmdCode"], how="inner")
    df_2024 = df_2024.drop(columns=["iso"])

    #print(df.shape)
    #print(f"2024 {df_2024.shape}")
    df = removeRows(df)
    df_2024 = removeRows(df_2024)
    #print(df.shape)
    #print(f"2024 {df_2024.shape}")
    ##print(df.shape)

    df = removeColumns(df)
    df_2024 = removeColumns(df_2024)
    #print(f"Before: {df.shape}")
    #print(f"2024 {df_2024.shape}")
    #df = remove_reps(df)

    cols = ["reporterISO", "partnerISO", "cmdCode"]  # bruk riktig kolonnenavn
#     df = (
#     pd.concat([df, df_2024], ignore_index=True)
#       .drop_duplicates(subset=cols, keep="first")
# )
    #print(f"After: {df.shape}")
    #print(f"2024 {df_2024.shape}")

    #print(df.shape)
    #print(f"2024 {df_2024.shape}")
    # Merge HS4 ranges into placeholder cmdCode(s) to reduce K in optimization.
    #print(f"Unique HS4 codes: {df['cmdCode'].nunique()}")
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
        "2301": "Food industry residues (HS4 2301–2308)",
        "2618": "Slags (HS4 2618–2619)",
        "2620": "Ashes (HS4 2620–2621)",
        "2701": "Coals (HS4 2701–2704)",
        "2706": "Tar (HS4 2706–2707)",
        "2714": "Asphalts (HS4 2714)",
        "3105": "Fertilizers (HS4 3101–3105)",
        "3801": "Misc chemical products, Powder (HS4 3801–3816)",
        "3901": "Polymers (HS4 3901–3903)",
        "4701": "Pulp of wood (HS4 4701–4707)",
        "7206": "Iron and steel products (HS4 7206–7229)",
        "7301": "Articles of iron and steel (HS4 7301–7309)",
        "7608": "Aluminium products (HS4 7608–7610)",
    }
    print(f"Unique HS4 codes before merging: {df['cmdCode'].unique()}")
    df = merge_hs4_ranges(
        df,
        hs4_merge_ranges,
        cmd_col="cmdCode",
        desc_map=hs4_desc_map,
        desc_col="cmdDesc",
    )

    #print(f"Unique HS4 codes: {df['cmdCode'].nunique()}")
    # Keep only top 5 export commodities (HS4) per reporter by total primaryValue
    """top_n = 5
    top_pairs = (
        df.groupby(["reporterISO", "cmdCode"], as_index=False)["primaryValue"]
        .sum()
        .sort_values(["reporterISO", "primaryValue"], ascending=[True, False])
        .groupby("reporterISO")
        .head(top_n)
    )
    df = df.merge(top_pairs[["reporterISO", "cmdCode"]], on=["reporterISO", "cmdCode"], how="inner")
    #print(df.shape)"""
    # Normalize country naming for downstream lookups
    #df["reporterDesc"] = df["reporterDesc"].replace({"Viet Nam": "Vietnam"})
    #df["partnerDesc"] = df["partnerDesc"].replace({"Viet Nam": "Vietnam"})
    return df

if __name__ == "__main__":
    df = build_comtrade_df()
    df.to_csv(Path("Data/cleanedHS4database2024.csv"), index=False)
    unique_hs4_codes = sorted(
        df["cmdCode"]
        .astype(str)
        .str.zfill(4)
        .unique()
        .tolist()
    )
    print("Unique HS4 codes in cleaned database:", unique_hs4_codes)
    print(df.shape)
    ##print(sorted(df["cmdCode"].astype(str).unique().tolist()))
    ##print(df["cmdCode"].value_counts())

    #reporters = sorted(df["reporterDesc"].dropna().unique().tolist())
    ##print(reporters)

    #print(df["qtyUnitAbbr"].unique())

"""#-------------------------Data Visualisation#-------------------------
food_subset = [
    #9,  # Coffee, tea, matÂ´e and spices 
    10,  # Cereals (hvete, mais, ris, bygg osv.)
    #11,  # Milling products, malt, stivelse, gluten
    12,  # Oil seeds and oleaginous fruits (soya, raps, solsikke)
    14,  # Vegetable plaiting materials (rÃ¥materialer i bulk)
    15,  # Animal or vegetable fats and oils (tank)
    17,  # Sugars and sugar confectionery
    23,  # Residues and waste from food industries (fÃ´r, soyameal)
]
fossil_fuel_subset = [
    27,  # Mineral fuels, oils, bituminous substances (rÃ¥olje, LNG, petroleum)
]
chemical_subset = [
    28,  # Inorganic chemicals (syrer, kaustisk soda)
    #29,  # Organic chemicals (metanol, etanol, benzen, m.m.)
    #32,  # Tanning or dyeing extracts, pigments (Mye tÃ¸rrbulk)
    #34,  # Soap, surface-active agents (kjemikalier i tank, utenom LAS & SLES)
    38,  # Miscellaneous chemical products (kjemikalier, kunstgjÃ¸dsel, etc.)
    31,  # Fertilisers (ammoniumnitrat, urea, fosfat)
    39,  # Plastics and articles thereof (pellets, granulat)
    40,  # Rubber and articles thereof (gummi i bulk)
    44,  # Wood and articles of wood (tÃ¸rrbulk)
    47,  # Pulp of wood or other fibrous cellulosic material (tÃ¸rrbulk)
]
metals_ore_stone_subset = [
    25,  # Salt, sulphur, earths, stone, plastering materials, lime
    26,  # Ores, slag and ash (jernmalm, bauxitt, kobberkonsentrat)

    #68,  # Articles of stone, plaster, cement, etc. (sement, klinker)

    72,  # Iron and steel (rÃ¥jern, stÃ¥lbarrer, pellets)
    73,  # Articles of iron or steel (noe gÃ¥r som break-bulk)
    74,  # Copper and articles thereof
    75,  # Nickel and articles thereof
    76,  # Aluminium and articles thereof
    78,  # Lead and articles thereof
    79,  # Zinc and articles thereof
    80,  # Tin and articles thereof
    #81,  # Other base metals (molybden, titan, wolfram, etc.)
]

# Create mapping from HS2 to categories
category_map = {}
for code in food_subset:
    category_map[code] = 'Food & Agri'
for code in fossil_fuel_subset:
    category_map[code] = 'Fossil Fuels'
for code in chemical_subset:
    category_map[code] = 'Chemicals'
for code in metals_ore_stone_subset:
    category_map[code] = 'Metals, Ores & Stone'

# 
cmd_hs2 = (
    df["cmdCode"]
    .astype(str)
    .str.zfill(4)
    .str[:2]
    .astype(int)
)
df['cmd_hs2'] = cmd_hs2
df['category'] = df['cmd_hs2'].map(category_map)

# Create palet
category_colors = {
    'Food & Agri': '#66c2a5',           # Green
    'Fossil Fuels': '#fc8d62',          # Orange
    'Chemicals': '#8da0cb',             # Blue
    'Metals, Ores & Stone': '#e78ac3',  # Pink
}


# Count per HS4 (cmdCode) and color by HS2 category
code_count = (
    df.groupby('cmdCode')
    .size()
    .reset_index(name='count')
)

# Assign category from HS2 prefix of HS4 code
code_count['cmd_hs2'] = (
    code_count['cmdCode']
    .astype(str)
    .str.zfill(4)
    .str[:2]
    .astype(int)
)
code_count['category'] = code_count['cmd_hs2'].map(category_map)

# Plot
plt.figure(figsize=(14,6))
ax = sns.barplot(
    data=code_count,
    x='cmdCode', y='count',
    hue='category',
    dodge=False,
    palette=category_colors
)
plt.xlabel('HS4 Commodity Code')

# Place values on bar plot
for container in ax.containers:
    ax.bar_label(container, fmt='%d', label_type='edge', padding=3, fontsize=11)

plt.title('Total count per HS4 Code (Colored by HS2 Category)')
plt.xlabel('HS4 Commodity Code')
plt.ylabel('Total count (USD)')
plt.xticks(rotation=90)
plt.grid(axis='y', linestyle='--', alpha=0.7)
plt.legend(title='Category')
plt.tight_layout()
plt.show()

# Summer tradevalue per HS4
code_trade = (
    df.groupby('cmdCode', as_index=False)['primaryValue']
    .sum()
)

# Finn HS2 fra HS4 og map kategori
code_trade['cmd_hs2'] = (
    code_trade['cmdCode']
    .astype(str)
    .str.zfill(4)
    .str[:2]
    .astype(int)
)
code_trade['category'] = code_trade['cmd_hs2'].map(category_map)

# Plot
plt.figure(figsize=(14,6))
ax = sns.barplot(
    data=code_trade,
    x='cmdCode', y='primaryValue',
    hue='category',
    dodge=False,
    palette=category_colors
)

for container in ax.containers:
    ax.bar_label(
        container,
        labels=[f"{v/1e9:.1f}B" for v in container.datavalues],
        label_type='edge',
        padding=3,
        fontsize=11
    )

plt.title('Total Trade Value by HS4 Code (Colored by HS2 Category)')
plt.xlabel('HS4 Commodity Code')
plt.ylabel('Total Trade Value (USD)')
plt.xticks(rotation=90)
plt.grid(axis='y', linestyle='--', alpha=0.7)
plt.legend(title='Category')
plt.tight_layout()
plt.show()

df[df['primaryValue']].sort()"""

##print(df["cmdCode"].value_counts())
