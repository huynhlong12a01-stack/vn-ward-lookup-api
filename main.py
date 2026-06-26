import os
import sqlite3
import zlib
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from shapely.geometry import Point
from shapely import wkb


DB_FILE = Path("data/wards_lookup.sqlite")
API_KEY = os.getenv("API_KEY")


app = FastAPI(
    title="Vietnam Ward Lookup API",
    description="Tra cứu tọa độ lat/lon thuộc xã/phường và tỉnh/thành nào.",
    version="2.1.0",
)


app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/")
def home():
    return {
        "message": "Vietnam Ward Lookup API",
        "version": "sqlite-compressed-wkb",
        "example": "/lookup?lat=10.7769&lon=106.7009",
        "docs": "/docs",
    }


@app.get("/health")
def health():
    if not DB_FILE.exists():
        return {
            "status": "error",
            "message": f"Không thấy database: {DB_FILE}",
        }

    with get_conn() as conn:
        total = conn.execute("SELECT COUNT(*) AS n FROM wards").fetchone()["n"]

    return {
        "status": "ok",
        "database": str(DB_FILE),
        "ward_count": total,
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

    point = Point(lon, lat)

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT *
            FROM wards
            WHERE
                minx <= ?
                AND maxx >= ?
                AND miny <= ?
                AND maxy >= ?
        """, (lon, lon, lat, lat)).fetchall()

    matches = []

    for row in rows:
        geom_wkb = zlib.decompress(row["geom_wkb_zlib"])
        geom = wkb.loads(geom_wkb)

        if geom.covers(point):
            matches.append({
                "ward_code": row["ward_code"],
                "ward_name": row["ward_name"],
                "ward_name_en": row["ward_name_en"],
                "ward_full_name": row["ward_full_name"],
                "ward_full_name_en": row["ward_full_name_en"],
                "province_code": row["province_code"],
                "province_name": row["province_name"],
                "province_name_en": row["province_name_en"],
                "province_full_name": row["province_full_name"],
                "province_full_name_en": row["province_full_name_en"],
            })

    if not matches:
        return {
            "found": False,
            "input": {
                "lat": lat,
                "lon": lon,
            },
            "candidate_count": len(rows),
            "message": "Không tìm thấy xã/tỉnh chứa điểm này. Có thể điểm nằm ngoài Việt Nam hoặc lat/lon bị nhập ngược.",
        }

    return {
        "found": True,
        "input": {
            "lat": lat,
            "lon": lon,
        },
        "candidate_count": len(rows),
        "match_count": len(matches),
        "result": matches[0],
        "all_matches": matches,
        "note": "Nếu match_count > 1, điểm có thể nằm đúng trên ranh giới hoặc dữ liệu polygon bị chồng lấn nhẹ.",
    }