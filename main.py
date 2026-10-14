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

TIMEFRAME_CURRENT = "Áp dụng từ ngày 20/09/2026 đến nay theo Nghị quyết 388/NQ-UBTVQH16 (Mô hình 2 cấp: 34 tỉnh/thành, 3.321 xã/phường)"
TIMEFRAME_HISTORICAL = "Áp dụng trước ngày 01/03/2025 theo mô hình 3 cấp (63 tỉnh/thành, 696 quận/huyện, 10.035 xã/phường/thị trấn)"
EFFECTIVE_FROM_CURRENT = "2026-09-20"
EFFECTIVE_TO_HISTORICAL = "2025-02-28"


app = FastAPI(
    title="Vietnam Administrative Units & Ward Lookup API",
    description="Tra cứu ranh giới địa giới hành chính Việt Nam hiện hành (Nghị quyết 388/NQ-UBTVQH16) và đối chiếu đơn vị hành chính lịch sử (thời kỳ 63 tỉnh/thành & cấp Quận/Huyện).",
    version="2.7.0-v5.2.0-historical",
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
                # Các trường hiện hành cấp cao nhất (giữ nguyên tương thích ngược)
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

                # Thông tin đơn vị hành chính lịch sử đối ứng (áp dụng trước 01/03/2025)
                "historical_ward_name": row["historical_ward_name"],
                "historical_ward_full_name": row["historical_ward_full_name"],
                "historical_district_code": row["historical_district_code"],
                "historical_district_name": row["historical_district_name"],
                "historical_district_full_name": row["historical_district_full_name"],
                "historical_province_code": row["historical_province_code"],
                "historical_province_name": row["historical_province_name"],
                "historical_province_full_name": row["historical_province_full_name"],

                # Khối phân định rõ ràng 2 thời kỳ kèm mốc thời gian áp dụng
                "current_period": {
                    "status": "Hiện hành",
                    "applied_timeframe": row["applied_timeframe"] or TIMEFRAME_CURRENT,
                    "effective_from": row["effective_from"] or EFFECTIVE_FROM_CURRENT,
                    "legal_basis": "Nghị quyết 388/NQ-UBTVQH16 (Mô hình 2 cấp chính quyền)",
                    "ward_code": row["ward_code"],
                    "ward_name": row["ward_name"],
                    "ward_full_name": row["ward_full_name"],
                    "province_code": row["province_code"],
                    "province_name": row["province_name"],
                    "province_full_name": row["province_full_name"],
                    "postal_code": row["postal_code"],
                },
                "historical_period": {
                    "status": "Lịch sử / Cũ",
                    "applied_timeframe": row["historical_applied_timeframe"] or TIMEFRAME_HISTORICAL,
                    "effective_to": row["historical_effective_to"] or EFFECTIVE_TO_HISTORICAL,
                    "legal_basis": "Trước QĐ 19/2025/QĐ-TTg (Thời kỳ 63 tỉnh/thành & cấp Quận/Huyện)",
                    "ward_name": row["historical_ward_full_name"] or row["historical_ward_name"] or row["ward_full_name"],
                    "district_code": row["historical_district_code"],
                    "district_name": row["historical_district_full_name"] or row["historical_district_name"],
                    "district_short_name": row["historical_district_name"],
                    "district_full_name": row["historical_district_full_name"],
                    "province_code": row["historical_province_code"],
                    "province_name": row["historical_province_full_name"] or row["historical_province_name"],
                    "province_short_name": row["historical_province_name"],
                    "province_full_name": row["historical_province_full_name"],
                } if row["historical_district_name"] else None,
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
                "applied_timeframe": row["applied_timeframe"] or TIMEFRAME_CURRENT,
                "effective_from": row["effective_from"] or EFFECTIVE_FROM_CURRENT,
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
    counts = {}
    try:
        with get_conn() as conn:
            meta_rows = conn.execute("SELECT key, value FROM metadata").fetchall()
            meta = {r["key"]: r["value"] for r in meta_rows}
            counts["current_wards"] = conn.execute("SELECT COUNT(*) FROM wards").fetchone()[0]
            counts["current_provinces"] = conn.execute("SELECT COUNT(*) FROM provinces").fetchone()[0]
            counts["historical_provinces"] = conn.execute("SELECT COUNT(*) FROM historical_provinces").fetchone()[0]
            counts["historical_districts"] = conn.execute("SELECT COUNT(*) FROM historical_districts").fetchone()[0]
            counts["historical_wards"] = conn.execute("SELECT COUNT(*) FROM historical_wards").fetchone()[0]
    except Exception:
        pass

    return {
        "message": "Vietnam Administrative Units & Ward Lookup API",
        "version": "2.7.0-v5.2.0-historical",
        "dataset_version": meta.get("DatasetVersion", "v5.2.0"),
        "latest_decree": meta.get("LatestDecree", "388/NQ-UBTVQH16"),
        "timeframes": {
            "current": {
                "period": "Hiện hành",
                "applied_timeframe": meta.get("timeframe_current", TIMEFRAME_CURRENT),
                "effective_from": meta.get("effective_from_current", EFFECTIVE_FROM_CURRENT),
                "province_count": counts.get("current_provinces", 34),
                "ward_count": counts.get("current_wards", 3321),
            },
            "historical": {
                "period": "Lịch sử / Cũ",
                "applied_timeframe": meta.get("timeframe_historical", TIMEFRAME_HISTORICAL),
                "effective_to": meta.get("effective_to_historical", EFFECTIVE_TO_HISTORICAL),
                "province_count": counts.get("historical_provinces", 63),
                "district_count": counts.get("historical_districts", 696),
                "ward_count": counts.get("historical_wards", 10035),
            },
        },
        "endpoints": {
            "coordinates_lookup": "/lookup?lat=10.7769&lon=106.7009",
            "coordinates_text": "/lookup_text?lat=10.7769&lon=106.7009",
            "historical_search": "/historical/search?q=Quận 1",
            "historical_provinces": "/historical/provinces",
            "historical_districts": "/historical/districts?province_code=01",
            "historical_wards": "/historical/wards?district_code=001",
            "compare_ward": "/historical/compare?ward_code=26740",
            "postal_code_lookup": "/lookup_by_postal_code?postal_code=11120",
            "code_lookup": "/lookup_by_code?ward_code=06970",
            "interactive_ui": "/ui",
            "swagger_docs": "/docs",
        },
        "source_repo": meta.get("source_repo", "https://github.com/thanglequoc/vietnamese-provinces-database"),
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
        hp_total = conn.execute("SELECT COUNT(*) AS n FROM historical_provinces").fetchone()["n"]
        hd_total = conn.execute("SELECT COUNT(*) AS n FROM historical_districts").fetchone()["n"]
        hw_total = conn.execute("SELECT COUNT(*) AS n FROM historical_wards").fetchone()["n"]
        meta_rows = conn.execute("SELECT key, value FROM metadata").fetchall()
        meta = {r["key"]: r["value"] for r in meta_rows}

    return {
        "status": "ok",
        "database": str(DB_FILE),
        "current_units": {
            "provinces": prov_total,
            "wards": ward_total,
            "timeframe": meta.get("timeframe_current", TIMEFRAME_CURRENT),
            "effective_from": meta.get("effective_from_current", EFFECTIVE_FROM_CURRENT),
        },
        "historical_units": {
            "provinces": hp_total,
            "districts": hd_total,
            "wards": hw_total,
            "timeframe": meta.get("timeframe_historical", TIMEFRAME_HISTORICAL),
            "effective_to": meta.get("effective_to_historical", EFFECTIVE_TO_HISTORICAL),
        },
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
                "timeframe_notes": {
                    "current": TIMEFRAME_CURRENT,
                    "historical": TIMEFRAME_HISTORICAL,
                },
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
                    "current_period": {
                        "status": "Hiện hành",
                        "applied_timeframe": TIMEFRAME_CURRENT,
                        "effective_from": EFFECTIVE_FROM_CURRENT,
                        "province_name": prov["province_full_name"],
                    },
                    "historical_period": None,
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
        "timeframe_notes": {
            "current": TIMEFRAME_CURRENT,
            "historical": TIMEFRAME_HISTORICAL,
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
    include_historical: bool = Query(False, description="Bao gồm cả thông tin huyện/tỉnh lịch sử"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    """
    Trả text gọn cho Excel/HTML:
    Mặc định: Sai Gon|Ho Chi Minh
    Khi include_historical=true: Sai Gon|Ho Chi Minh|Quan 1|Ho Chi Minh
    """
    validate_key(key)
    validate_lat_lon(lat, lon)

    _, result = get_first_result(lat, lon)

    if not result:
        _, p_matches = lookup_province_core(lat, lon)
        if p_matches:
            prov = p_matches[0]
            prov_en = prov.get("province_name_en") or prov.get("province_name") or ""
            return PlainTextResponse(f"|{prov_en}", headers={"Cache-Control": "no-store"})

        return PlainTextResponse("NOT_FOUND", headers={"Cache-Control": "no-store"})

    ward_en = result.get("ward_name_en") or result.get("ward_name") or ""
    province_en = result.get("province_name_en") or result.get("province_name") or ""

    if include_historical:
        hist_w = result.get("historical_ward_name") or result.get("ward_name") or ""
        hist_d = result.get("historical_district_full_name") or result.get("historical_district_name") or ""
        hist_p = result.get("historical_province_name") or ""
        return PlainTextResponse(
            f"{ward_en}|{province_en}|{hist_w}|{hist_d}|{hist_p}",
            headers={"Cache-Control": "no-store"}
        )

    return PlainTextResponse(
        f"{ward_en}|{province_en}",
        headers={"Cache-Control": "no-store"},
    )


@app.get("/lookup_ward_text", response_class=PlainTextResponse)
def lookup_ward_text(
    lat: float = Query(..., description="Latitude / vĩ độ"),
    lon: float = Query(..., description="Longitude / kinh độ"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    validate_key(key)
    validate_lat_lon(lat, lon)

    _, result = get_first_result(lat, lon)

    if not result:
        return PlainTextResponse("NOT_FOUND", headers={"Cache-Control": "no-store"})

    ward_en = result.get("ward_name_en") or result.get("ward_name") or "NOT_FOUND"
    return PlainTextResponse(ward_en, headers={"Cache-Control": "no-store"})


@app.get("/lookup_province_text", response_class=PlainTextResponse)
def lookup_province_text(
    lat: float = Query(..., description="Latitude / vĩ độ"),
    lon: float = Query(..., description="Longitude / kinh độ"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    validate_key(key)
    validate_lat_lon(lat, lon)

    _, result = get_first_result(lat, lon)

    if not result:
        _, p_matches = lookup_province_core(lat, lon)
        if p_matches:
            prov_en = p_matches[0].get("province_name_en") or p_matches[0].get("province_name") or "NOT_FOUND"
            return PlainTextResponse(prov_en, headers={"Cache-Control": "no-store"})

        return PlainTextResponse("NOT_FOUND", headers={"Cache-Control": "no-store"})

    province_en = result.get("province_name_en") or result.get("province_name") or "NOT_FOUND"
    return PlainTextResponse(province_en, headers={"Cache-Control": "no-store"})


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
            "input": {"lat": lat, "lon": lon},
            "candidate_count": len(rows),
            "message": "Không tìm thấy xã/phường chứa điểm này.",
        }

    return {
        "found": True,
        "input": {"lat": lat, "lon": lon},
        "result": {
            "ward_code": result.get("ward_code"),
            "ward_name": result.get("ward_name"),
            "ward_name_en": result.get("ward_name_en"),
            "ward_full_name": result.get("ward_full_name"),
            "ward_full_name_en": result.get("ward_full_name_en"),
            "ward_code_name": result.get("ward_code_name"),
            "postal_code": result.get("postal_code"),
            "area_km2": result.get("area_km2"),
            "historical_district_full_name": result.get("historical_district_full_name"),
            "applied_timeframe": result.get("current_period", {}).get("applied_timeframe"),
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
                "input": {"lat": lat, "lon": lon},
                "candidate_count": len(p_rows),
                "result": {
                    "province_code": prov.get("province_code"),
                    "province_name": prov.get("province_name"),
                    "province_name_en": prov.get("province_name_en"),
                    "province_full_name": prov.get("province_full_name"),
                    "province_full_name_en": prov.get("province_full_name_en"),
                    "province_code_name": prov.get("province_code_name"),
                    "postal_code_prefix": prov.get("postal_code_prefix"),
                    "applied_timeframe": prov.get("applied_timeframe"),
                },
            }

        return {
            "found": False,
            "input": {"lat": lat, "lon": lon},
            "candidate_count": len(rows),
            "message": "Không tìm thấy tỉnh/thành chứa điểm này.",
        }

    return {
        "found": True,
        "input": {"lat": lat, "lon": lon},
        "result": {
            "province_code": result.get("province_code"),
            "province_name": result.get("province_name"),
            "province_name_en": result.get("province_name_en"),
            "province_full_name": result.get("province_full_name"),
            "province_full_name_en": result.get("province_full_name_en"),
            "province_code_name": result.get("province_code_name"),
            "applied_timeframe": result.get("current_period", {}).get("applied_timeframe"),
        },
    }


# =====================================================================
# CÁC ENDPOINT TRA CỨU ĐƠN VỊ HÀNH CHÍNH LỊCH SỬ (THỜI KỲ CŨ)
# =====================================================================

@app.get("/historical/search")
def historical_search(
    q: str = Query(..., description="Từ khóa tìm kiếm tỉnh, huyện, hoặc xã cũ (ví dụ: 'Ba Đình', 'Quận 1', 'Bến Nghé', 'Hà Tây', 'Từ Liêm')"),
    limit: int = Query(20, ge=1, le=100, description="Số lượng kết quả tối đa cho mỗi nhóm"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    """
    Tìm kiếm đơn vị hành chính thời kỳ lịch sử (trước 01/03/2025):
    Tìm đồng thời trong danh mục 63 tỉnh/thành, 696 quận/huyện, và 10.035 xã/phường.
    Mỗi kết quả đều ghi chú rõ mốc thời gian áp dụng.
    """
    validate_key(key)
    keyword = f"%{q.strip()}%"

    with get_conn() as conn:
        provinces = conn.execute("""
            SELECT province_code, province_name, province_full_name, province_name_en,
                   region_name, administrative_unit_full_name, applied_timeframe, effective_to
            FROM historical_provinces
            WHERE province_name LIKE ? OR province_full_name LIKE ? OR province_code_name LIKE ?
            LIMIT ?
        """, (keyword, keyword, keyword, limit)).fetchall()

        districts = conn.execute("""
            SELECT district_code, district_name, district_full_name, district_name_en,
                   province_code, province_name, province_full_name,
                   administrative_unit_full_name, applied_timeframe, effective_to
            FROM historical_districts
            WHERE district_name LIKE ? OR district_full_name LIKE ? OR district_code_name LIKE ?
            LIMIT ?
        """, (keyword, keyword, keyword, limit)).fetchall()

        wards = conn.execute("""
            SELECT ward_code, ward_name, ward_full_name, ward_name_en,
                   district_code, district_name, district_full_name,
                   province_code, province_name, province_full_name,
                   administrative_unit_full_name, applied_timeframe, effective_to
            FROM historical_wards
            WHERE ward_name LIKE ? OR ward_full_name LIKE ? OR ward_code_name LIKE ?
            LIMIT ?
        """, (keyword, keyword, keyword, limit)).fetchall()

    return {
        "query": q,
        "timeframe_note": TIMEFRAME_HISTORICAL,
        "effective_to": EFFECTIVE_TO_HISTORICAL,
        "total_matches": len(provinces) + len(districts) + len(wards),
        "results": {
            "provinces": [dict(r) for r in provinces],
            "districts": [dict(r) for r in districts],
            "wards": [dict(r) for r in wards],
        },
    }


@app.get("/historical/provinces")
def get_historical_provinces(key: Optional[str] = Query(None)):
    """
    Danh sách 63 tỉnh/thành phố thời kỳ lịch sử (trước 01/03/2025).
    """
    validate_key(key)
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT province_code, province_name, province_name_en, province_full_name,
                   province_full_name_en, province_code_name, region_name,
                   administrative_unit_full_name, applied_timeframe, effective_to
            FROM historical_provinces
            ORDER BY CAST(province_code AS INTEGER)
        """).fetchall()

    return {
        "count": len(rows),
        "applied_timeframe": TIMEFRAME_HISTORICAL,
        "effective_to": EFFECTIVE_TO_HISTORICAL,
        "provinces": [dict(r) for r in rows],
    }


@app.get("/historical/districts")
def get_historical_districts(
    province_code: str = Query(..., description="Mã tỉnh lịch sử (ví dụ 01 cho Hà Nội, 79 cho TP.HCM)"),
    key: Optional[str] = Query(None),
):
    """
    Danh sách các quận/huyện/thị xã thuộc một tỉnh trong thời kỳ lịch sử.
    """
    validate_key(key)
    clean_pcode = province_code.strip().zfill(2)

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT district_code, district_name, district_name_en, district_full_name,
                   district_full_name_en, district_code_name, province_code, province_name,
                   province_full_name, administrative_unit_full_name, applied_timeframe, effective_to
            FROM historical_districts
            WHERE province_code = ?
            ORDER BY CAST(district_code AS INTEGER)
        """, (clean_pcode,)).fetchall()

    return {
        "province_code": clean_pcode,
        "applied_timeframe": TIMEFRAME_HISTORICAL,
        "effective_to": EFFECTIVE_TO_HISTORICAL,
        "district_count": len(rows),
        "districts": [dict(r) for r in rows],
    }


@app.get("/historical/wards")
def get_historical_wards(
    district_code: str = Query(..., description="Mã quận/huyện lịch sử (ví dụ 001 cho Ba Đình, 760 cho Quận 1)"),
    key: Optional[str] = Query(None),
):
    """
    Danh sách các xã/phường/thị trấn thuộc một quận/huyện trong thời kỳ lịch sử.
    """
    validate_key(key)
    clean_dcode = district_code.strip().zfill(3)

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT ward_code, ward_name, ward_name_en, ward_full_name,
                   ward_full_name_en, ward_code_name, district_code, district_name,
                   district_full_name, province_code, province_name, province_full_name,
                   administrative_unit_full_name, applied_timeframe, effective_to
            FROM historical_wards
            WHERE district_code = ?
            ORDER BY CAST(ward_code AS INTEGER)
        """, (clean_dcode,)).fetchall()

    return {
        "district_code": clean_dcode,
        "applied_timeframe": TIMEFRAME_HISTORICAL,
        "effective_to": EFFECTIVE_TO_HISTORICAL,
        "ward_count": len(rows),
        "wards": [dict(r) for r in rows],
    }


@app.get("/historical/compare")
def compare_ward(
    ward_code: str = Query(..., description="Mã xã/phường (ví dụ 00004, 26740, 06970)"),
    key: Optional[str] = Query(None),
):
    """
    So sánh chi tiết một đơn vị hành chính qua 2 thời kỳ:
    - Thời kỳ Lịch sử (trước 01/03/2025): Thuộc quận/huyện nào, tỉnh nào cũ.
    - Thời kỳ Hiện hành (từ 20/09/2026): Thuộc tỉnh nào mới, mã bưu chính là gì.
    """
    validate_key(key)
    clean_code = ward_code.strip().zfill(5)

    with get_conn() as conn:
        current_ward = conn.execute("""
            SELECT ward_code, ward_name, ward_full_name, postal_code,
                   province_code, province_name, province_full_name,
                   applied_timeframe, effective_from
            FROM wards
            WHERE ward_code = ?
        """, (clean_code,)).fetchone()

        hist_ward = conn.execute("""
            SELECT ward_code, ward_name, ward_full_name,
                   district_code, district_name, district_full_name,
                   province_code, province_name, province_full_name,
                   applied_timeframe, effective_to
            FROM historical_wards
            WHERE ward_code = ?
        """, (clean_code,)).fetchone()

    if not current_ward and not hist_ward:
        return {
            "found": False,
            "ward_code": clean_code,
            "message": f"Không tìm thấy đơn vị hành chính với mã {clean_code}",
        }

    return {
        "found": True,
        "ward_code": clean_code,
        "comparison": {
            "current_period": {
                "status": "Hiện hành",
                "applied_timeframe": current_ward["applied_timeframe"] if current_ward else None,
                "effective_from": current_ward["effective_from"] if current_ward else None,
                "legal_basis": "Nghị quyết 388/NQ-UBTVQH16 (Mô hình 2 cấp)",
                "ward_name": current_ward["ward_full_name"] if current_ward else "Đã sáp nhập / thay đổi",
                "province_name": current_ward["province_full_name"] if current_ward else None,
                "postal_code": current_ward["postal_code"] if current_ward else None,
            } if current_ward else None,
            "historical_period": {
                "status": "Lịch sử / Cũ",
                "applied_timeframe": hist_ward["applied_timeframe"] if hist_ward else None,
                "effective_to": hist_ward["effective_to"] if hist_ward else None,
                "legal_basis": "Thời kỳ 63 tỉnh/thành & cấp Huyện/Quận (trước 01/03/2025)",
                "ward_name": hist_ward["ward_full_name"] if hist_ward else None,
                "district_name": hist_ward["district_full_name"] if hist_ward else None,
                "province_name": hist_ward["province_full_name"] if hist_ward else None,
            } if hist_ward else None,
        },
    }


# =====================================================================
# CÁC ENDPOINT TRA CỨU MÃ BƯU CHÍNH VÀ MÃ ĐƠN VỊ
# =====================================================================

@app.get("/lookup_by_postal_code")
def lookup_by_postal_code(
    postal_code: str = Query(..., description="Mã bưu chính của xã/phường (ví dụ 11120, 02006, 71016)"),
    key: Optional[str] = Query(None, description="API key nếu có bật"),
):
    validate_key(key)
    clean_code = postal_code.strip()

    with get_conn() as conn:
        rows = conn.execute("""
            SELECT
                ward_code, ward_name, ward_name_en, ward_full_name, ward_full_name_en, ward_code_name,
                postal_code, province_code, province_name, province_name_en, province_full_name,
                province_full_name_en, province_code_name, administrative_unit_short_name, area_km2,
                applied_timeframe, effective_from, historical_district_full_name, historical_province_full_name
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
    ward_code: Optional[str] = Query(None, description="Mã xã/phường 5 chữ số (ví dụ 00004, 06970, 26740)"),
    province_code: Optional[str] = Query(None, description="Mã tỉnh/thành 2 chữ số (ví dụ 01, 22, 79)"),
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
                    province_full_name_en, province_code_name, administrative_unit_short_name, area_km2,
                    applied_timeframe, effective_from, historical_district_full_name, historical_province_full_name
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
                    administrative_unit_full_name, area_km2, applied_timeframe, effective_from
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