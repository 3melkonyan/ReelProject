"""Who is the lecturer in the blur stage — admin choice > group refs (if they match) > automatic.

reel.blur.analyze/blur_faces and reel.lecturer.discover are replaced by fakes; the
privacy stage must use exactly the reference photos the blur used.
"""

from __future__ import annotations

import json

import pytest
from face_models import requires_face_models

import reel.blur
import reel.lecturer
import reel.stages as st
from reel.pipeline import Ctx


@pytest.fixture
def env(tmp_path, monkeypatch):
    (tmp_path / "refs" / "lecturer1").mkdir(parents=True)
    (tmp_path / "refs" / "lecturer1" / "ref_0.jpg").write_bytes(b"x")
    monkeypatch.setattr(st, "ROOT", tmp_path)
    calls = {
        "analyze": [],
        "blur": [],
        "discover": [],
        "notify": [],
        "scan": [],
        "track_refs": [],
        "relabel": 0,
    }
    share = {"value": 0.8}
    extra = {"added": 0}

    def analyze(src, refs, fps=5.0, keep_features=False):
        calls["analyze"].append(list(refs))
        n = 200
        k = round(n * share["value"])
        items = [
            {"t": i / 5, "box": [0, 0, 10, 10], "lecturer": i < k, "sim": None} for i in range(n)
        ]
        if keep_features:
            for i, a in enumerate(items):
                a.update(track=i, feat=[0.1] * 128)
        return items

    def track_refs(src, analysis, out_dir, others=()):
        calls["track_refs"].append(list(others))
        return extra["added"]

    def relabel(analysis, refs_dir):
        calls["relabel"] += 1
        for a in analysis:
            a["lecturer"] = True
        return analysis

    monkeypatch.setattr(reel.lecturer, "track_refs", track_refs)
    monkeypatch.setattr(reel.lecturer, "relabel", relabel)

    def blur_faces(src, out, refs, analysis=None, decisions_path=None):
        calls["blur"].append((list(refs), analysis))
        return {
            "frames": 10,
            "blurred_boxes": 1,
            "lecturer_boxes": 2,
            "uncertain_boxes": 1,
            "fps_processed": 1.0,
        }

    found = {
        "mode": "auto",
        "refs_dir": str(tmp_path / "work" / "auto_refs"),
        "lecturer_rank": 1,
        "refs": 5,
        "face_frames": 500,
        "sampled": 1000,
    }

    def discover(src, out_dir, choice=None, **kw):
        calls["discover"].append(choice)
        return dict(found)

    monkeypatch.setattr(reel.blur, "analyze", analyze)
    monkeypatch.setattr(reel.blur, "blur_faces", blur_faces)
    monkeypatch.setattr(reel.lecturer, "discover", discover)
    monkeypatch.setattr(st, "_notify", calls["notify"].append)

    def ctx(lecturers=("lecturer1",)):
        c = Ctx(
            None,
            7,
            tmp_path / "src.mp4",
            tmp_path / "work",
            "G",
            {"groups": {"G": {"lecturers": list(lecturers)}}},
        )
        c.workdir.mkdir(exist_ok=True)
        c._outputs["render"] = {"final": str(tmp_path / "work" / "final.mp4")}
        return c

    return type(
        "E",
        (),
        {
            "root": tmp_path,
            "calls": calls,
            "share": share,
            "found": found,
            "extra": extra,
            "ctx": staticmethod(ctx),
        },
    )


def test_automatic_refs_are_extended_with_profile_views_and_tracks_relabelled(env):
    env.found["others"] = [[0.2] * 128]
    env.extra["added"] = 3
    env.share["value"] = 0.5
    out = st.blur(env.ctx(lecturers=()))
    assert env.calls["track_refs"] == [[[0.2] * 128]] and env.calls["relabel"] == 1
    assert out["lecturer"]["track_refs"] == 3 and out["lecturer_share"] == 1.0  # relabelled
    refs, analysis = env.calls["blur"][0]
    assert all("feat" not in a for a in analysis)  # features never leave


def test_group_refs_are_never_extended(env):
    st.blur(env.ctx())
    assert env.calls["track_refs"] == [] and env.calls["relabel"] == 0


def test_group_refs_that_match_are_used_and_stale_auto_result_removed(env):
    c = env.ctx()
    (c.workdir / "auto_refs").mkdir()
    out = st.blur(c)
    assert out["lecturer_mode"] == "refs" and out["lecturers"] == ["lecturer1"]
    assert (
        out["refs_dirs"] == [str(env.root / "refs" / "lecturer1")] and out["lecturer_share"] == 0.8
    )
    assert env.calls["discover"] == [] and len(env.calls["analyze"]) == 1
    assert not (c.workdir / "auto_refs").exists()


def test_group_refs_that_do_not_match_fall_back_to_automatic(env, monkeypatch):
    calls = env.calls
    orig = reel.blur.analyze

    def analyze(src, refs, fps=5.0, keep_features=False):  # a substitute lecturer: lecturer1 is in
        env.share["value"] = (
            0.05 if "lecturer1" in refs[0] else 0.9
        )  # 5% of faces, the auto refs match
        return orig(src, refs, fps, keep_features)

    monkeypatch.setattr(reel.blur, "analyze", analyze)
    out = st.blur(env.ctx())
    assert out["lecturer_mode"] == "auto" and out["refs_unmatched"] == "10/200"
    assert calls["discover"] == [None] and len(calls["analyze"]) == 2 and calls["notify"] == []
    assert "automatic recognition used" in out["auto_note"]
    assert calls["blur"][0][0] == [env.found["refs_dir"]] and out["lecturer_share"] == 0.9


