"""
City Map Poster Maker - Streamlit app
-------------------------------------
Pick a city, click the map to set the area, choose layers and colors ->
preview -> download a print-ready PDF.

Run:   streamlit run app.py
Data:  OpenStreetMap (downloaded once per city/radius, then cached)
"""

import io
import math
import time

import matplotlib
matplotlib.use("Agg")                      # render off-screen (no GUI window)
import matplotlib.pyplot as plt
from matplotlib import font_manager

# Fallback fonts for non-Latin street names (e.g. Amharic). Matplotlib uses the
# first font that has each character. Only fonts actually installed are used.
_installed = {f.name for f in font_manager.fontManager.ttflist}
plt.rcParams["font.family"] = ["DejaVu Sans"] + [
    f for f in ["Noto Sans Ethiopic", "Abyssinica SIL", "Noto Sans Arabic", "Noto Sans CJK JP"]
    if f in _installed]
import geopandas as gpd
import osmnx as ox
import folium
import streamlit as st
from streamlit_folium import st_folium
from shapely.geometry import Point, box

# =============================== CONFIG ====================================

CITIES = {                                  # name -> (lat, lon) of map center
    "Austin, TX": (30.2747, -97.7404),
    "Houston, TX": (29.7604, -95.3698),
    "Dallas, TX": (32.7767, -96.7970),
    "San Antonio, TX": (29.4241, -98.4936),
    "New York, NY": (40.7831, -73.9712),
    "Chicago, IL": (41.8781, -87.6298),
    "San Francisco, CA": (37.7749, -122.4194),
    "Paris, France": (48.8566, 2.3522),
    "London, UK": (51.5072, -0.1276),
    "Dubai, UAE": (25.2048, 55.2708),
    "Addis Ababa, Ethiopia": (9.0300, 38.7400),
    "Bethel & Bisrate Gabriel, Addis": (8.9978, 38.7096),
}

PRESETS = {
    "Classic": dict(bg="#ffffff", roads="#000000", water="#e6e6e6", parks="#f2f2f2",
                    buildings="#d9d9d9", rail="#555555", text="#111111"),
    "Dark": dict(bg="#111111", roads="#ffffff", water="#2b2b2b", parks="#1c1c1c",
                 buildings="#333333", rail="#aaaaaa", text="#ffffff"),
    "Blueprint": dict(bg="#0b2e59", roads="#ffffff", water="#164a85", parks="#123d6e",
                      buildings="#1d5596", rail="#cfe0ff", text="#ffffff"),
    "Warm": dict(bg="#f6efe3", roads="#4a3426", water="#a9c6c9", parks="#cdd8b0",
                 buildings="#e2c9a6", rail="#7a5c48", text="#4a3426"),
    # Google-Maps-like: light land, white roads with gray edges, yellow highways
    "Google Maps": dict(bg="#f2efe9", roads="#ffffff", major_roads="#fdd77a",
                        casing="#c9c4bb", water="#aadaff", parks="#c3ecb2",
                        buildings="#e3e1dc", rail="#9e9e9e", text="#3c4043"),
}

# Road types that count as "major" (used by presets with a major_roads color)
MAJOR_ROADS = {"motorway", "trunk", "primary"}

# Custom title/subtitle for dropdown entries whose name isn't a good poster title
TITLE_OVERRIDES = {
    "Bethel & Bisrate Gabriel, Addis": ("Addis Ababa", "Ethiopia"),
}

# OpenStreetMap tags that define each optional layer
LAYER_TAGS = {
    "water": {"natural": "water", "waterway": "riverbank"},
    "parks": {"leisure": "park", "landuse": "grass"},
    "buildings": {"building": True},
    "rail": {"railway": "rail"},
}

ROAD_WIDTHS = {                             # thicker = bigger road
    "motorway": 2.2, "trunk": 2.0, "primary": 1.6, "secondary": 1.2,
    "tertiary": 0.9, "residential": 0.5, "unclassified": 0.5,
    "living_street": 0.4, "service": 0.25, "footway": 0.2, "path": 0.2,
    "cycleway": 0.2,
}

