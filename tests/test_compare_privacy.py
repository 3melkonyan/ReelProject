"""Full-timeline acceptance uses synthetic numerical data; no real media or DB."""

import json
import sqlite3
from copy import deepcopy

import pytest

from tools import compare_privacy as tool


def decisions(boxes=None):
    return {
        "fps": 25.0,
        "policy": "confident_students",
        "pixelated": boxes if boxes is not None else [[0, 10, 10, 34, 34]],
        "lecturer": [[0.0, 500, 100, 50, 60]],
        "unsure": [],
    }


def metrics(data):
    return {
        "blur": {
            "frames": 1000,
            "lecturer_mode": "auto",
            "lecturer_share": 0.8,
            "lecturer_boxes": 1000,
            "blurred_boxes": len(data["pixelated"]),
        },
        "privacy": {"analysis_fps": 5, "frames": 200, "warnings": 0, "other_faces": 0},
    }


def compare(reference=None, new=None, ref_metrics=None, new_metrics=None, privacy=None):
    reference = reference if reference is not None else decisions()
    new = new if new is not None else deepcopy(reference)
    return tool.compare(
        reference,
        new,
        ref_metrics if ref_metrics is not None else metrics(reference),
        new_metrics if new_metrics is not None else metrics(new),
        privacy,
    )


def test_identical_full_timeline_passes_and_reports_audit_limits():
    data = decisions([[0, 10, 10, 34, 34], [999, 100, 100, 130, 140]])
    result = compare(data)
    assert result["status"] == "PASS"
    assert result["bins"]["ge24"] == {"total": 2, "matched": 2, "missed": 0, "recall": 1}
    assert result["bins"]["16_to_lt24"]["recall"] is None
    assert result["privacy"]["independent_detection"] == "requires_code_audit"
    assert "including blur margins" in result["limitations"][0]


@pytest.mark.parametrize("missing_frame", [0, 499, 999])
def test_checks_every_reference_frame_including_start_middle_and_end(missing_frame):
    reference = decisions([[f, 0, 0, 24, 30] for f in range(1000)])
    new = deepcopy(reference)
    new["pixelated"] = [row for row in new["pixelated"] if row[0] != missing_frame]
    result = compare(reference, new)
    assert result["status"] == "FAIL"
    assert result["miss_count"] == 1
    assert result["misses"][0]["frame"] == missing_frame
    assert result["misses"][0]["t"] == missing_frame / 25
    assert result["misses"][0]["size_px"] == 24


def test_adjacent_frame_never_covers_reference():
    reference = decisions([[100, 0, 0, 24, 24]])
    new = decisions([[101, 0, 0, 24, 24]])
    result = compare(reference, new)
    assert result["status"] == "FAIL"
    assert result["miss_count"] == 1


@pytest.mark.parametrize("new_boxes", [[], [[0, 100, 100, 124, 124]]])
def test_empty_or_disjoint_new_pixelation_fails(new_boxes):
    result = compare(decisions(), decisions(new_boxes))
    assert result["status"] == "FAIL"
    assert result["bins"]["ge24"]["recall"] == 0


def test_empty_reference_cannot_vacuously_pass():
    result = compare(decisions([]))
    assert result["status"] == "FAIL"
    assert any("reference pixelated log is empty" in message for message in result["errors"])


def test_partial_overlap_fails_even_when_iou_is_high():
    reference = decisions([[0, 10, 10, 34, 34]])
    assert compare(reference, decisions([[0, 0, 0, 40, 40]]))["status"] == "PASS"
    assert compare(reference, decisions([[0, 11, 10, 34, 34]]))["status"] == "FAIL"


def test_reverse_containment_of_tiny_new_box_does_not_pass():
    reference = decisions([[0, 0, 0, 100, 100]])
    result = compare(reference, decisions([[0, 10, 10, 20, 20]]))
    assert result["status"] == "FAIL"
    assert result["bins"]["ge24"]["missed"] == 1


def test_size_bin_boundaries_use_shorter_rectangle_side():
    data = decisions(
        [[0, 0, 0, 15.999, 80], [1, 0, 0, 80, 16], [2, 0, 0, 23.999, 80], [3, 0, 0, 80, 24]]
    )
    result = compare(data)
    assert result["status"] == "PASS"
    assert {key: value["total"] for key, value in result["bins"].items()} == {
        "lt16": 1,
        "16_to_lt24": 2,
        "ge24": 1,
    }


