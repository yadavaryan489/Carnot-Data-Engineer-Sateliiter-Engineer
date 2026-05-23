"""
Carnot Data Engineer Assignment – Satellite Intelligence
Author: Aryan Yadav
"""

import pandas as pd
import numpy as np

READINGS_PATH = "parcel_readings.csv"
METADATA_PATH = "parcel_metadata.csv"
OUTPUT_PATH   = "cleaned_parcel_timeseries.csv"


# try a few date formats since the readings file had mixed formats
def parse_date(raw):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%b-%Y", "%d-%m-%Y"):
        try:
            return pd.to_datetime(raw, format=fmt)
        except:
            pass
    return pd.NaT


def clean_readings(df):
    df = df.copy()

    # normalise sensor_status — lots of variants like 'ok', 'ERROR', blanks, NaN
    df["sensor_status"] = df["sensor_status"].str.strip().str.upper()
    df["sensor_status"] = df["sensor_status"].replace({"NAN": None, "NA": None})
    df["sensor_status"] = df["sensor_status"].fillna("BAD_SENSOR")
    df["sensor_ok"] = df["sensor_status"] == "OK"

    # parse dates
    df["date"] = df["date"].apply(parse_date)
    df = df.dropna(subset=["date"])

    # drop NDVI values outside valid range [-1, 1]
    invalid = (~df["ndvi_value"].between(-1, 1)).sum()
    df = df[df["ndvi_value"].between(-1, 1)]
    print(f"dropped {invalid} rows with invalid NDVI")

    # remove duplicate parcel+date rows, keep first
    dupes = df.duplicated(subset=["parcel_id", "date"]).sum()
    df = df.drop_duplicates(subset=["parcel_id", "date"], keep="first")
    print(f"dropped {dupes} duplicate rows")

    return df


def clean_metadata(df):
    df = df.copy()
    df["sowing_date"] = pd.to_datetime(df["sowing_date"])
    df["crop_type"] = df["crop_type"].str.strip().str.lower()
    return df


def join_datasets(readings, metadata):
    # inner join drops parcels that don't have metadata (and vice versa)
    df = readings.merge(metadata, on="parcel_id", how="inner")
    print(f"joined dataset: {df.shape}")
    return df


def analyse_ndvi_around_sowing(df):
    # only use rows where sensor was working fine
    df = df[df["sensor_ok"]].copy()
    results = []

    for crop, grp in df.groupby("crop_type"):
        ndvi_before = []
        ndvi_after  = []
        n_parcels   = 0

        for pid, p in grp.groupby("parcel_id"):
            sow = p["sowing_date"].iloc[0]

            # readings in the 30 days before and after sowing
            before = p[(p["date"] >= sow - pd.Timedelta(days=30)) & (p["date"] < sow)]["ndvi_value"]
            after  = p[(p["date"] >  sow) & (p["date"] <= sow + pd.Timedelta(days=30))]["ndvi_value"]

            # only count this parcel if it has data in both windows
            if len(before) > 0 and len(after) > 0:
                ndvi_before.extend(before.tolist())
                ndvi_after.extend(after.tolist())
                n_parcels += 1

        results.append({
            "crop_type":        crop,
            "mean_ndvi_before": round(np.mean(ndvi_before), 4) if ndvi_before else None,
            "mean_ndvi_after":  round(np.mean(ndvi_after),  4) if ndvi_after  else None,
            "n_parcels":        n_parcels,
        })

    return pd.DataFrame(results).sort_values("crop_type").reset_index(drop=True)


def main():
    readings = pd.read_csv(READINGS_PATH)
    metadata = pd.read_csv(METADATA_PATH)
    print(f"loaded — readings: {readings.shape} | metadata: {metadata.shape}")

    readings = clean_readings(readings)
    metadata = clean_metadata(metadata)

    timeseries = join_datasets(readings, metadata)
    timeseries.to_csv(OUTPUT_PATH, index=False)
    print(f"written to {OUTPUT_PATH} — {len(timeseries)} rows")

    result = analyse_ndvi_around_sowing(timeseries)
    print("\nNDVI before vs after sowing:")
    print(result.to_string(index=False))


if __name__ == "__main__":
    main()
