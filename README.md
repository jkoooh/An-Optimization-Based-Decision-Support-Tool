# An Optimization-Based Decision-Support Tool

This repository contains code and data used in a master's thesis about vessel capacity utilization in maritime transport.

## Project Purpose

The goal of this project is to support planning decisions in maritime transport by using an optimization model.
The model helps evaluate routes, capacity use, and related results.

## Repository Structure

- `Cleaning/` - Scripts and files for data cleaning and preparation.
- `Comtrade_API_call/` - Code for retrieving trade-related input data.
- `Data/` - Input data used by the model.
- `Ports/` - Port-related data files.
- `Optimization/model_finished.py` - Main optimization model.
- `Optimization/Results.py` - Script for result analysis and reporting.
- `Optimization/Solutions/` - Saved solution files from model runs. Example runs are included.
- `routes_iter001.html` - Example route visualization output.

## Requirements

- Python 3.10+ (recommended)
- Active Gurobi liscence
- Exact package requirements depend on your local setup and solver license.

## Setup

1. Clone this repository.
2. Create and activate a virtual environment.
3. Install required packages.

## How to Run

Run the optimization model:

```bash
python Optimization/model_finished.py
```

Run result processing:

```bash
python Optimization/Results.py
```

## Outputs

Typical outputs are stored in:

- `Optimization/Solutions/` (CSV solution files)
- HTML route plots (for example `routes_iter001.html`)

## Reproducibility Notes

- Use the same Python version and solver version when possible.
- Keep input files in `Data/` and `Ports/` unchanged.
- Run `model_finished.py` before `Results.py`.

## Data

This repository includes project data used for analysis and model input.
If some original sources are external (for example APIs), see code in `Comtrade_API_call/`.

### Dataset Overview (Simple Description)

For each dataset below, the description follows this format:
- What it is
- Structure
- How it is used

#### `Data/HS4database2024.csv`
- What it is: Raw UN Comtrade trade data for 2024.
- Structure: Row-based table with many columns (for example reporter, partner, `cmdCode`, `netWgt`, `primaryValue`).
- How it is used: Raw source data before cleaning and filtering.

#### `Data/cleanedHS4database2024.csv`
- What it is: Cleaned trade dataset used by the optimization model.
- Structure: Row-based CSV with key columns like `reporterISO`, `partnerISO`, `cmdCode`, and trade volume/value fields.
- How it is used: Main trade input for building export/import pairs and bilateral trade limits.

#### `Data/cmd_groups.xlsx`
- What it is: Commodity master file with selected HS4 codes and grouping information.
- Structure: Excel file (sheet `cmdList`) with at least an `HS4` column (and grouping columns such as segment labels).
- How it is used: Defines which commodities are included in model runs and route plots.

#### `Data/cleaningMatrix.csv`
- What it is: Cargo hold cleaning compatibility matrix between commodity pairs.
- Structure: Wide matrix where both rows and columns are HS4 codes, and cell values represent cleaning effort/time.
- How it is used: Creates cleaning-time parameters and infeasible cargo transitions in the model.

#### `Data/commodity_rates_random_2024.csv`
- What it is: Commodity price/rate profiles over time.
- Structure: Wide daily table with `Day` as first column and HS4 codes as remaining columns.
- How it is used: Used by the model for time-dependent commodity rate values per leg.

#### `Data/port_handling_rates.xlsx`
- What it is: Port handling speed assumptions.
- Structure: Excel table with required columns `port_id`, `load_tpd`, `discharge_tpd`.
- How it is used: Sets loading and discharging rates at each port in the optimization model.

#### `Data/route_lengths.csv`
- What it is: Distance matrix between ports/clusters.
- Structure: Wide matrix with `cluster_id` as first column and destination ports as remaining columns.
- How it is used: Converted to route distance dictionary (`D_pq`) used in sailing-time and routing constraints.

#### `Data/valuePerTon.csv`
- What it is: Aggregated value and weight by commodity code.
- Structure: Row-based CSV with `cmdCode`, total value, and total net weight columns.
- How it is used: Support data for analysis/calibration of commodity economics.

#### `Data/clusters.pkl`
- What it is: Cached port cluster dataset.
- Structure: Pickle file containing a table with fields such as `Cluster_id` and `country`.
- How it is used: Maps ports to countries and supports route/port logic in model and results plotting.

#### `Data/routes.pkl`
- What it is: Cached route geometry between port pairs.
- Structure: Pickle dictionary keyed by `(export_port, import_port)` with route coordinates/paths.
- How it is used: Used by `Optimization/Results.py` to draw route maps (for example `routes_iter001.html`).

#### `Data/reporters_cache.pkl`
- What it is: Cached country-to-ISO metadata from Comtrade reporters reference.
- Structure: Pickle dictionary with country names and ISO3 tags.
- How it is used: Speeds up reporter-country lookup and avoids repeated API calls.

#### `Data/cache/route_lengths.pkl`
- What it is: Cached processed version of route lengths.
- Structure: Pickle dictionary keyed by `(origin_port, destination_port)` with numeric distances.
- How it is used: Performance cache so route distance data does not need to be rebuilt every run.

## Thesis Reference

Master's thesis title:

**An Optimization-Based Decision-Support Tool for Improving Vessel Capacity Utilization in Maritime Transport**
