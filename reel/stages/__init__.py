"""Ordered stage list = the whole Reel flow. Each stage's output is stored in DB."""
from __future__ import annotations

import importlib
import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

from reel.pipeline import Ctx, StopJob, _notify
from reel.stages import transcribe as tr

ROOT = Path(__file__).resolve().parents[2]
MIN_LESSON_SEC = 60  # short lessons and quick tests are allowed (was 30 min)
FIND_WAIT = (60, 120, 180)   # wait for YouTube uploads list to show a just-finished upload


DRY_RUN = os.environ.get("REEL_DRY_RUN") == "1"


def _pub(name: str):
    """Real publisher module, or its fake when REEL_DRY_RUN=1 (no network, for tests)."""
    if DRY_RUN:
        from reel.stages import _dryrun
        return getattr(_dryrun, name)
    return importlib.import_module(f"reel.publish.{name}")


def _group(ctx: Ctx) -> dict:
    group = ctx.config["groups"].get(ctx.group)
    if group is None:     # removed/renamed in Settings after the lesson was queued
        raise StopJob(f"group '{ctx.group}' is no longer in config.yaml — add it back in Settings and retry")
    return group


# --- media -------------------------------------------------------------------------
def join_stage(ctx: Ctx) -> dict:
    """A lesson recorded in parts (a folder) -> one file. A single file passes through untouched."""
    _group(ctx)           # a lesson of a removed group stops now, not after an hour of work
    if not ctx.source.is_dir():
        return {"parts": 1}
    from reel.pipeline import parts_of
    from reel.stages import join as jn
    parts = parts_of(ctx.source)
    if not parts:
        raise StopJob(f"folder '{ctx.source.name}' has no video files")
    if len(parts) == 1:
        return {"file": str(parts[0]), "parts": 1, "names": [parts[0].name]}
    return jn.join(parts, ctx.workdir)


def probe(ctx: Ctx) -> dict:
    try:
        dur = tr.media_duration(ctx.media())
    except Exception as e:  # noqa: BLE001
        raise StopJob(f"ffprobe cannot read file: {e}") from e
    if dur < MIN_LESSON_SEC:
        raise StopJob(f"recording is only {dur/60:.0f} min (< {MIN_LESSON_SEC//60}) — not a lesson?")
    return {"duration_sec": round(dur, 2)}


def audio(ctx: Ctx) -> dict:
    return {"wav": str(tr.extract_audio(ctx.media(), ctx.workdir / "audio16k.wav"))}


def transcribe_stage(ctx: Ctx) -> dict:
    return tr.transcribe(ctx.out("audio")["wav"], ctx.workdir / "transcript")


