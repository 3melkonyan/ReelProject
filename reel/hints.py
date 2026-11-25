"""Armenian "what happened / what to do" hints for a stopped job (operator notifications + web)."""

from __future__ import annotations

from reel.i18n import t as _t


def _hints():
    return {
        "join": (_t("text.hints.recording_parts_could_not_be_joined_a")),
        "probe": _t("text.hints.the_file_cannot_be_read_or_is"),
        "audio": _t("text.hints.audio_extraction_failed_select_retry_if_it"),
        "transcribe": _t("text.hints.transcription_failed_select_retry_if_it_fails"),
        "segments": (_t("text.hints.the_cut_plan_removes_too_much_content")),
        "render": _t("text.hints.video_editing_or_audio_normalization_failed_select"),
        "blur": (_t("text.hints.face_blurring_stopped_nothing_was_published_an")),
        "privacy": (_t("text.hints.a_non_lecturer_face_is_still_visible")),
        "metadata": _t("text.hints.the_title_or_chapters_could_not_be"),
        "youtube_upload": _t("text.hints.youtube_authorization_quota_or_network_issue_check"),
        "playlist": _t("text.hints.playlist_issue_check_the_group_s_playlists"),
        "thumbnail_set": _t("text.hints.youtube_thumbnail_issue_the_channel_must_be"),
        "slack_post": _t("text.hints.slack_token_or_channel_issue_check_env"),
        "classroom_post": _t("text.hints.classroom_authorization_or_course_id_issue_check"),
        "library_add": _t("text.hints.could_not_add_the_lesson_to_the"),
    }


AMBIGUOUS_KEY = "text.hints.publication_status_is_uncertain_after_a_crash"
AMBIGUOUS = _t(AMBIGUOUS_KEY, language="hy")


def hint(error: str | None) -> str | None:
    """`error` is the job error '[stage] reason' written by pipeline._stop."""
    if not error or not error.startswith("["):
        return None
    if "may already be posted" in error:
        return _t(AMBIGUOUS_KEY)
    if "YouTube is still processing the video" in error:
        return _t("text.hints.youtube_has_not_finished_processing_the_video")
    if "YouTube did not accept the video" in error:
        return _t("text.hints.youtube_rejected_the_video_processing_failed_or")
    if "lecturer recognition" in error or "clear photos of the lecturer" in error:
        return _t("text.hints.automatic_recognition_could_not_confidently_identify_the")
    if "chosen on the job page was not found" in error or "lecturer choice file" in error:
        return _t("text.hints.the_selected_lecturer_was_not_found_again")
    if "is no longer in config.yaml" in error:
        return _t("text.hints.this_lesson_s_group_was_deleted_or")
    if "choose the lesson's subject" in error:
        return _t("text.hints.the_lesson_s_subject_is_unclear_because")
    return _hints().get(error[1 : error.find("]")])
