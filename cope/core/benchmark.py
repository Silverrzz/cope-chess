from __future__ import annotations

import hashlib
import json
import re

from .models import HardwareInfo


NPS_LABEL = r"(?:nodes[ \t]*/[ \t]*(?:second|sec|s)|nodes[ \t]+per[ \t]+second|nps)"
NPS_VALUE = r"([0-9]+(?:[ ,._][0-9]{3})*)"
NPS_PREFIX = re.compile(rf"\b{NPS_LABEL}\b(?:[ \t]*[:=][ \t]*|[ \t]+){NPS_VALUE}(?![0-9,._])", re.IGNORECASE)
NPS_SUFFIX = re.compile(rf"\b{NPS_VALUE}[ \t]+{NPS_LABEL}\b", re.IGNORECASE)


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


def parse_benchmark_nps(output: str, *, summary_only: bool = False) -> int | None:
    telemetry_nps = None
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line.startswith("Completed ") and " bench at " in line:
            continue
        match = NPS_PREFIX.search(line) or NPS_SUFFIX.search(line)
        if match is None:
            continue
        digits = re.sub(r"[^0-9]", "", match.group(1))
        if len(digits) > 18:
            continue
        value = int(digits)
        if value <= 0:
            continue
        if line.startswith("info ") and not line.startswith("info string "):
            if telemetry_nps is None:
                telemetry_nps = value
            continue
        return value
    return None if summary_only else telemetry_nps