def segments_stage(ctx: Ctx) -> dict:
    from reel.stages import segments as seg
    r = seg.plan(ctx.media(), ctx.out("audio")["wav"], ctx.workdir / "transcript" / "transcript.json",
                 ctx.out("probe")["duration_sec"], ctx.config.get("cut"), ctx.workdir)
    from reel.insights import cut_summary
    try:
        transcript = json.loads((ctx.workdir / "transcript" / "transcript.json").read_text())
    except (OSError, ValueError):
        transcript = None
    from reel.stages.processing import for_ctx, pauses, to_final
    options=for_ctx(ctx)
    try:
        if options['smart_pauses']['enabled']:
            framing,_=_framing(ctx,ctx.media())
            if not framing or framing.get('version')!=2:
                from reel.framing import analyze_video
                framing=analyze_video(ctx.media(),mode='auto',correction='rotate')
            r=pauses(r,ctx.media(),ctx.out('audio')['wav'],transcript,framing,
                     options['smart_pauses'],ctx.config.get('cut',{}).get('max_cut_ratio',.4))
            (ctx.workdir/'segments.json').write_text(json.dumps(r,ensure_ascii=False,indent=2))
    except Exception as exc:
        r['processing_note']=f'Դադարների մշակումը բաց թողնվեց՝ {type(exc).__name__}'
    summary = cut_summary(r, transcript, ctx.out("join"))
    if r.get('time_map'):
        for cut in summary['cuts']:
            point=to_final(cut['start'],r['time_map'])
            if point is not None:cut['final_boundary']=point
        for event in r.get('pause_events',[]):
            saving=(event['end']-event['start'])*(1-1/event['speed']) if event['operation']=='speed' else event['end']-event['start']-event['retain_sec']
            summary['cuts'].append({**event,'kind':'other','duration':saving,
                'final_boundary':to_final(event['start'],r['time_map']),
                'first_words':'','last_words':'',
                'why':event['why']+(f" {event['speed']}× (ոչ հեռացում)" if event['operation']=='speed' else '')})
        summary['no_cuts_reason']=None
    summary["min_silence_sec"] = ctx.config.get("cut", {}).get("min_silence_sec", 180)
    return {"cut_summary": summary, "cuts": summary["cuts"],
            **{k:r[k] for k in ("time_map","pause_events","processing_note","labels","edges","no_cut_code","no_cut_details") if k in r},
            **{k: r[k] for k in ("keep", "original_sec", "final_sec", "cut_ratio", "unclear_ratio")},
            **({"auto_note": r["auto_note"]} if r.get("auto_note") else {})}     # Preserve the fallback explanation for the job page.


SILENT_LUFS = -35.0     # below this after normalisation the recording has no usable sound
BOARD_MAX_CUT = 0.001     # a framing that would leave out more of the board is not used


def _framing(ctx: Ctx, src) -> tuple[dict | None, dict]:
    """Optional framing; ambiguous intervals retain the full frame.
    The render stage sends one Armenian operator notice through the existing mechanism.
    Legacy board/rotate retains its static fast path.
    """
    requested = _group(ctx).get("framing", "off")
    if requested == "off":
        return None, {}
    correction = _group(ctx).get("framing_correction", "rotate")
    if requested in ("auto", "projector") or correction == "perspective":
        from reel.framing import analyze_video
        try:
            plan = analyze_video(src, mode=requested, correction=correction)
            return plan, {"board": "framed", "framing_uncertain_sec": plan["uncertain_seconds"],
                          "framing_confidence": plan["confidence"], "framing_mode": requested}
        except Exception as exc:
            return None, {"board": f"automatic framing failed ({type(exc).__name__}) — full frame kept"}
    from reel import board
    from reel.stages.render import video_info
    try:
        info = video_info(src)
        if info["width"] < info["height"]:
            return None, {"board": "portrait video — full frame kept"}
        found = board.detect(src)
        if not found["ok"]:
            return None, {"board": f"board not found reliably ({found['found']}/{found['frames']} frames)"
                                   " — full frame kept"}
        plan = board.plan(found["corners"], info["width"], info["height"])
        if not plan.get("cap_ok", True):
            return None, {"board": "framing would exceed 1.5x upscale — full frame kept"}
        if plan["board_cut"] > BOARD_MAX_CUT:
            return None, {"board": f"framing would cut {plan['board_cut']:.0%} of the board — full frame kept"}
        return plan, {"board": "framed", "board_tilt_deg": found["tilt_deg"], "board_agree": found["agree"]}
    except Exception as e:  # noqa: BLE001 — framing is optional, the lesson is not
        return None, {"board": f"board detection failed ({type(e).__name__}) — full frame kept"}


