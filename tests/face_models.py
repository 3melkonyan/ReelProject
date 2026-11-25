"""Skip marker for tests that load the OpenCV Zoo face models.

The YuNet and SFace weights are downloaded separately (see docs/INSTALLATION.md)
and are not part of the repository.
"""

import pytest

from reel.stages.privacy import DET_MODEL, REC_MODEL

requires_face_models = pytest.mark.skipif(
    not (DET_MODEL.is_file() and REC_MODEL.is_file()),
    reason="face models are not installed (see docs/INSTALLATION.md)",
)
