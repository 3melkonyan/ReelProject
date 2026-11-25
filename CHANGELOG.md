# Changelog

Notable changes to Reel are documented here, following [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## 1.0.0 - 2026-11-25

### Added

- Local lecture processing with FFmpeg, MLX Whisper, OpenCV and Ollama: multi-part joining, transcription, conservative editing, audio normalization and optional board/projector framing.
- Face blurring with lecturer identification, post-upload lecturer review and an independent final privacy scan.
- Armenian and English interfaces, account language preferences and separate transcription and generated-content language settings.
- FastAPI dashboard with resumable uploads, browser recording, processing progress, retries and per-stage checkpoints.
- Personal administrator, lecturer and student accounts, invitations, recovery, administrator email verification and group-scoped permissions.
- Searchable lesson library with chapter navigation, viewing progress, captions, summaries and study questions.
- Unlisted YouTube publishing, playlists and thumbnails; optional Slack, Google Classroom and email notifications.
- Administrator-only lesson deletion with cancellation, database-state backup, resumable external cleanup and retained source recordings.
- Bounded frame IO overlap for face processing, with byte-identical output in the documented synthetic benchmark.
- Dark and light themes, synthetic-data screenshots, installation and operations guides, core CI checks and an MIT license.