def render_stage(ctx: Ctx) -> dict:
    from reel.stages import render as rd
    keep = [tuple(k) for k in ctx.out("segments")["keep"]]
    src = ctx.media()
    framing, note = _framing(ctx, src)
    from reel.stages.processing import for_ctx
    options=for_ctx(ctx)
    try:
        if options['board_enhance']['enabled'] or options['ocr']['enabled']:
            if not framing or framing.get('version')!=2:
                from reel.framing import analyze_video
                # Obtain the target, preserving requested off framing via identity geometry.
                detected=analyze_video(src,mode='auto',correction='rotate')
                if _group(ctx).get('framing','off')=='off':
                    from reel.stages.render import video_info,target_size
                    info=video_info(src);w,h=info['width'],info['height'];ow,oh=target_size(info)
                    detected['output_size']=[ow,oh];detected['transition_sec']=0
                    for row in detected['timeline']:row['matrix']=[[ow/w,0,0],[0,oh/h,0],[0,0,1]]
                framing=detected
    except Exception as exc:
        note['auto_note']=f'Գրատախտակի մշակումը բաց թողնվեց՝ {type(exc).__name__}'
        _notify(f'⚠️ Դաս {ctx.job_id}․ գրատախտակի շրջանը չորոշվեց․ պահպանվել է սկզբնական պատկերը։')
    target_framing=framing
    if options['ocr']['enabled'] and not options['board_enhance']['enabled'] and _group(ctx).get('framing','off')=='off':
        framing=None # OCR samples do not require re-encoding an otherwise copyable stream.
    extra={}
    if options['studio_audio']['enabled'] or options['board_enhance']['enabled'] or ctx.out('segments').get('time_map'):
        extra={'processing':options,'time_map':ctx.out('segments').get('time_map')}
    try:
        r = rd.render(src, keep, ctx.workdir / "final.mp4", framing=framing, **extra)
    except Exception as e:  # noqa: BLE001
        if not framing:
            raise
        r = rd.render(src, keep, ctx.workdir / "final.mp4", **extra)         # framing is optional
        note = {"board": f"framed render failed ({type(e).__name__}) — full frame kept"}
    lufs = rd.measured_integrated(r["final"])
    if lufs < SILENT_LUFS:                    # critical: the lesson has (almost) no sound
        raise StopJob(f"audio is almost silent after normalisation ({lufs:.1f} LUFS) — check the recording")
    if abs(lufs - rd.TARGET["I"]) > 1.5:      # publish anyway, note it
        note = {**note, "auto_note": f"loudness {lufs:.1f} LUFS outside target {rd.TARGET['I']}±1.5 — published as is"}
    if _group(ctx).get("framing", "off") != "off" and (not framing or note.get("framing_uncertain_sec", 0) or "framed render failed" in note.get("board", "")):
        _notify(f"⚠️ Դաս {ctx.job_id} · {ctx.group}․ գրատախտակը կամ էկրանը վստահ չի որոշվել որոշ հատվածներում։ "
                "Այդ հատվածներում պահպանվել է ամբողջ կադրը։ Ստուգիր կադրավորման հաշվետվությունը։")
    return {**r, "lufs": lufs, **note, **({"target_framing":target_framing} if options["ocr"]["enabled"] else {})}


def _subject(ctx: Ctx) -> dict:
    """This lesson's subject {"name", "playlist"}: chosen at upload, else the group's only subject.
    Checked before the YouTube upload, so an unknown subject stops the job with nothing published."""
    subjects = _group(ctx)["subjects"]
    chosen = ctx.db.job(ctx.job_id)["subject"]
    if chosen:
        for s in subjects:
            if s["name"].casefold() == str(chosen).casefold():
                return s
        raise StopJob(f"subject '{chosen}' is no longer in group '{ctx.group}' — choose the lesson's subject and retry")
    if len(subjects) == 1:
        return subjects[0]
    raise StopJob(f"group '{ctx.group}' has {len(subjects)} subjects — choose the lesson's subject and retry")


def _refs(ctx: Ctx) -> list[Path]:
    return [ROOT / "refs" / name for name in _group(ctx).get("lecturers", [])]


MIN_LECTURER_SHARE = 0.10   # of pass-1 face detections; lower = wrong lecturer or bad refs
MIN_FACES_FOR_SHARE = 100   # too few faces (screen recording, empty room) -> no verdict


