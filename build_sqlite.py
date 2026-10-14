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

REPO_RAW_BASE = "https://raw.githubusercontent.com/thanglequoc/vietnamese-provinces-database/master/json"


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

    # 2. Đọc metadata
    metadata = {}
    if METADATA_FILE.exists():
        with open(METADATA_FILE, "r", encoding="utf-8") as f:
            metadata = json.load(f)
    print("Metadata:", metadata)

    # 3. Đọc dữ liệu administrative units
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

    print(f"Loaded administrative units: {len(prov_map)} provinces, {len(ward_map)} wards.")

    # 4. Chuẩn bị SQLite
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

    # Table provinces
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
            minx REAL,
            miny REAL,
            maxx REAL,
            maxy REAL,
            geom_wkb_zlib BLOB
        )
    """)

    # Table wards
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
            minx REAL,
            miny REAL,
            maxx REAL,
            maxy REAL,
            geom_wkb_zlib BLOB
        )
    """)

    conn.commit()

    # 5. Đọc GeoJSON từ file zip
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
                    minx,
                    miny,
                    maxx,
                    maxy,
                    geom_wkb_zlib
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, row)
            prov_count += 1

        conn.commit()
        print(f"Imported {prov_count} provinces.")

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
                    minx,
                    miny,
                    maxx,
                    maxy,
                    geom_wkb_zlib
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, row)
            ward_count += 1

            if ward_count % 500 == 0:
                conn.commit()
                print(f"Imported {ward_count} wards...")

        conn.commit()
        print(f"Imported {ward_count} wards.")

    # 6. Đánh index
    print("Creating indices...")
    cur.execute("CREATE INDEX idx_bbox_minx ON wards(minx)")
    cur.execute("CREATE INDEX idx_bbox_maxx ON wards(maxx)")
    cur.execute("CREATE INDEX idx_bbox_miny ON wards(miny)")
    cur.execute("CREATE INDEX idx_bbox_maxy ON wards(maxy)")
    cur.execute("CREATE INDEX idx_ward_code ON wards(ward_code)")
    cur.execute("CREATE INDEX idx_province_code ON wards(province_code)")
    cur.execute("CREATE INDEX idx_postal_code ON wards(postal_code)")

    cur.execute("CREATE INDEX idx_prov_bbox_minx ON provinces(minx)")
    cur.execute("CREATE INDEX idx_prov_bbox_maxx ON provinces(maxx)")
    cur.execute("CREATE INDEX idx_prov_bbox_miny ON provinces(miny)")
    cur.execute("CREATE INDEX idx_prov_bbox_maxy ON provinces(maxy)")
    cur.execute("CREATE INDEX idx_prov_code ON provinces(province_code)")

    conn.commit()
    print("Running VACUUM...")
    cur.execute("VACUUM")
    conn.close()

    # 7. Thay thế file SQLite cũ
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
    print(f"Total provinces: {prov_count}")
    print(f"Total wards: {ward_count}")


if __name__ == "__main__":
    main()