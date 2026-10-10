from familyshield.scam_db import (
    DEFAULT_SCAM_TXT_PATH,
    ScamRecord,
    is_scam_number,
    load_scam_set,
    lookup_scam_number,
    normalize_to_e164,
)


def test_normalize_to_e164():
    assert normalize_to_e164("+12013759200") == "+12013759200"
    assert normalize_to_e164("+41 79 123 45 67") == "+41791234567"
    assert normalize_to_e164("(202) 977-2403") == "+12029772403"
    assert normalize_to_e164("202-977-2403") == "+12029772403"
    assert normalize_to_e164("12029772403") == "+12029772403"
    assert normalize_to_e164("") == ""
    assert normalize_to_e164("invalid") == ""


def test_lookup_scam_number_exists():
    record = lookup_scam_number("+12013759200")
    assert isinstance(record, ScamRecord)
    assert record.phone_number == "+12013759200"
    assert record.source == "shalom_karr_ai_blocklist"
    assert is_scam_number("+12013759200") is True


def test_lookup_scam_number_formatted():
    # Nathanu98 source number formatted as (202) 977-2403 -> normalized +12029772403
    record = lookup_scam_number("(202) 977-2403")
    assert isinstance(record, ScamRecord)
    assert record.phone_number == "+12029772403"
    assert record.source == "nathanu98_scammer_numbers"
    assert is_scam_number("(202) 977-2403") is True


def test_lookup_clean_number_not_found():
    # Test number that should not be in scam blocklist
    clean_number = "+41799999999"
    assert lookup_scam_number(clean_number) is None
    assert is_scam_number(clean_number) is False


def test_lookup_empty_or_nonexistent_db():
    assert lookup_scam_number("") is None
    assert is_scam_number("") is False
    assert lookup_scam_number("+12013759200", db_path="/nonexistent/path/db.sqlite") is None


def test_load_scam_set():
    scam_set = load_scam_set(DEFAULT_SCAM_TXT_PATH)
    assert len(scam_set) >= 70000
    assert "+12013759200" in scam_set
    assert "+12029772403" in scam_set

    # Missing path returns empty set
    assert load_scam_set("/nonexistent/file.txt") == set()


def test_screening_lookup_states_and_swiss_normalization(tmp_path):
    from scam_the_scamers.caller import lookup_caller

    assert lookup_caller("").status == "not_provided"
    assert lookup_caller("not a phone").status == "invalid"
    assert lookup_caller("079 123 45 67").number == "+41791234567"
    assert lookup_caller("0041 79 123 45 67").number == "+41791234567"
    assert lookup_caller("+12013759200").status == "matched"
    assert lookup_caller("+12013759200").source == "shalom_karr_ai_blocklist"
    assert lookup_caller("+41799999999").status == "no_match"
    assert lookup_caller("+12013759200", db_path=tmp_path / "missing.db").status == "unavailable"
    invalid_db = tmp_path / "broken.db"
    invalid_db.write_text("not sqlite")
    assert lookup_caller("+12013759200", db_path=invalid_db).status == "unavailable"
