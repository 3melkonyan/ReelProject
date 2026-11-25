"""Armenian captions on the edited timeline, YouTube caption track, search inside lessons."""
from contextlib import closing
import json
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from reel import captions, learning
from reel.db import DB
from reel.pipeline import Ctx
from reel.publish.errors import PublishError
from reel.web import accounts as a
from test_web_upload import web  # noqa: F401 - pytest fixture

KEEP = [[20, 500], [600, 990]]
SEGMENTS = [
    {"start": 1, "end": 10, "text": "նախքան դասը զրույց"},                    # inside the cut before class
    {"start": 30, "end": 40, "text": "Այսօր կխոսենք closure-ի մասին"},
    {"start": 41, "end": 44, "text": "Այսօր կխոսենք closure-ի մասին"},         # Whisper repetition loop
    {"start": 60, "end": 62, "text": "Շնորհակալություն դիտելու համար"},        # silence hallucination
    {"start": 495, "end": 505, "text": "ընդմիջումից առաջ վերջին միտքը"},       # half kept -> kept
    {"start": 590, "end": 602, "text": "ընդմիջման աղմուկ"},                    # mostly cut -> dropped
    {"start": 610, "end": 620, "text": "Ինչո՞ւ է ֆունկցիան հիշում իր միջավայրը"},
]


def test_final_cues_follow_the_cut_timeline():
    cues = captions.final_cues(SEGMENTS, KEEP)
    assert [(c["start"], c["end"]) for c in cues] == [(10, 20), (475, 480), (490, 500)]
    assert cues[0]["text"] == "Այսօր կխոսենք closure-ի մասին"
    assert captions.final_cues([{"start": 5, "end": 9, "text": "ok"}]) == [{"start": 5, "end": 9, "text": "ok"}]


def test_split_wrap_and_formats():
    long = {"start": 100, "end": 120, "text": " ".join(["բառ%02d" % i for i in range(40)])}
    parts = captions.split_cues([long])
    assert len(parts) >= 3 and all(len(p["text"]) <= captions.MAX_CHARS for p in parts)
    assert parts[0]["start"] == 100 and parts[-1]["end"] == 120
    assert all(x["end"] == y["start"] for x, y in zip(parts, parts[1:]))
    assert " ".join(p["text"] for p in parts) == long["text"]
    srt = captions.to_srt([{"start": 3661.5, "end": 3663.25, "text": "a <b> --> c"}])
    assert srt == "1\n01:01:01,500 --> 01:01:03,250\na ‹b› → c\n"
    vtt = captions.to_vtt([{"start": 0, "end": 1.2, "text": "x < y & z"}])
    assert vtt == "WEBVTT\n\n00:00:00.000 --> 00:00:01.200\nx &lt; y &amp; z\n"
    assert max(map(len, captions.wrap("ա" * 30 + " " + "բ" * 30))) <= 42
    assert captions.norm("Ինչո՞ւ  ԵՎ և") == "ինչու եվ եւ"


def _job(db, name="g40", group="Group-B", subject="Python"):
    job = db.create_job(f"/fixture/{name}.mp4", name, group, subject)
    for stage, output in {"segments": {"keep": KEEP, "final_sec": 870}, "privacy": {},
                          "metadata": {"title": "Closure", "chapters": []}, "youtube_upload": {"lesson_no": 3}}.items():
        db.stage_start(job, stage)
        db.stage_done(job, stage, output)
    return job


def _transcript(root, job):
    folder = root / "work" / str(job) / "transcript"
    folder.mkdir(parents=True)
    (folder / "transcript.json").write_text(json.dumps({"segments": SEGMENTS}, ensure_ascii=False), encoding="utf-8")


