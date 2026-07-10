"""Generate 128×128 thumbnails for list rendering (faster load, less bandwidth).

Shares the same PIL logic across expense images, invoice files, and any future image
upload endpoints. Gracefully degrades if Pillow is not installed or the image is
corrupt — callers always get a valid path back (original or thumbnail).
"""

import os

THUMB_SIZE = (128, 128)
THUMB_SUFFIX = '_thumb.jpg'
THUMB_QUALITY = 85
MAX_DIM = 1920       # max width/height for original images
COMPRESS_QUALITY = 80

try:
    from PIL import Image as _PILImage
    from PIL import ImageOps
    HAS_PIL = True
except ImportError:
    _PILImage = None  # type: ignore
    ImageOps = None  # type: ignore
    HAS_PIL = False


def thumb_name(original_filename: str) -> str:
    """Derive thumbnail filename from original. 'abc.png' → 'abc_thumb.jpg'"""
    base = os.path.splitext(original_filename)[0]
    return f'{base}{THUMB_SUFFIX}'


def generate_thumbnail(original_path: str, thumb_path: str) -> str | None:
    """Generate a 128×128 JPEG thumbnail. Returns thumb_path on success, None on failure.

    Callers should fall back to the original image when None is returned.
    """
    if not HAS_PIL:
        return None
    if not os.path.isfile(original_path):
        return None
    try:
        with _PILImage.open(original_path) as img:  # type: ignore[union-attr]
            # Apply EXIF orientation (fixes rotated phone photos)
            img = ImageOps.exif_transpose(img)  # type: ignore[union-attr]
            img.thumbnail(THUMB_SIZE, _PILImage.LANCZOS)  # type: ignore[union-attr]
            # Convert to RGB if needed (PNG with alpha, palette, etc.)
            if img.mode in ('RGBA', 'P', 'LA'):
                bg = _PILImage.new('RGB', img.size, (255, 255, 255))  # type: ignore[union-attr]
                if img.mode in ('RGBA', 'LA'):
                    bg.paste(img, mask=img.split()[-1])
                else:
                    bg.paste(img.convert('RGBA'))
                img = bg
            img.save(thumb_path, 'JPEG', quality=THUMB_QUALITY, optimize=True)
        return thumb_path
    except Exception:
        return None


def compress_original(fileobj, save_path: str, max_dim: int = MAX_DIM, quality: int = COMPRESS_QUALITY) -> int:
    """Compress an uploaded image file to JPEG. Handles EXIF orientation.

    Args:
        fileobj: A file-like object (e.g., Flask FileStorage .stream or an open file).
        save_path: Where to save the compressed JPEG.
        max_dim: Maximum width/height (default 1920px).
        quality: JPEG quality 1-100 (default 80).

    Returns:
        File size in bytes on success. Raises on failure (caller should fall back to
        saving the raw file).
    """
    if not HAS_PIL:
        raise RuntimeError('Pillow not installed')
    with _PILImage.open(fileobj) as img:  # type: ignore[union-attr]
        img = ImageOps.exif_transpose(img)  # type: ignore[union-attr]
        # Convert to RGB
        if img.mode in ('RGBA', 'P', 'LA'):
            bg = _PILImage.new('RGB', img.size, (255, 255, 255))  # type: ignore[union-attr]
            if img.mode in ('RGBA', 'LA'):
                bg.paste(img, mask=img.split()[-1])
            else:
                bg.paste(img.convert('RGBA'))
            img = bg
        elif img.mode != 'RGB':
            img = img.convert('RGB')
        # Resize if larger than max_dim
        w, h = img.size
        if w > max_dim or h > max_dim:
            if w > h:
                h = round(h * max_dim / w)
                w = max_dim
            else:
                w = round(w * max_dim / h)
                h = max_dim
            img = img.resize((w, h), _PILImage.LANCZOS)  # type: ignore[union-attr]
        img.save(save_path, 'JPEG', quality=quality, optimize=True)
    return os.path.getsize(save_path)
