"""Reel web dashboard: JSON API + static single-page UI.

Run:  .venv/bin/python -m uvicorn reel.web.app:app --host 127.0.0.1 --port 8765
Auth: personal admin/lecturer/student accounts on the existing signed session cookie.
The existing admin password opens first-account setup only when users is empty.
Legacy viewer/uploader/student passwords remain available until a personal admin disables them.
See reel.web.accounts/access/account_api and docs/WEB.md. No worker starts with this app.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re

from reel.hints import hint as _hint
import secrets
import shutil
import sqlite3
import subprocess
import tempfile
from types import SimpleNamespace
from contextlib import closing

from starlette.concurrency import run_in_threadpool
import time
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from reel import pipeline
from reel.db import DB
from reel import lesson_dates
from reel.web import accounts as account_auth

ROOT = Path(__file__).resolve().parents[2]
STATIC = Path(__file__).parent / "static"
WORK = ROOT / "work"
SECRETS_FILE = ROOT / "secrets" / "web.json"

from reel.web.labels import STAGES, STAGE_LABEL
PUB_TARGETS = ["youtube", "playlist", "slack", "classroom", "library"]


# --- secrets & auth -------------------------------------------------------------------
def _load_secrets() -> dict:
    if SECRETS_FILE.exists():
        data = json.loads(SECRETS_FILE.read_text())
    else:
        SECRETS_FILE.parent.mkdir(mode=0o700, exist_ok=True)
        data = {"session_key": secrets.token_urlsafe(32),
                "admin_password": secrets.token_urlsafe(10),
                "viewer_password": secrets.token_urlsafe(10)}
        SECRETS_FILE.write_text(json.dumps(data, indent=1))
        SECRETS_FILE.chmod(0o600)
    if "uploader_password" not in data:          # added later: upload without admin rights
        data["uploader_password"] = secrets.token_urlsafe(10)
        SECRETS_FILE.write_text(json.dumps(data, indent=1))   # before env overrides: never persist those
        SECRETS_FILE.chmod(0o600)
    if "student_password" not in data:
        data["student_password"] = secrets.token_urlsafe(10)
        SECRETS_FILE.write_text(json.dumps(data, indent=1))
        SECRETS_FILE.chmod(0o600)
    data["student_password"] = os.environ.get("REEL_STUDENT_PASSWORD", data["student_password"])
    data["admin_password"] = os.environ.get("REEL_ADMIN_PASSWORD", data["admin_password"])
    data["viewer_password"] = os.environ.get("REEL_VIEWER_PASSWORD", data["viewer_password"])
    data["uploader_password"] = os.environ.get("REEL_UPLOADER_PASSWORD", data["uploader_password"])
    return data


SECRETS = _load_secrets()

app = FastAPI(title="Reel", docs_url=None, redoc_url=None, openapi_url=None)



@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    # YouTube's embedded player needs the embedding origin (Error 153 without a Referer).
    # Cross-origin requests get the bare origin only; tokens never live in URLs.
    if "referrer-policy" not in resp.headers:      # keep stricter per-route policies
        resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if resp.headers.get("content-type", "").startswith("application/json"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def _ip(request: Request) -> str:
    return (request.headers.get("cf-connecting-ip")
            or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
            or (request.client.host if request.client else "?"))


def authenticated(request: Request) -> str:
    r = request.session.get("role")
    if r not in ("admin", "viewer", "uploader", "student", "lecturer", "bootstrap"):
        raise HTTPException(401, "login required")
    return r


def role(request: Request) -> str:
    r = authenticated(request)
    if r not in ("admin", "viewer", "uploader", "lecturer"):
        raise HTTPException(403, "role not allowed")
    return r


def admin(request: Request) -> str:
    if role(request) != "admin":
        raise HTTPException(403, "admin only")
    if request.headers.get("x-requested-with") != "reel":   # CSRF: forces CORS preflight
        raise HTTPException(400, "bad request")
    return "admin"


def uploader(request: Request) -> str:
    r = role(request)
    if r not in ("admin", "uploader", "lecturer"):
        raise HTTPException(403, "upload not allowed")
    if request.headers.get("x-requested-with") != "reel":   # CSRF, as for admin actions
        raise HTTPException(400, "bad request")
    return r


def db() -> DB:
    database = DB(ROOT / "reel.db")
    permitted = account_auth.groups()
    if permitted is not None:
        def scoped_jobs(limit=50):
            placeholders = ','.join('?' for _ in permitted) or 'NULL'
            return database.conn.execute(f'SELECT * FROM jobs WHERE grp IN ({placeholders}) ORDER BY id DESC LIMIT ?', (*permitted, limit)).fetchall()
        database.jobs = scoped_jobs
    return database


# --- helpers ----------------------------------------------------------------------------
def _read_json(p: Path) -> dict | None:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _ts(s: str | None) -> float | None:
    return datetime.fromisoformat(s).timestamp() if s else None


def _privacy_passed(job_id: int) -> bool:
    s = {x["name"]: x for x in db().stages(job_id)}.get("privacy")
    return s is not None and s["status"] == "done"


def _video_file(job_id: int, who: str, quality: str = "preview") -> Path | None:
    """Only the BLURRED video is ever served (never final.mp4). A viewer gets it only after
    the privacy guard passed; the admin may inspect a blurred file the guard stopped."""
    if who != "admin" and not _privacy_passed(job_id):
        return None
    names = ("final_blurred.mp4",) if quality == "full" else ("preview.mp4", "final_blurred.mp4")
    for name in names:
        p = WORK / str(job_id) / name
        if p.is_file():
            return p
    return None


def _worker() -> dict:
    w = _read_json(ROOT / "out" / "worker.json") or {}
    alive = False
    if pid := w.get("pid"):
        try:
            os.kill(int(pid), 0)
            alive = True
        except (OSError, ValueError):
            alive = False
    return {"alive": alive, "dry_run": bool(w.get("dry_run")), "since": w.get("started")}


def _inbox() -> list[dict]:
    """Lessons waiting in inbox/<group>/ (not picked up yet: still copying, or worker off):
    a video file, or a folder with the parts of one lesson."""
    from reel.pipeline import VIDEO_EXT, parts_of
    out, base = [], ROOT / "inbox"
    if base.is_dir():
        for g in sorted(p for p in base.iterdir() if p.is_dir()):
            for f in sorted(g.iterdir()):
                if f.name.startswith("."):
                    continue
                try:
                    if f.is_file() and f.suffix.lower() in VIDEO_EXT:
                        sts, n = [f.stat()], 1
                    elif f.is_dir() and (parts := parts_of(f)):
                        sts, n = [p.stat() for p in parts], len(parts)
                    else:
                        continue
                except OSError:          # moved away while listing
                    continue
                out.append({"group": g.name, "file": f.name, "parts": n,
                            "size_mb": round(sum(s.st_size for s in sts) / 1e6),
                            "age_sec": round(time.time() - max(s.st_mtime for s in sts))})
    return out[:20]


def _dry(yt) -> bool:
    """Publications made by the dry-run fakes (reel/stages/_dryrun.py): never real, never linked."""
    return yt is not None and "dry" in str(yt["external_id"] or "")


def _job_brief(d: DB, j) -> dict:
    st = {s["name"]: s for s in d.stages(j["id"])}
    names = [n for n, _ in STAGES if not (n == "join" and "join" not in st and "probe" in st)]  # jobs before 'join'
    done = sum(1 for n in names if st.get(n) is not None and st[n]["status"] == "done")
    meta = _read_json(WORK / str(j["id"]) / "metadata.json") or {}
    seg = d.stage_output(j["id"], "segments") or {}
    probe = d.stage_output(j["id"], "probe") or {}
    yt = d.publication(j["id"], "youtube")
    created, updated = _ts(j["created_at"]), _ts(j["updated_at"])
    proc = 0.0                       # real processing time: sum of stage runs (robust to re-runs)
    for s in st.values():
        a, b = _ts(s["started_at"]), _ts(s["finished_at"])
        if a and b and b >= a:
            proc += b - a
    return {
        "id": j["id"], "status": j["status"], "group": j["grp"],
        "file": Path(j["source_path"]).name, "title": meta.get("title"), "subject": j["subject"],
        "current_stage": j["current_stage"], "current_label": STAGE_LABEL.get(j["current_stage"] or ""),
        "error": j["error"], "hint": _hint(j["error"]) if j["status"] == "stopped" else None,
        "created": created, "updated": updated,
        "lesson_date": lesson_dates.job_date(j), "lesson_date_inferred": not bool(j["lesson_date"]),
        "done_stages": done, "total_stages": len(names),
        "progress": d.insights(j["id"]).get("progress"),
        "duration_sec": probe.get("duration_sec"), "final_sec": seg.get("final_sec"),
        "cut_ratio": seg.get("cut_ratio"), "youtube_url": yt["url"] if yt and not _dry(yt) else None,
        "dry": _dry(yt),
        "has_thumbnail": (WORK / str(j["id"]) / "thumbnail.jpg").is_file(),
        "elapsed_sec": (updated - created) if (created and updated) else None,
        "proc_sec": round(proc) if proc else None,
    }


def _stage_summary(name: str, out: dict) -> dict:
    """Small, human-readable subset of a stage output (never the raw blob)."""
    keys = {
        "join": ["parts", "mode", "joined_sec"], "probe": ["duration_sec"], "transcribe": ["speed_x", "segments", "elapsed_sec"],
        "segments": ["original_sec", "final_sec", "cut_ratio", "unclear_ratio"],
        "render": ["lufs"], "preview": ["size_mb", "skipped"], "blur": ["lecturer_mode", "blurred_boxes", "lecturer_boxes", "uncertain_boxes", "fps_processed"],
        "privacy": ["frames", "lecturer_frames", "other_faces", "warnings"], "metadata": ["title"],
        "youtube_upload": ["url", "reused"], "slack_post": ["reused"], "classroom_post": ["reused", "skipped"],
    }.get(name, [])
    summary = {k: out[k] for k in keys if k in out}
    if isinstance(out.get("auto_note"), str):
        summary["auto_note"] = out["auto_note"]
    if name != "render" or not isinstance(out.get("board"), str):
        return summary

    import math

    def finite(value):
        if type(value) not in (int, float):
            return False
        try:
            return math.isfinite(value)
        except OverflowError:
            return False

    summary["board"] = out["board"]
    for key in ("board_tilt_deg", "board_agree"):
        if finite(out.get(key)):
            summary[key] = out[key]
    framing = out.get("framing")
    if isinstance(framing, dict):
        # Keep only small framing measurements, never detector/debug output.
        clean = {key: framing[key] for key in ("rotate_deg", "zoom", "board_cut")
                 if finite(framing.get(key))}
        for key, length in (("size", 2), ("crop", 4)):
            value = framing.get(key)
            if isinstance(value, (list, tuple)) and len(value) == length and all(finite(v) for v in value):
                clean[key] = list(value)
        if clean:
            summary["framing"] = clean
    return summary


# --- routes -----------------------------------------------------------------------------
@app.get("/healthz")
def healthz():
    return {"ok": True}


@app.post("/api/login")
async def login(request: Request):
    try:
        body = await request.json()
        if not isinstance(body, dict): raise ValueError()
    except ValueError:
        raise HTTPException(400, "Անվավեր հարցում։") from None
    password = body.get("password", "")
    identity = str(body.get("login") or "").strip()[:254]
    ip = request.client.host if request.client else "?"
    d = db()
    try:
        account_auth.login_limit(d, identity or "shared", ip)
        if identity:
            row = account_auth.find_user(d, identity)
            valid = account_auth.verify_password(password, row["password_hash"] if row else account_auth.DUMMY_HASH)
            if row is not None and row["active"] and valid:
                account_auth.login_limit(d, identity, ip, failed=False)
                if row["role"]=="admin":return await _admin_login(request,row,d,globals())
                d.conn.execute("UPDATE users SET last_login_at=? WHERE id=?", (account_auth.utc(), row["id"]))
                row = d.conn.execute("SELECT * FROM users WHERE id=?", (row["id"],)).fetchone()
                account_auth.session_user(request, row)
                return {"role": row["role"], "user": account_auth.public_user(row)}
        else:
            pw = str(password).encode("utf-8")
            has_users = d.conn.execute("SELECT count(*) FROM users").fetchone()[0]
            if not has_users and secrets.compare_digest(pw, SECRETS["admin_password"].encode()):
                request.session.clear(); request.session.update(role="bootstrap", bootstrap=True)
                return {"role": "bootstrap", "bootstrap": True}
            if account_auth.legacy_enabled(d):
                for who in ("viewer", "uploader", "student"):
                    secret = SECRETS.get(who + "_password")
                    if secret and secrets.compare_digest(pw, secret.encode()):
                        account_auth.login_limit(d, "shared", ip, failed=False)
                        request.session.clear(); request.session.update(role=who, legacy=True)
                        return {"role": who}
        account_auth.login_limit(d, identity or "shared", ip, failed=True)
        raise HTTPException(401, account_auth.AUTH_ERROR)
    finally:
        d.conn.close()


@app.post("/api/logout")
def logout(request: Request, _: str = Depends(authenticated)):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
def me(request: Request, r: str = Depends(authenticated)):
    p = getattr(request.state, "principal", None)
    return {"role": r, **({"user": p} if p and p.get("id") else {}), **({"bootstrap": True} if r == "bootstrap" else {})}


@app.get("/api/summary")
def summary(who: str = Depends(role)):
    d = db()
    jobs = [_job_brief(d, j) for j in d.jobs(500)]
    if who != "admin":
        for item in jobs:
            item["has_thumbnail"] = item["has_thumbnail"] and _privacy_passed(item["id"])
    by = {s: sum(1 for j in jobs if j["status"] == s) for s in ("queued", "running", "stopped", "done")}
    real_counts=dict(by)
    from reel.demo import job_cards
    demos=job_cards(d,account_auth.groups())
    by['done']+=len(demos)
    done = [j for j in jobs if j["status"] == "done"]
    today = datetime.now().date()
    cut_min = sum((j["duration_sec"] or 0) - (j["final_sec"] or j["duration_sec"] or 0) for j in done) / 60
    timed = [j["proc_sec"] for j in done if j["proc_sec"]]
    avg = (sum(timed) / len(timed) / 60) if timed else None
    running = next((j for j in jobs if j["status"] == "running"), None)
    return {
        "counts": by, "real_counts":real_counts, "demo_count":len(demos), "total": len(jobs)+len(demos),
        "done_today": sum(1 for j in done if j["updated"] and
                          datetime.fromtimestamp(j["updated"]).date() == today),
        "avg_processing_min": round(avg, 1) if avg else None,
        "minutes_cut": round(cut_min, 1),
        "hours_processed": round(sum(j["duration_sec"] or 0 for j in done) / 3600, 1),
        "running": running, "recent": sorted(jobs+demos,key=lambda j:j["updated"] or 0,reverse=True)[:6], "worker": _worker(), "inbox": [x for x in _inbox() if account_auth.allowed(x["group"])],
        "disk_free_gb": round(shutil.disk_usage(ROOT).free / 1e9),
        "stages": [{"name": n, "label": l} for n, l in STAGES],
    }


@app.get("/api/jobs")
def jobs(status: str | None = None, who: str = Depends(role)):
    d = db()
    out = [_job_brief(d, j) for j in d.jobs(500)]
    if who != "admin":
        for item in out:
            item["has_thumbnail"] = item["has_thumbnail"] and _privacy_passed(item["id"])
    from reel.demo import job_cards
    out+=job_cards(d,account_auth.groups())
    return [j for j in out if not status or j['status']==status]


@app.get("/api/activity")
def activity(_: str = Depends(role)):
    """Last stage events across recent jobs (for the dashboard feed)."""
    d, ev = db(), []
    for j in d.jobs(20):
        meta = _read_json(WORK / str(j["id"]) / "metadata.json") or {}
        title = meta.get("title") or Path(j["source_path"]).name
        for s in d.stages(j["id"]):
            st, en = _ts(s["started_at"]), _ts(s["finished_at"])
            t = en or (st if s["status"] == "running" else None)
            if not t:
                continue
            ev.append({"t": t, "job": j["id"], "title": title, "label": STAGE_LABEL.get(s["name"], s["name"]),
                       "status": s["status"], "dur": (en - st) if (st and en and en >= st) else None})
    ev.sort(key=lambda e: e["t"], reverse=True)
    return ev[:12]


@app.post("/api/jobs/{job_id}/date")
async def job_lesson_date(job_id: int, request: Request, _: str = Depends(admin)):
    try:
        body = await request.json()
        day = lesson_dates.validate(body.get("lesson_date") if isinstance(body, dict) else None)
    except (ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from None
    database = db()
    try:
        try:
            database.job(job_id)
        except KeyError:
            raise HTTPException(404, "Դասը չի գտնվել։") from None
        database.set_job(job_id, lesson_date=day)
    finally:
        database.conn.close()
    return {"lesson_date": day, "lesson_date_inferred": False}


@app.get("/api/jobs/{job_id}")
def job_detail(job_id: int, who: str = Depends(role)):
    d = db()
    try:
        j = d.job(job_id)
    except KeyError:
        raise HTTPException(404, "not found") from None
    wd = WORK / str(job_id)
    meta = _read_json(wd / "metadata.json") or d.stage_output(job_id, "metadata") or {}
    if j["lesson_date"]:
        meta = lesson_dates.dated_metadata(meta, j["lesson_date"])
    st = {s["name"]: s for s in d.stages(job_id)}
    stages = []
    for name, label in STAGES:
        s = st.get(name)
        out = json.loads(s["output"]) if s is not None and s["output"] else {}
        # Older metadata wrappers retain only title/description/chapters in the DB.
        if (name == "metadata" and s is not None and s["status"] == "done"
                and "auto_note" not in out and isinstance(meta.get("auto_note"), str)):
            out = {**out, "auto_note": meta["auto_note"]}
        start, end = (_ts(s["started_at"]), _ts(s["finished_at"])) if s is not None else (None, None)
        stages.append({
            "name": name, "label": label, "status": s["status"] if s is not None else "pending",
            "attempts": s["attempts"] if s is not None else 0,
            "started": start, "duration_sec": (end - start) if (start and end and end >= start) else None,
            "error": (s["error"] or "").strip().splitlines()[0][:400] if s is not None and s["error"] else None,
            "info": _stage_summary(name, out),
        })
    seg = (_read_json(wd / "segments.json") or d.stage_output(job_id, "segments") or {}) if who != "uploader" else {}
    priv = _read_json(wd / "privacy" / "privacy.json") or {}
    pubs = {t: (dict(p) if (p := d.publication(job_id, t)) else None) for t in PUB_TARGETS}
    yt_dry = _dry(d.publication(job_id, "youtube"))
    for p in pubs.values():   # each publication on its own; an id-less row (old fake library) follows YouTube
        if p is not None and (_dry(p) or (yt_dry and not p.get("external_id"))):
            p["dry"], p["url"] = True, None      # fake link (youtu.be/dry0001) would 404
    faces = []   # crops may show a student's face: admin only
    if who == "admin" and (wd / "privacy").is_dir():
        hit_t = {round(float(h["t"]), 1) for h in priv.get("hits", [])}
        for p in sorted((wd / "privacy").glob("face_*.jpg"))[:30]:
            m = re.match(r"face_(\d+(?:\.\d+)?)s_", p.name)
            t = float(m.group(1)) if m else None
            faces.append({"name": p.name, "t": t,
                          "kind": "hit" if t is not None and round(t, 1) in hit_t else "warning"})
    return {
        **_job_brief(d, j), "stages": stages,
        "has_thumbnail": (WORK / str(job_id) / "thumbnail.jpg").is_file() and (who == "admin" or _privacy_passed(job_id)),
        "upload_media": _read_json(wd / "upload_media.json") or {},
        "segments": {k: seg.get(k) for k in ("keep", "cuts", "labels", "original_sec", "final_sec",
                                              "cut_ratio", "unclear_ratio")} if seg else None,
        "metadata": {"title": meta.get("title"), "description": meta.get("description"),
                     "youtube_description": meta.get("youtube_description"),
                     "chapters": meta.get("chapters", [])} if meta else None,
        "privacy": {k: priv.get(k) for k in ("frames", "lecturer_frames", "other_faces", "warnings",
                                             "lecturer_by_blur_track", "already_blurred")} if priv else None,
        "faces": faces, "faces_hidden": who != "admin", "publications": pubs,
        "subject_choices": _subject_names(j["grp"]),
        "has_video": _video_file(job_id, who) is not None,
        "has_full": _video_file(job_id, who, "full") is not None,
        "has_preview": (wd / "preview.mp4").is_file(), "privacy_passed": _privacy_passed(job_id),
    }


@app.get("/api/jobs/{job_id}/video")
def job_video(job_id: int, q: str = "preview", who: str = Depends(role)):
    p = _video_file(job_id, who, "full" if q == "full" else "preview")
    if not p:
        raise HTTPException(404, "no video yet")
    return FileResponse(p, media_type="video/mp4")   # Starlette serves HTTP Range requests


@app.get("/api/jobs/{job_id}/thumbnail")
def job_thumb(job_id: int, who: str = Depends(role)):
    p = WORK / str(job_id) / "thumbnail.jpg"
    if who != "admin" and not _privacy_passed(job_id):
        raise HTTPException(404, "no thumbnail")
    if WORK.is_symlink() or p.parent.is_symlink() or p.is_symlink() or not p.is_file():
        raise HTTPException(404, "no thumbnail")
    return FileResponse(p, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@app.get("/api/jobs/{job_id}/faces/{name}")
def job_face(job_id: int, name: str, who: str = Depends(role)):
    if who != "admin":
        raise HTTPException(403, "admin only")
    if not (name.startswith("face_") and name.endswith(".jpg") and "/" not in name and ".." not in name):
        raise HTTPException(400, "bad name")
    p = WORK / str(job_id) / "privacy" / name
    if not p.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(p, media_type="image/jpeg")


def _subject_names(group: str) -> list[str]:
    try:
        from reel.config import load_config
        return [s["name"] for s in load_config(str(ROOT / "config.yaml"))["groups"][group]["subjects"]]
    except Exception:  # noqa: BLE001 - unknown group / unreadable config: no choices
        return []


@app.post("/api/jobs/{job_id}/subject")
async def job_subject(job_id: int, request: Request, _: str = Depends(admin)):
    """Choose the lesson's subject (= playlist) when the group has several and none was chosen."""
    try:
        body = await request.json()
    except ValueError:
        body = {}
    d = db()
    try:
        j = d.job(job_id)
    except (KeyError, TypeError):
        raise HTTPException(404, "not found") from None
    if j["status"] not in ("queued", "stopped"):
        raise HTTPException(409, "only queued or stopped lessons can change subject")
    if d.publication(job_id, "youtube"):
        raise HTTPException(409, "already on YouTube: the playlist cannot change any more")
    names = {n.casefold(): n for n in _subject_names(j["grp"])}
    chosen = names.get(str(body.get("subject", "")).casefold())
    if not chosen:
        raise HTTPException(400, "unknown subject for this group")
    d.set_job(job_id, subject=chosen)
    return {"ok": True, "subject": chosen}