def _choice(ctx: Ctx) -> tuple[dict | None, str | None]:
    """The admin's lecturer choice for this lesson (job page), if any, plus a note when a
    broken choice file was ignored (never stop for this)."""
    path = ctx.workdir / "lecturer_choice.json"
    if not path.is_file():
        return None, None
    try:
        choice = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return None, f"lecturer choice file unreadable ({type(e).__name__}) — ignored"
    if not isinstance(choice, dict) or not (choice.get("none") is True or isinstance(choice.get("centre"), list)):
        return None, "lecturer choice file invalid — ignored"
    return choice, None


def _matched(analysis: list[dict]) -> tuple[int, int]:
    return sum(1 for a in analysis if a["lecturer"]), len(analysis)


def blur(ctx: Ctx) -> dict:
    """Blur every face except the lecturer's. Who the lecturer is:
    1) the admin's choice on the job page, 2) the group's reference photos when they match,
    3) automatic — the person most often in the frame (reel/lecturer.py); unsure -> StopJob."""
    import shutil
    from reel import lecturer as lec
    from reel.blur import analyze, blur_faces
    src = ctx.out("render")["final"]
    auto_dir = ctx.workdir / "auto_refs"
    choice, note = _choice(ctx)
    notes = [note] if note else []
    info: dict = {}
    analysis = refs = None
    if choice is None:
        names = _group(ctx).get("lecturers", [])
        group_refs = [str(d) for d in _refs(ctx) if d.is_dir() and any(d.glob("*.jpg"))]
        if group_refs:
            analysis = analyze(src, group_refs, fps=5.0)   # pass 1, reused by blur_faces (no 2nd run)
            known, faces = _matched(analysis)
            if faces < MIN_FACES_FOR_SHARE or known / faces >= MIN_LECTURER_SHARE:
                shutil.rmtree(auto_dir, ignore_errors=True)        # no stale automatic result
                refs, info = group_refs, {"lecturer_mode": "refs", "lecturers": names}
            else:   # a substitute lecturer? the group's photos do not match: recognise automatically
                # No Slack message: the note is shown on the job page
                notes.append(f"the group's lecturer photos match only {known}/{faces} faces — automatic recognition used")
                analysis, info = None, {"refs_unmatched": f"{known}/{faces}"}
        elif names:
            info = {"refs_missing": names}
    if refs is None:
        segment_info = ctx.out("segments") if ctx.db is not None or "segments" in ctx._outputs else {}
        found = lec.discover(src, auto_dir, choice=choice,
                             framing=ctx.out("render").get("framing"),
                             keep=segment_info.get("keep"),
                             time_map=segment_info.get("time_map"))  # unclear -> everyone blurred
        if found.get("auto_note"):
            notes.append(found["auto_note"])
        refs = [found["refs_dir"]] if found.get("refs_dir") else []
        info = {**info, "lecturer_mode": found["mode"],
                "lecturer": {k: found.get(k) for k in ("lecturer_rank", "refs", "face_frames", "sampled", "reason")}}
        analysis = analyze(src, refs, fps=5.0, keep_features=True) if refs else []   # "none": nobody exempt
        if refs:     # front-only automatic refs: add the lecturer's profile views from the tracks
            added = lec.track_refs(src, analysis, auto_dir, others=found.get("others", []))
            if added:
                analysis = lec.relabel(analysis, auto_dir)
                info["lecturer"]["track_refs"] = added
            for a in analysis:
                a.pop("feat", None)
        known, faces = _matched(analysis)
        if refs and found["mode"] == "auto" and faces >= MIN_FACES_FOR_SHARE and known / faces < MIN_LECTURER_SHARE:
            # Continue processing; the auto refs may be a student's -> blur everyone
            notes.append(f"the recognised lecturer matches only {known}/{faces} faces — everyone blurred")
            refs, analysis = [], []
            info["lecturer_mode"] = "none"
    out = ctx.workdir / "final_blurred.mp4"
    decisions = ctx.workdir / "blur_decisions.json"
    stats = blur_faces(src, str(out), refs, analysis=analysis, decisions_path=str(decisions))
    known, faces = _matched(analysis)
    return {"final": str(out), "decisions": str(decisions), "refs_dirs": refs, **info,
            "lecturer_share": round(known / faces, 3) if faces else None, **stats,
            **({"auto_note": "; ".join(notes)} if notes else {})}


