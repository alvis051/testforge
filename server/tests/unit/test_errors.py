from testforge.errors import AppError


def test_app_error_carries_code_status_and_details():
    error = AppError("case_key_not_found", "no such case", 404, {"case_key": "CHK-9"})
    assert error.code == "case_key_not_found"
    assert error.status_code == 404
    assert error.details == {"case_key": "CHK-9"}
    assert str(error) == "no such case"


def test_app_error_defaults():
    error = AppError("duplicate_case_key", "already exists")
    assert error.status_code == 400
    assert error.details == {}
