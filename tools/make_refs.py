"""One-time setup: build lecturer reference photos from a lesson video.

Samples frames, detects faces, groups them by identity (SFace), and saves the
best crops of the most frequent person to refs/<name>/. Writes a preview sheet
of every identity found so a human can confirm who the lecturer is.

  python tools/make_refs.py samples/lesson21.mp4 --name lecturer1 [--every 30] [--cluster N]
"""
from __future__ import annotations

import hashlib
import argparse
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from reel.stages.privacy import COSINE_MATCH, FaceID, _frames  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--name", required=True)
    ap.add_argument("--every", type=float, default=30.0, help="seconds between sampled frames")
    ap.add_argument("--cluster", type=int, help="use this cluster index instead of the largest")
    ap.add_argument("--clusters", help="comma-separated cluster indices, e.g. 0,1,2,3")
    ap.add_argument("--keep", type=int, default=5)
    a = ap.parse_args()

    fid = FaceID([])
    original_features: dict[int, np.ndarray] = {}
    clusters: list[dict] = []   # {"feat": mean feature, "items": [(score, t, crop)]}
    for t, img in _frames(Path(a.video), 1.0 / a.every, 1280):
        for f in fid.faces(img):
            feat = fid.feature(img, f)
            x, y, w, h = (int(v) for v in f[:4])
            m = int(max(w, h) * 0.8)                       # generous margin: refs must re-detect
            crop = img[max(y - m, 0):y + h + m, max(x - m, 0):x + w + m].copy()
            original_features[id(crop)] = feat.copy()
            quality = float(w * h) * float(f[14])          # size x detector confidence
            best, best_sim = None, 0.0
            for c in clusters:
                sim = fid.rec.match(feat, c["feat"], cv2.FaceRecognizerSF_FR_COSINE)
                if sim > best_sim:
                    best, best_sim = c, sim
            if best is not None and best_sim >= COSINE_MATCH:
                best["items"].append((quality, t, crop))
                n = len(best["items"])
                best["feat"] = (best["feat"] * (n - 1) + feat) / n
            else:
                clusters.append({"feat": feat, "items": [(quality, t, crop)]})

    if not clusters:
        sys.exit("no faces found")
    clusters.sort(key=lambda c: len(c["items"]), reverse=True)

    # preview: one row per identity (up to 6), 4 best crops each
    rows = []
    for i, c in enumerate(clusters[:6]):
        best = sorted(c["items"], key=lambda x: x[0], reverse=True)[:4]
        tiles = [cv2.resize(cr, (160, 160)) for _, _, cr in best]
        tiles += [np.zeros((160, 160, 3), np.uint8)] * (4 - len(tiles))
        row = np.hstack(tiles)
        cv2.putText(row, f"#{i} n={len(c['items'])}", (5, 20), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                    (0, 255, 255), 2)
        rows.append(row)
    preview = ROOT / "out" / f"refs_preview_{a.name}.jpg"
    preview.parent.mkdir(exist_ok=True)
    cv2.imwrite(str(preview), np.vstack(rows))

    if a.clusters and a.cluster is not None:
        ap.error("use either --cluster or --clusters")
    try:
        indices = list(dict.fromkeys(int(i.strip()) for i in a.clusters.split(","))) if a.clusters else [a.cluster if a.cluster is not None else 0]
    except ValueError:
        ap.error("--clusters must be comma-separated integer indices")
    if a.keep < 1 or any(i < 0 or i >= len(clusters) for i in indices):
        ap.error("invalid cluster index or --keep value")
    out = ROOT / "refs" / a.name
    out.mkdir(parents=True, exist_ok=True)
    picked = []
    for index in indices:
        best = sorted(clusters[index]["items"], key=lambda x: x[0], reverse=True)
        selected, times = [], []
        for q, t, cr in best:
            if all(abs(t - u) >= 300 for u in times):
                selected.append(cr); times.append(t)
            if len(selected) >= a.keep:
                break
        picked.extend(selected)
    # Replace only tool-generated crops when regenerating the selected identity.
    for old in out.glob("ref_*.npz"):
        old.unlink()
    for old in out.glob("ref_*.jpg"):
        old.unlink()
    for k, cr in enumerate(picked):
        if not cv2.imwrite(str(out / f"ref_{k}.jpg"), cr):
            raise RuntimeError("Cannot write lecturer reference crop")
        image_path = out / f"ref_{k}.jpg"
        # Preserve the original full-frame alignment: profile/back crops may
        # not be detected a second time after cropping/JPEG encoding.
        np.savez(image_path.with_suffix(".npz"),
                 feature=original_features[id(cr)],
                 image_sha256=np.array(hashlib.sha256(image_path.read_bytes()).hexdigest()))
    print(f"identities found: {[len(c['items']) for c in clusters[:6]]}")
    print(f"saved {len(picked)} refs of clusters {indices} to {out}")
    print(f"preview: {preview}")


if __name__ == "__main__":
    main()