@app.post("/api/jobs/{job_id}/retry")
def job_retry(job_id: int, _: str = Depends(admin)):
    d = db()
    if d.job(job_id)["status"] != "stopped":
        raise HTTPException(409, "only stopped jobs can be retried")
    pipeline.retry(d, job_id)
    return {"ok": True}


@app.post("/api/jobs/{job_id}/resolve")
async def job_resolve(job_id: int, request: Request, _: str = Depends(admin)):
    body = await request.json()
    target, state = body.get("target"), body.get("state")
    if target not in PUB_TARGETS or state not in ("posted", "not-posted"):
        raise HTTPException(400, "bad target/state")
    d = db()
    if state == "posted" and not d.publication(job_id, target):
        d.add_publication(job_id, target, "manual", None)
    (WORK / str(job_id) / f"pub_{target}.pending").unlink(missing_ok=True)
    return {"ok": True}


@app.get("/api/groups")
def groups(_: str = Depends(role)):
    try:
        from reel.config import load_config
        cfg = load_config(str(ROOT / "config.yaml"))
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"error": f"config.yaml: {e}"}, status_code=200)

    def mask(v):
        v = str(v or "")
        return v if len(v) <= 6 else v[:3] + "•••" + v[-3:]

    from reel.web.group_status import completed_groups
    database = db()
    try:
        completed = completed_groups(database)
    finally:
        database.conn.close()
    out = []
    for name, g in cfg["groups"].items():
        if name in completed or not account_auth.allowed(name):
            continue
        lect = [{"name": n, "refs": len(list((ROOT / "refs" / n).glob("*.jpg")))}
                for n in g.get("lecturers", [])]
        out.append({"name": name, "playlist": ", ".join(mask(s["playlist"]) for s in g["subjects"] if s["playlist"]),
                    "subjects": [{"name": s["name"], "playlist": mask(s["playlist"])} for s in g["subjects"]],
                    "lecturer_mode": "refs" if any(l["refs"] for l in lect) else "auto",
                    "slack": mask(g.get("slack_channel")),
                    "classroom": mask(g.get("classroom_course_id")), "lecturers": lect,
                    "inbox": f"inbox/{name}/"})
    return {"groups": out}