def test_search_speech_and_ocr_scoped(tmp_path):
    db = DB(tmp_path / "reel.db")
    one, two = _job(db, "one"), _job(db, "two")
    _transcript(tmp_path, one)
    learning.ensure_cues(db, [one, two], tmp_path / "work")
    assert len(learning.lesson_cues(db, one)) == 3 and learning.lesson_cues(db, two) == []
    # schema: final-video times, source board|projector
    db.conn.execute("CREATE TABLE ocr_segments (job_id INTEGER NOT NULL, t_start REAL NOT NULL, t_end REAL NOT NULL,"
                    " source TEXT NOT NULL, text TEXT NOT NULL)")
    db.conn.execute("INSERT INTO ocr_segments VALUES (?,?,?,?,?)", (two, 42.0, 50.0, "projector", "Closure և scope"))
    found = learning.search(db, "ԻՆՉՈՒ ֆունկցիան", [one, two])
    assert found["results"] == [{"job_id": one, "total": 1, "hits": [
        {"t": 490.0, "end": 500.0, "text": "Ինչո՞ւ է ֆունկցիան հիշում իր միջավայրը", "source": "speech"}]}]
    both = learning.search(db, "closure", [one, two])
    assert {(r["job_id"], r["hits"][0]["source"]) for r in both["results"]} == {(one, "speech"), (two, "slide")}
    assert learning.search(db, "closure", [two])["results"][0]["job_id"] == two          # scope is honoured
    assert learning.search(db, "%", [one])["results"] == [] and learning.search(db, "x", [one])["terms"] == []
    db.conn.close()


class FakeYouTube:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    def find_captions(self, video, language="hy"):
        return None

    def upload_captions(self, video, path, language="hy"):
        self.calls.append((video, open(path, encoding="utf-8").read(), language))
        if self.fail:
            raise self.fail
        return "caption-1"


def test_captions_stage_writes_files_uploads_once_and_never_stops(tmp_path, monkeypatch):
    from reel import stages
    from reel.stages import learning_stages
    db = DB(tmp_path / "reel.db")
    job = _job(db)
    _transcript(tmp_path, job)
    db.add_publication(job, "youtube", "abcdefghijk", "https://youtu.be/abcdefghijk")
    db.stage_start(job, "youtube_upload")
    db.stage_done(job, "youtube_upload", {"external_id": "abcdefghijk", "lesson_no": 3})
    fake = FakeYouTube()
    monkeypatch.setattr(stages, "_pub", lambda name: fake)
    monkeypatch.setattr(learning_stages, "CAPTION_RETRY_WAIT", (0, 0, 0))
    ctx = Ctx(db, job, tmp_path / "src.mp4", tmp_path / "work" / str(job), "Group-B", {})
    out = learning_stages.captions_stage(ctx)
    assert out["uploaded"] is True and out["caption_id"] == "caption-1" and out["cues"] == 3
    assert fake.calls[0][0] == "abcdefghijk" and fake.calls[0][2] == "hy"
    assert fake.calls[0][1].startswith("1\n00:00:10,000 --> 00:00:20,000\nԱյսօր կխոսենք closure-ի մասին")
    assert (ctx.workdir / "captions" / "captions.hy.vtt").read_text(encoding="utf-8").startswith("WEBVTT")
    assert learning_stages.captions_stage(ctx)["reused"] is True and len(fake.calls) == 1   # idempotent
    db.conn.execute("DELETE FROM publications WHERE target='captions'")
    broken = FakeYouTube(PublishError("upload_captions: Google API HTTP 403", False))
    monkeypatch.setattr(stages, "_pub", lambda name: broken)
    failed = learning_stages.captions_stage(ctx)
    assert failed["uploaded"] is False and "published without them" in failed["auto_note"] and len(broken.calls) == 1
    db.conn.close()


def test_captions_stage_is_in_the_pipeline_after_the_thumbnail():
    from reel.stages import STAGES
    from reel.web.labels import stage_labels
    STAGE_LABEL = stage_labels()
    names = [name for name, _ in STAGES]
    assert names.index("captions") == names.index("thumbnail_set") + 1 and STAGE_LABEL["captions"]


