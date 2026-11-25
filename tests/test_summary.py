"""AI summary, key terms and self-check questions; the lesson works without them."""

import json
from contextlib import closing

import pytest

from reel import learning, lesson_summary, llm
from reel.db import DB
from reel.pipeline import Ctx
from reel.web import accounts as a

GOOD = {
    "summary": [
        "1. Դասում քննարկվեց closure-ը և դրա կիրառությունները։",
        "- Ֆունկցիան հիշում է իր ստեղծման միջավայրը։",
        "**Lexical scope**-ը որոշվում է կոդը գրելու պահին։",
        "Ցուցադրվեց հաշվիչի օրինակ closure-ով։",
        "Քննարկվեց հիշողության արտահոսքի վտանգը։",
        "Ֆունկցիան հիշում է իր ստեղծման միջավայրը։",
    ],
    "terms": [
        {"term": "closure", "meaning": "Ֆունկցիա, որը հիշում է արտաքին փոփոխականները"},
        {"term": "lexical scope", "meaning": "Տեսանելիության տիրույթ՝ ըստ կոդի կառուցվածքի"},
        {"term": "garbage collector", "meaning": "Հիշողության ավտոմատ մաքրիչ"},
        {"term": "English only", "meaning": "no Armenian here"},
    ],
    "questions": [
        "Ի՞նչ է closure-ը։",
        "Ինչո՞ւ է հաշվիչը պահում իր արժեքը։",
        "Ե՞րբ է որոշվում lexical scope-ը։",
    ],
}


def test_validate_cleans_markdown_bullets_duplicates_and_non_armenian():
    notes = lesson_summary.validate(GOOD)
    assert len(notes["summary"]) == 5 and notes["summary"][0].startswith("Դասում")
    assert (
        notes["summary"][1] == "function-ը հիշում է իր ստեղծման միջավայրը։"
    )  # metadata term spelling
    assert notes["summary"][2].startswith("Lexical scope-ը")
    assert [t["term"] for t in notes["terms"]] == ["closure", "lexical scope", "garbage collector"]
    assert len(notes["questions"]) == 3
    with pytest.raises(ValueError, match="summary"):
        lesson_summary.validate(
            {**GOOD, "summary": ["Only English sentences here, nothing else."] * 6}
        )
    with pytest.raises(ValueError, match="questions"):
        lesson_summary.validate({**GOOD, "questions": ["?", "Ի՞նչ է closure-ը։"]})
    with pytest.raises(ValueError):
        lesson_summary.validate(["not", "an", "object"])


def test_a_paragraph_in_one_item_becomes_separate_sentences():
    paragraph = " ".join(f"Դասի {n}-րդ միտքը closure-ի մասին է։" for n in range(1, 8))
    notes = lesson_summary.validate(
        {**GOOD, "summary": [paragraph, "Վերջին առանձին միտքը closure-ի մասին։"]}
    )
    assert (
        notes["summary"][0] == "Դասի 1-րդ միտքը closure-ի մասին է։" and len(notes["summary"]) == 8
    )


def test_terms_must_appear_in_the_lesson_material():
    answer = {
        **GOOD,
        "terms": [
            {"term": "closure", "meaning": "closure - արտաքին փոփոխականները հիշող կառուցվածք"},
            {"term": "event loop", "meaning": "Հերթի մեխանիզմ"},  # not in this lesson
            {"term": "lexical scope", "meaning": "Տեսանելիության տիրույթ"},
            {"term": "makeCounter", "meaning": "Հաշվիչ ստեղծող function"},
        ],
    }
    material = "closures and lexical scope; a makecounter example"
    notes = lesson_summary.validate(answer, material)
    assert [t["term"] for t in notes["terms"]] == ["closure", "lexical scope", "makeCounter"]
    assert (
        notes["terms"][0]["meaning"] == "Արտաքին փոփոխականները հիշող կառուցվածք"
    )  # no "closure - " echo
    assert notes["terms"][2]["meaning"] == "Հաշվիչ ստեղծող function"
    with pytest.raises(ValueError, match="appear in the material"):
        lesson_summary.validate(answer, "closures only")


def test_generate_retries_once_with_feedback_then_gives_up():
    calls = []

    def chat(system, user, **kwargs):
        calls.append((system, user, kwargs))
        return {"summary": [], "terms": [], "questions": []} if len(calls) == 1 else GOOD

    meta = {"title": "Closure", "chapters": [{"title": "Lexical scope"}]}
    parts = [
        {"topic": "closures", "summary": "Closures capture variables."},
        {
            "topic": "scope",
            "summary": "Lexical scope is static; the garbage collector frees memory.",
        },
    ]
    assert lesson_summary.generate(meta, parts, "տեքստ", chat=chat)["terms"][0]["term"] == "closure"
    assert "rejected" in calls[1][0] and calls[0][2]["schema"] == lesson_summary.SCHEMA
    material = json.loads(calls[0][1])
    assert material["internal_summaries"][1]["summary"].startswith("Lexical scope is static")
    assert material["transcript_excerpt"] == ""  # English parts are enough
    with pytest.raises(ValueError):
        lesson_summary.generate(meta, [], "տեքստ", chat=lambda *x, **k: {"summary": ["x"]})
    assert json.loads(lesson_summary.material(meta, [], "տեքստ"))["transcript_excerpt"] == "տեքստ"


