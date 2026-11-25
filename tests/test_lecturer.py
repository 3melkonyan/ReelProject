"""Automatic lecturer recognition (reel/lecturer.py) on synthetic identity features.

No video: _sample() is replaced by synthetic samples. Each person is a random unit
vector in SFace's 128-D space (two random people: cosine ~0 +- 0.09); a sample is
the person's vector plus noise. The written refs must load in FaceID exactly like
tools/make_refs.py refs.
"""
from __future__ import annotations

import json

import cv2
import numpy as np
import pytest

from reel import lecturer as lec
from reel.pipeline import StopJob
from reel.stages.privacy import COSINE_MATCH, FaceID
from face_models import requires_face_models

RNG = np.random.default_rng(7)
PEOPLE = [v / np.linalg.norm(v) for v in RNG.normal(size=(4, 128))]
CROP = cv2.imencode(".jpg", np.full((60, 60, 3), 128, np.uint8))[1].tobytes()


def samples(spec, frames, noise=0.03, every=2.0, seed=1):
    """spec: [(vector, frame indices)] -> lec._Samples, chronological like the real sampler."""
    rng = np.random.default_rng(seed)
    rows = sorted((f, k) for k, (_, fr) in enumerate(spec) for f in fr)
    s = lec._Samples()
    for f, k in rows:
        v = spec[k][0] + rng.normal(0, noise, 128)
        s.t.append(f * every)
        s.frame.append(f)
        s.box.append((100.0 + 10 * k, 60.0, 40.0, 40.0))
        s.score.append(0.9)
        s.raw.append(v.reshape(1, 128).astype(np.float32))
        s.unit.append(lec._unit(v))
        s.jpg.append(CROP)
    s.frames, s.size = frames, (960, 540)
    s.finish()
    return s


def run(tmp_path, monkeypatch, s, **kw):
    monkeypatch.setattr(lec, "_sample", lambda video, every: s)
    return lec.discover(tmp_path / "lesson.mp4", tmp_path / "auto_refs", **kw)


def stored(tmp_path):
    return json.loads((tmp_path / "auto_refs" / "auto.json").read_text())


def test_people_are_far_apart_in_feature_space():
    sims = [float(a @ b) for i, a in enumerate(PEOPLE) for b in PEOPLE[i + 1:]]
    assert max(sims) < COSINE_MATCH


@requires_face_models
def test_the_person_most_often_in_frame_is_the_lecturer(tmp_path, monkeypatch):
    s = samples([(PEOPLE[0], range(300)), (PEOPLE[1], range(40))], frames=400)   # student in 40 frames
    r = run(tmp_path, monkeypatch, s)
    out = tmp_path / "auto_refs"
    assert r["mode"] == "auto" and r["lecturer_rank"] == 1 and r["refs_dir"] == str(out)
    assert r["identities"][0]["presence"] == 300 and r["identities"][1]["presence"] == 40
    assert 3 <= r["refs"] <= lec.REFS_KEEP
    assert (out / "identity_1.jpg").is_file() and (out / "identity_2.jpg").is_file()
    assert not (tmp_path / "auto_refs.part").exists()
    data = stored(tmp_path)
    assert data["mode"] == "auto" and len(data["centres"]) == len(data["identities"]) == 2
    assert data["refs_dir"] == str(out)                                           # not the .part folder
    assert len(data["centres"][0]) == 128
    # refs are in the tools/make_refs.py format: FaceID loads every descriptor (npz + image hash)
    fid = FaceID([out])
    assert len(fid.refs) == r["refs"]
    assert all(float(lec._unit(f) @ PEOPLE[0]) > 0.8 for f in fid.refs)        # the lecturer, not the student
    times = sorted(r["ref_times"])
    assert all(b - a >= 50 for a, b in zip(times, times[1:]))                     # spread over the lesson


def test_two_people_equally_often_is_unsure_and_blurs_everyone(tmp_path, monkeypatch):
    s = samples([(PEOPLE[0], range(0, 400, 2)), (PEOPLE[1], range(1, 300, 2))], frames=400)
    r = run(tmp_path, monkeypatch, s)                     # never stop
    assert r["mode"] == "none" and "everyone blurred" in r["auto_note"]
    data = stored(tmp_path)
    assert data["mode"] == "none" and "almost equally" in data["reason"]
    assert (tmp_path / "auto_refs" / "identity_2.jpg").is_file()
    assert not list((tmp_path / "auto_refs").glob("ref_*"))                       # nobody exempted


