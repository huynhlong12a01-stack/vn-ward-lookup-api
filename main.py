import os
import sqlite3
import zlib
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse, FileResponse
from shapely.geometry import Point
from shapely import wkb


DB_FILE = Path("data/wards_lookup.sqlite")
HTML_FILE = Path("vn_ward_province_lookup_by_location.html")
API_KEY = os.getenv("API_KEY")


app = FastAPI(
    title="Vietnam Ward & Province Lookup API",
    description="Tra cứu tọa độ lat/lon thuộc xã/phường và tỉnh/thành nào (dữ liệu cập nhật v5.2.0 - Nghị quyết 388/NQ-UBTVQH16).",
    version="2.6.0-v5.2.0-sqlite",
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
    """
    1. Lọc bbox trong SQLite bảng wards.
    2. Giải nén polygon ứng viên.
    3. Kiểm tra điểm nằm trong polygon.
    """
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
                "ward_code_name": row["ward_code_name"],
                "postal_code": row["postal_code"],

                "province_code": row["province_code"],
                "province_name": row["province_name"],
                "province_name_en": row["province_name_en"],
                "province_full_name": row["province_full_name"],
                "province_full_name_en": row["province_full_name_en"],
                "province_code_name": row["province_code_name"],

                "administrative_unit_short_name": row["administrative_unit_short_name"],
                "administrative_unit_full_name": row["administrative_unit_full_name"],
                "area_km2": row["area_km2"],
            })

    return rows, matches


def lookup_province_core(lat: float, lon: float):
    """
    Tra cứu trực tiếp qua bảng provinces (hữu ích cho vùng biển/đảo hoặc kiểm tra cấp tỉnh).
    """
    point = Point(lon, lat)

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT *
            FROM provinces
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
                "province_code": row["province_code"],
                "province_name": row["province_name"],
                "province_name_en": row["province_name_en"],
                "province_full_name": row["province_full_name"],
                "province_full_name_en": row["province_full_name_en"],
                "province_code_name": row["province_code_name"],
                "postal_code_prefix": row["postal_code_prefix"],
                "administrative_unit_short_name": row["administrative_unit_short_name"],
                "administrative_unit_full_name": row["administrative_unit_full_name"],
                "area_km2": row["area_km2"],
            })

    return rows, matches


def get_first_result(lat: float, lon: float):
    rows, matches = lookup_core(lat, lon)

    if not matches:
        return rows, None

    return rows, matches[0]


@app.get("/")
def home():
    meta = {}
    try:
        with get_conn() as conn:
            meta_rows = conn.execute("SELECT key, value FROM metadata").fetchall()
            meta = {r["key"]: r["value"] for r in meta_rows}
    except Exception:
        pass

    return {
        "message": "Vietnam Ward & Province Lookup API",
        "version": "2.6.0-v5.2.0-sqlite",
        "dataset_version": meta.get("DatasetVersion", "v5.2.0"),
        "latest_decree": meta.get("LatestDecree", "388/NQ-UBTVQH16"),
        "source_repo": meta.get("source_repo", "https://github.com/thanglequoc/vietnamese-provinces-database"),
        "json_example": "/lookup?lat=10.7769&lon=106.7009",
        "text_example": "/lookup_text?lat=10.7769&lon=106.7009",
        "ward_text_example": "/lookup_ward_text?lat=10.7769&lon=106.7009",
        "province_text_example": "/lookup_province_text?lat=10.7769&lon=106.7009",
        "postal_code_example": "/lookup_by_postal_code?postal_code=11120",
        "ui": "/ui",
        "cache": "disabled",
        "docs": "/docs",
    }


@app.get("/ui", response_class=FileResponse)
def ui():
    if HTML_FILE.exists():
        return FileResponse(HTML_FILE)
    raise HTTPException(status_code=404, detail="File UI không tồn tại")


