import os
import sqlite3
import zlib
from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from shapely.geometry import Point
from shapely import wkb


DB_FILE = Path("data/wards_lookup.sqlite")
API_KEY = os.getenv("API_KEY")


app = FastAPI(
    title="Vietnam Ward Lookup API",
    description="Tra cứu tọa độ lat/lon thuộc xã/phường và tỉnh/thành nào.",
    version="2.4.0",
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


def validate_key(key: Optional[str]):
    if API_KEY and key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")


def validate_lat_lon(lat: float, lon: float):
    if not (-90 <= lat <= 90):
        raise HTTPException(status_code=400, detail="lat không hợp lệ")

    if not (-180 <= lon <= 180):
        raise HTTPException(status_code=400, detail="lon không hợp lệ")


def lookup_core(lat: float, lon: float):
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

    return rows, matches


@lru_cache(maxsize=50000)
def lookup_text_cached(lat_key: str, lon_key: str):
    """
    Trả về:
    Sai Gon|Ho Chi Minh
    """
    lat = float(lat_key)
    lon = float(lon_key)

    _, matches = lookup_core(lat, lon)

    if not matches:
        return "NOT_FOUND"

    result = matches[0]

    ward_en = (
        result.get("ward_name_en")
        or result.get("ward_name")
        or ""
    )

    province_en = (
        result.get("province_name_en")
        or result.get("province_name")
        or ""
    )

    return f"{ward_en}|{province_en}"


@lru_cache(maxsize=50000)
def lookup_ward_text_cached(lat_key: str, lon_key: str):
    """
    Trả về:
    Sai Gon
    """
    lat = float(lat_key)
    lon = float(lon_key)

    _, matches = lookup_core(lat, lon)

    if not matches:
        return "NOT_FOUND"

    result = matches[0]

    return (
        result.get("ward_name_en")
        or result.get("ward_name")
        or "NOT_FOUND"
    )


@lru_cache(maxsize=50000)
def lookup_province_text_cached(lat_key: str, lon_key: str):
    """
    Trả về:
    Ho Chi Minh

    Không trả:
    Ho Chi Minh City
    """
    lat = float(lat_key)
    lon = float(lon_key)

    _, matches = lookup_core(lat, lon)

    if not matches:
        return "NOT_FOUND"

    result = matches[0]

    return (
        result.get("province_name_en")
        or result.get("province_name")
        or "NOT_FOUND"
    )


@app.get("/")
def home():
    return {
        "message": "Vietnam Ward Lookup API",
        "version": "sqlite-compressed-wkb-cache-short-province",
        "json_example": "/lookup?lat=10.7769&lon=106.7009",
        "text_example": "/lookup_text?lat=10.7769&lon=106.7009",
        "ward_text_example": "/lookup_ward_text?lat=10.7769&lon=106.7009",
        "province_text_example": "/lookup_province_text?lat=10.7769&lon=106.7009",
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
        "lookup_text_cache": lookup_text_cached.cache_info()._asdict(),
        "lookup_ward_text_cache": lookup_ward_text_cached.cache_info()._asdict(),
        "lookup_province_text_cache": lookup_province_text_cached.cache_info()._asdict(),
    }


@app.get("/cache_status")
def cache_status():
    return {
        "lookup_text_cache": lookup_text_cached.cache_info()._asdict(),
        "lookup_ward_text_cache": lookup_ward_text_cached.cache_info()._asdict(),
        "lookup_province_text_cache": lookup_province_text_cached.cache_info()._asdict(),
    }


@app.get("/lookup")
def lookup(
    lat: float = Query(..., description="Latitude / vĩ độ"),
    lon: float = Query(..., description="Longitude / kinh độ"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    validate_key(key)
    validate_lat_lon(lat, lon)

    rows, matches = lookup_core(lat, lon)

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


@app.get("/lookup_text", response_class=PlainTextResponse)
def lookup_text(
    lat: float = Query(..., description="Latitude / vĩ độ"),
    lon: float = Query(..., description="Longitude / kinh độ"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    """
    Endpoint dành cho Excel WEBSERVICE.

    Trả text gọn:
    Sai Gon|Ho Chi Minh
    """
    validate_key(key)
    validate_lat_lon(lat, lon)

    lat_key = f"{lat:.6f}"
    lon_key = f"{lon:.6f}"

    text = lookup_text_cached(lat_key, lon_key)

    return PlainTextResponse(
        text,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable"
        },
    )


@app.get("/lookup_ward_text", response_class=PlainTextResponse)
def lookup_ward_text(
    lat: float = Query(..., description="Latitude / vĩ độ"),
    lon: float = Query(..., description="Longitude / kinh độ"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    """
    Chỉ trả tên xã/phường tiếng Anh.

    Ví dụ:
    Sai Gon
    """
    validate_key(key)
    validate_lat_lon(lat, lon)

    lat_key = f"{lat:.6f}"
    lon_key = f"{lon:.6f}"

    text = lookup_ward_text_cached(lat_key, lon_key)

    return PlainTextResponse(
        text,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable"
        },
    )


@app.get("/lookup_province_text", response_class=PlainTextResponse)
def lookup_province_text(
    lat: float = Query(..., description="Latitude / vĩ độ"),
    lon: float = Query(..., description="Longitude / kinh độ"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    """
    Chỉ trả tên tỉnh/thành tiếng Anh dạng ngắn.

    Ví dụ:
    Ho Chi Minh

    Không trả:
    Ho Chi Minh City
    """
    validate_key(key)
    validate_lat_lon(lat, lon)

    lat_key = f"{lat:.6f}"
    lon_key = f"{lon:.6f}"

    text = lookup_province_text_cached(lat_key, lon_key)

    return PlainTextResponse(
        text,
        headers={
            "Cache-Control": "public, max-age=31536000, immutable"
        },
    )


@app.get("/lookup_ward")
def lookup_ward(
    lat: float = Query(..., description="Latitude / vĩ độ"),
    lon: float = Query(..., description="Longitude / kinh độ"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    """
    JSON chỉ trả thông tin xã/phường.
    """
    validate_key(key)
    validate_lat_lon(lat, lon)

    rows, matches = lookup_core(lat, lon)

    if not matches:
        return {
            "found": False,
            "input": {
                "lat": lat,
                "lon": lon,
            },
            "candidate_count": len(rows),
            "message": "Không tìm thấy xã/phường chứa điểm này.",
        }

    result = matches[0]

    return {
        "found": True,
        "input": {
            "lat": lat,
            "lon": lon,
        },
        "result": {
            "ward_code": result.get("ward_code"),
            "ward_name": result.get("ward_name"),
            "ward_name_en": result.get("ward_name_en"),
            "ward_full_name": result.get("ward_full_name"),
            "ward_full_name_en": result.get("ward_full_name_en"),
        },
    }


@app.get("/lookup_province")
def lookup_province(
    lat: float = Query(..., description="Latitude / vĩ độ"),
    lon: float = Query(..., description="Longitude / kinh độ"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    """
    JSON chỉ trả thông tin tỉnh/thành.

    Tên tiếng Anh dùng province_name_en:
    Ho Chi Minh

    Không dùng province_full_name_en:
    Ho Chi Minh City
    """
    validate_key(key)
    validate_lat_lon(lat, lon)

    rows, matches = lookup_core(lat, lon)

    if not matches:
        return {
            "found": False,
            "input": {
                "lat": lat,
                "lon": lon,
            },
            "candidate_count": len(rows),
            "message": "Không tìm thấy tỉnh/thành chứa điểm này.",
        }

    result = matches[0]

    return {
        "found": True,
        "input": {
            "lat": lat,
            "lon": lon,
        },
        "result": {
            "province_code": result.get("province_code"),
            "province_name": result.get("province_name"),
            "province_name_en": result.get("province_name_en"),
        },
    }