"""
Carnot Data Engineer Assignment – Satellite Intelligence
Author: Aryan Yadav

Quick note on approach: I kept this as a single flat script intentionally.
No unnecessary abstraction — each function does one thing, the main() just
wires them together in order. Easy to read, easy to debug, easy to extend.
"""

import pandas as pd
import numpy as np



READINGS_PATH = "parcel_readings.csv"
METADATA_PATH = "parcel_metadata.csv"
OUTPUT_PATH   = "cleaned_parcel_timeseries.csv"


# ------------------------------------------------------------------------------
# Load
# ------------------------------------------------------------------------------

def load_data(readings_path: str, metadata_path: str):
    readings = pd.read_csv(readings_path)
    metadata = pd.read_csv(metadata_path)
    print(f"[load] readings: {readings.shape} | metadata: {metadata.shape}")
    return readings, metadata


# ------------------------------------------------------------------------------
# Date parsing
#
# Three formats were floating around in the readings file: YYYY-MM-DD,
# DD/MM/YYYY, and DD-Mon-YYYY (e.g. 20-Jan-2026). pd.to_datetime with
# infer_datetime_format handles some of these but misfires on the mixed
# cases, so I wrote a small cascade that tries formats in order and
# returns NaT if nothing matches. Keeps it explicit.
# ------------------------------------------------------------------------------

def _parse_date(raw: str) -> pd.Timestamp:
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d-%m-%Y"):
        try:
            return pd.to_datetime(raw, format=fmt)
        except (ValueError, TypeError):
            pass
    return pd.NaT


# ------------------------------------------------------------------------------
# Clean readings
# ------------------------------------------------------------------------------

def clean_readings(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    # sensor_status has a real mess of variants: 'ok', 'OK', ' OK', 'Error',
    # 'ERROR', 'error', 'NaN', 'NA', and plain blanks.
    # Strip whitespace first, then uppercase so everything collapses to two
    # meaningful values. Null-ish tokens get mapped to BAD_SENSOR so I have
    # a single consistent flag for "we don't actually know what the sensor did".
    df["sensor_status"] = df["sensor_status"].str.strip().str.upper()
    df["sensor_status"] = df["sensor_status"].replace({"NAN": None, "NA": None})
    df["sensor_status"] = df["sensor_status"].fillna("BAD_SENSOR")

    # Convenience column — downstream filtering becomes just df[df.sensor_ok]
    df["sensor_ok"] = df["sensor_status"] == "OK"

    # Parse dates with the cascade above, drop anything that still won't parse
    df["date"] = df["date"].apply(_parse_date)
    unparseable = df["date"].isnull().sum()
    if unparseable:
        print(f"[warn] {unparseable} rows with unparseable dates dropped")
        df = df.dropna(subset=["date"])

    # NDVI is physically bounded to [-1, 1]. Every row that breaks this also
    # happens to carry an Error sensor status, which tells me the sensor was
    # malfunctioning rather than reporting a genuine extreme value. Dropping
    # is cleaner than imputing here — you can't recover a broken sensor reading.
    invalid_ndvi = (~df["ndvi_value"].between(-1, 1)).sum()
    df = df[df["ndvi_value"].between(-1, 1)]
    print(f"[clean] dropped {invalid_ndvi} out-of-range NDVI rows")

    # A handful of (parcel_id, date) pairs appear more than once. No version
    # column to disambiguate, so I keep the first occurrence and move on.
    dupes = df.duplicated(subset=["parcel_id", "date"]).sum()
    df = df.drop_duplicates(subset=["parcel_id", "date"], keep="first")
    print(f"[clean] dropped {dupes} duplicate (parcel_id, date) rows")

    print(f"[clean] readings after cleaning: {len(df)}")
    return df


# ------------------------------------------------------------------------------
# Clean metadata
# ------------------------------------------------------------------------------

def clean_metadata(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["sowing_date"] = pd.to_datetime(df["sowing_date"])
    # Lowercase crop_type so 'Sugarcane' and 'sugarcane' don't split in groupbys
    df["crop_type"] = df["crop_type"].str.strip().str.lower()
    print(f"[clean] metadata: {len(df)} parcels")
    return df


# ------------------------------------------------------------------------------
# Join
#
# Inner join is the right call here. PARCEL_098 and PARCEL_099 have readings
# but no metadata — without crop_type and sowing_date they're useless for
# the analysis. The three metadata parcels with no readings (050, 051, 052)
# disappear from the output for the same reason. Inner join communicates
# that intent clearly without any extra filtering code.
# ------------------------------------------------------------------------------

def join_datasets(readings: pd.DataFrame, metadata: pd.DataFrame) -> pd.DataFrame:
    df = readings.merge(metadata, on="parcel_id", how="inner")
    dropped = len(readings) - len(df)
    print(f"[join] shape after join: {df.shape} ({dropped} orphan reading rows dropped)")
    return df


# ------------------------------------------------------------------------------
# Analysis — NDVI 30 days before vs after sowing per crop type
#
# Only using sensor_ok rows. For each parcel I compute the mean NDVI in the
# 30-day window before sowing and the 30-day window after. I only include a
# parcel in the aggregate if it has at least one reading in *both* windows —
# a parcel with only post-sowing data would skew the before mean if included.
# ------------------------------------------------------------------------------

def analyse_ndvi_around_sowing(df: pd.DataFrame) -> pd.DataFrame:
    df_good = df[df["sensor_ok"]].copy()
    results = []

    for crop, grp in df_good.groupby("crop_type"):
        ndvi_before, ndvi_after = [], []
        n_parcels = 0

        for pid, p in grp.groupby("parcel_id"):
            sow = p["sowing_date"].iloc[0]

            before = p.loc[
                (p["date"] >= sow - pd.Timedelta(days=30)) & (p["date"] < sow),
                "ndvi_value",
            ]
            after = p.loc[
                (p["date"] > sow) & (p["date"] <= sow + pd.Timedelta(days=30)),
                "ndvi_value",
            ]

            if len(before) > 0 and len(after) > 0:
                ndvi_before.extend(before.tolist())
                ndvi_after.extend(after.tolist())
                n_parcels += 1

        results.append({
            "crop_type":        crop,
            "mean_ndvi_before": round(float(np.mean(ndvi_before)), 4) if ndvi_before else None,
            "mean_ndvi_after":  round(float(np.mean(ndvi_after)),  4) if ndvi_after  else None,
            "n_parcels":        n_parcels,
        })

    return pd.DataFrame(results).sort_values("crop_type").reset_index(drop=True)


# ------------------------------------------------------------------------------
# Main
# ------------------------------------------------------------------------------

def main():
    readings_raw, metadata_raw = load_data(READINGS_PATH, METADATA_PATH)

    readings_clean = clean_readings(readings_raw)
    metadata_clean = clean_metadata(metadata_raw)

    timeseries = join_datasets(readings_clean, metadata_clean)
    timeseries.to_csv(OUTPUT_PATH, index=False)
    print(f"\n[output] {OUTPUT_PATH} written — {len(timeseries)} rows\n")

    result = analyse_ndvi_around_sowing(timeseries)
    result.to_csv("output_data/ndvi_sowing_analysis.csv", index=False)
    print("── NDVI before vs after sowing ─────────────────────────────────────")
    print(result.to_string(index=False))

    print()

    return timeseries, result


if __name__ == "__main__":
    main()