@app.get("/api/uploads/next-number")
def upload_next_number(group: str, subject: str | None = None, _: str = Depends(uploader)):
    from reel.config import load_config
    from reel.stages import next_lesson_number
    cfg = load_config(str(ROOT / "config.yaml"))
    if group not in cfg["groups"]:
        raise HTTPException(400, "unknown group")
    _require_open_group(group)
    choices = {s["name"].casefold(): s["name"] for s in cfg["groups"][group]["subjects"]}
    chosen = choices.get(str(subject or "").casefold())
    if chosen is None and len(choices) == 1 and not subject:
        chosen = next(iter(choices.values()))
    if chosen is None:
        raise HTTPException(400, "choose the subject")
    path = ROOT / "reel.db"
    if not path.exists():
        number = 1
    else:
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as connection:
            number = next_lesson_number(SimpleNamespace(conn=connection), cfg, group, chosen)
    return {"number": number, "subject": chosen}


def _atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _upload_receipt(uid: str) -> dict | None:
    if not _UID.fullmatch(uid):
        return None
    path = _uploads() / "receipts" / (uid + ".json")
    receipt = _read_json(path) if not path.is_symlink() and not path.parent.is_symlink() else None
    if receipt and account_auth.groups() is not None:
        jid = receipt.get("job_id") or receipt.get("duplicate_of")
        if jid:
            database = db()
            try:
                try: account_auth.require_group(database.job(jid)["grp"])
                except KeyError: raise HTTPException(404, "Չի գտնվել։") from None
            finally: database.conn.close()
    return receipt