@app.get("/health")
def health():
    if not DB_FILE.exists():
        return {
            "status": "error",
            "message": f"Không thấy database: {DB_FILE}",
            "cache": "disabled",
        }

    with get_conn() as conn:
        ward_total = conn.execute("SELECT COUNT(*) AS n FROM wards").fetchone()["n"]
        prov_total = conn.execute("SELECT COUNT(*) AS n FROM provinces").fetchone()["n"]
        meta_rows = conn.execute("SELECT key, value FROM metadata").fetchall()
        meta = {r["key"]: r["value"] for r in meta_rows}

    return {
        "status": "ok",
        "database": str(DB_FILE),
        "ward_count": ward_total,
        "province_count": prov_total,
        "dataset_version": meta.get("DatasetVersion", "v5.2.0"),
        "latest_decree": meta.get("LatestDecree", "388/NQ-UBTVQH16"),
        "dataset_generated_at": meta.get("GeneratedAt"),
        "source_repo": meta.get("source_repo"),
        "cache": "disabled",
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
        # Nếu chưa tìm thấy cấp xã, thử tra cứu cấp tỉnh (ví dụ hải đảo xa hoặc vùng biên giới)
        p_rows, p_matches = lookup_province_core(lat, lon)
        if p_matches:
            prov = p_matches[0]
            return {
                "found": True,
                "input": {
                    "lat": lat,
                    "lon": lon,
                },
                "candidate_count": len(rows) + len(p_rows),
                "match_count": 1,
                "result": {
                    "ward_code": None,
                    "ward_name": None,
                    "ward_name_en": None,
                    "ward_full_name": None,
                    "ward_full_name_en": None,
                    "ward_code_name": None,
                    "postal_code": None,
                    "province_code": prov["province_code"],
                    "province_name": prov["province_name"],
                    "province_name_en": prov["province_name_en"],
                    "province_full_name": prov["province_full_name"],
                    "province_full_name_en": prov["province_full_name_en"],
                    "province_code_name": prov["province_code_name"],
                    "area_km2": prov["area_km2"],
                },
                "all_matches": [],
                "note": "Điểm thuộc địa phận tỉnh nhưng không nằm trong ranh giới xã/phường cụ thể.",
            }

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
    Trả text gọn cho Excel/HTML:

    Sai Gon|Ho Chi Minh
    """
    validate_key(key)
    validate_lat_lon(lat, lon)

    _, result = get_first_result(lat, lon)

    if not result:
        # Thử fallback qua tỉnh
        _, p_matches = lookup_province_core(lat, lon)
        if p_matches:
            prov = p_matches[0]
            prov_en = prov.get("province_name_en") or prov.get("province_name") or ""
            return PlainTextResponse(f"|{prov_en}", headers={"Cache-Control": "no-store"})

        return PlainTextResponse(
            "NOT_FOUND",
            headers={
                "Cache-Control": "no-store"
            },
        )

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

    return PlainTextResponse(
        f"{ward_en}|{province_en}",
        headers={
            "Cache-Control": "no-store"
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
    Ba Dinh
    """
    validate_key(key)
    validate_lat_lon(lat, lon)

    _, result = get_first_result(lat, lon)

    if not result:
        return PlainTextResponse(
            "NOT_FOUND",
            headers={
                "Cache-Control": "no-store"
            },
        )

    ward_en = (
        result.get("ward_name_en")
        or result.get("ward_name")
        or "NOT_FOUND"
    )

    return PlainTextResponse(
        ward_en,
        headers={
            "Cache-Control": "no-store"
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
    """
    validate_key(key)
    validate_lat_lon(lat, lon)

    _, result = get_first_result(lat, lon)

    if not result:
        _, p_matches = lookup_province_core(lat, lon)
        if p_matches:
            prov_en = p_matches[0].get("province_name_en") or p_matches[0].get("province_name") or "NOT_FOUND"
            return PlainTextResponse(prov_en, headers={"Cache-Control": "no-store"})

        return PlainTextResponse(
            "NOT_FOUND",
            headers={
                "Cache-Control": "no-store"
            },
        )

    province_en = (
        result.get("province_name_en")
        or result.get("province_name")
        or "NOT_FOUND"
    )

    return PlainTextResponse(
        province_en,
        headers={
            "Cache-Control": "no-store"
        },
    )


@app.get("/lookup_ward")
def lookup_ward(
    lat: float = Query(..., description="Latitude / vĩ độ"),
    lon: float = Query(..., description="Longitude / kinh độ"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    validate_key(key)
    validate_lat_lon(lat, lon)

    rows, result = get_first_result(lat, lon)

    if not result:
        return {
            "found": False,
            "input": {
                "lat": lat,
                "lon": lon,
            },
            "candidate_count": len(rows),
            "message": "Không tìm thấy xã/phường chứa điểm này.",
        }

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
            "ward_code_name": result.get("ward_code_name"),
            "postal_code": result.get("postal_code"),
            "area_km2": result.get("area_km2"),
        },
    }


@app.get("/lookup_province")
def lookup_province(
    lat: float = Query(..., description="Latitude / vĩ độ"),
    lon: float = Query(..., description="Longitude / kinh độ"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    validate_key(key)
    validate_lat_lon(lat, lon)

    rows, result = get_first_result(lat, lon)

    if not result:
        p_rows, p_matches = lookup_province_core(lat, lon)
        if p_matches:
            prov = p_matches[0]
            return {
                "found": True,
                "input": {
                    "lat": lat,
                    "lon": lon,
                },
                "candidate_count": len(p_rows),
                "result": {
                    "province_code": prov.get("province_code"),
                    "province_name": prov.get("province_name"),
                    "province_name_en": prov.get("province_name_en"),
                    "province_full_name": prov.get("province_full_name"),
                    "province_full_name_en": prov.get("province_full_name_en"),
                    "province_code_name": prov.get("province_code_name"),
                    "postal_code_prefix": prov.get("postal_code_prefix"),
                },
            }

        return {
            "found": False,
            "input": {
                "lat": lat,
                "lon": lon,
            },
            "candidate_count": len(rows),
            "message": "Không tìm thấy tỉnh/thành chứa điểm này.",
        }

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
            "province_full_name": result.get("province_full_name"),
            "province_full_name_en": result.get("province_full_name_en"),
            "province_code_name": result.get("province_code_name"),
        },
    }


@app.get("/lookup_by_postal_code")
def lookup_by_postal_code(
    postal_code: str = Query(..., description="Mã bưu chính của xã/phường (ví dụ 11120, 02006)"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    validate_key(key)
    clean_code = postal_code.strip()

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                ward_code, ward_name, ward_name_en, ward_full_name, ward_full_name_en, ward_code_name,
                postal_code, province_code, province_name, province_name_en, province_full_name,
                province_full_name_en, province_code_name, administrative_unit_short_name, area_km2
            FROM wards
            WHERE postal_code = ?
        """, (clean_code,)).fetchall()

    if not rows:
        return {
            "found": False,
            "postal_code": clean_code,
            "message": f"Không tìm thấy xã/phường nào có mã bưu chính {clean_code}",
        }

    matches = [dict(r) for r in rows]
    return {
        "found": True,
        "postal_code": clean_code,
        "match_count": len(matches),
        "result": matches[0],
        "all_matches": matches,
    }


@app.get("/lookup_by_code")
def lookup_by_code(
    ward_code: Optional[str] = Query(None, description="Mã xã/phường 5 chữ số (ví dụ 00004, 06970)"),
    province_code: Optional[str] = Query(None, description="Mã tỉnh/thành 2 chữ số (ví dụ 01, 22)"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    validate_key(key)

    if not ward_code and not province_code:
        raise HTTPException(status_code=400, detail="Cần cung cấp ít nhất ward_code hoặc province_code")

    with get_conn() as conn:
        if ward_code:
            clean_ward_code = ward_code.strip().zfill(5)
            row = conn.execute("""
                SELECT
                    ward_code, ward_name, ward_name_en, ward_full_name, ward_full_name_en, ward_code_name,
                    postal_code, province_code, province_name, province_name_en, province_full_name,
                    province_full_name_en, province_code_name, administrative_unit_short_name, area_km2
                FROM wards
                WHERE ward_code = ?
            """, (clean_ward_code,)).fetchone()

            if not row:
                return {
                    "found": False,
                    "ward_code": clean_ward_code,
                    "message": f"Không tìm thấy xã/phường có mã {clean_ward_code}",
                }

            return {
                "found": True,
                "ward_code": clean_ward_code,
                "result": dict(row),
            }

        if province_code:
            clean_prov_code = province_code.strip().zfill(2)
            row = conn.execute("""
                SELECT
                    province_code, province_name, province_name_en, province_full_name, province_full_name_en,
                    province_code_name, postal_code_prefix, administrative_unit_short_name,
                    administrative_unit_full_name, area_km2
                FROM provinces
                WHERE province_code = ?
            """, (clean_prov_code,)).fetchone()

            if not row:
                return {
                    "found": False,
                    "province_code": clean_prov_code,
                    "message": f"Không tìm thấy tỉnh/thành có mã {clean_prov_code}",
                }

            ward_count = conn.execute(
                "SELECT COUNT(*) AS n FROM wards WHERE province_code = ?",
                (clean_prov_code,)
            ).fetchone()["n"]

            result = dict(row)
            result["ward_count"] = ward_count

            return {
                "found": True,
                "province_code": clean_prov_code,
                "result": result,
            }