@pytest.mark.parametrize(
    "small_count,misses,expected",
    [(200, 1, "FAIL"), (199, 1, "FAIL"), (400, 2, "FAIL"), (400, 3, "FAIL")],
)
def test_any_small_face_miss_fails(small_count, misses, expected):
    reference = decisions(
        [[999, 100, 100, 124, 124]] + [[f, 0, 0, 16, 20] for f in range(small_count)]
    )
    new = deepcopy(reference)
    new["pixelated"] = [reference["pixelated"][0]] + reference["pixelated"][1 + misses :]
    result = compare(reference, new)
    assert result["status"] == expected
    assert result["bins"]["16_to_lt24"]["missed"] == misses
    assert len(result["misses"]) == misses


def test_open_lecturer_is_reported_alongside_any_small_face_miss():
    reference = decisions([[999, 100, 100, 124, 124]] + [[f, 0, 0, 16, 20] for f in range(200)])
    new = deepcopy(reference)
    new["pixelated"] = reference["pixelated"][:1] + reference["pixelated"][2:]
    new["lecturer"].append([0.0, 0, 0, 16, 20])
    result = compare(reference, new)
    assert result["bins"]["16_to_lt24"]["recall"] == 0.995
    assert result["status"] == "FAIL"
    assert result["open_lecturer_count"] == 1


def test_covered_region_overlapping_lecturer_is_not_false_positive():
    reference = decisions()
    new = deepcopy(reference)
    new["lecturer"] = [[0.0, 10, 10, 24, 24]]
    result = compare(reference, new)
    assert result["status"] == "PASS"
    assert result["open_lecturer_count"] == 0


def test_open_lecturer_uses_policy_window_and_is_checked_even_below_16px():
    reference = decisions([[1, 10, 10, 34, 34], [0, 0, 0, 10, 10]])
    new = decisions([[1, 10, 10, 34, 34]])
    new["lecturer"] = [[0.32, 0, 0, 10, 10]]
    assert compare(reference, new)["status"] == "FAIL"
    new["lecturer"][0][0] = 0.0
    result = compare(reference, new)
    assert result["status"] == "FAIL"
    assert result["open_lecturers"][0]["size_px"] == 10


def test_new_open_unsure_between_sparse_samples_fails_even_with_995_percent_recall():
    reference = decisions([[999, 100, 100, 124, 124]] + [[f, 0, 0, 16, 20] for f in range(200)])
    new = deepcopy(reference)
    new["pixelated"] = reference["pixelated"][:1] + reference["pixelated"][2:]
    new["unsure"] = [[0.3, 0, 0, 16, 20]]
    result = compare(reference, new)
    assert result["bins"]["16_to_lt24"]["recall"] == 0.995
    assert result["status"] == "FAIL"
    assert result["open_face_count"] == 1
    assert result["open_faces"][0]["open_states"] == ["unsure"]
    assert result["open_lecturer_count"] == 0


def test_identity_coordinates_can_straddle_image_edge():
    data = decisions()
    data["lecturer"] = [[0, -5, 100, 50, 60]]
    data["unsure"] = [[0, 100, -5, 50, 60]]
    assert compare(data)["status"] == "PASS"


@pytest.mark.parametrize(
    "key,value",
    [
        ("lecturer_share", 0.76),
        ("lecturer_share", 0.84),
        ("lecturer_boxes", 950),
        ("lecturer_boxes", 1050),
    ],
)
def test_lecturer_five_percent_boundaries_pass(key, value):
    data = decisions()
    new_metrics = metrics(data)
    new_metrics["blur"][key] = value
    assert compare(data, new_metrics=new_metrics)["status"] == "PASS"


@pytest.mark.parametrize(
    "key,value",
    [
        ("lecturer_share", 0.759),
        ("lecturer_share", 0.841),
        ("lecturer_boxes", 949),
        ("lecturer_boxes", 1051),
        ("lecturer_mode", "none"),
        ("frames", 999),
    ],
)
def test_lecturer_drift_wrong_mode_or_truncated_output_fails(key, value):
    data = decisions()
    new_metrics = metrics(data)
    new_metrics["blur"][key] = value
    assert compare(data, new_metrics=new_metrics)["status"] == "FAIL"


def test_zero_reference_lecturer_boxes_cannot_allow_positive_new_count():
    data = decisions()
    before, after = metrics(data), metrics(data)
    before["blur"]["lecturer_boxes"] = 0
    after["blur"]["lecturer_boxes"] = 1
    assert compare(data, ref_metrics=before, new_metrics=after)["status"] == "FAIL"


