"""Optional metadata failures publish safe defaults; source failures remain errors."""
from __future__ import annotations

from datetime import datetime, timezone
import json

import sqlite3

import pytest

from reel.stages import metadata

GROUND_NOTE = "grounded chapters unavailable — default metadata"
LLM_NOTE = "LLM metadata unavailable — default metadata"
STYLE_NOTE = "LLM chapter wording unusable — grounded chapters kept"


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2031, 2, 3, 1, 2, 3, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    monkeypatch.setattr(metadata, "datetime", FixedDateTime)
    monkeypatch.setattr(metadata, "LLM_TEXT", True)

    def forbidden(*args, **kwargs):
        raise AssertionError("An actual LLM call is forbidden")

    monkeypatch.setattr(metadata.llm, "chat_json", forbidden)


def transcript(tmp_path, segments=None):
    path = tmp_path / "transcript.json"
    path.write_text(json.dumps({"segments": [{"start": 0, "end": 3000, "text": "Դասի նյութը"}]
                                         if segments is None else segments}), encoding="utf-8")
    return path


def map_result():
    topics = ["Lexical Scope", "Arrow Functions", "Event Loop", "Prototype Chain", "Class Syntax", "Closures"]
    return {"topic": topics[0], "summary": "Specific source topics.",
            "topic_changes": [{"t": i * 500, "topic": name} for i, name in enumerate(topics)]}


def assert_default(result, output, note):
    artifact = json.loads((output / "metadata.json").read_text(encoding="utf-8"))
    assert result["chapters"] == [] and result["auto_note"] == note
    assert result["title"].startswith("Դաս՝ ") and 0 < len(result["title"]) <= 100
    assert "<" not in result["title"] and ">" not in result["title"]
    assert metadata._ARMENIAN_TEXT.search(result["title"])
    assert metadata._ARMENIAN_TEXT.search(result["description"])
    assert 0 < len(result["youtube_description"]) <= 5000
    assert artifact["generation_mode"] == "default"
    assert {key: artifact[key] for key in result} == result
    assert not list(output.glob("*.tmp"))
    return artifact


@pytest.mark.parametrize("llm_text", [False, True])
def test_unavailable_grounded_chapters_finish_with_default(tmp_path, monkeypatch, llm_text):
    monkeypatch.setattr(metadata, "LLM_TEXT", llm_text)
    calls = []

    def chat(system, user, **kwargs):
        calls.append(system)
        assert system == metadata.MAP_SYS
        return {"topic": "JavaScript Function Context", "summary": "One repetitive subject.",
                "topic_changes": [{"t": i * 300, "topic": "JavaScript Function Context"} for i in range(10)]}

    monkeypatch.setattr(metadata.llm, "chat_json", chat)
    output = tmp_path / "result"
    result = metadata.generate(transcript(tmp_path), [(0, 3000)], output)
    assert_default(result, output, GROUND_NOTE)
    assert len(calls) == 1 and result["title"] == "Դաս՝ Դաս, 2031-02-03"


@pytest.mark.parametrize("response", [None, [], {}, {"topic": 42, "summary": "s", "topic_changes": []},
                                     {"topic": "Scope", "summary": None, "topic_changes": []},
                                     {"topic": "Scope", "summary": "s", "topic_changes": "bad"},
                                     {"topic": "Scope", "summary": "s", "topic_changes": [None]}])
def test_malformed_map_response_uses_default(tmp_path, monkeypatch, response):
    monkeypatch.setattr(metadata.llm, "chat_json", lambda *args, **kwargs: response)
    output = tmp_path / "result"
    result = metadata.generate(transcript(tmp_path), [(0, 3000)], output)
    assert_default(result, output, LLM_NOTE)


def test_map_llm_error_uses_default_without_critical_stop(tmp_path, monkeypatch):
    def unavailable(*args, **kwargs):
        raise metadata.llm.LLMError("offline fixture")

    monkeypatch.setattr(metadata.llm, "chat_json", unavailable)
    output = tmp_path / "result"
    result = metadata.generate(transcript(tmp_path), [(0, 3000)], output)
    assert_default(result, output, LLM_NOTE)