def test_several_people_often_in_frame_is_a_crowd(tmp_path, monkeypatch):
    s = samples([(PEOPLE[0], range(300)), (PEOPLE[1], range(100)), (PEOPLE[2], range(100, 190))], frames=400)
    r = run(tmp_path, monkeypatch, s)
    assert r["mode"] == "none" and "people are often in the frame" in r["auto_note"]


def test_a_rarely_visible_top_face_is_not_trusted(tmp_path, monkeypatch):
    # the top identity is in 30 frames but faces are in 330 frames, spread over many others
    spec = [(PEOPLE[0], range(30))] + [(lec._unit(RNG.normal(size=128)), range(30 + 3 * k, 33 + 3 * k)) for k in range(100)]
    s = samples(spec, frames=400)
    r = run(tmp_path, monkeypatch, s)
    assert r["mode"] == "none" and "only 30 of" in r["auto_note"]


def test_front_and_profile_fragments_of_one_person_are_merged(tmp_path, monkeypatch):
    # one person seen from the front (frames 0-149) and in profile (150-299): their centres are
    # similar (0.45) but single noisy samples are not, so greedy grouping makes two fragments
    profile = lec._unit(0.45 * PEOPLE[0] + np.sqrt(1 - 0.45 ** 2) * PEOPLE[3])
    s = samples([(PEOPLE[0], range(150)), (profile, range(150, 300)), (PEOPLE[1], range(40))],
                frames=400, noise=0.09)
    assert len([m for m in lec._cluster(s.unit, s.frame) if len(m) > 20]) >= 3      # fragmented ...
    r = run(tmp_path, monkeypatch, s)
    assert r["mode"] == "auto" and r["identities"][0]["presence"] == 300           # ... and merged
    assert r["refs"] >= 3


def test_two_similar_people_seen_together_are_never_merged(tmp_path, monkeypatch):
    twin = lec._unit(0.45 * PEOPLE[0] + np.sqrt(1 - 0.45 ** 2) * PEOPLE[3])
    s = samples([(PEOPLE[0], range(150)), (twin, range(150))], frames=400, noise=0.09)   # same frames
    assert run(tmp_path, monkeypatch, s)["mode"] == "none"                                # unsure -> blur all


def test_almost_no_faces_means_nobody_is_exempt(tmp_path, monkeypatch):
    s = samples([(PEOPLE[0], range(10))], frames=400)
    r = run(tmp_path, monkeypatch, s)
    assert r["mode"] == "none" and r["refs_dir"] is None
    assert not list((tmp_path / "auto_refs").glob("ref_*"))


@requires_face_models
def test_admin_choice_picks_the_person_even_when_not_dominant(tmp_path, monkeypatch):
    s = samples([(PEOPLE[0], range(0, 400, 2)), (PEOPLE[1], range(1, 300, 2))], frames=400)
    assert run(tmp_path, monkeypatch, s)["mode"] == "none"                     # unsure -> blur all
    centre = stored(tmp_path)["centres"][1]                                    # the admin picks #2
    r = run(tmp_path, monkeypatch, s, choice={"identity": 2, "centre": centre})
    assert r["mode"] == "chosen" and r["lecturer_rank"] == 2
    assert all(float(lec._unit(f) @ PEOPLE[1]) > 0.8 for f in FaceID([tmp_path / "auto_refs"]).refs)


def test_admin_choice_not_found_again_blurs_everyone(tmp_path, monkeypatch):
    s = samples([(PEOPLE[0], range(300))], frames=400)
    r = run(tmp_path, monkeypatch, s, choice={"identity": 1, "centre": PEOPLE[2].tolist()})
    assert r["mode"] == "none" and "not found again" in r["auto_note"]


def test_admin_choice_blur_everyone(tmp_path, monkeypatch):
    s = samples([(PEOPLE[0], range(300))], frames=400)
    r = run(tmp_path, monkeypatch, s, choice={"none": True})
    assert r["mode"] == "none" and r["refs_dir"] is None and "admin" in r["reason"]


