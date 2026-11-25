"""Account language preferences, with deployment defaults for accounts without a choice."""
from contextlib import closing
from pathlib import Path
import sqlite3

from reel.i18n import DEFAULT_LANGUAGE, LANGUAGES


def user_language(conn, user_id, default=DEFAULT_LANGUAGE):
    row = conn.execute('SELECT language FROM user_languages WHERE user_id=?', (user_id,)).fetchone()
    return row[0] if row and row[0] in LANGUAGES else default


def recipient_language(root: Path, email: str):
    from reel.web.localization import configured_language
    default = configured_language(root)
    try:
        # Rendering a message must not create a database or change recipient records.
        with closing(sqlite3.connect((root / 'reel.db').as_uri() + '?mode=ro', uri=True)) as conn:
            row = conn.execute('SELECT id FROM users WHERE email=? COLLATE NOCASE', (email,)).fetchone()
            return user_language(conn, row[0], default) if row else default
    except sqlite3.Error:
        return default