def test_youtube_caption_adapter(monkeypatch, tmp_path):
    from reel.publish import youtube
    seen = {}

    class Request:
        def __init__(self, result): self.result = result
        def execute(self, num_retries=0): return self.result

    class Captions:
        def insert(self, part, body, media_body):
            seen.update(part=part, body=body, mime=media_body.mimetype())
            return Request({"id": "cap-9"})

        def list(self, part, videoId):
            return Request({"items": [
                {"id": "auto", "snippet": {"language": "hy", "trackKind": "asr", "name": youtube.CAPTION_NAME}},
                {"id": "other", "snippet": {"language": "en", "trackKind": "standard", "name": youtube.CAPTION_NAME}},
                {"id": "ours", "snippet": {"language": "hy", "trackKind": "standard", "name": youtube.CAPTION_NAME}}]})

    monkeypatch.setattr(youtube, "_service", lambda: SimpleNamespace(captions=Captions))
    file = tmp_path / "c.srt"
    file.write_text("1\n00:00:00,000 --> 00:00:01,000\nբարև\n", encoding="utf-8")
    assert youtube.upload_captions("abcdefghijk", str(file)) == "cap-9"
    assert seen["body"]["snippet"] == {"videoId": "abcdefghijk", "language": "hy", "name": "Հայերեն", "isDraft": False}
    assert youtube.find_captions("abcdefghijk") == "ours"
    with pytest.raises(PublishError):
        youtube.upload_captions("abcdefghijk", str(tmp_path / "missing.srt"))


def test_http_search_is_scoped_and_links_to_the_minute(web):
    d = DB(web.root / "reel.db")
    with closing(d.conn):
        job = _job(d, "http", group="Group-B")
        d.add_publication(job, "youtube", "abcdefghijk", "https://youtu.be/abcdefghijk")
        d.set_job(job, status="done")
        a.create_user(d, "g40-other", "g40-search-password", {"name": "Այլ ուսանող", "email": "other@example.test",
                                                              "role": "student", "groups": ["Group-A"]})
    _transcript(web.root, job)
    admin = web.client("admin")
    code, data = admin.call("GET", "/api/library/search?q=" + quote("միջավայրը"))
    assert code == 200 and data["results"][0]["job_id"] == job
    assert data["results"][0]["hits"][0]["url"] == "https://youtu.be/abcdefghijk?t=490"
    assert data["results"][0]["subject"] == "Python"
    other = web.client()
    assert other.call("POST", "/api/login", body={"login": "g40-other", "password": "g40-search-password"})[0] == 200
    assert other.call("GET", "/api/library/search?q=" + quote("միջավայրը"))[1]["results"] == []
    assert admin.call("GET", "/api/library/search?q=" + "x" * 201)[0] == 400
    assert web.client("uploader").call("GET", "/api/library/search?q=closure")[0] == 403


def test_ui_highlight_escapes_html_and_filters_by_selected_group():
    from test_brand import run_node
    run_node(r'''
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const app=fs.readFileSync(process.argv[1],'utf8'),source=fs.readFileSync(process.argv[1].replace('app.js','learning.js'),'utf8');
const box={innerHTML:'',isConnected:true},els={'#library-text-results':box,'#library-group':{value:'Group-B'},'#library-subject':{value:''}};
const c={S:{},$:s=>els[s],setTimeout,clearTimeout,clock:s=>String(s)};vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);
vm.runInContext(app.slice(app.indexOf('const esc ='),app.indexOf('\n',app.indexOf('const esc ='))),c);vm.runInContext(source,c);
assert.equal(c.markTerms('<b>closure</b> Closure',['closure']),'&lt;b&gt;<mark>closure</mark>&lt;/b&gt; <mark>Closure</mark>');
c.S.library=[{job_id:1,group:'Group-A',subject:'Python',title:'A',url:'https://youtu.be/aaaaaaaaaaa'},
 {job_id:2,group:'Group-B',subject:'Python',title:'B',url:'https://youtu.be/bbbbbbbbbbb'}];
c.S.libraryText={query:'closure',terms:['closure'],results:[{job_id:1,total:1,hits:[{t:5,text:'closure',source:'speech'}]},
 {job_id:2,total:3,hits:[{t:61.9,text:'Closure <img>',source:'slide'}]}]};
c.renderLibraryText();
assert(box.innerHTML.includes('https://youtu.be/bbbbbbbbbbb?t=61'));assert(!box.innerHTML.includes('aaaaaaaaaaa'));
assert(box.innerHTML.includes('Սլայդ')&&box.innerHTML.includes('&lt;img&gt;')&&box.innerHTML.includes('Եվս 2 համընկնում'));
''')