def privacy_stage(ctx: Ctx) -> dict:
    """Final check on the BLURRED video, judged against the blur stage's decisions,
    with exactly the reference photos the blur used (automatic references or none)."""
    from reel.stages import privacy as pv
    b = ctx.out("blur")
    refs = b["refs_dirs"] if "refs_dirs" in b else _refs(ctx)     # Compatibility with saved jobs that lack reference metadata.
    r = pv.scan(b["final"], refs, ctx.workdir / "privacy", decisions=b.get("decisions"))
    if r.get("warnings"):   # isolated 1-2 frame cases never stop the job (privacy policy): review them
        _notify(f"⚠️ Դաս {ctx.job_id}. մշուշումից հետո {r.get('isolated_events', r['warnings'])} կարճ (1–2 կադր) "
                f"դեմքի դեպք կա, մշակումը շարունակվում է։ Ստուգիր դասի էջում՝ «Գաղտնիություն»։")
    return r


def preview_stage(ctx: Ctx) -> dict:
    """Web-dashboard copy of the guarded (blurred) video. Never blocks publishing."""
    from reel.stages import preview as pv
    try:
        return pv.make_preview(ctx.out("blur")["final"], ctx.workdir / "preview.mp4")
    except Exception as e:  # noqa: BLE001
        return {"skipped": str(e)[:300]}


