#!/usr/bin/env python3
"""Render assets/fyrn-chat-test-48.png,
assets/fyrn-chat-test-256.png and assets/fyrn-chat-test.png
from assets/fyrn-chat-test.svg.

Build-time helper, shipped in the zip: build_zip.py calls it
when the PNGs are missing, as in a fresh clone (the repo holds
only the SVG). Uses QSvgRenderer when available, else draws
the same shapes with QPainter. Verifies the PNGs by reading
them back.
"""

import os
import shutil
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

BASE = "fyrn-chat-test"


def render_with_svg(app):
    from PySide6.QtGui import QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer
    renderer = QSvgRenderer(
        os.path.join(ROOT, "assets", BASE + ".svg"))
    if not renderer.isValid():
        return False
    for size in (48, 256):
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        renderer.render(painter)
        painter.end()
        out = os.path.join(ROOT, "assets",
                           "%s-%d.png" % (BASE, size))
        image.save(out, "PNG")
    return True


def render_with_painter(app):
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import (QColor, QImage, QPainter, QPen,
                               QPolygonF)
    for size in (48, 256):
        scale = size / 256.0
        image = QImage(size, size, QImage.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(scale, scale)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#131f18"))
        painter.drawRoundedRect(QRectF(8, 8, 240, 240), 48, 48)
        painter.setBrush(QColor(46, 125, 50, 64))  # bench glow
        painter.drawEllipse(QRectF(36, 50, 184, 164))
        painter.setBrush(QColor("#2e7d32"))
        painter.drawPolygon(QPolygonF([
            QPointF(86, 168), QPointF(98, 206),
            QPointF(126, 168)]))
        painter.drawRoundedRect(QRectF(48, 62, 160, 112), 30, 30)
        painter.setPen(QPen(QColor("#e9f2ec"), 18, Qt.SolidLine,
                            Qt.RoundCap, Qt.RoundJoin))
        painter.setBrush(Qt.NoBrush)
        painter.drawPolyline(QPolygonF([
            QPointF(88, 118), QPointF(116, 146),
            QPointF(172, 88)]))
        painter.end()
        out = os.path.join(ROOT, "assets",
                           "%s-%d.png" % (BASE, size))
        image.save(out, "PNG")
    return True


def main():
    from PySide6.QtGui import QGuiApplication, QImage
    # Reuse the application when one already exists (the zip
    # test builds inside a process that already made one):
    # a second QGuiApplication aborts the process.
    app = QGuiApplication.instance()
    if app is None:
        app = QGuiApplication(sys.argv)
    try:
        used = "svg" if render_with_svg(app) else "painter"
    except ImportError:
        render_with_painter(app)
        used = "painter"
    # The window icon is the 256 px render under its plain name.
    shutil.copyfile(
        os.path.join(ROOT, "assets", BASE + "-256.png"),
        os.path.join(ROOT, "assets", BASE + ".png"))
    ok = True
    for name, size in ((BASE + "-48.png", 48),
                       (BASE + "-256.png", 256),
                       (BASE + ".png", 256)):
        path = os.path.join(ROOT, "assets", name)
        image = QImage(path)
        good = (not image.isNull() and image.width() == size
                and image.height() == size
                and os.path.getsize(path) > 100)
        print("%s: %dx%d, %d bytes, ok=%s"
              % (path, image.width(), image.height(),
                 os.path.getsize(path), good))
        ok = ok and good
    print("renderer: %s" % used)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
