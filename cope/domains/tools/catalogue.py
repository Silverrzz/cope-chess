from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlencode

from cope.db.tools import ToolJobRecord


@dataclass(frozen=True)
class Tool:
    name: str
    label: str
    description: str
    href: str
    status: str = "available"


INSPECT_OPTIONS = Tool("who_has_this", "Who Has This",
    "Inspect engine UCI handshakes across any selection of versions.", "/admin/tools/who-has-this")
PUZZLE_SUITE = Tool("puzzle_suite_manager", "Puzzle Suite Manager",
    "Verify unique solutions, measure solve effort, finetune misses, and order puzzle suites by engine-rated difficulty.",
    "/admin/tools/puzzle-suite-manager")
TOOLS = (
    INSPECT_OPTIONS,
    PUZZLE_SUITE,
    Tool("invalidate_rating_list_engine", "Invalidate engine games",
         "Uncommit one engine's games from every affected rating list, then invalidate them.",
         "/admin/tools/invalidate-engine-games"),
    Tool("tournament_creator", "Tournament creator",
         "Create rating divisions or build a balanced Elo gauntlet as editable tournament drafts.",
         "/admin/tools/tournament-creator"),
    Tool("clone_environment", "Clone environment",
         "Pull a dependency-complete, verified selection of data and engine artifacts from another Cope host.",
         "/admin/tools/clone-environment"),
)


@dataclass(frozen=True)
class Workload:
    key: str
    tool: Tool
    label: str


UCI_INSPECTION = Workload("who_has_this", INSPECT_OPTIONS, INSPECT_OPTIONS.label)
PUZZLE_UNIQUENESS = Workload("puzzle_suite_uniqueness", PUZZLE_SUITE, "Puzzle uniqueness filter")
PUZZLE_DIFFICULTY = Workload("puzzle_suite_difficulty", PUZZLE_SUITE, "Puzzle difficulty rating")
WORKLOADS = {item.key: item for item in (UCI_INSPECTION, PUZZLE_UNIQUENESS, PUZZLE_DIFFICULTY)}


@dataclass(frozen=True)
class JobPresentation:
    label: str
    href: str
    tool_label: str
    progress: str


def describe_job(job: ToolJobRecord) -> JobPresentation:
    workload = WORKLOADS.get(job.tool_name)
    if workload is UCI_INSPECTION:
        label = str(job.input.get("option_name") or workload.label)
        progress = f"Inspecting {job.completed_items} of {job.total_items} engines"
        href = f"{workload.tool.href}?{urlencode({'job': job.id})}"
    elif workload is not None and workload.tool is PUZZLE_SUITE:
        label = "Puzzle miss finetuning" if job.input.get("suite_stage") == "miss_finetuning" else workload.label
        progress = f"Processed {job.progress_current} of {job.progress_total} searches"
        suite_id = job.input.get("suite_id")
        href = f"{workload.tool.href}?{urlencode({'suite': suite_id})}" if suite_id is not None else workload.tool.href
    else:
        return JobPresentation(job.tool_name, "/admin/tools", "Tools", job.progress_detail)
    return JobPresentation(label, href, workload.tool.label, job.progress_detail or progress)
