"""Dashboard stage labels resolved in the current request language."""
from reel.i18n import t

STAGE_NAMES = (
    "join", "probe", "audio", "transcribe", "segments", "render",
    "blur", "privacy", "preview", "metadata", "summary", "thumbnail",
    "youtube_upload", "playlist", "thumbnail_set", "captions",
    "slack_post", "classroom_post", "library_add",
)


def localized_stages(*, language=None):
    return [(name, t("stage." + name, language=language)) for name in STAGE_NAMES]


def stage_labels(*, language=None):
    return dict(localized_stages(language=language))