def metadata_stage(ctx: Ctx) -> dict:
    from reel.stages import metadata as md
    keep = [tuple(k) for k in ctx.out("segments")["keep"]]
    from reel.stages.processing import for_ctx, remap_transcript
    options=for_ctx(ctx);rows=ctx.out('segments').get('time_map')
    transcript=ctx.workdir/'transcript'/'transcript.json'
    if rows:
        transcript=remap_transcript(transcript,rows,ctx.workdir/'transcript_final.json')
        keep=[(0,rows[-1]['final_end'])]
    m = md.generate(transcript, keep, ctx.workdir)
    if rows:
        from reel.stages.processing import to_source
        for chapter in m['chapters']:chapter['t']=to_source(chapter['final_t'],rows)
        existing=json.loads((ctx.workdir/'metadata.json').read_text())
        (ctx.workdir/'metadata.json').write_text(json.dumps({**existing,**m},ensure_ascii=False,indent=2))
    if options['ocr']['enabled']:
        from reel.stages import ocr
        try:
            from reel.stages.processing import make_map
            ocr_map=rows or make_map(ctx.out('segments')['keep'])
            result=ocr.extract(ctx.out('blur')['final'],ctx.out('render').get('target_framing',ctx.out('render').get('framing')),options['ocr'],time_map=ocr_map)
            ocr.save(ctx.db.conn,ctx.job_id,result['segments'])
            if not result['segments']:
                m['auto_note']=' · '.join(filter(None,[m.get('auto_note'),'OCR՝ վստահ ճանաչված տեքստ չի գտնվել']))
            (ctx.workdir/'ocr.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
            m=ocr.add_chapters(m,result['chapters'],ocr_map)
            existing=json.loads((ctx.workdir/'metadata.json').read_text())
            (ctx.workdir/'metadata.json').write_text(json.dumps({**existing,**m},ensure_ascii=False,indent=2))
        except Exception as e:
            m['auto_note']=' · '.join(filter(None,[m.get('auto_note'),f'OCR բաց թողնվեց՝ {type(e).__name__}: {str(e)[:120]}']))
    return {"title": m["title"], "description": m["youtube_description"], "chapters": m["chapters"],
            **({"auto_note": m["auto_note"]} if m.get("auto_note") else {})}     # Preserve the fallback explanation for the job page.


def thumbnail(ctx: Ctx) -> dict:
    try:
        from reel.thumbnail import make_thumbnail
    except ImportError:
        return {"skipped": "reel.thumbnail not available"}
    try:
        from reel.stages.processing import for_ctx
        option=for_ctx(ctx)['thumbnail'];extra={}
        if option['enabled']:
            subject=_subject(ctx)['name']
            extra={'lesson_info':{'course':ctx.group.split()[0], 'group':ctx.group,
                    'lesson':_lesson_number(ctx,subject),'subject':subject},'variant':option['variant']}
        path = make_thumbnail(ctx.out("blur")["final"], ctx.out("metadata")["title"],
                              str(ctx.workdir / "thumbnail.jpg"), group=ctx.group, **extra)
        return {"path": path}
    except Exception as e:  # noqa: BLE001 - a missing thumbnail must not block publishing
        return {"skipped": str(e), "auto_note": f"thumbnail skipped ({type(e).__name__}) — published without it"}


# --- publishing (idempotent via ctx.publish_once) -----------------------------------
def _youtube_title(subject: str, n: int, topic: str) -> str:
    """Keep the first complete topic within 70 characters; preserve the numbered prefix."""
    topic = " ".join(str(topic or "").replace("<", "").replace(">", "").split())
    if topic.startswith("Դաս՝"):
        topic = topic[len("Դաս՝"):].strip()
    head = " ".join(f"{subject} | Դաս {n}".replace("<", "").replace(">", "").split())
    if len(head) > 70:                         # Keep the complete course prefix even when it exceeds the title budget.
        return head
    topic = re.split(r",|;|:| - | — ", topic, maxsplit=1)[0].strip()
    words = topic.split()
    room = 70 - len(head) - len(" | ")
    while words:
        words[-1] = words[-1].rstrip(".,;:!?։՝՜՞՛…—–-()[]{}\"'«»")
        if not words[-1] or words[-1] == "և":
            words.pop()
        elif len(" ".join(words)) <= room:
            break
        else:
            words.pop()
    return f"{head} | {' '.join(words)}" if words else head


def youtube_upload(ctx: Ctx) -> dict:
    subject = _subject(ctx)["name"]                 # unknown subject -> stop before publishing anything
    n = _lesson_number(ctx, subject)                # the same N goes to the title and to Slack
    youtube = _pub("youtube")
    tag = f"reel-{ctx.job_id}"
    marker = ctx.workdir / "pub_youtube.pending"

    def find():
        waits = FIND_WAIT if marker.exists() else (0,)   # just crashed mid-upload? give YouTube time
        for w in waits:
            time.sleep(w)
            vid = youtube.find_by_tag(tag)
            if vid:
                return vid, f"https://www.youtube.com/watch?v={vid}"
        return None

    def do():
        m = ctx.out("metadata")
        r = youtube.upload(ctx.out("blur")["final"], _youtube_title(subject, n, m["title"]), m["description"],
                           [tag, ctx.group], privacy="unlisted")
        return r["video_id"], r["url"]

    return {**ctx.publish_once("youtube", do, find), "lesson_no": n}


def playlist(ctx: Ctx) -> dict:
    """Add the video to the playlist of THIS lesson's subject (one playlist per lesson)."""
    youtube = _pub("youtube")
    vid = ctx.out("youtube_upload")["external_id"]
    subject = _subject(ctx)
    pl = subject.get("playlist") or ""
    if not pl:      # the subject has no playlist yet (Settings): publish without one, never stop
        return {"skipped": "this subject has no playlist yet", "subject": subject["name"]}
    r = ctx.publish_once("playlist", lambda: (youtube.add_to_playlist(vid, pl), None),
                         lambda: ("existing", None) if youtube.is_in_playlist(vid, pl) else None)
    return {**r, "playlist": pl, "subject": subject["name"]}


# Measured 2026-11-18 on a real channel: thumbnails.set right after the upload got
# HTTP 403, and the same call ~1 min later succeeded. Wait and retry before giving up.
THUMB_RETRY_WAIT = (60, 120)


def thumbnail_set(ctx: Ctx) -> dict:
    path = ctx.out("thumbnail").get("path")
    if not path:
        return {"skipped": True}
    youtube = _pub("youtube")
    vid = ctx.out("youtube_upload")["external_id"]
    error, attempt = None, 0
    for attempt, wait in enumerate((0, *THUMB_RETRY_WAIT), 1):
        time.sleep(wait)
        try:
            youtube.set_thumbnail(vid, path)   # idempotent overwrite: retrying is safe
            return {"set": True, "attempts": attempt}
        except Exception as e:  # noqa: BLE001
            error = e
    return {"set": False, "error": str(error), "attempts": attempt,
            "auto_note": f"thumbnail not set after {attempt} tries — video published without it"}


# Owner 2026-11-19: the students' Slack link goes out only after YouTube has finished processing
# the video (a fresh upload is not playable for minutes), never sooner than YT_MIN_WAIT after the
# upload. Rejected by YouTube, or still not processed after YT_MAX_WAIT -> stop, no Slack message.
YT_MIN_WAIT = 0
YT_POLL = 15
YT_MAX_WAIT = 60 * 60


def _wait_youtube_ready(ctx: Ctx, video_id: str) -> None:
    if DRY_RUN or ctx.db.publication(ctx.job_id, "slack"):
        return
    youtube = _pub("youtube")
    start = time.monotonic()
    from reel.progress import observed_sleep, processing_fraction, report
    def expired():
        raise StopJob(f"YouTube is still processing the video after {YT_MAX_WAIT // 60} min — Slack not sent")
    while True:
        elapsed = time.monotonic() - start
        if elapsed >= YT_MAX_WAIT:
            expired()
        report(processing_fraction(elapsed))
        status = youtube.processing_status(video_id)
        if status not in ("processed", "uploaded", "unknown"):
            raise StopJob(f"YouTube did not accept the video (status '{status}') — Slack not sent")
        remaining = YT_MAX_WAIT - (time.monotonic() - start)
        if remaining <= 0:
            expired()
        if status == "processed":
            return
        observed_sleep(min(YT_POLL, remaining), sleep=time.sleep,
                       fraction=lambda: processing_fraction(time.monotonic()-start))


_LESSON_NO = re.compile(r"(?:դաս|das|lesson)?[\s._-]*0*([1-9]\d{0,2})", re.IGNORECASE)


def _name_number(source_path: object) -> int | None:
    """The number in a lesson's own upload name ("5", "Դաս 5", "das-5"), if it has one.
    Never the parent folder: that is archive/<job id> or the group's inbox folder."""
    source = Path(str(source_path or ""))
    for name in (source.name, source.stem):
        if match := _LESSON_NO.fullmatch(name.strip()):
            return int(match.group(1))
    return None


def _course_lessons(db, group_cfg: dict, group: str, subject: str, exclude: int | None,
                    pending: bool) -> list[tuple[object, bool]]:
    """(source_path, announced) of the course's (group + subject) lessons announced in Slack
    (dry-run posts not counted) and, with pending, of those still queued or running."""
    single = len(group_cfg["subjects"]) == 1
    rows = db.conn.execute(
        "SELECT j.id, j.subject, j.source_path, j.status, p.job_id, p.external_id FROM jobs j"
        " LEFT JOIN publications p ON p.job_id = j.id AND p.target = 'slack' WHERE j.grp = ?",
        (group,)).fetchall()
    out = []
    for jid, subj, source, status, posted, ext in rows:
        if jid == exclude or not (single or str(subj or "").casefold() == subject.casefold()):
            continue
        announced = posted is not None and "dry" not in str(ext or "")
        if announced or (pending and status in ("queued", "running")):
            out.append((source, announced))
    return out


def _next_number(lessons: list[tuple[object, bool]]) -> int:
    named = [n for n in (_name_number(source) for source, _ in lessons) if n is not None]
    return max([1 + len(lessons)] + [1 + n for n in named])


def _lesson_number(ctx: Ctx, subject: str) -> int:
    """N of "the N-th lesson of the course" (group + subject). The number in the lesson's
    upload name ("Դաս 5") wins; otherwise 1 + the course's lessons already announced in
    Slack, and never below 1 + the highest number such a lesson was named with."""
    job = ctx.db.job(ctx.job_id)
    own = _name_number(job["source_path"]) if job else None
    if own is not None:
        return own
    return _next_number(_course_lessons(ctx.db, _group(ctx), ctx.group, subject, ctx.job_id, False))


def next_lesson_number(db, config: dict, group: str, subject: str | None = None) -> int:
    """upload page prefill: the number the course's next lesson gets — 1 + its lessons
    announced in Slack or still queued/running, never below 1 + the highest named number.
    subject=None means the group's only subject. KeyError for an unknown group."""
    group_cfg = config["groups"][group]
    if subject is None:
        subject = group_cfg["subjects"][0]["name"]
    return _next_number(_course_lessons(db, group_cfg, group, subject, None, True))


def slack_post(ctx: Ctx) -> dict:
    slack = _pub("slack")
    yt = ctx.out("youtube_upload")
    subject = _subject(ctx)["name"]
    n = yt.get("lesson_no") or _lesson_number(ctx, subject)    # the same N as the YouTube title
    line = f"{subject} դասընթացի {n}-{'ին' if n == 1 else 'րդ'} դասի տեսանյութը տեղադրված է YouTube-ում"
    _wait_youtube_ready(ctx, yt["external_id"])
    return ctx.publish_once("slack", lambda: (slack.post_link(_group(ctx)["slack_channel"], line, yt["url"]), None))


def classroom_post(ctx: Ctx) -> dict:
    if not _group(ctx).get("classroom_course_id"):     # Classroom is off for this group (config.yaml)
        return {"skipped": "Classroom is off for this group"}
    classroom = _pub("classroom")
    yt = ctx.out("youtube_upload")
    return ctx.publish_once("classroom", lambda: (classroom.announce(
        _group(ctx)["classroom_course_id"], ctx.out("metadata")["title"], yt["url"]), None))


def library_add(ctx: Ctx) -> dict:
    library = _pub("library")
    yt = ctx.out("youtube_upload")
    entry = {"job_id": str(ctx.job_id), "group": ctx.group, "subject": _subject(ctx)["name"],
             "title": ctx.out("metadata")["title"],
             "lesson_no": yt.get("lesson_no"), "duration": (ctx.out("segments") if ctx.db is not None else ctx._outputs.get("segments", {})).get("final_sec"),
             "chapters": ctx.out("metadata").get("chapters", []),
             "url": yt["url"], "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}

    def do():
        library.add(entry)
        return None, None

    return ctx.publish_once("library", do)


STAGES = [
    ("join", join_stage), ("probe", probe), ("audio", audio), ("transcribe", transcribe_stage),
    ("segments", segments_stage), ("render", render_stage), ("blur", blur), ("privacy", privacy_stage),
    ("preview", preview_stage), ("metadata", metadata_stage), ("thumbnail", thumbnail),
    ("youtube_upload", youtube_upload), ("playlist", playlist), ("thumbnail_set", thumbnail_set),
    ("slack_post", slack_post), ("classroom_post", classroom_post), ("library_add", library_add),
]

# Study notes need metadata; captions need the final timeline.
from reel.stages.learning_stages import install as _install_learning_stages  # noqa: E402
_install_learning_stages(STAGES)
