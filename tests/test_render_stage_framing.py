"""G-24 in the render stage: conservative fallback; G-35 notifies the operator without stopping."""
import pytest

import reel.board
import reel.stages as st
import reel.stages.render as rd
from reel.pipeline import Ctx


@pytest.fixture
def env(tmp_path, monkeypatch):
    calls = {"render": [], "detect": 0, "notify": []}
    state = {"found": {"ok": True, "found": 20, "frames": 20, "agree": 0.9, "corners": [[0, 0]] * 4,
                       "tilt_deg": -0.4},
             "cut": 0.0, "fail_framed": False, "info": {"width": 3840, "height": 2160}}

    def render(src, keep, out, *, framing=None, **kw):
        calls["render"].append(framing)
        if framing and state["fail_framed"]:
            raise RuntimeError("render failed — reencode/frame: boom")
        return {"final": str(out), "mode": "reencode", "path": "frame" if framing else "normalize"}

    def detect(src, **kw):
        calls["detect"] += 1
        if state["found"] is None:
            raise OSError("ffmpeg missing")
        return dict(state["found"])

    monkeypatch.setattr(rd, "render", render)
    monkeypatch.setattr(rd, "measured_integrated", lambda p: rd.TARGET["I"])
    monkeypatch.setattr(rd, "video_info", lambda p: dict(state["info"]))
    monkeypatch.setattr(reel.board, "detect", detect)
    monkeypatch.setattr(reel.board, "plan", lambda c, w, h: {"crop": [1, 2, 3, 4], "board_cut": state["cut"]})
    monkeypatch.setattr(st, "_notify", calls["notify"].append)

    def ctx(framing="board"):
        g = {"lecturers": []} if framing is None else {"lecturers": [], "framing": framing}
        c = Ctx(None, 7, tmp_path / "src.mp4", tmp_path / "work", "G", {"groups": {"G": g}})
        c.workdir.mkdir(exist_ok=True)
        c._outputs.update(segments={"keep": [[0, 10]]}, join={"file": str(tmp_path / "j.mov")})
        return c

    return type("E", (), {"calls": calls, "state": state, "ctx": staticmethod(ctx)})


def test_groups_without_framing_are_rendered_as_before(env):
    out = st.render_stage(env.ctx(framing=None))
    assert env.calls["render"] == [None] and env.calls["detect"] == 0 and "board" not in out


def test_a_sure_board_is_framed(env):
    out = st.render_stage(env.ctx())
    assert env.calls["render"] == [{"crop": [1, 2, 3, 4], "board_cut": 0.0}] and out["board"] == "framed"
    assert out["board_tilt_deg"] == -0.4 and out["path"] == "frame"


@pytest.mark.parametrize("change, words", [
    ({"found": {"ok": False, "found": 4, "frames": 20, "agree": 0.2, "corners": None, "tilt_deg": None}}, "not found"),
    ({"found": None}, "detection failed"),
    ({"cut": 0.2}, "cut 20%"),
    ({"info": {"width": 1080, "height": 1920}}, "portrait"),
    ({"fail_framed": True}, "framed render failed"),
])
def test_anything_unsure_keeps_full_frame_and_notifies_once(env, change, words):
    env.state.update(change)
    out = st.render_stage(env.ctx())
    assert env.calls["render"][-1] is None and words in out["board"] and "full frame kept" in out["board"]
    assert len(env.calls["notify"]) == 1 and "ամբողջ կադրը" in env.calls["notify"][0]


def test_loudness_off_target_is_published_with_a_note_but_silence_stops(env, monkeypatch):
    from reel.pipeline import StopJob
    monkeypatch.setattr(rd, "measured_integrated", lambda p: rd.TARGET["I"] - 3)
    out = st.render_stage(env.ctx(framing=None))
    assert "published as is" in out["auto_note"] and env.calls["notify"] == []
    monkeypatch.setattr(rd, "measured_integrated", lambda p: -50.0)
    with pytest.raises(StopJob, match="almost silent"):
        st.render_stage(env.ctx(framing=None))
