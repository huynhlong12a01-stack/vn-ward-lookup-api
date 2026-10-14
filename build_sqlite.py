import json
import sqlite3
import time
import urllib.request
import zipfile
import zlib
from pathlib import Path

from shapely.geometry import shape


DATA_DIR = Path("data")
DB_FILE = DATA_DIR / "wards_lookup.sqlite"
DB_FILE_TMP = DATA_DIR / "wards_lookup.sqlite.tmp"
ZIP_FILE = DATA_DIR / "vn_provinces_wards_geojson.zip"
UNITS_FILE = DATA_DIR / "full_json_generated_data_vn_units.json"
METADATA_FILE = DATA_DIR / "vn_provinces_metadata.json"
HISTORICAL_FILE = DATA_DIR / "historical_v2.4.1_vn_units.json"

REPO_RAW_BASE = "https://raw.githubusercontent.com/thanglequoc/vietnamese-provinces-database/master/json"
HISTORICAL_URL = "https://raw.githubusercontent.com/thanglequoc/vietnamese-provinces-database/v2.4.1/json/full_json_generated_data_vn_units.json"

TIMEFRAME_CURRENT = "Áp dụng từ ngày 20/09/2026 đến nay theo Nghị quyết 388/NQ-UBTVQH16 (Mô hình 2 cấp: 34 tỉnh/thành, 3.321 xã/phường)"
TIMEFRAME_HISTORICAL = "Áp dụng trước ngày 01/03/2025 theo mô hình 3 cấp (63 tỉnh/thành, 696 quận/huyện, 10.035 xã/phường/thị trấn)"
EFFECTIVE_FROM_CURRENT = "2026-09-20"
EFFECTIVE_TO_HISTORICAL = "2025-02-28"

# 5 đặc khu / huyện đảo trước đây không có xã trực thuộc
SPECIAL_ISLAND_MAPPINGS = {
    "11948": ("031", "Bạch Long Vĩ", "Huyện Bạch Long Vĩ", "31", "Hải Phòng", "Thành phố Hải Phòng"),
    "21548": ("522", "Lý Sơn", "Huyện Lý Sơn", "51", "Quảng Ngãi", "Tỉnh Quảng Ngãi"),
    "20333": ("498", "Hoàng Sa", "Huyện Hoàng Sa", "48", "Đà Nẵng", "Thành phố Đà Nẵng"),
    "19742": ("468", "Cồn Cỏ", "Huyện đảo Cồn Cỏ", "45", "Quảng Trị", "Tỉnh Quảng Trị"),
    "26732": ("754", "Côn Đảo", "Huyện Côn Đảo", "77", "Bà Rịa - Vũng Tàu", "Tỉnh Bà Rịa - Vũng Tàu"),
}


def ensure_file(path: Path, url: str):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Downloading {path.name} from {url}...")
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req) as resp, open(path, "wb") as out:
            out.write(resp.read())
        print(f"Downloaded {path.name} successfully.")


