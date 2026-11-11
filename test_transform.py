import numpy as np
import pandas as pd

from transform import (standard_Letters, convert_Datetime, convert_Numeric,
                       fill_missing, drop_dupes, range_check, drop_reqs,
                       add_time, filter_earth, add_region, fill_depth)


def test_standard_Letters():
    '''Headers and text values get lowercased and trimmed.'''
    df = pd.DataFrame({" magType": [" Mww "], "TYPE": ["Quarry  Blast"]})
    out = standard_Letters(df, ["magtype", "type"])
    assert list(out.columns) == ["magtype", "type"]
    assert out["magtype"].iloc[0] == "mww"
    assert out["type"].iloc[0] == "quarry blast"


def test_convert_Datetime():
    '''Strings become UTC timestamps, junk coerces, null updated falls back to time.'''
    df = pd.DataFrame({
        "time": ["2006-01-01T00:03:17+00:00", "2006-01-02T00:00:00+00:00", "junk"],
        "updated": ["2014-11-07T01:27:59+00:00", None, None],
    })
    out = convert_Datetime(df)
    assert out["updated"].iloc[1] == out["time"].iloc[1]
    assert pd.isna(out["time"].iloc[2])


def test_convert_Numeric():
    '''Numbers parse, junk becomes NaN, negative depth survives.'''
    df = pd.DataFrame({"mag": ["4.5", "abc"], "longitude": ["-97.6", "0"],
                       "latitude": ["15.9", "0"], "depth_km": ["16", "-1.849"]})
    out = convert_Numeric(df)
    assert out["mag"].iloc[0] == 4.5
    assert pd.isna(out["mag"].iloc[1])
    assert out["depth_km"].iloc[1] == -1.849


def test_fill_missing():
    '''Nulls become the string unknown.'''
    df = pd.DataFrame({"magtype": [pd.NA, "mb"]})
    assert fill_missing(df, ["magtype"])["magtype"].tolist() == ["unknown", "mb"]


def test_drop_dupes():
    '''Same event twice keeps the newest revision.'''
    df = pd.DataFrame({
        "id": ["usp000e7an", "usp000e7an", "usp000e7ap"],
        "mag": [4.0, 4.3, 3.1],
        "updated": pd.to_datetime(["2014-11-07", "2025-12-22", "2014-11-07"], utc=True),
    })
    out = drop_dupes(df)
    assert len(out) == 2
    assert out.loc[out["id"] == "usp000e7an", "mag"].iloc[0] == 4.3


def test_range_check():
    '''Impossible coordinates and magnitudes become NaN.'''
    df = pd.DataFrame({"latitude": [15.9, 200.0], "longitude": [-97.6, -400.0],
                       "mag": [4.0, 99.0]})
    out = range_check(df)
    assert out["latitude"].iloc[0] == 15.9
    assert out[["latitude", "longitude", "mag"]].iloc[1].isna().all()


def test_drop_reqs():
    '''Rows without id, time, or coordinates are unusable.'''
    df = pd.DataFrame({
        "id": ["a", None, "c"],
        "time": pd.to_datetime(["2006-01-01", "2006-01-02", None], utc=True),
        "latitude": [15.9, 20.0, 30.0],
        "longitude": [-97.6, 0.0, 0.0],
    })
    assert drop_reqs(df)["id"].tolist() == ["a"]


def test_add_time():
    '''Year, month, and date columns for the seasonality question.'''
    df = pd.DataFrame({"time": pd.to_datetime(["2006-03-15T06:45:00+00:00"], utc=True)})
    out = add_time(df)
    assert out["year"].iloc[0] == 2006
    assert out["month"].iloc[0] == 3
    assert str(out["event_date"].iloc[0]) == "2006-03-15"


def test_filter_earth():
    '''Explosions and quarry blasts are not earthquakes.'''
    df = pd.DataFrame({"type": ["earthquake", "explosion", "quarry blast"]})
    assert filter_earth(df)["type"].tolist() == ["earthquake"]


def test_add_region():
    '''Region is the text after the last comma, or the whole string if none.'''
    df = pd.DataFrame({"place": ["8 km SSE of Santa Rosa de Lima, Mexico",
                                 "Fiji region", None]})
    assert add_region(df)["region"].tolist() == ["Mexico", "Fiji region", "unknown"]


def test_fill_depth():
    '''Missing depth uses its own region median, not the global one.'''
    df = pd.DataFrame({
        "region": ["CA", "CA", "CA", "Fiji", "Fiji", "Fiji"],
        "depth_km": [5.0, 7.0, np.nan, 500.0, 600.0, np.nan],
    })
    out = fill_depth(df)
    assert out["depth_km"].iloc[2] == 6.0      # CA median
    assert out["depth_km"].iloc[5] == 550.0    # Fiji median
    assert out["depth_imputed"].tolist() == [False, False, True, False, False, True]