@pytest.mark.parametrize("failure", ["llm_error", "invalid_response"])
def test_unavailable_reducer_uses_default_with_empty_chapters(tmp_path, monkeypatch, failure):
    calls = []

    def chat(system, user, **kwargs):
        if system == metadata.MAP_SYS:
            return map_result()
        calls.append(system)
        if failure == "llm_error":
            raise metadata.llm.LLMError("offline fixture")
        return {"title": 42, "description": "bad metadata"}

    monkeypatch.setattr(metadata.llm, "chat_json", chat)
    output = tmp_path / "result"
    result = metadata.generate(transcript(tmp_path), [(0, 3000)], output)
    artifact = assert_default(result, output, LLM_NOTE)
    assert len(calls) == 2 and artifact["chapter_plan"]
    assert not result["chapters"]  # A diagnostic plan must not become invented publication chapters.


def test_empty_valid_transcript_can_use_default_without_calling_llm(tmp_path):
    output = tmp_path / "result"
    result = metadata.generate(transcript(tmp_path, []), [(0, 3000)], output)
    assert_default(result, output, GROUND_NOTE)


def create_job(root, source="/archive/7/Python lesson.mov", group="Խումբ Ա", created="2026-12-31T23:59:00+00:00"):
    database = root / "reel.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, source_path TEXT, grp TEXT, created_at TEXT)")
        connection.execute("INSERT INTO jobs VALUES (7, ?, ?, ?)", (source, group, created))
    return database


@pytest.mark.parametrize("source, group, lesson", [
    ("/archive/7/Python lesson.mov", "Խումբ Ա", "Python lesson"),
    ("/archive/7/Դաս 1. Introduction", "Խումբ Ա", "Դաս 1. Introduction"),
    ("", "Խումբ Ա", "Խումբ Ա"),
    ("<>\n\x00", "Խումբ <Ա>", "Խումբ Ա"),
    ("", "", "Դաս"),
])
def test_default_uses_job_lesson_group_and_created_date(tmp_path, monkeypatch, source, group, lesson):
    create_job(tmp_path, source, group)
    monkeypatch.setattr(metadata.llm, "chat_json", lambda *args, **kwargs: None)
    output = tmp_path / "work" / "7"
    result = metadata.generate(transcript(tmp_path), [(0, 3000)], output)
    assert_default(result, output, LLM_NOTE)
    assert result["title"] == f"Դաս՝ {lesson}, 2027-01-01"


def test_default_title_sanitization_preserves_date_within_youtube_limit(tmp_path, monkeypatch):
    create_job(tmp_path, "/archive/7/" + "Երկար <դասի>\nանուն " * 30 + ".MP4")
    monkeypatch.setattr(metadata.llm, "chat_json", lambda *args, **kwargs: None)
    output = tmp_path / "work" / "7"
    result = metadata.generate(transcript(tmp_path), [(0, 3000)], output, extra_footer="Լրացում " * 1000)
    assert_default(result, output, LLM_NOTE)
    assert len(result["title"]) <= 100 and result["title"].endswith(", 2027-01-01")
    assert not any(character in result["title"] for character in "<>\n\x00")
    assert len(result["youtube_description"]) == 5000
    assert result["chapters"] == []


@pytest.mark.parametrize("created", [None, "invalid-date", "2026-02-30T10:00:00+00:00"])
def test_invalid_job_date_uses_yerevan_today(tmp_path, created):
    create_job(tmp_path, created=created)
    assert metadata._default_context(tmp_path / "work" / "7") == ("Python lesson", "2031-02-03")


def test_context_reads_existing_job_readonly_and_closes_connection(tmp_path, monkeypatch):
    database = create_job(tmp_path)
    before = (database.read_bytes(), database.stat().st_mtime_ns)
    original_connect = sqlite3.connect
    calls, closed, sql = [], [], []

    class Connection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            sql.append(statement)
            return super().execute(statement, *args, **kwargs)

        def close(self):
            closed.append(True)
            return super().close()

    def connect(database_uri, **kwargs):
        calls.append((database_uri, kwargs))
        return original_connect(database_uri, factory=Connection, **kwargs)

    monkeypatch.setattr(metadata.sqlite3, "connect", connect)
    assert metadata._default_context(tmp_path / "work" / "7") == ("Python lesson", "2027-01-01")
    assert len(calls) == len(closed) == 1 and "?mode=ro" in calls[0][0] and calls[0][1]["uri"] is True
    assert len(sql) == 1 and sql[0].startswith("SELECT source_path, grp, created_at FROM jobs")
    assert (database.read_bytes(), database.stat().st_mtime_ns) == before