def main():
    start_time = time.time()
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Tải nếu thiếu
    ensure_file(METADATA_FILE, f"{REPO_RAW_BASE}/vn_provinces_metadata.json")
    ensure_file(UNITS_FILE, f"{REPO_RAW_BASE}/full_json_generated_data_vn_units.json")
    ensure_file(ZIP_FILE, f"{REPO_RAW_BASE}/vn_provinces_wards_geojson.zip")
    ensure_file(HISTORICAL_FILE, HISTORICAL_URL)

    # 2. Đọc metadata
    metadata = {}
    if METADATA_FILE.exists():
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    print("Metadata:", metadata)

    # 3. Đọc dữ liệu administrative units hiện hành
    with open(UNITS_FILE, "r", encoding="utf-8") as f:
        provinces_data = json.load(f)

    prov_map = {}
    ward_map = {}
    for p in provinces_data:
        p_code = str(p.get("Code", "")).zfill(2)
        prov_map[p_code] = p
        for w in p.get("Wards", []):
            w_code = str(w.get("Code", "")).zfill(5)
            ward_map[w_code] = (w, p)

    print(f"Loaded current units: {len(prov_map)} provinces, {len(ward_map)} wards.")

    # 4. Đọc dữ liệu hành chính lịch sử (v2.4.1)
    with open(HISTORICAL_FILE, "r", encoding="utf-8") as f:
        historical_data = json.load(f)

    hist_prov_map = {}
    hist_dist_map = {}
    hist_ward_map = {}

    for p in historical_data:
        p_code = str(p.get("Code", "")).zfill(2)
        hist_prov_map[p_code] = p
        for d in (p.get("District") or p.get("Districts", [])):
            d_code = str(d.get("Code", "")).zfill(3)
            hist_dist_map[d_code] = (d, p)
            for w in (d.get("Ward") or d.get("Wards", [])):
                w_code = str(w.get("Code", "")).zfill(5)
                hist_ward_map[w_code] = (w, d, p)

    print(f"Loaded historical units: {len(hist_prov_map)} provinces, {len(hist_dist_map)} districts, {len(hist_ward_map)} wards.")

    # 5. Chuẩn bị SQLite
    if DB_FILE_TMP.exists():
        DB_FILE_TMP.unlink()

    conn = sqlite3.connect(DB_FILE_TMP)
    cur = conn.cursor()

    cur.execute("PRAGMA journal_mode = OFF")
    cur.execute("PRAGMA synchronous = OFF")
    cur.execute("PRAGMA temp_store = MEMORY")
    cur.execute("PRAGMA cache_size = -64000")

    # Table metadata
    cur.execute("""
        CREATE TABLE metadata (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    """)

    for k, v in metadata.items():
        cur.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", (str(k), str(v)))
    cur.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", ("source_repo", "https://github.com/thanglequoc/vietnamese-provinces-database"))
    cur.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", ("built_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())))
    cur.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", ("timeframe_current", TIMEFRAME_CURRENT))
    cur.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", ("timeframe_historical", TIMEFRAME_HISTORICAL))
    cur.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", ("effective_from_current", EFFECTIVE_FROM_CURRENT))
    cur.execute("INSERT INTO metadata (key, value) VALUES (?, ?)", ("effective_to_historical", EFFECTIVE_TO_HISTORICAL))

    # Table provinces (hiện hành)
    cur.execute("""
        CREATE TABLE provinces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            province_code TEXT UNIQUE,
            province_name TEXT,
            province_name_en TEXT,
            province_full_name TEXT,
            province_full_name_en TEXT,
            province_code_name TEXT,
            postal_code_prefix TEXT,
            administrative_unit_short_name TEXT,
            administrative_unit_full_name TEXT,
            area_km2 REAL,
            applied_timeframe TEXT,
            effective_from TEXT,
            minx REAL,
            miny REAL,
            maxx REAL,
            maxy REAL,
            geom_wkb_zlib BLOB
        )
    """)

    # Table wards (hiện hành + ánh xạ lịch sử)
    cur.execute("""
        CREATE TABLE wards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ward_code TEXT UNIQUE,
            ward_name TEXT,
            ward_name_en TEXT,
            ward_full_name TEXT,
            ward_full_name_en TEXT,
            ward_code_name TEXT,
            postal_code TEXT,
            province_code TEXT,
            province_name TEXT,
            province_name_en TEXT,
            province_full_name TEXT,
            province_full_name_en TEXT,
            province_code_name TEXT,
            administrative_unit_short_name TEXT,
            administrative_unit_full_name TEXT,
            area_km2 REAL,
            applied_timeframe TEXT,
            effective_from TEXT,
            historical_district_code TEXT,
            historical_district_name TEXT,
            historical_district_full_name TEXT,
            historical_province_code TEXT,
            historical_province_name TEXT,
            historical_province_full_name TEXT,
            historical_applied_timeframe TEXT,
            historical_effective_to TEXT,
            minx REAL,
            miny REAL,
            maxx REAL,
            maxy REAL,
            geom_wkb_zlib BLOB
        )
    """)

    # Table historical_provinces (63 tỉnh/thành cũ)
    cur.execute("""
        CREATE TABLE historical_provinces (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            province_code TEXT UNIQUE,
            province_name TEXT,
            province_name_en TEXT,
            province_full_name TEXT,
            province_full_name_en TEXT,
            province_code_name TEXT,
            region_name TEXT,
            administrative_unit_full_name TEXT,
            applied_timeframe TEXT,
            effective_to TEXT
        )
    """)

    # Table historical_districts (696 quận/huyện cũ)
    cur.execute("""
        CREATE TABLE historical_districts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            district_code TEXT UNIQUE,
            district_name TEXT,
            district_name_en TEXT,
            district_full_name TEXT,
            district_full_name_en TEXT,
            district_code_name TEXT,
            province_code TEXT,
            province_name TEXT,
            province_full_name TEXT,
            administrative_unit_full_name TEXT,
            applied_timeframe TEXT,
            effective_to TEXT
        )
    """)

    # Table historical_wards (10.035 xã/phường cũ)
    cur.execute("""
        CREATE TABLE historical_wards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ward_code TEXT UNIQUE,
            ward_name TEXT,
            ward_name_en TEXT,
            ward_full_name TEXT,
            ward_full_name_en TEXT,
            ward_code_name TEXT,
            district_code TEXT,
            district_name TEXT,
            district_full_name TEXT,
            province_code TEXT,
            province_name TEXT,
            province_full_name TEXT,
            administrative_unit_full_name TEXT,
            applied_timeframe TEXT,
            effective_to TEXT
        )
    """)

    conn.commit()

    # 6. Import dữ liệu lịch sử vào các bảng historical_*
    print("Importing historical provinces, districts, wards...")
    for p_code, p in hist_prov_map.items():
        cur.execute("""
            INSERT INTO historical_provinces (
                province_code, province_name, province_name_en, province_full_name, province_full_name_en,
                province_code_name, region_name, administrative_unit_full_name, applied_timeframe, effective_to
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            p_code,
            p.get("Name"),
            p.get("NameEn"),
            p.get("FullName"),
            p.get("FullNameEn"),
            p.get("CodeName"),
            p.get("AdministrativeRegionName"),
            p.get("AdministrativeUnitFullName"),
            TIMEFRAME_HISTORICAL,
            EFFECTIVE_TO_HISTORICAL,
        ))

    for d_code, (d, p) in hist_dist_map.items():
        cur.execute("""
            INSERT INTO historical_districts (
                district_code, district_name, district_name_en, district_full_name, district_full_name_en,
                district_code_name, province_code, province_name, province_full_name, administrative_unit_full_name,
                applied_timeframe, effective_to
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            d_code,
            d.get("Name"),
            d.get("NameEn"),
            d.get("FullName"),
            d.get("FullNameEn"),
            d.get("CodeName"),
            str(p.get("Code", "")).zfill(2),
            p.get("Name"),
            p.get("FullName"),
            d.get("AdministrativeUnitFullName"),
            TIMEFRAME_HISTORICAL,
            EFFECTIVE_TO_HISTORICAL,
        ))

    for w_code, (w, d, p) in hist_ward_map.items():
        cur.execute("""
            INSERT INTO historical_wards (
                ward_code, ward_name, ward_name_en, ward_full_name, ward_full_name_en,
                ward_code_name, district_code, district_name, district_full_name,
                province_code, province_name, province_full_name, administrative_unit_full_name,
                applied_timeframe, effective_to
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            w_code,
            w.get("Name"),
            w.get("NameEn"),
            w.get("FullName"),
            w.get("FullNameEn"),
            w.get("CodeName"),
            str(d.get("Code", "")).zfill(3),
            d.get("Name"),
            d.get("FullName"),
            str(p.get("Code", "")).zfill(2),
            p.get("Name"),
            p.get("FullName"),
            w.get("AdministrativeUnitFullName"),
            TIMEFRAME_HISTORICAL,
            EFFECTIVE_TO_HISTORICAL,
        ))

    conn.commit()
    print(f"Historical import done: {len(hist_prov_map)} provinces, {len(hist_dist_map)} districts, {len(hist_ward_map)} wards.")

    # 7. Đọc GeoJSON từ file zip
    print(f"Reading geojson from {ZIP_FILE}...")
    with zipfile.ZipFile(ZIP_FILE, "r") as zf:
        namelist = zf.namelist()

        prov_files = [n for n in namelist if "/wards/" not in n and n.endswith(".geojson")]
        ward_files = [n for n in namelist if "/wards/" in n and n.endswith(".geojson")]

        print(f"Found in zip: {len(prov_files)} province geojsons, {len(ward_files)} ward geojsons.")

        # Import provinces
        prov_count = 0
        for name in prov_files:
            content = json.loads(zf.read(name).decode("utf-8"))
            features = content.get("features", [])
            if not features:
                continue

            feature = features[0]
            props = feature.get("properties", {})
            geom_data = feature.get("geometry")
            if not geom_data:
                continue

            p_code = str(props.get("code", "")).zfill(2)
            p_unit = prov_map.get(p_code, {})

            geom = shape(geom_data)
            minx, miny, maxx, maxy = geom.bounds
            geom_blob = zlib.compress(geom.wkb, level=9)

            row = (
                p_code,
                p_unit.get("Name") or props.get("name"),
                p_unit.get("NameEn") or props.get("nameEn"),
                p_unit.get("FullName") or props.get("fullName"),
                p_unit.get("FullNameEn") or props.get("fullNameEn"),
                p_unit.get("CodeName") or props.get("codeName"),
                p_unit.get("PostalCodePrefix") or props.get("postalCodePrefix"),
                p_unit.get("AdministrativeUnitShortName"),
                p_unit.get("AdministrativeUnitFullName"),
                props.get("areaKm2"),
                TIMEFRAME_CURRENT,
                EFFECTIVE_FROM_CURRENT,
                minx,
                miny,
                maxx,
                maxy,
                geom_blob,
            )

            cur.execute("""
                INSERT INTO provinces (
                    province_code,
                    province_name,
                    province_name_en,
                    province_full_name,
                    province_full_name_en,
                    province_code_name,
                    postal_code_prefix,
                    administrative_unit_short_name,
                    administrative_unit_full_name,
                    area_km2,
                    applied_timeframe,
                    effective_from,
                    minx,
                    miny,
                    maxx,
                    maxy,
                    geom_wkb_zlib
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, row)
            prov_count += 1

        conn.commit()
        print(f"Imported {prov_count} current provinces.")

        # Import wards
        ward_count = 0
        for name in ward_files:
            content = json.loads(zf.read(name).decode("utf-8"))
            features = content.get("features", [])
            if not features:
                continue

            feature = features[0]
            props = feature.get("properties", {})
            geom_data = feature.get("geometry")
            if not geom_data:
                continue

            w_code = str(props.get("code", "")).zfill(5)
            unit_pair = ward_map.get(w_code)
            w_unit = unit_pair[0] if unit_pair else {}
            p_unit = unit_pair[1] if unit_pair else {}

            p_code = str(w_unit.get("ProvinceCode") or "").zfill(2)
            if not p_code and p_unit:
                p_code = str(p_unit.get("Code", "")).zfill(2)

            geom = shape(geom_data)
            minx, miny, maxx, maxy = geom.bounds
            geom_blob = zlib.compress(geom.wkb, level=9)

            # Ánh xạ lịch sử cho xã này
            hist_d_code = None
            hist_d_name = None
            hist_d_full_name = None
            hist_p_code = None
            hist_p_name = None
            hist_p_full_name = None

            if w_code in hist_ward_map:
                hw, hd, hp = hist_ward_map[w_code]
                hist_d_code = str(hd.get("Code", "")).zfill(3)
                hist_d_name = hd.get("Name")
                hist_d_full_name = hd.get("FullName")
                hist_p_code = str(hp.get("Code", "")).zfill(2)
                hist_p_name = hp.get("Name")
                hist_p_full_name = hp.get("FullName")
            elif w_code in SPECIAL_ISLAND_MAPPINGS:
                m = SPECIAL_ISLAND_MAPPINGS[w_code]
                hist_d_code = m[0]
                hist_d_name = m[1]
                hist_d_full_name = m[2]
                hist_p_code = m[3]
                hist_p_name = m[4]
                hist_p_full_name = m[5]

            row = (
                w_code,
                w_unit.get("Name") or props.get("name"),
                w_unit.get("NameEn") or props.get("nameEn"),
                w_unit.get("FullName") or props.get("fullName"),
                w_unit.get("FullNameEn") or props.get("fullNameEn"),
                w_unit.get("CodeName") or props.get("codeName"),
                w_unit.get("PostalCode") or props.get("postalCode"),
                p_code,
                p_unit.get("Name"),
                p_unit.get("NameEn"),
                p_unit.get("FullName"),
                p_unit.get("FullNameEn"),
                p_unit.get("CodeName"),
                w_unit.get("AdministrativeUnitShortName"),
                w_unit.get("AdministrativeUnitFullName"),
                props.get("areaKm2"),
                TIMEFRAME_CURRENT,
                EFFECTIVE_FROM_CURRENT,
                hist_d_code,
                hist_d_name,
                hist_d_full_name,
                hist_p_code,
                hist_p_name,
                hist_p_full_name,
                TIMEFRAME_HISTORICAL if hist_d_name else None,
                EFFECTIVE_TO_HISTORICAL if hist_d_name else None,
                minx,
                miny,
                maxx,
                maxy,
                geom_blob,
            )

            cur.execute("""
                INSERT INTO wards (
                    ward_code,
                    ward_name,
                    ward_name_en,
                    ward_full_name,
                    ward_full_name_en,
                    ward_code_name,
                    postal_code,
                    province_code,
                    province_name,
                    province_name_en,
                    province_full_name,
                    province_full_name_en,
                    province_code_name,
                    administrative_unit_short_name,
                    administrative_unit_full_name,
                    area_km2,
                    applied_timeframe,
                    effective_from,
                    historical_district_code,
                    historical_district_name,
                    historical_district_full_name,
                    historical_province_code,
                    historical_province_name,
                    historical_province_full_name,
                    historical_applied_timeframe,
                    historical_effective_to,
                    minx,
                    miny,
                    maxx,
                    maxy,
                    geom_wkb_zlib
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, row)
            ward_count += 1

            if ward_count % 500 == 0:
                conn.commit()
                print(f"Imported {ward_count} current wards...")

        conn.commit()
        print(f"Imported {ward_count} current wards.")

    # 8. Đánh index
    print("Creating indices...")
    cur.execute("CREATE INDEX idx_bbox_minx ON wards(minx)")
    cur.execute("CREATE INDEX idx_bbox_maxx ON wards(maxx)")
    cur.execute("CREATE INDEX idx_bbox_miny ON wards(miny)")
    cur.execute("CREATE INDEX idx_bbox_maxy ON wards(maxy)")
    cur.execute("CREATE INDEX idx_ward_code ON wards(ward_code)")
    cur.execute("CREATE INDEX idx_province_code ON wards(province_code)")
    cur.execute("CREATE INDEX idx_postal_code ON wards(postal_code)")
    cur.execute("CREATE INDEX idx_hist_d_code ON wards(historical_district_code)")
    cur.execute("CREATE INDEX idx_hist_p_code ON wards(historical_province_code)")

    cur.execute("CREATE INDEX idx_prov_bbox_minx ON provinces(minx)")
    cur.execute("CREATE INDEX idx_prov_bbox_maxx ON provinces(maxx)")
    cur.execute("CREATE INDEX idx_prov_bbox_miny ON provinces(miny)")
    cur.execute("CREATE INDEX idx_prov_bbox_maxy ON provinces(maxy)")
    cur.execute("CREATE INDEX idx_prov_code ON provinces(province_code)")

    cur.execute("CREATE INDEX idx_hp_code ON historical_provinces(province_code)")
    cur.execute("CREATE INDEX idx_hp_name ON historical_provinces(province_name)")
    cur.execute("CREATE INDEX idx_hd_code ON historical_districts(district_code)")
    cur.execute("CREATE INDEX idx_hd_name ON historical_districts(district_name)")
    cur.execute("CREATE INDEX idx_hd_pcode ON historical_districts(province_code)")
    cur.execute("CREATE INDEX idx_hw_code ON historical_wards(ward_code)")
    cur.execute("CREATE INDEX idx_hw_name ON historical_wards(ward_name)")
    cur.execute("CREATE INDEX idx_hw_dcode ON historical_wards(district_code)")
    cur.execute("CREATE INDEX idx_hw_pcode ON historical_wards(province_code)")

    conn.commit()
    print("Running VACUUM...")
    cur.execute("VACUUM")
    conn.close()

    # 9. Thay thế file SQLite cũ
    if DB_FILE.exists():
        backup_file = DATA_DIR / "wards_lookup.sqlite.bak"
        if backup_file.exists():
            backup_file.unlink()
        DB_FILE.rename(backup_file)

    DB_FILE_TMP.rename(DB_FILE)

    elapsed = time.time() - start_time
    size_mb = DB_FILE.stat().st_size / 1024 / 1024
    print(f"Done in {elapsed:.2f}s!")
    print(f"Database: {DB_FILE} ({size_mb:.2f} MB)")
    print(f"Current: {prov_count} provinces, {ward_count} wards.")
    print(f"Historical: {len(hist_prov_map)} provinces, {len(hist_dist_map)} districts, {len(hist_ward_map)} wards.")


if __name__ == "__main__":
    main()