def _upload_video_info(parts: list[Path]) -> dict:
    """Advisory metadata only: the pipeline still performs the authoritative probe.

    Probe failures are reported, not treated as evidence of adequate resolution.
    This also preserves byte-upload compatibility for older clients.
    """
    files = []
    for path in parts:  # sequential; never spawn parallel ffprobes for phone parts
        item = {"file": path.name}
        try:
            result = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                "-show_entries", "stream=width,height:stream_tags=creation_time,com.apple.quicktime.creationdate:format_tags=creation_time,com.apple.quicktime.creationdate", "-of", "json", str(path)],
                capture_output=True, text=True, check=True, timeout=30)
            probe = json.loads(result.stdout)
            item["recorded_date"] = lesson_dates.recording_date(probe)
            stream = probe["streams"][0]
            width, height = int(stream["width"]), int(stream["height"])
            if min(width, height) <= 0:
                raise ValueError("invalid dimensions")
            item.update(width=width, height=height, low_resolution=min(width, height) < 1080)
        except (OSError, TypeError, ValueError, KeyError, IndexError, subprocess.SubprocessError):
            item["resolution_unknown"] = True
        files.append(item)
    return {"files": files, "recorded_date": next((x["recorded_date"] for x in files if x.get("recorded_date")), None),
            "low_resolution": any(x.get("low_resolution") for x in files),
            "resolution_unknown": any(x.get("resolution_unknown") for x in files)}