@pytest.mark.parametrize(
    "key,value",
    [
        ("warnings", 1),
        ("other_faces", 1),
        ("analysis_fps", 0.5),
        ("frames", 198),
        ("frames", 199),
        ("frames", 1000),
        ("stopped", "visible face"),
    ],
)
def test_privacy_failures_are_independent_of_perfect_blur_recall(key, value):
    data = decisions()
    new_metrics = metrics(data)
    new_metrics["privacy"][key] = value
    result = compare(data, new_metrics=new_metrics)
    assert result["status"] == "FAIL"
    assert result["bins"]["ge24"]["recall"] == 1


def test_privacy_one_hz_full_duration_passes():
    data = decisions()
    new_metrics = metrics(data)
    new_metrics["privacy"].update(analysis_fps=1, frames=40)
    assert compare(data, new_metrics=new_metrics)["status"] == "PASS"


def test_clean_privacy_report_cannot_hide_dirty_stage_summary():
    data = decisions()
    new_metrics = metrics(data)
    report = deepcopy(new_metrics["privacy"])
    new_metrics["privacy"]["warnings"] = 1
    result = compare(data, new_metrics=new_metrics, privacy=report)
    assert result["status"] == "FAIL"
    assert any("disagrees" in message for message in result["errors"])


@pytest.mark.parametrize(
    "key,value",
    [
        ("fps", 0),
        ("fps", float("nan")),
        ("fps", float("inf")),
        ("fps", True),
        ("fps", "25"),
        ("policy", "unknown"),
        ("pixelated", None),
        ("lecturer", None),
        ("unsure", None),
    ],
)
def test_invalid_decision_metadata_fails_closed(key, value):
    new = decisions()
    new_metrics = metrics(new)
    new[key] = value
    with pytest.raises(ValueError):
        compare(new=new, new_metrics=new_metrics)


@pytest.mark.parametrize(
    "row",
    [
        [-1, 0, 0, 24, 24],
        [1000, 0, 0, 24, 24],
        [0.5, 0, 0, 24, 24],
        [True, 0, 0, 24, 24],
        [0, 0, 0, 0, 24],
        [0, -1, 0, 24, 24],
        [0, 24, 0, 0, 24],
        [0, 0, 0, float("inf"), 24],
        [0, 1],
    ],
)
def test_invalid_pixelated_rows_fail_closed(row):
    with pytest.raises(ValueError):
        compare(new=decisions([row]))


@pytest.mark.parametrize(
    "section,key,value",
    [
        ("blur", "lecturer_share", None),
        ("blur", "lecturer_share", 1.1),
        ("blur", "frames", 0),
        ("blur", "frames", 1000.5),
        ("blur", "blurred_boxes", 2),
        ("blur", "lecturer_boxes", -1),
        ("privacy", "warnings", False),
        ("privacy", "warnings", -1),
        ("privacy", "other_faces", None),
        ("privacy", "analysis_fps", None),
        ("privacy", "frames", None),
    ],
)
def test_invalid_or_missing_stage_metadata_fails_closed(section, key, value):
    data = decisions()
    new_metrics = metrics(data)
    new_metrics[section][key] = value
    with pytest.raises(ValueError):
        compare(data, new_metrics=new_metrics)


def test_changed_output_fps_fails_even_if_frame_rectangles_match():
    new = decisions()
    new["fps"] = 24
    new_metrics = metrics(new)
    new_metrics["privacy"]["frames"] = 208
    result = compare(new=new, new_metrics=new_metrics)
    assert result["status"] == "FAIL"
    assert any("fps changed" in message for message in result["errors"])


def test_missing_privacy_output_is_not_treated_as_zero():
    data = decisions()
    new_metrics = metrics(data)
    del new_metrics["privacy"]
    with pytest.raises(ValueError, match="new privacy"):
        compare(data, new_metrics=new_metrics)


def test_readonly_db_loader_uses_immutable_uri_and_bound_job_id(tmp_path, monkeypatch):
    data = metrics(decisions())
    queries = []

    class Connection:
        def execute(self, sql, args):
            queries.append((sql, args))
            return self

        def fetchall(self):
            return [(name, "done", json.dumps(value)) for name, value in data.items()]

        def close(self):
            queries.append("closed")

    def connect(database, **kwargs):
        assert database == (tmp_path / "synthetic.db").as_uri() + "?mode=ro&immutable=1"
        assert kwargs == {"uri": True}
        return Connection()

    monkeypatch.setattr(tool.sqlite3, "connect", connect)
    assert tool.read_stage_metrics(tmp_path / "synthetic.db", 7) == data
    assert queries[0][1] == (7,)
    assert queries[-1] == "closed"