def test_excerpt_fits_the_context_and_covers_the_lesson():
    cues = [{"text": f"նախադասություն {i:04d} " + "բառ " * 20} for i in range(2000)]
    text = lesson_summary.excerpt(cues)
    numbers = [int(line.split()[1]) for line in text.splitlines()]
    assert (
        len(text) <= lesson_summary.EXCERPT_CHARS and numbers[0] == 0 and numbers[-1] > 1500
    )  # start..end
    assert lesson_summary.excerpt(cues[:3]) == "\n".join(c["text"] for c in cues[:3])


def _job(db, group="Group-B"):
    job = db.create_job("/fixture/summary.mp4", "summary-" + group, group, "Python")
    for stage, output in {
        "segments": {"keep": [[0, 100]], "final_sec": 100},
        "privacy": {},
        "metadata": {"title": "Closure", "chapters": [{"title": "Lexical scope", "t": 0}]},
    }.items():
        db.stage_start(job, stage)
        db.stage_done(job, stage, output)
    return job


def test_summary_stage_success_and_model_unavailable(tmp_path, monkeypatch):
    from reel.stages import learning_stages

    db = DB(tmp_path / "reel.db")
    job = _job(db)
    work = tmp_path / "work" / str(job)
    work.mkdir(parents=True)
    (work / "metadata.json").write_text(
        json.dumps(
            {
                "parts": [
                    {"topic": "x", "summary": "Closures and lexical scope."},
                    {"topic": "y", "summary": "The garbage collector."},
                ]
            }
        )
    )
    ctx = Ctx(db, job, tmp_path / "src.mp4", work, "Group-B", {})
    monkeypatch.setattr(llm, "chat_json", lambda *args, **kwargs: GOOD)
    out = learning_stages.summary_stage(ctx)
    assert len(out["summary"]) == 5 and out["model"] and out["generated_at"]

    def offline(*args, **kwargs):
        raise llm.LLMError("ollama request failed: connection refused")

    monkeypatch.setattr(llm, "chat_json", offline)
    skipped = learning_stages.summary_stage(ctx)
    assert skipped["skipped"].startswith("LLMError") and "without it" in skipped["auto_note"]
    assert learning.public_summary(skipped) is None and learning.public_summary(None) is None
    # Project rule (metadata): programming terms stay English, so «Ֆունկցիա» becomes «function».
    assert learning.public_summary(out)["terms"][0] == {
        "term": "closure",
        "meaning": "function, որը հիշում է արտաքին փոփոխականները",
    }
    db.conn.close()


def test_summary_stage_runs_right_after_metadata():
    from reel.stages import STAGES
    from reel.web.labels import stage_labels

    STAGE_LABEL = stage_labels()
    names = [name for name, _ in STAGES]
    assert names.index("summary") == names.index("metadata") + 1 and STAGE_LABEL["summary"]


def test_http_lesson_notes_are_scoped(web):
    d = DB(web.root / "reel.db")
    with closing(d.conn):
        job = _job(d)
        d.stage_start(job, "summary")
        d.stage_done(
            job,
            "summary",
            {**lesson_summary.validate(GOOD), "generated_at": "2026-11-20T10:00:00+00:00"},
        )
        d.add_publication(job, "youtube", "abcdefghijk", "https://youtu.be/abcdefghijk")
        hidden = _job(d, "Group-A")
        d.add_publication(hidden, "youtube", "bcdefghijkl", "https://youtu.be/bcdefghijkl")
        a.create_user(
            d,
            "g40-notes",
            "g40-notes-password",
            {
                "name": "Ուսանող",
                "email": "notes@example.test",
                "role": "student",
                "groups": ["Group-B"],
            },
        )
    student = web.client()
    assert (
        student.call(
            "POST", "/api/login", body={"login": "g40-notes", "password": "g40-notes-password"}
        )[0]
        == 200
    )
    code, data = student.call("GET", f"/api/library/{job}")
    assert (
        code == 200
        and data["summary"]["questions"][0] == "Ի՞նչ է closure-ը։"
        and data["subject"] == "Python"
    )
    assert student.call("GET", f"/api/library/{hidden}")[0] == 404
    assert web.client("admin").call("GET", f"/api/library/{hidden}")[1]["summary"] is None
    assert web.client("uploader").call("GET", f"/api/library/{job}")[0] == 403


def test_ui_notes_escape_and_label_ai():
    from test_brand import run_node

    run_node(r"""
const fs=require('fs'),vm=require('vm'),assert=require('assert/strict');
const app=fs.readFileSync(process.argv[1],'utf8'),source=fs.readFileSync(process.argv[1].replace('app.js','learning.js'),'utf8');
const c={S:{},ic:()=>''};vm.createContext(c);require(require('path').resolve('tests/browser_locale.cjs')).install(c);
vm.runInContext(app.slice(app.indexOf('const esc ='),app.indexOf('const STATUS =')),c);vm.runInContext(source,c);
const html=c.notesHtml({summary:['<script>x</script>'],terms:[{term:'closure',meaning:'բացատրություն'}],questions:['Ի՞նչ է closure-ը։']});
assert(html.includes('&lt;script&gt;')&&!html.includes('<script>'));assert(html.includes('AI-ով')&&html.includes('Ստուգեք ինքներդ'));
assert(c.notesHtml(null).includes('դեռ չկա'));
""")