def test_no_lecturers_means_automatic(env):
    out = st.blur(env.ctx(lecturers=()))
    assert out["lecturer_mode"] == "auto" and out["refs_dirs"] == [env.found["refs_dir"]]
    assert out["lecturer"]["lecturer_rank"] == 1 and env.calls["analyze"] == [
        [env.found["refs_dir"]]
    ]


def test_admin_choice_wins_over_group_refs(env):
    c = env.ctx()
    choice = {"identity": 2, "centre": [0.1] * 128, "by": "admin"}
    (c.workdir / "lecturer_choice.json").write_text(json.dumps(choice))
    env.found["mode"] = "chosen"
    out = st.blur(c)
    assert env.calls["discover"] == [choice] and out["lecturer_mode"] == "chosen"
    assert env.calls["analyze"] == [[env.found["refs_dir"]]]  # the group refs were not consulted


def test_invalid_choice_file_is_ignored_with_a_note(env):
    c = env.ctx(lecturers=())
    (c.workdir / "lecturer_choice.json").write_text('{"identity": 2}')
    out = st.blur(c)
    assert env.calls["discover"] == [None] and "choice file invalid" in out["auto_note"]


def test_nobody_is_the_lecturer_blurs_every_face(env):
    env.found.update(mode="none", refs_dir=None)
    out = st.blur(env.ctx(lecturers=()))
    assert out["lecturer_mode"] == "none" and out["refs_dirs"] == []
    assert env.calls["analyze"] == [] and env.calls["blur"] == [([], [])]


def test_automatic_choice_that_matches_few_faces_blurs_everyone(env):
    env.share["value"] = 0.05  # never stop
    out = st.blur(env.ctx(lecturers=()))
    assert (
        out["lecturer_mode"] == "none"
        and out["refs_dirs"] == []
        and env.calls["blur"][-1] == ([], [])
    )
    assert "matches only 10/200" in out["auto_note"] and env.calls["notify"] == []


def test_admin_choice_is_not_second_guessed_by_the_share_check(env):
    env.share["value"] = 0.05
    env.found["mode"] = "chosen"
    c = env.ctx(lecturers=())
    (c.workdir / "lecturer_choice.json").write_text(
        json.dumps({"identity": 3, "centre": [0.1] * 128})
    )
    assert st.blur(c)["lecturer_mode"] == "chosen"


def test_privacy_uses_exactly_the_blur_refs(env, monkeypatch):
    from reel.stages import privacy as pv

    seen = []
    monkeypatch.setattr(
        pv, "scan", lambda video, refs, out_dir, decisions=None: seen.append(list(refs)) or {}
    )
    c = env.ctx()
    c._outputs["blur"] = {"final": "f.mp4", "decisions": "d.json", "refs_dirs": []}
    st.privacy_stage(c)
    c._outputs["blur"] = {
        "final": "f.mp4",
        "decisions": "d.json",
    }  # A saved job without reference metadata.
    st.privacy_stage(c)
    assert seen == [[], [env.root / "refs" / "lecturer1"]]


@requires_face_models
def test_analyze_without_refs_labels_nobody_but_empty_refs_dirs_still_fail(tmp_path):
    """'none' mode runs the privacy scan with no refs at all; a refs dir without faces is still an error."""
    import subprocess

    video = tmp_path / "v.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-nostdin",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x240:rate=10:duration=1",
            "-pix_fmt",
            "yuv420p",
            str(video),
        ],
        check=True,
        timeout=60,
    )
    real_analyze = (
        reel.blur.analyze.__wrapped__
        if hasattr(reel.blur.analyze, "__wrapped__")
        else reel.blur.analyze
    )
    assert real_analyze(str(video), [], fps=5.0) == []
    (tmp_path / "empty").mkdir()
    with pytest.raises(RuntimeError, match="No usable lecturer reference photos"):
        real_analyze(str(video), [str(tmp_path / "empty")], fps=5.0)


def test_isolated_privacy_warnings_notify_but_never_stop(env, monkeypatch):
    from reel.stages import privacy as pv

    monkeypatch.setattr(
        pv,
        "scan",
        lambda *a, **k: {"frames": 9, "other_faces": 0, "warnings": 3, "isolated_events": 2},
    )
    c = env.ctx()
    c._outputs["blur"] = {"final": "f.mp4", "decisions": "d.json", "refs_dirs": []}
    assert st.privacy_stage(c)["warnings"] == 3
    assert len(env.calls["notify"]) == 1 and "2 կարճ" in env.calls["notify"][0]
    monkeypatch.setattr(
        pv,
        "scan",
        lambda *a, **k: {"frames": 9, "other_faces": 0, "warnings": 0, "isolated_events": 0},
    )
    st.privacy_stage(c)
    assert len(env.calls["notify"]) == 1  # nothing to review -> no message
