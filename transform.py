import numpy as np
import pandas as pd
from datetime import datetime
from pathlib import Path
# if you want a clean csv uncomment these and run this file with the extracted csv in the same folder
#DATA_DIR = Path(__file__).resolve().parent
#RAW_CSV = DATA_DIR / "earthquakes_2006_present.csv"
#CLEAN_CSV = DATA_DIR / "earthquakes_clean.csv"
################################### cleaning functions #######################################


def standard_Letters(df, col):
    '''standardize txt strip spaces make lowercase takes in df and the column you clean'''
    df = df.copy()
    df.columns = df.columns.str.strip().str.lower()
    for c in col:
        df[c] =(df[c].astype("string")
                .str.strip()
                .str.lower()
                .str.replace(r"\s+", " ", regex=True)
                .replace("", pd.NA))
    return df

def convert_Datetime(df):
    '''convert time and updated to date time if updated is missing fill in with time'''
    df = df.copy()
    for c in ("time","updated"):
        df[c] = pd.to_datetime(df[c], utc=True, errors="coerce")
    df["updated"] = df["updated"].fillna(df["time"])
    return df

def convert_Numeric(df):
    '''convert columns to numeric''' 
    df = df.copy()
    for c in ("mag","longitude","latitude","depth_km"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df

def fill_missing(df,col):
    '''takes df and column name replaces all na with str unknown'''
    df = df.copy()
    for c in col:
        df[c] = df[c].fillna("unknown")
    return df

def drop_dupes(df):
    '''drops all duplicates keeps just the most recent row for each id'''
    df = df.copy()
    df = df.sort_values("updated",na_position="first",kind="mergesort")
    df = df.drop_duplicates(subset=["id"], keep="last").reset_index(drop=True)
    return df

def range_check(df):
    '''turns all impossible values as nan make sure to do before fill missing'''
    df = df.copy()
    df.loc[~df["latitude"].between(-90, 90), "latitude"] = np.nan
    df.loc[~df["longitude"].between(-180, 180), "longitude"] = np.nan
    df.loc[~df["mag"].between(-2, 10), "mag"] = np.nan
    return df

def drop_reqs(df,col=("id", "time", "latitude", "longitude")):
    '''drop rows with missing data we need'''
    df = df.copy()
    df = df.dropna(subset= list(col))
    df = df.reset_index(drop=True)
    return df

def add_time(df):
    '''adds precise time columns'''
    df = df.copy()
    df["year"] = df["time"].dt.year
    df["month"] = df["time"].dt.month
    df["event_date"] = df["time"].dt.date
    return df

def filter_earth(df):
    '''drop all the rows taht arent earthquakes'''
    df = df.copy()
    df = df[df["type"].isin(["earthquake"])]
    return df.reset_index(drop= True)

def add_region(df):
    '''extract a usable region out of the txt in place column(txt after ,)'''
    df = df.copy()
    region = df["place"].astype("string").str.split(",").str[-1].str.strip()
    df["region"] = region.replace("", pd.NA).fillna("unknown")
    return df

def fill_depth(df):
    '''fill missing depths with region medians'''
    df = df.copy()
    df["depth_imputed"] = df["depth_km"].isna()
    regional = df.groupby("region")["depth_km"].transform("median")
    df["depth_km"] = df["depth_km"].fillna(regional)
    df["depth_km"] = df["depth_km"].fillna(df["depth_km"].median())
    return df



################################# cleaning stuff ######################################
text_cols = ["magtype", "type", "status"]
label_cols = ["magtype", "type", "status", "place"]
def clean(df):
    stats = {"received": len(df)}

    #standarize stuff fix type
    df = standard_Letters(df, text_cols)
    df = convert_Datetime(df)
    df = convert_Numeric(df)
    df = range_check(df)

    #drop the columns count #
    n = len(df)
    df = drop_reqs(df)
    stats["dropped_required"] = n - len(df)

    n = len(df)
    df = drop_dupes(df)
    stats["duplicates"] = n - len(df)

    n = len(df)
    df = filter_earth(df)
    stats["non_earthquake"] = n - len(df)


    #fill in missing stuff
    for c in label_cols:
        stats["missing_" + c] = int(df[c].isna().sum())
    stats["missing_mag"] = int(df["mag"].isna().sum())

    df = fill_missing(df, label_cols)
    df = add_region(df)

    stats["missing_depth_km"] = int(df["depth_km"].isna().sum())
    df = fill_depth(df)

    df = add_time(df)

    

    stats["after_cleaning"] = len(df)
    return df, stats


missing_treatment = {
    "magtype":  "filled with 'unknown'",
    "type":     "filled with 'unknown'",
    "status":   "filled with 'unknown'",
    "place":    "filled with 'unknown'",
    "mag":      "left null",
    "depth_km": "filled with regional median",
}
#####################################################  print the summary  ##################################

def print_summary(stats, since=None):
    '''Print the data quality summary required for every ETL run.'''
    window = f" (incremental since {since})" if since else " (full pull)"

    print(f"\nETL run {datetime.now():%Y-%m-%d %H:%M}")
    print(f"received from API: {stats['received']:,} rows{window}")
    print(f"after cleaning: {stats['after_cleaning']:,} rows, "
          f"{stats['duplicates']:,} duplicates removed")

    parts = []
    for c, how in missing_treatment.items():
        n = stats.get("missing_" + c, 0)
        parts.append(f"{c} {n:,} {how}")
    print("missing values: " + ", ".join(parts))

    print(f"rows dropped: {stats['dropped_required']:,} missing id/time/coords, "
          f"{stats['non_earthquake']:,} non-earthquake events")

######################################  main function #########################################
if __name__ == "__main__":
    raw = pd.read_csv("earthquakes_2006_present.csv", dtype=str)
    cleaned, stats = clean(raw)
    print_summary(stats)
    cleaned.to_csv("earthquakes_clean.csv", index=False)