SIZES = {"18 x 24 in": (18, 24), "12 x 16 in": (12, 16), "24 x 36 in": (24, 36),
         "A3 (11.7 x 16.5 in)": (11.7, 16.5), "A4 (8.27 x 11.69 in)": (8.27, 11.69)}

# ============================ DATA (cached) =================================

def utm_crs(lat, lon):
    """Local metric projection so maps aren't stretched."""
    return gpd.GeoSeries([Point(lon, lat)], crs=4326).estimate_utm_crs()


def first(v):
    return v[0] if isinstance(v, list) else v


# Overpass = the free OpenStreetMap query server osmnx downloads from.
# The main one sometimes refuses connections when overloaded, so we fall back
# to public mirrors (same OSM data, different host).
OVERPASS_MIRRORS = [
    "https://overpass-api.de/api",
    "https://overpass.private.coffee/api",
    "https://maps.mail.ru/osm/tools/overpass/api",
]
# "No features of this type here" is a normal result, not a server failure
NO_DATA_ERRORS = tuple(e for e in [
    getattr(getattr(ox, "_errors", None), "InsufficientResponseError", None)] if e)


# Print every download request/response to the terminal running streamlit.
ox.settings.log_console = True
ox.settings.requests_timeout = 180       # big street queries can take 1-3 min
ox.settings.overpass_rate_limit = False  # don't pause 60s when a server is down


def use_server(url):
    # osmnx 2.x calls it overpass_url, 1.x overpass_endpoint - set both
    ox.settings.overpass_url = url
    ox.settings.overpass_endpoint = url


def with_mirrors(fn, *args):
    """Try each Overpass server in turn; raise only if all of them fail."""
    errors = []
    for url in OVERPASS_MIRRORS:
        use_server(url)
        print(f"[map-app] trying {url}", flush=True)
        try:
            return fn(*args)
        except NO_DATA_ERRORS:
            raise
        except Exception as e:              # refused / timeout / 429 / 504
            errors.append(f"{url.split('/')[2]}: {type(e).__name__}")
    raise ConnectionError("All OpenStreetMap servers failed -> " + "; ".join(errors))


def _download_roads(lat, lon, dist, network):
    G = ox.graph_from_point((lat, lon), dist=dist, network_type=network,
                            retain_all=True, simplify=True)
    e = ox.graph_to_gdfs(G, nodes=False).reindex(columns=["highway", "name", "geometry"])
    e = gpd.GeoDataFrame(e, geometry="geometry", crs=4326)
    e["rtype"] = e["highway"].apply(lambda v: str(first(v)).replace("_link", ""))
    e["name"] = e["name"].apply(first)
    return e[["rtype", "name", "geometry"]].to_crs(utm_crs(lat, lon))


def _download_layer(lat, lon, dist, layer):
    g = ox.features_from_point((lat, lon), tags=LAYER_TAGS[layer], dist=dist)
    keep = ["LineString", "MultiLineString"] if layer == "rail" else ["Polygon", "MultiPolygon"]
    g = g[g.geom_type.isin(keep)]
    return gpd.GeoDataFrame(geometry=g.geometry.values, crs=g.crs).to_crs(utm_crs(lat, lon))


# Failures are never cached (Streamlit only caches successful returns),
# so clicking Generate again retries for real.
@st.cache_data(show_spinner=False)
def fetch_roads(lat, lon, dist, network):
    return with_mirrors(_download_roads, lat, lon, dist, network)


@st.cache_data(show_spinner=False)
def fetch_layer(lat, lon, dist, layer):
    try:
        return with_mirrors(_download_layer, lat, lon, dist, layer)
    except NO_DATA_ERRORS:                  # e.g. no railways in this area
        return gpd.GeoDataFrame(geometry=[], crs=utm_crs(lat, lon))

# =============================== RENDER =====================================

def to_dms(deg, pos, neg):
    h = pos if deg >= 0 else neg
    deg = abs(deg); d = int(deg); m = int((deg - d) * 60)
    s = (deg - d - m / 60) * 3600
    return f"{d}°{m:02d}'{s:04.1f}\"{h}"