# --- lesson upload (admin, uploader) -------------------------------------------------------------
# A lesson (one file or its parts) arrives in chunks: Cloudflare's free tunnel rejects request bodies
# over 100 MB, and a dropped connection resumes from the bytes already on disk (start is idempotent).
# On finish the parts become ONE job exactly like an inbox folder (pipeline.enqueue: dedup, archive).
UPLOAD_CHUNK = 16 * 1024 * 1024
UPLOAD_MAX_FILES = 8
UPLOAD_MAX_BYTES = 40 * 1024 ** 3          # per file
UPLOAD_KEEP_SEC = 3 * 24 * 3600            # abandoned uploads are deleted after 3 days
_UID = re.compile(r"[0-9a-f]{20}")
_UNSAFE = re.compile(r"[^\w\-. ()]+")
_CHUNK_LOCKS: dict[str, asyncio.Lock] = {}


def _uploads() -> Path:
    return ROOT / "out" / "uploads"


def _clean_name(value, limit: int = 80) -> str:
    return _UNSAFE.sub("_", str(value)).strip(" ._-")[:limit].strip(" ._-")


def _upload_meta(uid: str) -> dict:
    meta = _read_json(_uploads() / uid / "meta.json") if _UID.fullmatch(uid) else None
    directory = _uploads() / uid
    if not meta or directory.is_symlink() or (directory / "parts").is_symlink() or (directory / "meta.json").is_symlink():
        raise HTTPException(404, "unknown upload")
    return meta


