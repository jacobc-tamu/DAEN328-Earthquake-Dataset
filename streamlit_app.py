import os
from datetime import datetime, time, timezone

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL

load_dotenv()
st.set_page_config(page_title="USGS Earthquake Dashboard", layout="wide")

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
FROM_SQL = "FROM earthquakes e JOIN regions r ON r.region_id = e.region_id"

@st.cache_resource
def get_engine():
    url = URL.create(
        "postgresql+psycopg2",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],
        host=os.environ["POSTGRES_HOST"],
        port=int(os.environ.get("POSTGRES_PORT", "5432")),
        database=os.environ["POSTGRES_DB"],
    )
    return create_engine(url, pool_pre_ping=True)


@st.cache_data(ttl=600, show_spinner=False)
def run_query(sql, params=None):
    '''run a SELECT and return a DataFrame (cached for 10 minutes)'''
    with get_engine().connect() as conn:
        return pd.read_sql(text(sql), conn, params=params or {})

st.title("USGS Earthquake Dashboard")

try:
    bounds = run_query("SELECT MIN(event_time)::date AS lo, "
                       "MAX(event_time)::date AS hi FROM earthquakes")
except Exception as exc:
    st.error(f"Could not read from the database: {exc}")
    st.stop()

if bounds.empty or pd.isna(bounds.loc[0, "lo"]):
    st.warning("The earthquakes table is empty. Run the ETL pipeline first.")
    st.stop()

lo, hi = bounds.loc[0, "lo"], bounds.loc[0, "hi"]

st.sidebar.header("Filters")
date_range = st.sidebar.date_input("Date range", (lo, hi),
                                   min_value=lo, max_value=hi)
if len(date_range) != 2:
    st.info("Pick an end date to continue.")
    st.stop()
start_date, end_date = date_range

min_mag = st.sidebar.slider("Minimum magnitude", -2.0, 9.0, -2.0, 0.1)

all_regions = run_query(
    "SELECT region_name FROM regions ORDER BY region_name")["region_name"].tolist()
chosen_regions = st.sidebar.multiselect(
    "Regions (leave empty for all)", all_regions)


def build_where():
    '''WHERE clause + bind parameters shared by every query'''
    clauses = ["e.event_time >= :start", "e.event_time < :end"]
    params = {
        "start": datetime.combine(start_date, time.min, tzinfo=timezone.utc),
        "end": datetime.combine(end_date, time.max, tzinfo=timezone.utc),
    }
    if min_mag > -2.0:
        clauses.append("e.mag >= :min_mag")
        params["min_mag"] = min_mag
    if chosen_regions:
        clauses.append("r.region_name = ANY(:regions)")
        params["regions"] = list(chosen_regions)
    return " AND ".join(clauses), params


WHERE, PARAMS = build_where()

kpi = run_query(
    f"SELECT COUNT(*) AS events, ROUND(AVG(e.mag), 2) AS avg_mag, "
    f"MAX(e.mag) AS max_mag, COUNT(DISTINCT r.region_id) AS regions "
    f"{FROM_SQL} WHERE {WHERE}", PARAMS).iloc[0]

if kpi["events"] == 0:
    st.warning("No earthquakes match these filters.")
    st.stop()

c1, c2, c3, c4 = st.columns(4)
c1.metric("Earthquakes", f"{int(kpi['events']):,}")
c2.metric("Average magnitude", f"{kpi['avg_mag']}")
c3.metric("Largest magnitude", f"{kpi['max_mag']}")
c4.metric("Regions", f"{int(kpi['regions']):,}")

tab1, tab2, tab3, tab4 = st.tabs([
    "1. Magnitude distribution", "2. Most / least active regions",
    "3. Earthquake seasons", "4. Browse events"])

# Question 1: Magnitude Skew
with tab1:
    st.subheader("Do earthquake magnitudes exhibit a left-skewed distribution?")
    width = st.selectbox("Histogram bin width", [0.1, 0.2, 0.5], index=1)

    hist = run_query(
        f"SELECT FLOOR(e.mag / :w) * :w AS bin, COUNT(*) AS events "
        f"{FROM_SQL} WHERE {WHERE} AND e.mag IS NOT NULL "
        f"GROUP BY 1 ORDER BY 1", {**PARAMS, "w": width})

    stats = run_query(
        f"""WITH f AS (SELECT e.mag::float8 AS mag {FROM_SQL}
                       WHERE {WHERE} AND e.mag IS NOT NULL),
                 s AS (SELECT COUNT(*) AS n, AVG(mag) AS m,
                              STDDEV_POP(mag) AS sd,
                              PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY mag) AS med
                       FROM f)
            SELECT s.n, s.m, s.sd, s.med,
                   AVG(POWER(f.mag - s.m, 3)) / NULLIF(POWER(s.sd, 3), 0) AS skew
            FROM f CROSS JOIN s GROUP BY s.n, s.m, s.sd, s.med""", PARAMS)

    if not hist.empty and not stats.empty and pd.notna(stats.loc[0, "skew"]):
        skew = float(stats.loc[0, "skew"])
        mean, median = float(stats.loc[0, "m"]), float(stats.loc[0, "med"])
        fig = px.bar(hist, x="bin", y="events",
                     labels={"bin": "Magnitude", "events": "Number of earthquakes"})
        fig.add_vline(x=mean, line_dash="dash", annotation_text="mean")
        fig.add_vline(x=median, line_dash="dot", annotation_text="median")
        st.plotly_chart(fig, use_container_width=True)

        if skew < -0.5:
            verdict = "left-skewed (a longer tail toward low magnitudes)"
        elif skew > 0.5:
            verdict = "right-skewed (a longer tail toward high magnitudes)"
        else:
            verdict = "roughly symmetric"
        m1, m2, m3 = st.columns(3)
        m1.metric("Skewness", f"{skew:.2f}")
        m2.metric("Mean", f"{mean:.2f}")
        m3.metric("Median", f"{median:.2f}")
    else:
        st.info("Not enough magnitude data for these filters.")