def render(o, step=lambda label: None):
    """step(label) is called as each stage starts, so the UI can show progress."""
    lat, lon, r = o["lat"], o["lon"], o["radius"]
    crs = utm_crs(lat, lon)
    c = gpd.GeoSeries([Point(lon, lat)], crs=4326).to_crs(crs).iloc[0]
    col = o["colors"]

    fig = plt.figure(figsize=o["size"], facecolor=col["bg"])
    ax = fig.add_axes([0.06, 0.20, 0.88, 0.76])
    ax.set_axis_off(); ax.set_facecolor(col["bg"]); ax.set_aspect("equal")

    # Visible frame + clip shape (map box is portrait: width/height ratio)
    ratio = (0.88 * o["size"][0]) / (0.76 * o["size"][1])
    if o["shape"] == "Circle":
        clip = c.buffer(r)
        half_w, half_h = (r, r / ratio) if ratio < 1 else (r * ratio, r)
    else:
        half_h, half_w = r, r * ratio
        clip = box(c.x - half_w, c.y - half_h, c.x + half_w, c.y + half_h)
    ax.set_xlim(c.x - half_w, c.x + half_w)
    ax.set_ylim(c.y - half_h, c.y + half_h)

    # Fetch radius must cover the corners of the rectangle
    dist = int(math.hypot(half_w, half_h) * 1.05) if o["shape"] != "Circle" else int(r * 1.05)

    # Layers bottom -> top
    for layer, z in [("water", 1), ("parks", 1), ("buildings", 2)]:
        if o["layers"][layer]:
            step(f"Downloading {layer}")
            g = fetch_layer(lat, lon, dist, layer)
            if len(g):
                gpd.clip(g, clip).plot(ax=ax, color=col[layer], linewidth=0, zorder=z)

    if o["layers"]["rail"]:
        step("Downloading railways")
        g = fetch_layer(lat, lon, dist, "rail")
        if len(g):
            gpd.clip(g, clip).plot(ax=ax, color=col["rail"], linewidth=0.6,
                                   linestyle=(0, (4, 2)), zorder=3)

    step("Downloading streets (slowest step on first run)")
    roads = gpd.clip(fetch_roads(lat, lon, dist, o["network"]), clip)
    step(f"Drawing {len(roads):,} road segments")
    roads["w"] = roads["rtype"].map(ROAD_WIDTHS).fillna(0.4) * o["road_scale"]
    roads["major"] = roads["rtype"].isin(MAJOR_ROADS)

    # Optional outline under every road (Google-Maps look: white roads, gray edges)
    if "casing" in col:
        for w in sorted(roads["w"].unique()):
            roads[roads["w"] == w].plot(ax=ax, color=col["casing"], linewidth=w * 1.6 + 0.3,
                                        capstyle="round", zorder=3.5)

    # Roads: thin first; major roads get their own color if the preset has one
    for w in sorted(roads["w"].unique()):
        for is_major, sub in roads[roads["w"] == w].groupby("major"):
            color = col["major_roads"] if (is_major and "major_roads" in col) else col["roads"]
            sub.plot(ax=ax, color=color, linewidth=w, capstyle="round", zorder=4)

    # Street names (major roads first, each name not repeated within 1.5 km)
    if o["labels"]:
        step("Placing street names")
        placed = {}
        named = roads[roads["name"].notna() & (roads.geometry.length > 250)]
        for nm, geom in zip(named.sort_values("w", ascending=False)["name"],
                            named.sort_values("w", ascending=False).geometry):
            mid = geom.interpolate(0.5, normalized=True)
            if any(mid.distance(p) < 1500 for p in placed.get(nm, [])):
                continue
            a, b = (geom.interpolate(t, normalized=True) for t in (0.45, 0.55))
            ang = math.degrees(math.atan2(b.y - a.y, b.x - a.x))
            ang = ang - 180 if ang > 90 else ang + 180 if ang < -90 else ang
            ax.text(mid.x, mid.y, nm, fontsize=o["label_size"], color=col["text"], rotation=ang,
                    rotation_mode="anchor", ha="center", va="center", zorder=5,
                    bbox=dict(facecolor=col["bg"], edgecolor="none", pad=0.15))
            placed.setdefault(nm, []).append(mid)

    # Text block
    scale = o["size"][0] / 18                          # keep text proportional
    fig.text(0.5, 0.125, "  ".join(o["title"].upper()), ha="center", va="center",
             fontsize=72 * scale, fontweight="bold", color=col["text"])
    fig.text(0.5, 0.085, o["subtitle"].upper(), ha="center", va="center",
             fontsize=26 * scale, color=col["text"])
    fig.text(0.5, 0.055, f"{to_dms(lat, 'N', 'S')}  |  {to_dms(lon, 'E', 'W')}",
             ha="center", va="center", fontsize=20 * scale, color=col["text"])
    fig.text(0.94, 0.02, "© OpenStreetMap contributors", ha="right",
             fontsize=8 * scale, color=col["text"], alpha=0.5)
    return fig


