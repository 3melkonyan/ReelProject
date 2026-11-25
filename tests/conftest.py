"""Background outbound mail is opt-in in tests; dedicated cases override with fake SMTP."""
import pytest


@pytest.fixture(autouse=True)
def no_automatic_student_mail(monkeypatch):
    monkeypatch.setenv('REEL_STUDENT_MAIL_ENABLED','0')
