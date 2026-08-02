import update_manager


def test_protected_files_are_frozen_and_exact():
    assert update_manager.PROTECTED_FILES == frozenset({"license.lic", "public_key.pem"})


def test_is_protected_true_for_license_and_public_key():
    assert update_manager.is_protected("license.lic") is True
    assert update_manager.is_protected("public_key.pem") is True


def test_is_protected_false_for_other_names():
    assert update_manager.is_protected("main.py") is False
    assert update_manager.is_protected("") is False
    assert update_manager.is_protected("license.lic.bak") is False


def test_is_protected_is_case_sensitive():
    assert update_manager.is_protected("LICENSE.LIC") is False
