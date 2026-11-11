"""
Tables 
    regions      (region_id PK, region_name UNIQUE)
    mag_types    (magtype PK)
    earthquakes  (id PK, region_id FK, magtype FK, ...)
    etl_state    (key PK, value)
"""
import os

import pandas as pd
import psycopg2
from dotenv import load_dotenv
from psycopg2.extras import execute_values

load_dotenv()

def get_connection():
    '''open a connection using the variables in .env'''
    return psycopg2.connect(
        host=os.environ["POSTGRES_HOST"],
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ["POSTGRES_DB"],
        user=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
    )

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS regions (
    region_id    SERIAL PRIMARY KEY,
    region_name  VARCHAR(200) NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS mag_types (
    magtype      VARCHAR(20) PRIMARY KEY
);

CREATE TABLE IF NOT EXISTS earthquakes (
    id             VARCHAR(30) PRIMARY KEY,
    event_time     TIMESTAMPTZ NOT NULL,
    updated        TIMESTAMPTZ NOT NULL,
    mag            NUMERIC(4, 2),
    magtype        VARCHAR(20) NOT NULL REFERENCES mag_types (magtype),
    place          VARCHAR(300) NOT NULL,
    region_id      INTEGER NOT NULL REFERENCES regions (region_id),
    latitude       NUMERIC(9, 6) NOT NULL,
    longitude      NUMERIC(9, 6) NOT NULL,
    depth_km       NUMERIC(8, 3),
    depth_imputed  BOOLEAN NOT NULL DEFAULT FALSE,
    status         VARCHAR(20) NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_eq_time   ON earthquakes (event_time);
CREATE INDEX IF NOT EXISTS idx_eq_region ON earthquakes (region_id);

CREATE TABLE IF NOT EXISTS etl_state (
    key    VARCHAR(50) PRIMARY KEY,
    value  TIMESTAMPTZ NOT NULL
);
"""


def create_tables(conn):
    '''create all tables if they do not exist yet'''
    with conn.cursor() as cur:
        cur.execute(SCHEMA_SQL)
    conn.commit()

def _count(rows):
    '''rows come back as (..., was_inserted). returns (inserted, updated)'''
    inserted = sum(1 for r in rows if r[-1])
    return inserted, len(rows) - inserted


def _to_python(df):
    '''pandas NA / NaN / NaT -> None so psycopg2 writes NULL'''
    return df.astype(object).where(df.notna(), None)

def load_regions(conn, df):
    '''upsert distinct regions, return ({name: region_id}, inserted, updated)'''
    names = [(n,) for n in df["region"].dropna().unique()]
    with conn.cursor() as cur:
        rows = execute_values(
            cur,
            """INSERT INTO regions (region_name) VALUES %s
               ON CONFLICT (region_name) DO UPDATE
                   SET region_name = EXCLUDED.region_name
               RETURNING region_id, region_name, (xmax = 0)""",
            names, fetch=True)
    mapping = {name: rid for rid, name, _ in rows}
    ins, upd = _count(rows)
    return mapping, ins, upd


def load_mag_types(conn, df):
    '''upsert distinct magnitude types'''
    types = [(t,) for t in df["magtype"].dropna().unique()]
    with conn.cursor() as cur:
        rows = execute_values(
            cur,
            """INSERT INTO mag_types (magtype) VALUES %s
               ON CONFLICT (magtype) DO UPDATE SET magtype = EXCLUDED.magtype
               RETURNING magtype, (xmax = 0)""",
            types, fetch=True)
    return _count(rows)


def load_earthquakes(conn, df, region_ids):
    '''upsert earthquakes. existing ids are updated, new ids are inserted'''
    out = pd.DataFrame({
        "id": df["id"],
        "event_time": df["time"],
        "updated": df["updated"],
        "mag": df["mag"],
        "magtype": df["magtype"],
        "place": df["place"],
        "region_id": df["region"].map(region_ids),
        "latitude": df["latitude"],
        "longitude": df["longitude"],
        "depth_km": df["depth_km"],
        "depth_imputed": df["depth_imputed"],
        "status": df["status"],
    })
    out = _to_python(out)
    data = list(out.itertuples(index=False, name=None))

    sql = """INSERT INTO earthquakes
                 (id, event_time, updated, mag, magtype, place, region_id,
                  latitude, longitude, depth_km, depth_imputed, status)
             VALUES %s
             ON CONFLICT (id) DO UPDATE SET
                 event_time    = EXCLUDED.event_time,
                 updated       = EXCLUDED.updated,
                 mag           = EXCLUDED.mag,
                 magtype       = EXCLUDED.magtype,
                 place         = EXCLUDED.place,
                 region_id     = EXCLUDED.region_id,
                 latitude      = EXCLUDED.latitude,
                 longitude     = EXCLUDED.longitude,
                 depth_km      = EXCLUDED.depth_km,
                 depth_imputed = EXCLUDED.depth_imputed,
                 status        = EXCLUDED.status
             RETURNING (xmax = 0)"""
    inserted = updated = 0
    with conn.cursor() as cur:
        for i in range(0, len(data), 5000):          # batches of 5000
            rows = execute_values(cur, sql, data[i:i + 5000], fetch=True)
            ins, upd = _count(rows)
            inserted += ins
            updated += upd
    return inserted, updated

def get_last_updated(conn):
    '''latest "updated" timestamp already loaded, or None on the first run.
    extract.py can pass this to the USGS API as the updatedafter parameter.'''
    with conn.cursor() as cur:
        cur.execute("SELECT value FROM etl_state WHERE key = 'last_updated'")
        row = cur.fetchone()
    return row[0] if row else None


def save_last_updated(conn, ts):
    '''store the watermark (only moves forward)'''
    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO etl_state (key, value) VALUES ('last_updated', %s)
               ON CONFLICT (key) DO UPDATE
                   SET value = GREATEST(etl_state.value, EXCLUDED.value)""",
            (ts,))

def load(df):
    '''load a cleaned dataframe (output of transform.clean). returns counts.
    Everything runs in one transaction, so a failure leaves no half-load.'''
    counts = {}
    conn = get_connection()
    try:
        create_tables(conn)
        if df.empty:
            counts.update(regions=(0, 0), mag_types=(0, 0), earthquakes=(0, 0))
        else:
            region_ids, r_ins, r_upd = load_regions(conn, df)
            counts["regions"] = (r_ins, r_upd)
            counts["mag_types"] = load_mag_types(conn, df)
            counts["earthquakes"] = load_earthquakes(conn, df, region_ids)
            save_last_updated(conn, df["updated"].max().to_pydatetime())
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return counts


def print_load_summary(counts):
    '''the "rows inserted and updated in each table" lines of the summary'''
    for table, (ins, upd) in counts.items():
        print(f"{table}: {ins:,} inserted, {upd:,} updated")


if __name__ == "__main__":
    cleaned = pd.read_csv("earthquakes_clean.csv",
                          parse_dates=["time", "updated"],
                          keep_default_na=False, na_values=[""])
    cleaned["time"] = pd.to_datetime(cleaned["time"], utc=True)
    cleaned["updated"] = pd.to_datetime(cleaned["updated"], utc=True)
    print_load_summary(load(cleaned))
