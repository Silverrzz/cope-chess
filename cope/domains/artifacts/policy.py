from __future__ import annotations

MEDIA_POLICIES = {
    "application/octet-stream": "opaque-download-only.v1",
    "text/plain": "utf8-download-only.v1",
    "image/png": "png-signature-download-only.v1",
    "image/jpeg": "jpeg-signature-download-only.v1",
    "image/webp": "webp-signature-download-only.v1",
    "application/pdf": "pdf-signature-download-only.v1",
}

GUEST_EXTENSIONS = {
    "text/plain": frozenset({".txt", ".log", ".csv", ".tsv", ".json", ".pgn", ".epd", ".fen", ".md"}),
    "image/png": frozenset({".png"}),
    "image/jpeg": frozenset({".jpg", ".jpeg"}),
    "image/webp": frozenset({".webp"}),
    "application/pdf": frozenset({".pdf"}),
}

SIGNATURES = {
    "image/png": lambda data: data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR",
    "image/jpeg": lambda data: data.startswith(b"\xff\xd8\xff"),
    "image/webp": lambda data: data.startswith(b"RIFF") and data[8:12] == b"WEBP",
    "application/pdf": lambda data: data.startswith((b"%PDF-1.", b"%PDF-2.")),
}

GUEST_MAX_BYTES = 10 * 1024 * 1024


def media_allowed(accepted: tuple[str, ...], media_type: str, *, guest: bool) -> bool:
    return not accepted or media_type in accepted or (
        guest and "application/octet-stream" in accepted and media_type in GUEST_EXTENSIONS
    )
