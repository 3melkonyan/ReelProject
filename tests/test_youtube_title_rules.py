"""First-topic titles, whole words and immutable publication metadata; no network."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from reel import stages as st
from reel.db import DB
from reel.pipeline import Ctx

JOB1 = "Դաս՝ Hash Table Implementation և Collision Handling, Immutable Keys և Hash Stability"


def test_job1_exact_first_topic_is_64_characters():
    result = st._youtube_title("Python", 13, JOB1)
    assert result == "Python | Դաս 13 | Hash Table Implementation և Collision Handling"
    assert len(result) == 64


@pytest.mark.parametrize("separator", [",", ";", ":", " - ", " — "])
def test_each_first_thought_separator_stops_before_the_second_topic(separator):
    assert (
        st._youtube_title("Python", 5, "Closures" + separator + " Context")
        == "Python | Դաս 5 | Closures"
    )


@pytest.mark.parametrize(
    "subject,number,topic",
    [
        (
            "Python",
            12,
            "Hash Table Implementation և Collision Handling and additional details " * 6,
        ),
        (
            "Python",
            123,
            "Արտաքին փոփոխականների օգտագործումը և հիշողության աշխատանքի կանոնները " * 5,
        ),
        (
            "TypeScript և տվյալների բազաներ",
            123,
            "Closures և Context with many advanced examples and details",
        ),
    ],
)
def test_long_topics_drop_whole_words_only_and_never_leave_and_or_ellipsis(subject, number, topic):
    result = st._youtube_title(subject, number, topic)
    assert len(result) <= 70 and result.startswith(f"{subject} | Դաս {number}")
    if " | " in result.removeprefix(f"{subject} | Դաս {number}"):
        retained = result.split(f"{subject} | Դաս {number} | ", 1)[1]
        assert topic.startswith(retained)
    assert not result.endswith(("և", "…", ",", ";", ":", "-", "—", "։"))


@pytest.mark.parametrize(
    "topic,expected",
    [
        ("", "Python | Դաս 5"),
        (None, "Python | Դաս 5"),
        ("Դաս՝", "Python | Դաս 5"),
        ("A" * 120, "Python | Դաս 5"),
        ("Դաս՝ Closures և Context", "Python | Դաս 5 | Closures և Context"),
        ("Closures և", "Python | Դաս 5 | Closures"),
        ("Closures և։", "Python | Դաս 5 | Closures"),
        ("Closures…", "Python | Դաս 5 | Closures"),
        ("և", "Python | Դաս 5"),
        ("…", "Python | Դաս 5"),
        ("  <Closures>  և  <Context> ", "Python | Դաս 5 | Closures և Context"),
    ],
)
def test_prefix_empty_single_word_angle_brackets_and_terminal_cleanup(topic, expected):
    assert st._youtube_title("Python", 5, topic) == expected
    assert len(expected) <= 70


@pytest.mark.parametrize("number", [12, 123])
def test_number_and_subject_angle_brackets_are_preserved_and_cleaned(number):
    result = st._youtube_title(
        "<TypeScript> և տվյալների բազաներ", number, "Closures և Context with more words"
    )
    assert result.startswith(f"TypeScript և տվյալների բազաներ | Դաս {number}")
    assert len(result) <= 70 and "<" not in result and ">" not in result


def test_a_long_prefix_is_the_explicit_exception_and_does_not_add_a_topic():
    subject = "Ա" * 65
    head = f"{subject} | Դաս 123"
    assert 70 < len(head) <= 100
    assert st._youtube_title(subject, 123, "Closures և Context") == head


def test_upload_shortens_only_youtube_and_leaves_library_classroom_metadata_untouched(
    tmp_path, monkeypatch
):
    d = DB(tmp_path / "test.db")
    try:
        jid = d.create_job(str(tmp_path / "Դաս 13.mp4"), "g42-title-fixture", "G", "Python")
        config = {
            "groups": {
                "G": {
                    "subjects": [{"name": "Python", "playlist": "PL-fixture"}],
                    "slack_channel": "C-fixture",
                    "classroom_course_id": "course-fixture",
                }
            }
        }
        outputs = {
            "metadata": {"title": JOB1, "description": "Դասի նկարագրություն։", "chapters": []},
            "blur": {"final": str(tmp_path / "final_blurred.mp4")},
            "segments": {"final_sec": 900.09},
        }
        original = deepcopy(outputs["metadata"])
        calls = []

        def upload(video, title, description, tags, privacy):
            calls.append(("youtube", title))
            return {"video_id": "fixture-video", "url": "https://youtu.be/fixture-video"}

        def announce(course, title, url):
            calls.append(("classroom", title))
            return "announcement"

        def add(entry):
            calls.append(("library", entry["title"]))

        publishers = {
            "youtube": SimpleNamespace(find_by_tag=lambda tag: None, upload=upload),
            "classroom": SimpleNamespace(announce=announce),
            "library": SimpleNamespace(add=add),
        }
        monkeypatch.setattr(st, "_pub", lambda name: publishers[name])
        monkeypatch.setattr(
            st.time,
            "sleep",
            lambda seconds: None if seconds == 0 else pytest.fail("unexpected real wait"),
        )
        c = Ctx(d, jid, tmp_path / "Դաս 13.mp4", tmp_path, "G", config, _outputs=outputs)
        outputs["youtube_upload"] = st.youtube_upload(c)
        st.classroom_post(c)
        st.library_add(c)
        assert calls == [
            ("youtube", "Python | Դաս 13 | Hash Table Implementation և Collision Handling"),
            ("classroom", JOB1),
            ("library", JOB1),
        ]
        assert outputs["metadata"] == original
        assert outputs["youtube_upload"]["lesson_no"] == 13
        assert {"external_id", "url", "reused", "lesson_no"} <= outputs["youtube_upload"].keys()
    finally:
        d.conn.close()