# Question 2: Active Regions

with tab2:
    st.subheader("Which regions are more or less active?")
    a, b, c = st.columns(3)
    order = a.selectbox("Show", ["Most active", "Least active"])
    top_n = b.slider("Number of regions", 5, 30, 15)
    min_events = c.number_input("Minimum events to include", 1, 10000, 50)
    direction = "DESC" if order == "Most active" else "ASC"

    # this query joins earthquakes with regions
    regions_df = run_query(
        f"SELECT r.region_name AS region, COUNT(*) AS events, "
        f"ROUND(AVG(e.mag), 2) AS avg_mag, ROUND(MAX(e.mag), 1) AS max_mag "
        f"{FROM_SQL} WHERE {WHERE} GROUP BY r.region_name "
        f"HAVING COUNT(*) >= :min_events ORDER BY events {direction} LIMIT :top_n",
        {**PARAMS, "min_events": int(min_events), "top_n": int(top_n)})

    if regions_df.empty:
        st.info("No regions meet the minimum event count.")
    else:
        fig = px.bar(regions_df.sort_values("events"), x="events", y="region",
                     orientation="h", color="avg_mag",
                     color_continuous_scale="Reds",
                     hover_data=["max_mag"],
                     labels={"events": "Number of earthquakes", "region": "",
                             "avg_mag": "Avg magnitude"})
        st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Where the strongest earthquakes happened**")
    n_map = st.slider("Number of events on the map", 100, 5000, 1000, 100)
    map_df = run_query(
        f"SELECT e.latitude::float8 AS latitude, e.longitude::float8 AS longitude, "
        f"e.mag::float8 AS mag, e.place, e.event_time "
        f"{FROM_SQL} WHERE {WHERE} AND e.mag IS NOT NULL "
        f"ORDER BY e.mag DESC LIMIT :n", {**PARAMS, "n": int(n_map)})
    if not map_df.empty:
        fig = px.scatter_geo(map_df, lat="latitude", lon="longitude",
                             color="mag", size="mag", size_max=12,
                             hover_name="place", hover_data=["event_time"],
                             color_continuous_scale="YlOrRd",
                             projection="natural earth")
        # fix the view so the world does not shift when the data changes
        fig.update_geos(
            projection_type="natural earth",
            projection_rotation=dict(lon=0, lat=0, roll=0),
            center=dict(lon=0, lat=0),
            lonaxis_range=[-180, 180], lataxis_range=[-90, 90],
            fitbounds=False, showland=True, landcolor="#e8e8e8",
            showcountries=True, countrycolor="#bbbbbb",
            showocean=True, oceancolor="#f4f9ff")
        fig.update_layout(margin=dict(l=0, r=0, t=0, b=0))
        st.plotly_chart(fig, use_container_width=True)

# Question 3: Earthquake Seasons

with tab3:
    st.subheader("When were there more or fewer earthquakes?")
    monthly = run_query(
        f"SELECT EXTRACT(YEAR FROM e.event_time AT TIME ZONE 'UTC')::int AS year, "
        f"EXTRACT(MONTH FROM e.event_time AT TIME ZONE 'UTC')::int AS month, "
        f"COUNT(*) AS events {FROM_SQL} WHERE {WHERE} GROUP BY 1, 2 ORDER BY 1, 2",
        PARAMS)

    if monthly.empty:
        st.info("No data for these filters.")
    else:
        monthly["date"] = pd.to_datetime(
            dict(year=monthly["year"], month=monthly["month"], day=1))

        st.markdown("**Earthquakes per month**")
        fig = px.line(monthly, x="date", y="events",
                      labels={"date": "Month", "events": "Earthquakes"})
        st.plotly_chart(fig, use_container_width=True)
        st.caption("Big spikes are usually aftershock sequences after a very "
                   "large earthquake, not a true season.")

        left, right = st.columns(2)
        avg_month = (monthly.groupby("month")["events"].mean()
                     .reindex(range(1, 13)).reset_index())
        avg_month["name"] = MONTHS
        with left:
            st.markdown("**Average earthquakes by calendar month**")
            fig = px.bar(avg_month, x="name", y="events",
                         labels={"name": "Month", "events": "Average per year"})
            st.plotly_chart(fig, use_container_width=True)
        with right:
            st.markdown("**Year by month heatmap**")
            grid = (monthly.pivot(index="year", columns="month", values="events")
                    .reindex(columns=range(1, 13)))
            fig = px.imshow(grid, x=MONTHS, aspect="auto",
                            color_continuous_scale="YlOrRd",
                            labels={"x": "Month", "y": "Year", "color": "Events"})
            st.plotly_chart(fig, use_container_width=True)

# Browse Events

with tab4:
    st.subheader("Search events")
    search = st.text_input("Search place (for example 'Alaska' or 'Fiji')")
    params = {**PARAMS}
    extra = ""
    if search.strip():
        extra = " AND e.place ILIKE :pattern"
        params["pattern"] = f"%{search.strip()}%"
    table = run_query(
        f"SELECT e.id, e.event_time, e.mag, e.magtype, e.place, "
        f"r.region_name AS region, e.depth_km, e.status "
        f"{FROM_SQL} WHERE {WHERE}{extra} ORDER BY e.event_time DESC LIMIT 500",
        params)
    st.caption(f"Showing up to 500 of the most recent matching events "
               f"({len(table)} shown).")
    st.dataframe(table, use_container_width=True, hide_index=True)
