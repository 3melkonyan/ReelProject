"""Background outbound mail is opt-in in tests; dedicated cases override with fake SMTP."""
import pytest

from test_web import web as _processed_web
from test_admin_api import web as _admin_web

from test_web_upload import web as web
from test_web_dashboard import isolated as isolated
from test_two_factor import secured as secured


processed_web = _processed_web
admin_web = _admin_web


@pytest.fixture(autouse=True)
def no_automatic_student_mail(monkeypatch):
    monkeypatch.setenv('REEL_STUDENT_MAIL_ENABLED','0')


@pytest.fixture
def hy_locale():
    """Explicit Armenian message scope for tests of the existing interface copy."""
    from reel.i18n import language_context
    with language_context("hy"):
        yield
