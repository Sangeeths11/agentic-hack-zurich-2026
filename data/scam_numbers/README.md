# Scam Phone Numbers Database

This directory contains consolidated, verified, and normalized databases of scammer, robocaller, and fraud phone numbers for Family Shield.

## Summary

- **Total Unique Scam Numbers:** 73,491
- **Format:** E.164 (`+[country_code][subscriber_number]`, e.g. `+12013759200`)
- **Lookups:** Sub-millisecond indexed lookups via SQLite or in-memory set

## Files

| File | Description | Size |
|---|---|---|
| `scam_numbers_e164.txt` | Clean, newline-delimited sorted list of 73,491 E.164 phone numbers | ~955 KB |
| `scam_numbers.db` | Indexed SQLite database with metadata for high-speed queries | ~9.3 MB |
| `metadata.json` | Dataset build metadata, timestamps, and per-source statistics | ~1 KB |
| `blacklist_shalom_karr.txt` | Raw blocklist from Shalom-Karr AI Number Blocklist (44,192 numbers) | ~574 KB |
| `scammer_numbers_nathanu98.txt` | Raw scammer database from nathanu98 (35,926 numbers) | ~575 KB |

## Sources

1. **`Shalom-Karr/AI-Number-Blocklist`**
   - Source: [https://github.com/Shalom-Karr/AI-Number-Blocklist](https://github.com/Shalom-Karr/AI-Number-Blocklist)
   - Scope: Community blocklist of AI robocallers, automated telemarketers, and phone spam operations in standard E.164 format.
   - Raw URL: `https://raw.githubusercontent.com/Shalom-Karr/AI-Number-Blocklist/main/blacklist.txt`
   - Contribution: 44,191 unique E.164 numbers.

2. **`nathanu98/ScammerPhoneNumbers`**
   - Source: [https://github.com/nathanu98/ScammerPhoneNumbers](https://github.com/nathanu98/ScammerPhoneNumbers)
   - Scope: Aggregated scammer telephone numbers reported to scam databases.
   - Raw URL: `https://raw.githubusercontent.com/nathanu98/ScammerPhoneNumbers/main/ScammerNumbers.txt`
   - Contribution: 29,300 unique E.164 numbers (normalized from 10-digit US format).

## SQLite Database Schema

```sql
CREATE TABLE scam_numbers (
    phone_number TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    raw_number TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_scam_numbers_phone ON scam_numbers (phone_number);
```

## How to Query

### Python SDK (`familyshield.scam_db`)

```python
from familyshield.scam_db import is_scam_number, lookup_scam_number

# Check if a number is a known scam caller:
if is_scam_number("+12013759200"):
    print("Warning: Known scam caller detected!")

# Get full metadata:
record = lookup_scam_number("+12013759200")
if record:
    print(f"Matched {record.phone_number} from source {record.source}")
```

### CLI / SQLite

```bash
sqlite3 data/scam_numbers/scam_numbers.db "SELECT * FROM scam_numbers WHERE phone_number = '+12013759200';"
```

## Updating the Database

To re-fetch latest updates from the online sources and rebuild the database:

```bash
python3 scripts/download_scam_numbers.py --force-download
```
