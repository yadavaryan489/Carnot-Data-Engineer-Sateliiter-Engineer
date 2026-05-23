# Carnot Data Engineer Assignment – Satellite Intelligence

**Author:** Aryan Yadav
**Stack:** Python 3, Pandas, NumPy

**On AI tool usage:** I used Claude to sanity-check a couple of my cleaning decisions mid-way (specifically whether inner join was the right call for orphan parcels, and to verify my date format cascade logic). The audit, decisions, code structure, and writeup are my own.

---

## How to run

```bash
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python Pipeline/pipeline.py
```

The data files live under `src_data/` and the pipeline writes cleaned outputs to `output_data/`.

## GitHub Actions

This repository includes a GitHub Actions workflow at `.github/workflows/python-app.yml`.
On every push or pull request, GitHub will:

- install Python 3.11
- install `pandas` and `numpy`
- run `python Pipeline/pipeline.py`
- verify generated files in `output_data/`

To use this repo on GitHub:

1. Initialize git locally (if not already):
   ```bash
git init
git add .
git commit -m "Initial Carnot pipeline"
```
2. Add your GitHub remote and push:
   ```bash
git remote add origin https://github.com/<your-username>/<your-repo>.git
git branch -M main
git push -u origin main
```

---

## 1. Data Quality Audit

First thing I did was load both files and just look at them — value counts, nulls, ranges, dtypes. Most issues were in the readings file.

### parcel_readings.csv (3,447 rows raw)

**Mixed date formats**
Three formats coexist in the date column: `YYYY-MM-DD`, `DD/MM/YYYY`, and `DD-Mon-YYYY` (e.g. `20-Jan-2026`). Affects roughly 30% of rows. I repaired this with a format-cascade parser rather than dropping — no data needs to be lost here, you just have to be explicit about which formats you're handling.

**NDVI out of range** — 104 rows (3%)
NDVI is physically bounded to [-1, 1]. Every single out-of-range row also had an `Error` sensor status, which confirmed these weren't legitimate extreme values — the sensor was broken. I dropped these rows. Imputing a broken sensor reading doesn't make sense when you don't know what the real value was.

**sensor_status inconsistency** — ~540 rows (15.7%)
The column had: `OK`, `ok`, ` OK`, `Error`, `ERROR`, `error`, `NaN`, `NA`, and blank strings. I normalised everything with `str.strip().str.upper()` so it collapses to `OK` / `ERROR`. Null-ish tokens (`NaN`, `NA`, blank) became `BAD_SENSOR` — a distinct label meaning "we don't know", which is different from a confirmed error. I also added a `sensor_ok` boolean column for clean downstream filtering.

**Orphan parcel IDs** — 40 rows
`PARCEL_098` and `PARCEL_099` exist in the readings but have no metadata. Without crop type and sowing date, these rows are useless for any analysis. I let the inner join handle this rather than adding a separate filter — the intent is clearer that way.

**Duplicate (parcel_id, date) pairs** — 8 rows
A small number of rows share the same parcel and date. There's no version column to figure out which is "correct", so I kept the first occurrence and dropped the rest.

**temperature_c and rainfall_mm**
No nulls, no negative values, no issues found. Left as-is.

---

### parcel_metadata.csv (28 rows raw)

**Three parcels with no readings**
`PARCEL_050`, `PARCEL_051`, `PARCEL_052` appear in metadata but have zero matching readings. The inner join drops them naturally — flagging here just for visibility.

**sowing_date stored as string**
Cast to `datetime64` at ingest. No data issues, just a type thing.

**Everything else**
No nulls, no implausible area values, crop types are consistent. Clean file overall.

---

## 2. Pipeline

The script is intentionally flat. Five functions, each doing one thing:

- `load_data` — reads both CSVs
- `clean_readings` — fixes sensor_status, parses dates, drops bad NDVI rows, deduplicates
- `clean_metadata` — parses sowing_date, lowercases crop_type
- `join_datasets` — inner join on parcel_id
- `analyse_ndvi_around_sowing` — computes the before/after NDVI windows

The output is one row per `(parcel_id × date)` with all metadata columns appended. The `sensor_ok` boolean stays in the output so any downstream consumer can filter without re-deriving it.

**Final output:** 3,295 rows × 11 columns

---

## 3. Analysis — NDVI before vs after sowing

Only `sensor_ok == True` rows were used. A parcel was only counted if it had at least one reading in *both* windows — including parcels with data in only one window would skew the aggregate mean.

| crop_type | mean_ndvi_before | mean_ndvi_after | n_parcels |
|-----------|-----------------|----------------|-----------|
| soybean   | 0.1706          | 0.3126         | 4         |
| sugarcane | 0.1775          | 0.3361         | 19        |
| wheat     | 0.1761          | 0.3101         | 2         |

All three crop types show a jump of roughly 0.14–0.16 NDVI units after sowing, which makes sense — soil that's been tilled or is lying bare has low greenness, and early crop emergence pushes that up quickly. Sugarcane shows the highest post-sowing NDVI (0.34), which tracks with how fast it establishes a canopy compared to wheat or soybean. The wheat result is based on only 2 parcels so I wouldn't read too much into it specifically, but the direction is consistent with the others.

---

## 4. Production Readiness Reflection

### Three things I'd change at 100× scale

**1. Switch from CSV to partitioned Parquet on cloud storage**
Reading a flat CSV at 100× volume is slow and wasteful — you scan everything even when you only need the last day. Partitioning by date (e.g. `year=2026/month=05/day=21/`) means daily incremental runs only touch new partitions. Columnar format also compresses float columns like NDVI and temperature much better. On our GCP stack I'd land this in GCS and process it via Dataproc or BigQuery depending on query pattern.

**2. Add schema validation at ingest, not silent coercion**
The multi-format date issue only exists because the upstream system is inconsistent. In a daily pipeline, silently fixing this is actually risky — if the format changes again, the cascade might start returning NaT and rows will disappear without anyone noticing. I'd put a Great Expectations check at the ingest boundary that rejects malformed records to a quarantine table and fires an alert. Fix it at the source, not midway through the pipeline.

**3. Separate incremental ingest from aggregation**
Right now the script processes everything end to end. In production these should be two separate jobs: one that picks up only new daily partitions and appends them, and a separate one that recomputes rolling windows and aggregations. They run on different cadences and shouldn't block each other. I'd schedule both on Cloud Scheduler and orchestrate with Airflow if the dependency graph gets more complex.

---

### What I'd monitor

- **Row count per daily batch** — a sudden drop is the first sign of an upstream feed failure or sensor outage
- **BAD_SENSOR rate per parcel** — if one parcel's error rate starts climbing, it usually means hardware is degrading before it fully fails
- **NDVI distribution shift** — alert if any parcel's daily NDVI deviates more than 2σ from its 30-day rolling mean; catches both sensor faults and real field events that need agronomist review
- **Join yield / orphan rate** — if new parcels get registered in the field system before metadata is synced, this spikes and the pipeline quietly drops real data
- **Pipeline SLA** — if the cleaned output isn't available by a set time each morning, something upstream is stuck

---

### Most likely thing to silently break

The date parsing. If the upstream export format changes — new locale, Unix timestamps, different separator — the format cascade will start returning NaT, rows will be silently dropped, and the daily row count might only dip slightly. Not enough to trigger a coarse count alert, especially if it's a minority of parcels. The fix is a dedicated `unparseable_date_count` metric that alerts at any non-zero value, combined with schema validation at ingest so the problem surfaces at the boundary rather than mid-pipeline.
