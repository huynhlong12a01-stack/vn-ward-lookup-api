import gzip
import json
import os
from contextlib import asynccontextmanager
from numbers import Integral
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from shapely.geometry import shape, Point
from shapely.strtree import STRtree


GEOJSON_FILE = "data/wards_full.geojson.gz"

# Nếu muốn khóa API bằng mật khẩu, sau này đặt API_KEY trên Render.
# Nếu không đặt API_KEY thì API sẽ public.
API_KEY = os.getenv("API_KEY")


geometries = []
properties = []
tree = None
geom_id_to_index = {}


def read_geojson(path):
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)

    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def pick(props, *names):
    """
    Lấy field theo nhiều tên khác nhau.
    Lý do: QGIS đôi khi đặt tên cột hơi khác hoặc thêm prefix.
    """
    for name in names:
        if name in props and props[name] not in [None, ""]:
            return props[name]

    for name in names:
        name_lower = name.lower()
        for k, v in props.items():
            if k.lower().endswith(name_lower) and v not in [None, ""]:
                return v

    return None


def get_index_from_tree_item(item):
    """
    Shapely 2.x: STRtree trả về index.
    Shapely 1.x: STRtree trả về geometry.
    Hàm này xử lý cả hai.
    """
    if isinstance(item, Integral):
        return int(item)

    return geom_id_to_index[id(item)]


def load_data():
    global geometries, properties, tree, geom_id_to_index

    print("Loading wards_full.geojson.gz...")

    data = read_geojson(GEOJSON_FILE)

    for feature in data["features"]:
        geom = shape(feature["geometry"])
        props = feature.get("properties", {})

        geometries.append(geom)
        properties.append(props)

    tree = STRtree(geometries)
    geom_id_to_index = {id(g): i for i, g in enumerate(geometries)}

    print(f"Loaded {len(geometries)} ward geometries.")


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_data()
    yield


app = FastAPI(
    title="Vietnam Ward Lookup API",
    description="Tra cứu tọa độ lat/lon thuộc xã/phường và tỉnh/thành nào.",
    version="1.0.0",
    lifespan=lifespan,
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def home():
    return {
        "message": "Vietnam Ward Lookup API",
        "example": "/lookup?lat=10.7769&lon=106.7009",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    return {
        "status": "ok",
        "ward_count": len(geometries),
    }


@app.get("/lookup")
def lookup(
    lat: float = Query(..., description="Latitude / vĩ độ"),
    lon: float = Query(..., description="Longitude / kinh độ"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    if not (-90 <= lat <= 90):
        raise HTTPException(status_code=400, detail="lat không hợp lệ")

    if not (-180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="lon không hợp lệ")

    # Cực kỳ quan trọng:
    # Người dùng gửi lat/lon,
    # nhưng GIS/Shapely phải tạo điểm theo thứ tự lon, lat.
    point = Point(lon, lat)

    candidates = tree.query(point)
    matches = []

    for item in candidates:
        idx = get_index_from_tree_item(item)
        geom = geometries[idx]

        # covers tính cả điểm nằm đúng trên ranh giới.
        if geom.covers(point):
            props = properties[idx]

            matches.append({
    "ward_code": pick(props, "ward_code", "code", "Code"),
    "ward_name": pick(props, "ward_name", "name", "Name"),
    "ward_name_en": pick(props, "ward_name_en", "name_en", "NameEn"),
    "ward_full_name": pick(props, "ward_full_name", "full_name", "FullName"),
    "ward_full_name_en": pick(props, "ward_full_name_en", "full_name_en", "FullNameEn"),

    "province_code": pick(props, "province_code", "ProvinceCode"),
    "province_name": pick(props, "province_name"),
    "province_name_en": pick(props, "province_name_en"),
    "province_full_name": pick(props, "province_full_name"),
    "province_full_name_en": pick(props, "province_full_name_en"),
})

    if not matches:
        return {
            "found": False,
            "input": {
                "lat": lat,
                "lon": lon,
            },
            "message": "Không tìm thấy xã/tỉnh chứa điểm này. Có thể điểm nằm ngoài Việt Nam hoặc lat/lon bị nhập ngược.",
        }

    return {
        "found": True,
        "input": {
            "lat": lat,
            "lon": lon,
        },
        "match_count": len(matches),
        "result": matches[0],
        "all_matches": matches,
        "note": "Nếu match_count > 1, điểm có thể nằm đúng trên ranh giới hoặc dữ liệu polygon bị chồng lấn nhẹ.",
    }