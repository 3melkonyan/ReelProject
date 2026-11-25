# Contributing

Open an issue describing the problem and a small reproduction before proposing a substantial change. Include the macOS/Python versions, relevant stage and expected result. Use synthetic media and redact personal information from logs.

## Local setup

Follow [Installation](docs/INSTALLATION.md), then install development dependencies:

```sh
uv pip install --python .venv/bin/python -r requirements-dev.txt
REEL_DRY_RUN=1 .venv/bin/python -m pytest -q
```

Alternatively use `python -m pip install -r requirements-dev.txt` in the virtual environment. FFmpeg-dependent tests need FFmpeg on PATH; web integration tests bind local loopback sockets. Video tests use the selected H.264 encoder, falling back to software if VideoToolbox is unavailable. A failed environment prerequisite is not a passing test; include it in your test report.

## Changes

- Keep fixes focused and explain the user-visible behavior.
- Add regression coverage for a meaningful bug, using temporary paths and synthetic data.
- Never weaken a test to hide a failure.
- Preserve privacy checks and group permissions. Missing identity evidence must not be described as proof that a face is safe to publish.
- Mock external publishing, email and authentication in tests. Do not run tests against production credentials or a production database.
- Update the relevant guide when changing setup, commands or behavior.
- Keep credentials, recordings, model weights and personal data out of commits.

In pull requests, describe what changed, why, how it was checked, and any remaining limitations. Follow the existing Python and browser code conventions; the frontend uses static assets without a separate build step.

## Documentation

Write documentation in English and keep commands, configuration keys and relative links consistent with the code. UI catalogs support Armenian and English; user-entered lesson content is separate from interface translation.

Use synthetic data for screenshots. Seed a separate temporary root with `tools/demo_seed.py --apply --root /path/to/isolated-root` after copying the example configuration there. The seed creates local data, not isolation: keep real credentials out of that root, run no worker, and disable notification/service transports in a screenshot harness. Store optimized PNG or WebP files below 300 KB in `docs/images/`, with descriptive filenames and alt text.

By contributing, you agree that your contributions are licensed under the [MIT License](LICENSE).
