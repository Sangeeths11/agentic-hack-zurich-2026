#!/usr/bin/env python3
"""Download and consolidate online databases of scam and robocall phone numbers.

Fetches open scam phone number databases, normalizes numbers to standard E.164
format, deduplicates them, and generates:
1. data/scam_numbers/scam_numbers_e164.txt (newline-delimited E.164 text list)
2. data/scam_numbers/scam_numbers.db (indexed SQLite database for instant lookups)
3. data/scam_numbers/metadata.json (dataset metadata, source provenance, counts)
"""

import argparse
import datetime
import json
import re
import sqlite3
import urllib.request
from pathlib import Path

# Verified online open-source scam number databases
SOURCES = [
    {
        "name": "shalom_karr_ai_blocklist",
        "description": "Community blocklist of AI robocallers & scammers in E.164 format",
        "url": (
            "https://raw.githubusercontent.com/Shalom-Karr/AI-Number-Blocklist/main/blacklist.txt"
        ),
        "raw_filename": "blacklist_shalom_karr.txt",
        "format": "e164",
    },
    {
        "name": "nathanu98_scammer_numbers",
        "description": "Scammer phone numbers collected from scam databases (US NANP format)",
        "url": (
            "https://raw.githubusercontent.com/nathanu98/ScammerPhoneNumbers/main/ScammerNumbers.txt"
        ),
        "raw_filename": "scammer_numbers_nathanu98.txt",
        "format": "us_10digit",
    },
]

E164_REGEX = re.compile(r"^\+[1-9]\d{6,14}$")


def normalize_number(raw: str, default_country_code: str = "1") -> str | None:
    """Normalize a raw phone number string to standard E.164 format (+[country_code][number])."""
    s = raw.strip()
    if not s:
        return None

    # If it already starts with '+', strip formatting characters (spaces, dashes, parens)
    if s.startswith("+"):
        cleaned = "+" + re.sub(r"\D", "", s[1:])
        if E164_REGEX.match(cleaned):
            return cleaned
        return None

    # Extract all digits
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None

    # 10-digit North American (NANP) number e.g. (800) 555-0199 -> +18005550199
    if len(digits) == 10 and default_country_code == "1":
        candidate = f"+1{digits}"
        if E164_REGEX.match(candidate):
            return candidate

    # 11-digit number starting with 1
    if len(digits) == 11 and digits.startswith("1"):
        candidate = f"+{digits}"
        if E164_REGEX.match(candidate):
            return candidate

    return None


def fetch_or_read_source(source: dict, output_dir: Path, force_download: bool = False) -> str:
    """Read cached raw source or download if missing or force_download is set."""
    output_dir.mkdir(parents=True, exist_ok=True)
    raw_path = output_dir / source["raw_filename"]

    if raw_path.exists() and not force_download:
        print(f"[{source['name']}] Reading cached raw file: {raw_path}")
        return raw_path.read_text(encoding="utf-8", errors="ignore")

    print(f"[{source['name']}] Downloading from {source['url']} ...")
    req = urllib.request.Request(
        source["url"],
        headers={"User-Agent": "FamilyShield-ScamDB/1.0"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        content = resp.read().decode("utf-8", errors="ignore")

    raw_path.write_text(content, encoding="utf-8")
    print(f"[{source['name']}] Saved raw file to {raw_path} ({len(content)} chars)")
    return content


def build_database(
    output_dir: Path,
    db_path: Path,
    txt_path: Path,
    meta_path: Path,
    force_download: bool = False,
) -> dict:
    """Download/read sources, compile unified E.164 database, and persist artifacts."""
    output_dir.mkdir(parents=True, exist_ok=True)
    records: dict[str, dict] = {}  # normalized E.164 -> {"source": ..., "raw": ...}
    source_stats = {}

    for source in SOURCES:
        text = fetch_or_read_source(source, output_dir, force_download=force_download)
        lines = text.splitlines()
        added_from_source = 0
        invalid_count = 0

        for line in lines:
            line_str = line.strip()
            if not line_str or line_str.startswith("#"):
                continue

            normalized = normalize_number(line_str)
            if normalized:
                if normalized not in records:
                    records[normalized] = {
                        "source": source["name"],
                        "raw": line_str,
                    }
                    added_from_source += 1
            else:
                invalid_count += 1

        source_stats[source["name"]] = {
            "total_lines": len(lines),
            "valid_unique_added": added_from_source,
            "invalid_or_skipped": invalid_count,
            "url": source["url"],
            "description": source["description"],
        }
        print(
            f"[{source['name']}] Processed {len(lines)} lines -> "
            f"{added_from_source} unique E.164 numbers added."
        )

    # Sort all normalized numbers for deterministic output
    sorted_numbers = sorted(records.keys())
    print(f"Total unique consolidated scam phone numbers: {len(sorted_numbers)}")

    # 1. Write text file (one E.164 number per line)
    print(f"Writing E.164 text list to {txt_path} ...")
    txt_path.write_text("\n".join(sorted_numbers) + "\n", encoding="utf-8")

    # 2. Build indexed SQLite database
    print(f"Building SQLite database at {db_path} ...")
    if db_path.exists():
        db_path.unlink()

    conn = sqlite3.connect(db_path)
    with conn:
        conn.execute("""
            CREATE TABLE scam_numbers (
                phone_number TEXT PRIMARY KEY,
                source TEXT NOT NULL,
                raw_number TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
        """)
        conn.execute("CREATE INDEX idx_scam_numbers_phone ON scam_numbers (phone_number)")

        rows = [
            (num, records[num]["source"], records[num]["raw"])
            for num in sorted_numbers
        ]
        conn.executemany(
            "INSERT INTO scam_numbers (phone_number, source, raw_number) VALUES (?, ?, ?)",
            rows,
        )

    # 3. Write metadata JSON
    metadata = {
        "generated_at": datetime.datetime.now(datetime.UTC).isoformat(),
        "total_unique_numbers": len(sorted_numbers),
        "e164_txt_file": str(txt_path.relative_to(output_dir.parent.parent)),
        "sqlite_db_file": str(db_path.relative_to(output_dir.parent.parent)),
        "sources": source_stats,
    }
    meta_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    print(f"Wrote metadata to {meta_path}")

    return metadata


def main():
    parser = argparse.ArgumentParser(
        description="Download and compile scam phone numbers database."
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).resolve().parent.parent / "data" / "scam_numbers",
        help="Directory to save raw and compiled datasets",
    )
    parser.add_argument(
        "--force-download",
        action="store_true",
        help="Re-download datasets even if raw files already exist",
    )
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    db_path = output_dir / "scam_numbers.db"
    txt_path = output_dir / "scam_numbers_e164.txt"
    meta_path = output_dir / "metadata.json"

    meta = build_database(
        output_dir=output_dir,
        db_path=db_path,
        txt_path=txt_path,
        meta_path=meta_path,
        force_download=args.force_download,
    )
    print("\nDatabase build complete!")
    print(f"Total verified scam numbers: {meta['total_unique_numbers']:,}")
    print(f"SQLite DB: {db_path} ({db_path.stat().st_size / (1024*1024):.2f} MB)")
    print(f"E.164 TXT: {txt_path} ({txt_path.stat().st_size / (1024*1024):.2f} MB)")


if __name__ == "__main__":
    main()