def test_a_new_run_replaces_the_old_result(tmp_path, monkeypatch):
    run(tmp_path, monkeypatch, samples([(PEOPLE[0], range(300))], frames=400))
    assert list((tmp_path / "auto_refs").glob("ref_*.jpg"))
    run(tmp_path, monkeypatch, samples([(PEOPLE[0], range(10))], frames=400))     # now: no lecturer
    assert not list((tmp_path / "auto_refs").glob("ref_*.jpg")) and stored(tmp_path)["mode"] == "none"


# --- other views of the lecturer from pass-1 tracks (track_refs / relabel) ---------------------
PROFILE = lec._unit(0.2 * PEOPLE[0] + np.sqrt(1 - 0.2 ** 2) * PEOPLE[3])    # front refs do not match it


def face(t, track, vec, sim, size=80, seed=0):
    v = vec + np.random.default_rng(seed).normal(0, 0.03, 128)
    return {"t": t, "box": [500, 300, size, size], "lecturer": True, "sim": sim, "track": track,
            "feat": v.reshape(1, 128).astype(np.float32)}


@pytest.fixture
def frames(monkeypatch):
    monkeypatch.setattr(lec, "_frame_at", lambda video, t: np.full((1080, 1920, 3), 90, np.uint8))


def lecturer_tracks(n_tracks=4):
    """Each track: two strong front matches, then the lecturer turns to profile 3 times."""
    out = []
    for k in range(n_tracks):
        base = 100.0 * k
        out += [face(base, k, PEOPLE[0], 0.7, seed=k * 10), face(base + 0.2, k, PEOPLE[0], 0.65, seed=k * 10 + 1)]
        out += [face(base + 1 + 0.2 * j, k, PROFILE, 0.1, seed=k * 10 + 2 + j) for j in range(3)]
    return out


def test_profile_views_from_strong_lecturer_tracks_become_refs(tmp_path, frames):
    (tmp_path / "ref_0.jpg").write_bytes(CROP)
    added = lec.track_refs(tmp_path / "v.mp4", lecturer_tracks(), tmp_path)
    assert 1 <= added <= lec.EXTRA_KEEP and (tmp_path / "ref_1.jpg").is_file()        # numbered after ref_0
    feats = [np.load(p)["feature"] for p in sorted(tmp_path.glob("ref_*.npz"))]
    assert all(float(lec._unit(f) @ PROFILE) > 0.8 for f in feats)


def test_weak_tracks_one_off_views_and_frequent_other_people_are_never_used(tmp_path, frames):
    weak = [face(5.0 * j, 1, PEOPLE[0], 0.4, seed=j) for j in range(2)] + \
           [face(6.0 + 0.2 * j, 1, PROFILE, 0.1, seed=50 + j) for j in range(5)]          # matched < 0.5
    once = lecturer_tracks(1)[:3]                                                        # profile seen once
    assert lec.track_refs(tmp_path / "v.mp4", weak + once, tmp_path) == 0
    # the profile view resembles another frequent person of the lesson (a student?) -> excluded
    assert lec.track_refs(tmp_path / "v.mp4", lecturer_tracks(), tmp_path, others=[PROFILE.tolist()]) == 0
    assert not list(tmp_path.glob("ref_*"))


@requires_face_models
def test_relabel_keeps_a_lecturer_seen_only_in_profile(tmp_path, frames):
    analysis = lecturer_tracks()
    alone = [face(900 + 0.2 * j, 99, PROFILE, 0.1, seed=99 + j) for j in range(4)]       # never frontal
    for a in alone:
        a["lecturer"] = False
    student = [face(950 + 0.2 * j, 98, PEOPLE[1], 0.05, seed=200 + j) for j in range(4)]
    for a in student:
        a["lecturer"] = False
    analysis += alone + student
    assert lec.track_refs(tmp_path / "v.mp4", analysis, tmp_path) >= 1
    lec.relabel(analysis, tmp_path)
    assert all(a["lecturer"] for a in alone) and not any(a["lecturer"] for a in student)