def _prune_uploads() -> None:
    base = _uploads()
    receipts = base / "receipts"
    if receipts.is_dir() and not receipts.is_symlink():
        for receipt in receipts.glob("*.json"):
            try:
                if not receipt.is_symlink() and time.time() - receipt.stat().st_mtime > UPLOAD_KEEP_SEC:
                    receipt.unlink()
            except OSError:
                continue
    for d in (base.iterdir() if base.is_dir() else []):
        try:
            if (_UID.fullmatch(d.name) and d.is_dir()
                    and time.time() - (d / "meta.json").stat().st_mtime > UPLOAD_KEEP_SEC):
                shutil.rmtree(d)
        except OSError:
            continue


def _require_open_group(group: str) -> None:
    from reel.web.group_status import completed_groups
    database = db()
    try:
        if group in completed_groups(database):
            raise HTTPException(409, "Խումբն ավարտված է։ Նոր դաս վերբեռնելու համար դիմեք ադմինին։")
    finally:
        database.conn.close()


@app.post("/api/uploads")
async def upload_start(request: Request, who: str = Depends(uploader)):
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict):
        raise HTTPException(400, "bad request")
    try:
        from reel.config import load_config
        groups = load_config(str(ROOT / "config.yaml"))["groups"]
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "config.yaml is not readable") from None
    group = body.get("group")
    account_auth.require_group(group)
    if not isinstance(group, str) or group not in groups:
        raise HTTPException(400, "unknown group")
    _require_open_group(group)
    subjects = {s["name"].casefold(): s["name"] for s in groups[group]["subjects"]}
    subject = subjects.get(str(body.get("subject") or "").casefold())
    if subject is None and len(subjects) == 1 and not body.get("subject"):
        subject = next(iter(subjects.values()))          # the group's only subject
    if subject is None:
        raise HTTPException(400, "choose the subject")
    changed = body.get("lesson_date_changed", False)
    if type(changed) is not bool:
        raise HTTPException(400, "Ամսաթվի ընտրության նշումը պետք է լինի true կամ false։")
    try:
        day = lesson_dates.validate(body.get("lesson_date", lesson_dates.today().isoformat()))
        if changed and "lesson_date" not in body:
            raise ValueError("Նշեք ընտրված դասի ամսաթիվը։")
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    number = body.get("lesson_number")
    if number is not None and (type(number) is not int or not 1 <= number <= 999):
        raise HTTPException(400, "lesson number must be between 1 and 999")
    lesson = f"Դաս {number}" if number is not None else (_clean_name(body.get("lesson") or "") or datetime.now().strftime("das-%Y-%m-%d-%H%M"))
    files = body.get("files")
    if not isinstance(files, list) or not 1 <= len(files) <= UPLOAD_MAX_FILES:
        raise HTTPException(400, f"choose 1-{UPLOAD_MAX_FILES} video files")
    clean = []
    for f in files:
        name = _clean_name(f.get("name", ""), 120) if isinstance(f, dict) else ""
        size = f.get("size") if isinstance(f, dict) else None
        if (not name or Path(name).suffix.lower() not in pipeline.VIDEO_EXT
                or type(size) is not int or not 0 < size <= UPLOAD_MAX_BYTES):
            raise HTTPException(400, f"not a supported video file: {name or '?'}")
        # Names and sizes only: iPhone Photos re-stamps a re-selected video, so a file time in
        # the id would restart a reloaded page's transfer from zero instead of resuming it.
        clean.append({"name": name, "size": size})
    if len({c["name"].casefold() for c in clean}) != len(clean):
        raise HTTPException(400, "two files have the same name")
    # G-40/4: "ordered" = the list order is the lesson order (the page sorts by recording time and lets
    # the user reorder). Disk names then carry a "01 " prefix when natural name order would differ.
    ordered = body.get("ordered") is True
    disk = _recording.ordered_disk_names([c["name"] for c in clean]) if ordered else [c["name"] for c in clean]
    total = sum(c["size"] for c in clean)
    _prune_uploads()
    identity = [group, subject, lesson, clean]
    if changed:
        identity.append(day)
    if ordered:
        identity.append("ordered")
    uid = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:20]
    async with _CHUNK_LOCKS.setdefault(uid, asyncio.Lock()):
        if receipt := _upload_receipt(uid):
            return {"id": uid, "lesson": lesson, "subject": subject, "chunk": UPLOAD_CHUNK,
                    "received": [f["size"] for f in clean], "completed": True, "result": receipt}
        if shutil.disk_usage(ROOT).free < 3 * total + 5 * 1024 ** 3:  # archive + work copies
            raise HTTPException(507, "not enough free disk space on the server")
        d = _uploads() / uid
        if d.is_symlink() or (d / "parts").is_symlink():
            raise HTTPException(409, "invalid upload directory")
        (d / "parts").mkdir(parents=True, exist_ok=True)
        previous = _read_json(d / "meta.json") or {}
        _atomic_json(d / "meta.json", {"group": group, "subject": subject, "lesson": lesson,
                                     "lesson_date": previous.get("lesson_date", day),
                                     "lesson_date_changed": previous.get("lesson_date_changed", changed),
                                     "files": [{"name": n, "size": c["size"]} for n, c in zip(disk, clean)],
                                     "by": who})
        have = [(d / "parts" / n) for n in disk]
        return {"id": uid, "lesson": lesson, "subject": subject, "chunk": UPLOAD_CHUNK,
                "received": [p.stat().st_size if p.is_file() else 0 for p in have],
                "order": [c["name"] for c in (clean if ordered else
                                              sorted(clean, key=lambda c: pipeline.natural_key(c["name"])))]}


