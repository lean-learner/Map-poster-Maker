# Map Poster Maker

Turn any place in the world into a minimalist, print-ready city map poster, built from free OpenStreetMap data.

**Live app:** https://citymap-poster-maker.streamlit.app/

## Features

- **Interactive area picker:** click the map to set the center. A red outline shows exactly what the poster will contain.
- **Shapes:** rectangle or circle.
- **Layers:** streets (drive, walk, bike or all), water, parks, buildings, railways and street names.
- **Color presets:** Classic, Dark, Blueprint, Warm and Google Maps, with every color adjustable.
- **Poster sizes:** 12×16, 18×24, 24×36 in, A3 and A4.
- **Print-ready PDF:** vector output that stays sharp at any print size.
- **Non-Latin street names:** Amharic and other scripts render through Noto font fallbacks.
- **Reliable downloads:** live progress for each step, with automatic fallback to mirror servers if the main one is busy.

## How it works

1. Your click and radius define the area to fetch.
2. [osmnx](https://github.com/gboeing/osmnx) queries the [Overpass API](https://overpass-api.de/) for roads and map features. Results are cached, so restyling an area is instant.
3. The data is projected to a local metric grid (UTM) so the map isn't stretched.
4. matplotlib paints the layers bottom to top and exports a vector PDF.
5. [Streamlit](https://streamlit.io/) provides the interface; the picker uses [folium](https://python-visualization.github.io/folium/) (Leaflet).

## Run locally

```bash
git clone https://github.com/lean-learner/Map-poster-Maker.git
cd Map-poster-Maker
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\Activate.ps1
pip install -r requirements.txt
streamlit run app.py
```

Then open http://localhost:8501.

For non-Latin street names on Linux, install the Noto fonts: `sudo apt install fonts-noto-core`.

## Tips

- The first poster for a new area takes 1–5 minutes while the data downloads from OpenStreetMap. After that it's cached.
- On the hosted app, keep the radius at 2500 m or less and leave **Buildings** off for large areas.
- Every click on the picker moves the center, which means a new download. Colors, layers and labels reuse the cached data.

## Data and license

Map data © [OpenStreetMap contributors](https://www.openstreetmap.org/copyright), available under the Open Database License. The credit is printed on every poster, as the license requires.