def test_db_loader_creates_no_sidecars_or_missing_database(tmp_path):
    source = tmp_path / "synthetic.db"
    conn = sqlite3.connect(source)
    conn.execute("CREATE TABLE stages (job_id INTEGER,name TEXT,status TEXT,output TEXT)")
    conn.execute(
        "INSERT INTO stages VALUES (1,'blur','done',?)", (json.dumps(metrics(decisions())["blur"]),)
    )
    conn.commit()
    conn.close()
    before = source.read_bytes()
    assert "blur" in tool.read_stage_metrics(source)
    assert source.read_bytes() == before
    assert {p.name for p in tmp_path.iterdir()} == {"synthetic.db"}
    with pytest.raises(sqlite3.Error):
        tool.read_stage_metrics(tmp_path / "missing.db")
    assert not (tmp_path / "missing.db").exists()


def test_db_loader_refuses_nonempty_wal_instead_of_ignoring_pending_metrics(tmp_path):
    path = tmp_path / "synthetic.db"
    path.with_name(path.name + "-wal").write_bytes(b"not checkpointed")
    with pytest.raises(ValueError, match="nonempty SQLite WAL"):
        tool.read_stage_metrics(path)


def test_duplicate_json_metadata_keys_rejected(tmp_path):
    path = tmp_path / "synthetic.json"
    path.write_text('{"fps": 25, "fps": 1}')
    with pytest.raises(ValueError, match="Duplicate JSON key"):
        tool.read_json(path)


def test_cli_reports_full_misses_only_to_own_ignored_output(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    reference = decisions([[f, 0, 0, 24, 24] for f in range(50)])
    new = decisions([])
    paths = {}
    for name, value in {
        "reference": reference,
        "new": new,
        "reference_metrics": metrics(reference),
        "new_metrics": metrics(new),
    }.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(value))
    output = tmp_path / "out/performance/compare.json"
    args = [
        str(paths["new"]),
        "--reference",
        str(paths["reference"]),
        "--reference-metrics",
        str(paths["reference_metrics"]),
        "--new-metrics",
        str(paths["new_metrics"]),
        "--output",
        str(output),
    ]
    assert tool.main(args) == 1
    report = json.loads(output.read_text())
    assert len(report["misses"]) == 50
    assert report["misses"][-1]["t"] == 49 / 25
    display = json.loads(capsys.readouterr().out)
    assert display["status"] == "FAIL"
    assert display["miss_count"] == 50
    assert "misses" not in display
    outside = tmp_path / "elsewhere.json"
    with pytest.raises(ValueError, match="ignored out/performance"):
        tool.write_report(outside, report)
    assert not outside.exists()


def test_output_symlink_cannot_escape_own_out_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(tool, "ROOT", tmp_path)
    allowed = tmp_path / "out/performance"
    allowed.mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (allowed / "escape").symlink_to(elsewhere, target_is_directory=True)
    with pytest.raises(ValueError):
        tool.write_report(allowed / "escape/result.json", {"status": "PASS"})
    assert not list(elsewhere.iterdir())


def test_entire_out_directory_symlink_cannot_redirect_writes(tmp_path, monkeypatch):
    root = tmp_path / "tree"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (root / "out").symlink_to(elsewhere, target_is_directory=True)
    monkeypatch.setattr(tool, "ROOT", root)
    with pytest.raises(ValueError):
        tool.write_report(root / "out/performance/result.json", {"status": "PASS"})
    assert not list(elsewhere.iterdir())


def test_cli_success_accepts_separate_privacy_report_metadata(tmp_path, capsys):
    data = decisions()
    stage_metrics = metrics(data)
    privacy = stage_metrics["privacy"].copy()
    del stage_metrics["privacy"]["analysis_fps"]  # The actual DB summary omits it.
    paths = {}
    for name, value in {"decisions": data, "metrics": stage_metrics, "privacy": privacy}.items():
        paths[name] = tmp_path / f"{name}.json"
        paths[name].write_text(json.dumps(value))
    assert (
        tool.main(
            [
                str(paths["decisions"]),
                "--reference",
                str(paths["decisions"]),
                "--reference-metrics",
                str(paths["metrics"]),
                "--new-metrics",
                str(paths["metrics"]),
                "--privacy-output",
                str(paths["privacy"]),
            ]
        )
        == 0
    )
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "PASS"
    assert result["reference_pixelated"] == 1
    assert result["privacy"]["frames"] == 200


def test_cli_invalid_inputs_print_fail_with_nonzero_status(tmp_path, capsys):
    path = tmp_path / "synthetic.json"
    path.write_text("{}")
    assert (
        tool.main(
            [
                str(path),
                "--reference",
                str(path),
                "--reference-metrics",
                str(path),
                "--new-metrics",
                str(path),
            ]
        )
        == 1
    )
    assert json.loads(capsys.readouterr().out)["status"] == "FAIL"
