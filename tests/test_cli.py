import pytest

from familyshield.cli import OperatorError, import_contacts
from familyshield.contacts import init_db, list_contacts
from familyshield.simulation import simulation_settings


def test_import_contacts_adds_valid_rows_and_skips_bad_ones(tmp_path, capsys):
    settings = simulation_settings(contacts_db_path=str(tmp_path / "test.db"))
    init_db(settings.contacts_db_path)
    csv_path = tmp_path / "contacts.csv"
    csv_path.write_text(
        "name,phone,relationship,role\n"
        "Sarah,+41790000001,daughter,caregiver\n"
        "Leo,+41790000002,grandson,relative\n"
        "Bad,notaphone,,relative\n"
        "Sarah Dup,+41790000001,,relative\n"
        "NoRole,+41790000003,friend,emperor\n"
    )

    import_contacts(settings, csv_path)

    names = {c.name for c in list_contacts(settings.contacts_db_path)}
    assert names == {"Sarah", "Leo"}

    out = capsys.readouterr()
    assert "Imported 2 contact(s)." in out.out
    assert "Skipped 3 row(s)" in out.err
    assert "notaphone" in out.err
    assert "duplicate phone" in out.err
    assert "emperor" in out.err


def test_import_contacts_requires_name_and_phone_columns(tmp_path):
    settings = simulation_settings(contacts_db_path=str(tmp_path / "test.db"))
    init_db(settings.contacts_db_path)
    csv_path = tmp_path / "contacts.csv"
    csv_path.write_text("full_name,number\nSarah,+41790000001\n")

    with pytest.raises(OperatorError, match="name.*phone"):
        import_contacts(settings, csv_path)


def test_import_contacts_rejects_a_missing_file(tmp_path):
    settings = simulation_settings(contacts_db_path=str(tmp_path / "test.db"))
    with pytest.raises(OperatorError, match="No such file"):
        import_contacts(settings, tmp_path / "does-not-exist.csv")
