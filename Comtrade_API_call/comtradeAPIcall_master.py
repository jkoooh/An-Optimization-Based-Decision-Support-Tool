"""Master script for Comtrade API downloads."""

import time
from typing import Iterable, List

import comtradeapicall
import pandas as pd
import pycountry
from IPython.display import display


sub_key = "XXXXXXXXXXXXXX"


# All UN-recognized numeric country codes.
valid_numeric = [c.numeric for c in pycountry.countries]


# Landlocked ISO3 codes removed.
landlocked_iso3 = [
    "AFG", "AND", "ARM", "AUT", "AZE", "BDI", "BFA", "BOL", "BWA", "CAF", "CHE", "CZE", "ETH", "HUN",
    "KAZ", "KGZ", "LAO", "LIE", "LUX", "MKD", "MLI", "MNG", "MWI", "NER", "NPL", "PRY", "RWA", "SMR",
    "SRB", "SVK", "SWZ", "TCD", "TJK", "UGA", "UZB", "VAT", "ZMB", "ZWE", "SSD", "LSO", "BLR", "BIH",
]

landlocked_numeric = [pycountry.countries.get(alpha_3=c).numeric for c in landlocked_iso3]
countries = [c for c in valid_numeric if c not in landlocked_numeric]
print(len(countries))
print(len(valid_numeric))


# Fixed reporter list used for this data pull.
countries_code = [
    8, 24, 32, 36, 44, 48, 50, 52, 56, 64,
    70, 76, 84, 90, 96, 100, 104, 112, 116, 120, 124, 132, 152,
    156, 170, 178, 180, 188, 191, 192, 196, 204, 208, 214, 218,
    222, 233, 242, 246, 251, 258, 266, 268, 275, 276, 288, 300,
    320, 328, 340, 344, 352, 360, 364, 372, 376, 380, 384, 388,
    392, 400, 404, 410, 414, 422, 428, 430, 434, 440, 446, 450,
    458, 462, 470, 478, 480, 484, 498, 499, 500, 504, 508, 512,
    516, 528, 531, 533, 554, 558, 566, 579, 586, 591, 604, 608,
    616, 620, 624, 634, 642, 643, 662, 670, 682, 686, 690, 694,
    699, 702, 704, 705, 710, 724, 729, 740, 752, 764, 768, 780,
    784, 788, 792, 804, 818, 826, 834, 842, 858, 882, 887,
]
print(len(countries_code))


# Read relevant HS4 codes from Excel.
path = "Data/HS4 codes.xlsx"
sheet = "Stays"
temp = pd.read_excel(path, sheet_name=sheet, engine="openpyxl")

cols = ["HS2", "HS4"]
temp[cols] = temp[cols].astype("string").replace(r"\.0$", "", regex=True)

# Normalize HS fields and build HS4 code string.
temp["HS2"] = temp["HS2"].astype("string").str.zfill(2)
temp["HS4"] = temp["HS4"].astype("string").str.zfill(2)
temp["HS"] = temp["HS2"].str.cat(temp["HS4"])

cmdCodes = temp["HS"].tolist()
cmdCodes = [c for c in cmdCodes if c]
print(cmdCodes)
display(temp)

print("HS-codes total:", len(cmdCodes))
print("HS-codes unique:", len(set(cmdCodes)))


def chunked(lst: List[str], n: int) -> Iterable[List[str]]:
    """Yield list chunks of size n."""
    for i in range(0, len(lst), n):
        yield lst[i : i + n]


def fetch_with_retries(reporter_code: str, cmd_code: str, max_retries: int = 10) -> pd.DataFrame:
    """Call Comtrade API with exponential backoff retries."""
    delay = 1.0
    last_err = None

    for attempt in range(max_retries):
        try:
            df = comtradeapicall.getFinalData(
                typeCode="C",
                freqCode="M",
                clCode="HS",
                period="202401",
                reporterCode=reporter_code,
                cmdCode=cmd_code,
                flowCode="X",
                partnerCode=None,
                partner2Code=None,
                customsCode=None,
                motCode=None,
                maxRecords=500000,
                format_output="JSON",
                aggregateBy=None,
                breakdownMode="classic",
                countOnly=None,
                includeDesc=True,
                subscription_key=sub_key,
            )

            # Surface API error payloads and keep return type as DataFrame.
            if isinstance(df, dict) and df.get("error"):
                raise ValueError(df.get("error"))
            if isinstance(df, pd.DataFrame):
                return df
            return pd.DataFrame(df)

        except Exception as e:
            last_err = e
            time.sleep(delay)
            delay = min(delay * 2, 60)

    raise last_err


def download_tradeflows(countries, hs4_codes: List[str], hs_batch_size: int = 10) -> pd.DataFrame:
    """Download trade flows with HS batching and country fallback batching."""
    frames = []
    reporter_all = ",".join(map(str, countries))

    # First attempt: all reporters together, batched on HS4 only.
    try:
        for hs_batch in chunked(hs4_codes, hs_batch_size):
            cmd_all = ",".join(hs_batch)
            df = fetch_with_retries(reporter_all, cmd_all)
            if not df.empty:
                frames.append(df)
        if frames:
            return pd.concat(frames, ignore_index=True)
        return pd.DataFrame()

    except Exception:
        # Fallback: batch on both reporters and HS4.
        frames = []
        for c in countries:
            reporter = str(c)
            for hs_batch in chunked(hs4_codes, hs_batch_size):
                cmd_all = ",".join(hs_batch)
                df = fetch_with_retries(reporter, cmd_all)
                if not df.empty:
                    frames.append(df)

        if frames:
            return pd.concat(frames, ignore_index=True)
        return pd.DataFrame()


# Run download and write result.
df_all = download_tradeflows(countries_code, cmdCodes, hs_batch_size=10)
path = "Data/HS4database202401.csv"
df_all.to_csv(path, index=False)