@pytest.mark.parametrize("output", ["result", "work/not-a-job", "other/7", "work/7/result"])
def test_standalone_or_nonjob_output_never_opens_database(tmp_path, monkeypatch, output):
    create_job(tmp_path)

    def forbidden(*args, **kwargs):
        raise AssertionError("A standalone output must not query a database")

    monkeypatch.setattr(metadata.sqlite3, "connect", forbidden)
    assert metadata._default_context(tmp_path / output) == ("Դաս", "2031-02-03")


def test_missing_or_unreadable_optional_context_is_not_a_metadata_failure(tmp_path):
    assert metadata._default_context(tmp_path / "work" / "7") == ("Դաս", "2031-02-03")
    (tmp_path / "reel.db").write_bytes(b"not a SQLite database")
    assert metadata._default_context(tmp_path / "work" / "7") == ("Դաս", "2031-02-03")


def test_valid_llm_text_with_unusable_chapter_wording_keeps_grounded_chapters_and_note(tmp_path, monkeypatch):
    def chat(system, user, **kwargs):
        if system == metadata.MAP_SYS:
            return map_result()
        plan = json.loads(user)["chapter_plan"]
        return {"title": "JavaScript դասի թեմաները", "description": "Այս դասում ուսումնասիրվում է JavaScript-ի աշխատանքը։",
                "chapter_details": {item["id"]: "անհամապատասխան" for item in plan}}

    monkeypatch.setattr(metadata.llm, "chat_json", chat)
    output = tmp_path / "result"
    result = metadata.generate(transcript(tmp_path), [(0, 3000)], output)
    artifact = json.loads((output / "metadata.json").read_text())
    assert result["auto_note"] == artifact["auto_note"] == STYLE_NOTE
    assert len(result["chapters"]) >= 3
    assert artifact["generation_mode"] == "llm_text_grounded_chapters"


@pytest.mark.parametrize("keep", [[], [(0, 0)], [(5, 4)], [(-1, 10)], [(0, float("inf"))],
                                  [(float("nan"), 10)], [(False, 10)], [(0, 10), (9, 20)],
                                  [(10, 20), (0, 5)], [[0]], None])
def test_invalid_keep_remains_a_critical_error(tmp_path, keep):
    output = tmp_path / "result"
    with pytest.raises(ValueError, match="keep"):
        metadata.generate(transcript(tmp_path), keep, output)
    assert not (output / "metadata.json").exists()


@pytest.mark.parametrize("data", ["not JSON", "{}", '{"segments":null}', '{"segments":[null]}',
                                 '{"segments":[{"start":0,"end":3,"text":42}]}',
                                 '{"segments":[{"start":0,"end":"3","text":"x"}]}'])
def test_corrupt_transcript_remains_a_critical_error(tmp_path, data):
    path = tmp_path / "bad.json"
    path.write_text(data)
    output = tmp_path / "result"
    with pytest.raises(ValueError):
        metadata.generate(path, [(0, 3000)], output)
    assert not (output / "metadata.json").exists()


def test_missing_transcript_remains_a_critical_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        metadata.generate(tmp_path / "missing.json", [(0, 3000)], tmp_path / "result")


@pytest.mark.parametrize("created, expected", [
    ("2026-11-18T21:30:00+00:00", "2026-11-19"),
    ("2026-11-18T21:30:00", "2026-11-19"),
    ("2026-11-19T01:30:00+04:00", "2026-11-19"),
    ("2026-11-18T19:59:00+00:00", "2026-11-18"),
])
def test_job_creation_date_is_shown_in_yerevan_including_legacy_naive_utc(tmp_path, created, expected):
    create_job(tmp_path, created=created)
    assert metadata._default_context(tmp_path / "work" / "7") == ("Python lesson", expected)


def test_standalone_default_date_uses_yerevan_midnight_rollover(tmp_path, monkeypatch):
    class LateUTCDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return cls(2031, 2, 3, 21, 30, tzinfo=timezone.utc)

    monkeypatch.setattr(metadata, "datetime", LateUTCDateTime)
    assert metadata._default_context(tmp_path / "result") == ("Դաս", "2031-02-04")
