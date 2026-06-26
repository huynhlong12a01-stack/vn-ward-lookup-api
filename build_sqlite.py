import sqlite3
import zlib
from pathlib import Path

import ijson
from shapely.geometry import shape


GEOJSON_FILE = Path("data/wards_full.geojson")
DB_FILE = Path("data/wards_lookup.sqlite")


def pick(props, *names):
    for name in names:
        if name in props and props[name] not in [None, ""]:
            return props[name]

    for name in names:
        name_lower = name.lower()
        for k, v in props.items():
            if k.lower().endswith(name_lower) and v not in [None, ""]:
                return v

    return None


def main():
    if not GEOJSON_FILE.exists():
        raise FileNotFoundError(f"Không thấy file: {GEOJSON_FILE}")

    if DB_FILE.exists():
        DB_FILE.unlink()

    conn = sqlite3.connect(DB_FILE)
    cur = conn.cursor()

    cur.execute("PRAGMA journal_mode=OFF")
    cur.execute("PRAGMA synchronous=OFF")
    cur.execute("PRAGMA temp_store=MEMORY")

    cur.execute("""
        CREATE TABLE wards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ward_code TEXT,
            ward_name TEXT,
            ward_name_en TEXT,
            ward_full_name TEXT,
            ward_full_name_en TEXT,
            province_code TEXT,
            province_name TEXT,
            province_name_en TEXT,
            province_full_name TEXT,
            province_full_name_en TEXT,
            minx REAL,
            miny REAL,
            maxx REAL,
            maxy REAL,
            geom_wkb_zlib BLOB
        )
    """)

    count = 0

    with GEOJSON_FILE.open("rb") as f:
        for feature in ijson.items(f, "features.item"):
            props = feature.get("properties", {})
            geom_data = feature.get("geometry")

            if not geom_data:
                continue

            geom = shape(geom_data)
            minx, miny, maxx, maxy = geom.bounds

            # Lưu geometry dạng WKB rồi nén zlib để SQLite nhỏ hơn nhiều
            geom_blob = zlib.compress(geom.wkb, level=9)

            row = (
                pick(props, "ward_code", "code", "Code"),
                pick(props, "ward_name", "name", "Name"),
                pick(props, "ward_name_en", "name_en", "NameEn"),
                pick(props, "ward_full_name", "full_name", "FullName"),
                pick(props, "ward_full_name_en", "full_name_en", "FullNameEn"),
                pick(props, "province_code", "ProvinceCode"),
                pick(props, "province_name"),
                pick(props, "province_name_en"),
                pick(props, "province_full_name"),
                pick(props, "province_full_name_en"),
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
                    province_code,
                    province_name,
                    province_name_en,
                    province_full_name,
                    province_full_name_en,
                    minx,
                    miny,
                    maxx,
                    maxy,
                    geom_wkb_zlib
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, row)

            count += 1

            if count % 100 == 0:
                conn.commit()
                print(f"Imported {count} wards...")

    conn.commit()

    cur.execute("CREATE INDEX idx_bbox_minx ON wards(minx)")
    cur.execute("CREATE INDEX idx_bbox_maxx ON wards(maxx)")
    cur.execute("CREATE INDEX idx_bbox_miny ON wards(miny)")
    cur.execute("CREATE INDEX idx_bbox_maxy ON wards(maxy)")
    cur.execute("CREATE INDEX idx_ward_code ON wards(ward_code)")
    cur.execute("CREATE INDEX idx_province_code ON wards(province_code)")

    conn.commit()
    cur.execute("VACUUM")
    conn.close()

    size_mb = DB_FILE.stat().st_size / 1024 / 1024

    print(f"Done. Imported {count} wards.")
    print(f"Saved database: {DB_FILE}")
    print(f"SQLite size: {size_mb:.2f} MB")


if __name__ == "__main__":
    main()