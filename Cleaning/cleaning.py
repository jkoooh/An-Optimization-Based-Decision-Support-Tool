from __future__ import annotations

"""Build HS4 washing matrix from grouped commodity sheets."""

import argparse
from collections import OrderedDict
from pathlib import Path

import pandas as pd


def normalize_hs4(value) -> str | None:
    """Normalize one HS value to a 4-digit code, or None if empty."""
    if pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", ".")
    if text.endswith(".0"):
        text = text[:-2]
    if "." in text:
        text = text.split(".", 1)[0]
    if not text:
        return None
    return text.zfill(4)


def load_group_codes(df_cmds: pd.DataFrame) -> OrderedDict[str, list[str]]:
    """Read HS4 codes by group from the cmds sheet."""
    group_to_codes: OrderedDict[str, list[str]] = OrderedDict()
    seen: set[str] = set()
    duplicates: list[str] = []

    for group in df_cmds.columns:
        codes: list[str] = []
        for raw in df_cmds[group]:
            code = normalize_hs4(raw)
            if code is None:
                continue
            if code in seen:
                duplicates.append(code)
                continue
            seen.add(code)
            codes.append(code)
        group_to_codes[str(group).strip()] = codes

    if duplicates:
        duplicate_sample = ", ".join(sorted(set(duplicates))[:10])
        print(f"Warning: Ignoring duplicate HS4 codes across groups: {duplicate_sample}")

    return group_to_codes


def load_group_matrix(df_matrix: pd.DataFrame, groups: list[str]) -> pd.DataFrame:
    """Load and validate the group-level wash matrix."""
    first_col = df_matrix.columns[0]
    matrix = df_matrix.rename(columns={first_col: "__group__"}).copy()
    matrix["__group__"] = matrix["__group__"].astype(str).str.strip()
    matrix = matrix.set_index("__group__")
    matrix.columns = [str(c).strip() for c in matrix.columns]

    missing_rows = [g for g in groups if g not in matrix.index]
    missing_cols = [g for g in groups if g not in matrix.columns]
    if missing_rows or missing_cols:
        raise ValueError(
            "Missing group names in Matrix sheet. "
            f"missing_rows={missing_rows}, missing_cols={missing_cols}"
        )

    matrix = matrix.loc[groups, groups]
    return matrix.apply(pd.to_numeric, errors="coerce")


def build_hs4_matrix(
    xlsx_path: Path,
    cmds_sheet: str,
    matrix_sheet: str,
) -> pd.DataFrame:
    """Expand group-level matrix to full HS4 x HS4 matrix."""
    df_cmds = pd.read_excel(xlsx_path, sheet_name=cmds_sheet)
    df_matrix = pd.read_excel(xlsx_path, sheet_name=matrix_sheet)

    group_to_codes = load_group_codes(df_cmds)
    groups = list(group_to_codes.keys())
    group_matrix = load_group_matrix(df_matrix, groups)

    all_codes = [code for g in groups for code in group_to_codes[g]]
    out = pd.DataFrame(index=all_codes, columns=all_codes, dtype=float)

    for g_from in groups:
        codes_from = group_to_codes[g_from]
        for g_to in groups:
            codes_to = group_to_codes[g_to]
            value = group_matrix.loc[g_from, g_to]
            for hs_from in codes_from:
                for hs_to in codes_to:
                    out.loc[hs_from, hs_to] = value

    return out


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description=(
            "Build HS4 wash matrix from cmd_groups.xlsx (sheets: cmds + Matrix). "
            "Output uses same CSV structure as hs4_wash_matrix_days.csv."
        )
    )
    parser.add_argument(
        "--input",
        default="Data/cmd_groups.xlsx",
        help="Input Excel file with sheets cmds and Matrix.",
    )
    parser.add_argument(
        "--cmds-sheet",
        default="cmds",
        help="Sheet name with HS4 codes grouped in columns.",
    )
    parser.add_argument(
        "--matrix-sheet",
        default="Matrix",
        help="Sheet name with group matrix (days).",
    )
    parser.add_argument(
        "--output",
        default="Data/hs4_wash_matrix_days_from_cmd_groups.csv",
        help="Output CSV path.",
    )
    return parser.parse_args()


def main() -> None:
    """Build matrix and write CSV output."""
    args = parse_args()
    input_path = Path(args.input)
    output_path = Path(args.output)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    hs4_matrix = build_hs4_matrix(
        xlsx_path=input_path,
        cmds_sheet=args.cmds_sheet,
        matrix_sheet=args.matrix_sheet,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    hs4_matrix.to_csv(output_path)
    print(f"Built matrix with shape={hs4_matrix.shape} -> {output_path}")


if __name__ == "__main__":
    main()
