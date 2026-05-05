# -*- coding: utf-8 -*-
"""Опциональное распознавание текста с фото (EasyOCR)."""
from __future__ import annotations

from typing import Tuple

_OCR = None


def ocr_available() -> bool:
    try:
        import easyocr  # noqa: F401
        return True
    except ImportError:
        return False


def run_ocr_on_image(image_bytes: bytes, langs: Tuple[str, ...] = ("ru", "en")) -> str:
    """
    Возвращает сцепленный текст с изображения (таблица будет «как прочиталось»).
    Первый вызов может долго качать модели.
    """
    import easyocr
    from io import BytesIO
    from PIL import Image

    global _OCR
    if _OCR is None:
        _OCR = easyocr.Reader(list(langs), gpu=False, verbose=False)

    img = Image.open(BytesIO(image_bytes)).convert("RGB")
    import numpy as np

    arr = np.array(img)
    lines = _OCR.readtext(arr, detail=0, paragraph=True)
    if isinstance(lines, list):
        return "\n".join(str(x) for x in lines)
    return str(lines)


def ocr_hint() -> str:
    if ocr_available():
        return "EasyOCR установлен."
    return (
        "Для фото установите пакеты: `pip install easyocr` (первый запуск скачает модели). "
        "Либо вставьте текст вручную во вкладке «Текст»."
    )
