from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def typescript(schema: dict) -> str:
    if "enum" in schema:
        import json

        return " | ".join(json.dumps(item) for item in schema["enum"])
    primitive = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean", "null": "null"}
    if schema.get("type") in primitive:
        return primitive[schema["type"]]
    if schema.get("type") == "array":
        return f"Array<{typescript(schema['items'])}>"
    if "anyOf" in schema:
        return " | ".join(typescript(item) for item in schema["anyOf"])
    if schema.get("type") == "object" and "properties" in schema:
        required = schema.get("required", [])
        fields = [f"{key}{'' if key in required else '?'}: {typescript(value)}" for key, value in schema["properties"].items()]
        if not fields:
            return "Record<string, never>"
        return "{ " + "; ".join(fields) + " }"
    if schema.get("type") == "object":
        additional = schema.get("additionalProperties", True)
        return "Record<string, " + (typescript(additional) if isinstance(additional, dict) else "unknown" if additional else "never") + ">"
    if not schema:
        return "unknown"
    raise ValueError(f"Unsupported application schema: {schema}")


def main() -> int:
    sys.path.insert(0, str(ROOT))
    from cope.application.commands import COMMANDS
    from cope.application.queries import QUERIES

    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    lines = ["export interface ApplicationInputs {"]
    for name, command in sorted(COMMANDS.items()):
        lines.append(f"  '{name}': {typescript(command.input_model.model_json_schema())}")
    lines.extend(["}", "", "export type ApplicationCommand = keyof ApplicationInputs", ""])
    lines.append("export const applicationCommandContracts = {")
    for name, command in sorted(COMMANDS.items()):
        lines.append(f"  '{name}': {{ requiresTarget: {str(command.requires_target).lower()}, requiresVersion: {str(command.requires_version).lower()} }},")
    lines.extend(["} as const", "", "export interface ApplicationOutputs {"])
    for name, command in sorted(COMMANDS.items()):
        lines.append(f"  '{name}': {typescript(command.output_model.model_json_schema())}")
    lines.extend(["}", "", "export interface ApplicationQueryInputs {"])
    for name, query in sorted(QUERIES.items()):
        lines.append(f"  '{name}': {typescript(query.input_model.model_json_schema())}")
    lines.extend(["}", "", "export type ApplicationQuery = keyof ApplicationQueryInputs", ""])
    content = "\n".join(lines)
    target = ROOT / "frontend/src/types/application-inputs.ts"
    if args.check:
        if not target.exists() or target.read_text(encoding="utf-8") != content:
            print("Application TypeScript contracts need regeneration", file=sys.stderr)
            return 1
    else:
        target.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
