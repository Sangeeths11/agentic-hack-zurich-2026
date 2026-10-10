from familyshield.contacts import (
    add_contact,
    call_is_known_contact,
    find_contact_by_phone,
    init_db,
    list_contacts,
    log_call,
    update_call_risk,
)


def test_add_and_find_contact_by_phone(tmp_path):
    db = str(tmp_path / "test.db")
    init_db(db)
    added = add_contact(
        db, name="Sarah", phone_number="+41790000001", relationship="daughter", role="caregiver"
    )
    assert added.id is not None

    found = find_contact_by_phone(db, "+41790000001")
    assert found is not None
    assert found.name == "Sarah"
    assert found.role == "caregiver"

    assert find_contact_by_phone(db, "+41790000099") is None


def test_list_contacts_is_sorted_by_name(tmp_path):
    db = str(tmp_path / "test.db")
    init_db(db)
    add_contact(db, name="Zoe", phone_number="+41790000002", relationship="niece", role="relative")
    add_contact(db, name="Anna", phone_number="+41790000003", relationship="wife", role="relative")

    names = [c.name for c in list_contacts(db)]
    assert names == ["Anna", "Zoe"]


def test_log_call_does_not_duplicate_on_repeated_call_sid(tmp_path):
    db = str(tmp_path / "test.db")
    init_db(db)
    for _ in range(2):
        log_call(
            db,
            call_sid="CA1",
            caller_number="+41790000004",
            contact_id=None,
            line_type="mobile",
            carrier_name="Swisscom",
            lookup_error="",
        )
    import sqlite3

    conn = sqlite3.connect(db)
    rows = conn.execute("SELECT COUNT(*) FROM calls WHERE call_sid = 'CA1'").fetchone()
    conn.close()
    assert rows == (1,)


def test_update_call_risk_rejects_a_stale_lower_score(tmp_path):
    db = str(tmp_path / "test.db")
    init_db(db)
    log_call(
        db,
        call_sid="CA2",
        caller_number="+41790000005",
        contact_id=None,
        line_type="mobile",
        carrier_name="Swisscom",
        lookup_error="",
    )
    update_call_risk(db, "CA2", 60, "amber")
    update_call_risk(db, "CA2", 30, "none")  # stale/out-of-order — must not apply

    import sqlite3

    conn = sqlite3.connect(db)
    row = conn.execute("SELECT risk_score, risk_level FROM calls WHERE call_sid = 'CA2'").fetchone()
    conn.close()
    assert row == (60, "amber")

    update_call_risk(db, "CA2", 95, "critical")
    conn = sqlite3.connect(db)
    row = conn.execute("SELECT risk_score, risk_level FROM calls WHERE call_sid = 'CA2'").fetchone()
    conn.close()
    assert row == (95, "critical")


def test_call_is_known_contact(tmp_path):
    db = str(tmp_path / "test.db")
    init_db(db)
    contact = add_contact(
        db, name="Sarah", phone_number="+41790000006", relationship="daughter", role="caregiver"
    )
    log_call(
        db,
        call_sid="CA3",
        caller_number="+41790000006",
        contact_id=contact.id,
        line_type="mobile",
        carrier_name="Swisscom",
        lookup_error="",
    )
    log_call(
        db,
        call_sid="CA4",
        caller_number="+41790009999",
        contact_id=None,
        line_type="mobile",
        carrier_name="Swisscom",
        lookup_error="",
    )
    assert call_is_known_contact(db, "CA3") is True
    assert call_is_known_contact(db, "CA4") is False
    assert call_is_known_contact(db, "CA-unknown-sid") is False