def fig_bytes(fig, fmt, dpi=None):
    buf = io.BytesIO()
    fig.savefig(buf, format=fmt, dpi=dpi, facecolor=fig.get_facecolor())
    return buf.getvalue()

# ================================= UI =======================================
# No form: every sidebar change re-runs the script, which is cheap (it only
# redraws the picker map). The slow poster render runs only on the button.

st.set_page_config(page_title="City Map Poster Maker", layout="wide")
st.title("City Map Poster Maker")
ss = st.session_state
sb = st.sidebar

# ---------------- Place ----------------
sb.subheader("Place")
city = sb.selectbox("Jump to city", list(CITIES))
if ss.get("city") != city:                  # new city picked -> recenter on it
    ss["city"] = city
    ss["center"] = CITIES[city]
lat, lon = ss["center"]
sb.caption(f"Center: {lat:.4f}, {lon:.4f} — click the map to move it")
radius = sb.slider("Radius (meters)", 500, 10000, 3000, step=250,
                   help="Bigger = more city, slower first download")

# ---------------- Layout ----------------
sb.subheader("Layout")
shape = sb.radio("Shape", ["Rectangle", "Circle"], horizontal=True)
size = SIZES[sb.selectbox("Poster size", list(SIZES))]
if city in TITLE_OVERRIDES:
    default_title, default_sub = TITLE_OVERRIDES[city]
else:
    default_title, default_sub = city.split(",")[0], city.split(",")[1].strip()
title = sb.text_input("Title", default_title)
subtitle = sb.text_input("Subtitle", default_sub)

# ---------------- Layers ----------------
sb.subheader("Layers")
network = sb.selectbox("Streets", ["drive", "all", "walk", "bike"],
                       help="'all' and 'walk' add alleys/paths = denser map")
layers = {
    "water": sb.checkbox("Water", True),
    "parks": sb.checkbox("Parks", False),
    "buildings": sb.checkbox("Buildings", False, help="Slow for big radius"),
    "rail": sb.checkbox("Railways", False),
}
labels = sb.checkbox("Street names", False)
label_size = sb.slider("Street name size (pt)", 2.0, 10.0, 3.0, 0.5,
                       help="Small posters (A4) need bigger names, e.g. 5-6 pt")
road_scale = sb.slider("Road thickness", 0.3, 3.0, 1.0, 0.1)

# ---------------- Colors ----------------
sb.subheader("Colors")
preset = sb.selectbox("Color preset", list(PRESETS))
with sb.expander("Fine-tune colors"):
    # key includes preset name -> switching preset resets the pickers
    colors = {k: st.color_picker(k.replace("_", " ").capitalize(), v, key=f"{preset}_{k}")
              for k, v in PRESETS[preset].items()}

generate = sb.button("Generate poster", type="primary", width="stretch")

# ---------------- 1. Interactive area picker ----------------
st.subheader("1. Pick the area")
st.caption("Click anywhere to move the center. The red outline is exactly what the poster will show.")

