"""Shared validation and exception translation; no retry or publication state."""
from functools import wraps
import json
import socket
import errno
from http.client import HTTPException
import httplib2
from google.auth.exceptions import TransportError, RefreshError
from googleapiclient.errors import HttpError
from reel.publish.errors import PublishError
from urllib.parse import urlparse


def text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise PublishError(f"{name} must be a non-empty string", False)


def link(value: str) -> None:
    text(value, "url")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise PublishError("url must be an absolute HTTP(S) link", False)


def api_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PublishError:
            raise
        except HttpError as exc:
            status = int(exc.resp.status)
            reasons = set()
            try:
                reasons = {item.get("reason", "") for item in json.loads(exc.content).get("error", {}).get("errors", [])}
            except (ValueError, TypeError, AttributeError):
                pass
            transient = status in {408, 429} or status >= 500 or bool(reasons & {"rateLimitExceeded", "userRateLimitExceeded", "quotaExceeded", "dailyLimitExceeded", "backendError"})
            # Do not expose request URIs, bodies, tokens, or remote error text.
            raise PublishError(f"{function.__name__}: Google API HTTP {status}", transient) from None
        except RefreshError as exc:
            raise PublishError("Google credentials refresh failed; run python -m reel.google_auth --authorize", bool(getattr(exc, "retryable", False))) from None
        except (TransportError, httplib2.HttpLib2Error, ConnectionError, TimeoutError, socket.timeout, HTTPException):
            raise PublishError(f"{function.__name__}: network failure", True) from None
        except OSError as exc:
            network = exc.errno in {errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ECONNRESET, errno.ECONNREFUSED, errno.ETIMEDOUT}
            raise PublishError(f"{function.__name__}: I/O failure", network) from None
        except Exception as exc:
            raise PublishError(f"{function.__name__}: {type(exc).__name__}", False) from None
    return wrapped