@app.get("/api/uploads/{uid}")
async def upload_status(uid: str, _: str = Depends(uploader)):
    async with _CHUNK_LOCKS.setdefault(uid, asyncio.Lock()):
        if receipt := _upload_receipt(uid):
            return {"id": uid, "completed": True, "result": receipt}
        meta = _upload_meta(uid)
        paths = [_uploads() / uid / "parts" / f["name"] for f in meta["files"]]
        return {"id": uid, "received": [p.stat().st_size if p.is_file() else 0 for p in paths],
                "chunk": UPLOAD_CHUNK, "completed": False}



@app.put("/api/uploads/{uid}/{index}")
async def upload_chunk(uid: str, index: int, offset: int, request: Request, _: str = Depends(uploader)):
    async with _CHUNK_LOCKS.setdefault(uid, asyncio.Lock()):
        meta = _upload_meta(uid)
        if not 0 <= index < len(meta["files"]):
            raise HTTPException(404, "unknown file")
        f = meta["files"][index]
        if f.get("deleted"):                       # G-40/4: a recording part deleted before confirm
            raise HTTPException(410, "part deleted")
        path = _uploads() / uid / "parts" / f["name"]
        if path.is_symlink():
            raise HTTPException(409, "invalid upload file")
        have = path.stat().st_size if path.is_file() else 0
        if offset != have:
            return JSONResponse({"received": have}, status_code=409)
        n = 0
        try:
            with path.open("ab") as out:
                async for piece in request.stream():
                    if n + len(piece) > UPLOAD_CHUNK or have + n + len(piece) > f["size"]:
                        raise HTTPException(413, "chunk too large")
                    out.write(piece)
                    n += len(piece)
        except HTTPException:
            os.truncate(path, have)
            raise
        (path.parent.parent / "meta.json").touch()
        return {"received": have + n}


@app.post("/api/uploads/{uid}/finish")
async def upload_finish(uid: str, who: str = Depends(uploader)):
    async with _CHUNK_LOCKS.setdefault(uid, asyncio.Lock()):
        if receipt := _upload_receipt(uid):
            return receipt
        return await run_in_threadpool(_finish_upload, uid, who)


