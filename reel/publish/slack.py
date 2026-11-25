"""Slack bot publishing without internal retries or persistent state."""
from pathlib import Path
from http.client import HTTPException
import errno
import socket
from urllib.error import URLError
from dotenv import dotenv_values
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError, SlackRequestError
from reel.config import load_config
from reel.publish.errors import PublishError
from reel.publish._common import text, link


def _client() -> WebClient:
    # Read the project .env explicitly; do not mutate process environment.
    values = dotenv_values(Path(__file__).resolve().parents[2] / ".env", interpolate=False)
    token = values.get("SLACK_BOT_TOKEN")
    if not token:
        raise PublishError("Missing SLACK_BOT_TOKEN in project .env", False)
    return WebClient(token=token, timeout=30, retry_handlers=[])


def _send(channel: str, message: str, mrkdwn: bool = False) -> str:
    text(channel, "channel")
    text(message, "message")
    if len(message) > 40000:
        raise PublishError("Slack message exceeds 40000 characters", False)
    try:
        response = _client().chat_postMessage(channel=channel, text=message,
            mrkdwn=mrkdwn, unfurl_links=False, unfurl_media=False, parse="none")
        timestamp = response.get("ts")
        if not isinstance(timestamp, str) or not timestamp:
            raise PublishError("Slack response missing timestamp; reconcile before retry", False)
        return timestamp
    except SlackApiError as exc:
        code = exc.response.get("error", "unknown_error")
        status = int(exc.response.status_code)
        transient = status == 429 or status >= 500 or code in {
            "ratelimited", "internal_error", "fatal_error", "service_unavailable", "request_timeout"
        }
        # Only report the documented error code; never include the token/request.
        safe_code = code if isinstance(code, str) and code.replace("_", "").isalnum() else "unknown_error"
        raise PublishError(f"Slack API: {safe_code} (HTTP {status})", transient) from None
    except (URLError, ConnectionError, TimeoutError, socket.gaierror, HTTPException):
        raise PublishError("Slack network failure", True) from None
    except OSError as exc:
        network = exc.errno in {errno.ENETDOWN, errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ECONNRESET, errno.ECONNREFUSED, errno.ETIMEDOUT}
        raise PublishError("Slack I/O failure", network) from None
    except SlackRequestError as exc:
        raise PublishError(f"Slack request failure ({type(exc).__name__})", False) from None

    except PublishError:
        raise
    except Exception as exc:
        raise PublishError(f"Slack request failure ({type(exc).__name__})", False) from None

def _escape(value: str) -> str:
    """Slack's three control characters; this also defuses <!channel>-style mentions in a title."""
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def post_link(channel: str, title: str, url: str) -> str:
    text(title, "title")
    link(url)
    if any(ch in "<>|" or ch.isspace() for ch in url):
        raise PublishError("url contains characters that break a Slack link", False)
    # Owner 2026-11-19: the link must open with a click -> Slack's explicit <url> link markup
    # (mrkdwn on; the title is escaped, so it cannot add mentions or links of its own).
    return _send(channel, f"{_escape(title)}\n<{url}>", mrkdwn=True)


def notify(text: str) -> None:
    try:
        channel = load_config()["operator_slack_channel"]
    except ValueError:
        raise PublishError("Cannot load operator_slack_channel from config.yaml", False) from None
    _send(channel, text)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--smoke", action="store_true", required=True)
    parser.parse_args()
    from reel.publish._smoke import slack_smoke
    slack_smoke()
