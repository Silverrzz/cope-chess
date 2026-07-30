from __future__ import annotations

import hashlib
import json
import re

from .models import HardwareInfo


NPS_PATTERNS = (
    re.compile(
        r"(?im)\b(?:nodes\s*/\s*second|nodes\s+per\s+second|nps)\b"
        r"\s*(?:[:=]\s*|\s+)([0-9][0-9,._ ]*)"
    ),
    re.compile(r"(?im)\b([0-9][0-9,._ ]*)\s+(?:nodes\s*/\s*second|nps)\b"),
)


def benchmark_hardware_key(machine_id: str, hw: HardwareInfo) -> str:
    identity = {
        "cpu_model": hw.cpu_model.strip(),
        "gpu": (hw.gpu or "").strip(),
        "logical_cores": hw.logical_cores,
        "machine_id": machine_id.strip(),
        "physical_cores": hw.physical_cores,
        "ram_mb": hw.total_ram_mb,
    }
    encoded = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def parse_benchmark_nps(output: str) -> int | None:
    values: list[int] = []
    for pattern in NPS_PATTERNS:
        for match in pattern.finditer(output):
            digits = re.sub(r"[^0-9]", "", match.group(1))
            if digits:
                value = int(digits)
                if value > 0:
                    values.append(value)
    return values[-1] if values else None
