"""Drawn classroom fixtures only. No real faces or private sample frames."""

import cv2
import numpy as np

W, H = 640, 360


def surface(kind="white", *, angle=0, skew=False, person=False, person_x=280, writing=3, wall=None):
    colours = {"white": (240, 240, 240), "black": (35, 35, 35), "green": (42, 90, 48)}
    wall = wall or {"white": (220, 220, 220), "black": (25, 25, 25), "green": (40, 65, 42)}[kind]
    image = np.full((H, W, 3), wall, np.uint8)
    q = np.float32([[85, 85], [555, 85], [555, 285], [85, 285]])
    if skew:
        q = np.float32([[110, 70], [535, 105], [570, 290], [65, 275]])
    cv2.fillConvexPoly(image, q.astype(int), colours[kind])
    cv2.polylines(image, [q.astype(int)], True, (140, 140, 140), 5)
    ink = (35, 35, 35) if kind == "white" else (235, 235, 235)
    for i in range(writing):
        cv2.putText(
            image,
            "value = " + str(i + 1),
            (135, 135 + i * 32),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            ink,
            2,
        )
    if person:
        cv2.rectangle(image, (person_x, 170), (person_x + 48, 359), (60, 55, 80), -1)
        cv2.circle(image, (person_x + 24, 154), 17, (130, 150, 180), -1)
    if angle:
        matrix = cv2.getRotationMatrix2D((W / 2, H / 2), -angle, 1)
        image = cv2.warpAffine(image, matrix, (W, H), borderValue=wall)
        q = cv2.transform(q[None], matrix)[0]
    return image, q


def mixed(t, *, camera=False):
    image = np.full((H, W, 3), 65, np.uint8)
    board = np.int32([[30, 75], [300, 75], [300, 260], [30, 260]])
    screen = np.int32([[340, 60], [610, 60], [610, 245], [340, 245]])
    cv2.fillConvexPoly(image, board, (230, 230, 230))
    cv2.polylines(image, [board], True, (180, 180, 180), 4)
    lines = min(4, int(t // 10)) if t < 40 else 4 if t < 80 else min(8, 4 + int((t - 80) // 10))
    for i in range(lines):
        cv2.line(image, (55, 100 + i * 16), (240, 100 + i * 16), (40, 40, 40), 2)
    on = 40 <= t < 120
    if on:
        slide = int(t // 20) % 2
        cv2.rectangle(image, (340, 60), (475, 245), (230, 230, 230) if slide else (75, 75, 75), -1)
        cv2.rectangle(image, (475, 60), (610, 245), (75, 75, 75) if slide else (230, 230, 230), -1)
        cv2.putText(
            image,
            "SLIDE " + str(slide),
            (365, 160),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.65,
            (150, 180, 240),
            2,
        )
    else:
        cv2.fillConvexPoly(image, screen, (70, 70, 70))
    cv2.polylines(image, [screen], True, (140, 140, 140), 4)
    if t >= 120:
        image[:] = 65
    if camera:
        matrix = np.float32([[1, 0, 18], [0, 1, -14]])
        image = cv2.warpAffine(image, matrix, (W, H), borderValue=(65, 65, 65))
    return image