ratio = (0.88 * size[0]) / (0.76 * size[1])          # poster map box width/height
m_per_deg_lat = 111_320
m_per_deg_lon = 111_320 * math.cos(math.radians(lat))

m = folium.Map(location=[lat, lon], tiles="OpenStreetMap", control_scale=True)
if shape == "Circle":
    folium.Circle([lat, lon], radius=radius, color="#e63946", weight=3,
                  fill=True, fill_opacity=0.08).add_to(m)
    half_w = half_h = radius
else:
    half_h, half_w = radius, radius * ratio
    folium.Rectangle(
        [[lat - half_h / m_per_deg_lat, lon - half_w / m_per_deg_lon],
         [lat + half_h / m_per_deg_lat, lon + half_w / m_per_deg_lon]],
        color="#e63946", weight=3, fill=True, fill_opacity=0.08).add_to(m)
folium.Marker([lat, lon], tooltip="Poster center").add_to(m)
pad = 1.3                                            # show a little around the outline
m.fit_bounds([[lat - pad * half_h / m_per_deg_lat, lon - pad * half_w / m_per_deg_lon],
              [lat + pad * half_h / m_per_deg_lat, lon + pad * half_w / m_per_deg_lon]])

# returned_objects limits re-runs to clicks only (panning/zooming won't re-run)
out = st_folium(m, height=460, use_container_width=True,
                returned_objects=["last_clicked"], key="picker")
click = (out or {}).get("last_clicked")
if click and click != ss.get("last_click"):          # a NEW click -> move center
    ss["last_click"] = click
    ss["center"] = (click["lat"], click["lng"])
    st.rerun()

# ---------------- 2. Poster ----------------
st.subheader("2. Poster")
if generate:
    opts = dict(lat=lat, lon=lon, radius=radius, shape=shape, size=size,
                title=title, subtitle=subtitle, network=network, layers=layers,
                labels=labels, label_size=label_size, road_scale=road_scale, colors=colors)

    # Live status box: each stage shows a spinner line, then turns into a
    # checkmark with how long it took. Elapsed total is shown in the header.
    t_start = time.time()
    with st.status("Building your poster...", expanded=True) as status:
        current = {}                         # the stage that is running now

        def step(label):
            finish_current()
            current.update(label=label, t=time.time(), slot=st.empty())
            current["slot"].markdown(f"⏳ {label}...")
            status.update(label=f"{label}... ({time.time() - t_start:.0f}s elapsed)")

        def finish_current():
            if current:
                secs = time.time() - current["t"]
                note = "  (cached)" if secs < 1 and "Downloading" in current["label"] else ""
                current["slot"].markdown(f"✅ {current['label']} — {secs:.1f}s{note}")

        try:
            fig = render(opts, step)
            step("Exporting PDF and preview")
            ss["png"] = fig_bytes(fig, "png", dpi=80)
            ss["pdf"] = fig_bytes(fig, "pdf")
            ss["name"] = title.lower().replace(" ", "_") or "map"
            plt.close(fig)
            finish_current()
            status.update(label=f"Poster ready in {time.time() - t_start:.0f}s",
                          state="complete", expanded=False)
        except Exception as e:
            if current:
                current["slot"].markdown(f"❌ {current['label']} — failed")
            status.update(label="Something went wrong", state="error", expanded=True)
            st.error(f"Couldn't build the map: {e}")
            if "servers failed" in str(e):
                st.info("OpenStreetMap's free servers are busy or unreachable. "
                        "Wait a minute and click Generate again.")

if "png" in ss:
    left, right = st.columns([3, 1])
    left.image(ss["png"], width="stretch")
    right.download_button("Download PDF (print-ready)", ss["pdf"],
                          file_name=f"{ss['name']}_poster.pdf",
                          mime="application/pdf", type="primary")
    right.caption("The PDF is vector: sharp at any print size. "
                  "Preview is low-res on purpose so it loads fast.")
else:
    st.info("Pick the area above, then click **Generate poster** in the sidebar.")