def _finish_upload(uid: str, who: str):
    meta = _upload_meta(uid)
    d = _uploads() / uid
    for f in meta["files"]:
        p = d / "parts" / f["name"]
        if not p.is_file() or p.stat().st_size != f["size"]:
            raise HTTPException(409, f"not fully uploaded yet: {f['name']}")
    parts = pipeline.parts_of(d / "parts")
    if len(parts) != len(meta["files"]):
        raise HTTPException(409, "unexpected files in the upload")
    try:   # Settings may have changed during a long upload (G-21 review)
        from reel.config import load_config
        groups = load_config(str(ROOT / "config.yaml"))["groups"]
    except Exception:  # noqa: BLE001
        raise HTTPException(503, "config.yaml is not readable") from None
    if meta["group"] not in groups:
        raise HTTPException(409, "group removed during upload")
    # A subject removed meanwhile is NOT refused here (that would cost a re-upload): the
    # job stops before YouTube and the admin chooses the subject on the job page.
    media = _upload_video_info(parts)
    day = meta.get("lesson_date") or lesson_dates.today().isoformat()
    if not meta.get("lesson_date_changed") and media.get("recorded_date"):
        day = media["recorded_date"]
    try:
        day = lesson_dates.validate(day)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from None
    database = db()
    try:
        job_id, dup = pipeline.enqueue(database, ROOT, d / "parts", parts, meta["group"],
                                       notify=lambda _t: None, name=meta["lesson"], subject=meta.get("subject"), lesson_date=day)
    finally:
        database.conn.close()
    if dup:
        database = db()
        try: account_auth.require_group(database.job(dup)["grp"])
        finally: database.conn.close()
    receipt = {"duplicate_of": dup} if dup else {"job_id": job_id}
    if job_id:
        _atomic_json(WORK / str(job_id) / "upload_media.json", media)
    _atomic_json(_uploads() / "receipts" / (uid + ".json"), receipt)
    shutil.rmtree(d, ignore_errors=True)
    if dup:
        return receipt
    gb = sum(f["size"] for f in meta["files"]) / 1e9
    how = "Հեռախոսով ձայնագրվեց" if meta.get("kind") == "recording" else "Վեբով վերբեռնվեց"
    pipeline._notify(f"📥 {how} «{meta['lesson']}» ({len(parts)} ֆայլ, {gb:.1f} ԳԲ, {who}) "
                     f"→ job {job_id}, խումբ {meta['group']}, առարկա {meta.get('subject')}")
    return {"job_id": job_id}


@app.delete("/api/uploads/{uid}")
async def upload_cancel(uid: str, _: str = Depends(uploader)):
    async with _CHUNK_LOCKS.setdefault(uid, asyncio.Lock()):
        if _upload_receipt(uid):
            raise HTTPException(409, "upload already completed")
        _upload_meta(uid)
        shutil.rmtree(_uploads() / uid, ignore_errors=True)
        return {"ok": True}


from reel.web import admin as _admin_api   # noqa: E402 - /api/admin/* settings API
app.include_router(_admin_api.router)
from reel.web import lecturer_api as _lecturer_api   # noqa: E402 - job-page lecturer panel API
app.include_router(_lecturer_api.router)
from reel.web import thumbnail_styles as _thumbnail_styles_api
app.include_router(_thumbnail_styles_api.router)

# Academy data is read-only and retains operational role/group boundaries.
from reel.web.system_settings import make_router as _system_settings_router
app.include_router(_system_settings_router(globals()))
from reel.web.academy_api import make_router as _academy_router
app.include_router(_academy_router(globals(), sections=('overview','lessons','stats')))
from reel.web.insights import make_router as _insights_router
app.include_router(_insights_router(globals()))
from reel.web.student_mail_api import make_router as _student_mail_router
app.include_router(_student_mail_router(globals()))
from reel.web.watch_statistics_api import make_router as _watch_statistics_router
app.include_router(_watch_statistics_router(globals()))
from reel.web.learning_api import make_router as _learning_router   # G-40/3: library, player, stats
app.include_router(_learning_router(globals()))

from reel.web.account_api import make_router as _account_router
app.include_router(_account_router(globals()))

from reel.web.group_status import make_router as _group_status_router
app.include_router(_group_status_router(globals()))
def notify_group_expiry(channel: str, message: str):
    # Called only by the explicit expiry notifier after a durable deduplication claim.
    from reel.publish.slack import _send
    return _send(channel, message)


from reel.web.group_management import make_router as _group_management_router
app.include_router(_group_management_router(globals()))

from reel.web.demo_api import make_router as _demo_router
app.include_router(_demo_router(globals()))

from reel.web.audit_api import make_router as _audit_router, install as _install_audit
app.include_router(_audit_router(globals()))
_install_audit(app, globals())
from reel.web import recording as _recording   # noqa: E402 - G-40/4: phone recording + upload order
app.include_router(_recording.make_router(globals()))
from reel.web.two_factor import login as _admin_login, make_router as _two_factor_router
app.include_router(_two_factor_router(globals()))
from reel.web.privacy import make_router as _privacy_router
app.include_router(_privacy_router(globals()))
from reel.web.courses import make_router as _courses_router
app.include_router(_courses_router(globals()))
from reel.web.access import install as _install_account_access
_install_account_access(app, globals())

from reel.student_mail import install_scheduler as _install_student_mail
_install_student_mail(app, globals())

from reel.notifications import install as _install_monitor
_install_monitor(app, globals())

app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
