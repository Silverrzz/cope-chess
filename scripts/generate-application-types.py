from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def type_name(value: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_]","_",value)


def typescript(schema: dict, prefix: str = "") -> str:
    if "$ref" in schema:
        if not schema["$ref"].startswith("#/$defs/") or not prefix:
            raise ValueError("Application schemas must use registered local definitions")
        name = schema["$ref"][8:].replace("~1","/").replace("~0","~")
        return prefix+"_"+type_name(name)
    if "const" in schema:
        return json.dumps(schema["const"])
    if "enum" in schema:
        return " | ".join(json.dumps(item) for item in schema["enum"])
    primitive = {"string": "string", "integer": "number", "number": "number", "boolean": "boolean", "null": "null"}
    if schema.get("type") in primitive:
        return primitive[schema["type"]]
    if schema.get("type") == "array":
        return f"Array<{typescript(schema['items'],prefix)}>"
    if "anyOf" in schema:
        return " | ".join(typescript(item,prefix) for item in schema["anyOf"])
    if "oneOf" in schema:
        return " | ".join(typescript(item,prefix) for item in schema["oneOf"])
    if schema.get("type") == "object" and "properties" in schema:
        required = schema.get("required", [])
        fields = [f"{json.dumps(key)}{'' if key in required else '?'}: {typescript(value,prefix)}" for key, value in schema["properties"].items()]
        if not fields:
            additional = schema.get("additionalProperties",True)
            return "Record<string, "+(typescript(additional,prefix) if isinstance(additional,dict) else "unknown" if additional else "never")+">"
        result = "{ " + "; ".join(fields) + " }"
        if schema.get("additionalProperties"):
            result += " & Record<string, unknown>"
        return result
    if schema.get("type") == "object":
        additional = schema.get("additionalProperties", True)
        return "Record<string, " + (typescript(additional,prefix) if isinstance(additional, dict) else "unknown" if additional else "never") + ">"
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
    definitions = []

    def model_type(model, name: str, category: str) -> str:
        schema = model.model_json_schema()
        prefix = "ApplicationSchema_"+type_name(category+"_"+name)
        names = [type_name(key) for key in schema.get("$defs",{})]
        if len(names)!=len(set(names)):
            raise ValueError("Application schema definition names collide after TypeScript conversion")
        for key,value in sorted(schema.get("$defs",{}).items()):
            definitions.append("export type "+prefix+"_"+type_name(key)+" = "+typescript(value,prefix))
        return typescript(schema,prefix)

    lines = ["export interface ApplicationInputs {"]
    for name, command in sorted(COMMANDS.items()):
        lines.append(f"  '{name}': {model_type(command.input_model,name,'command_input')}")
    lines.extend(["}", "", "export type ApplicationCommand = keyof ApplicationInputs", ""])
    lines.append("export const applicationCommandContracts = {")
    for name, command in sorted(COMMANDS.items()):
        lines.append(f"  '{name}': {{ requiresTarget: {str(command.requires_target).lower()}, requiresVersion: {str(command.requires_version).lower()} }},")
    lines.extend(["} as const", "", "export interface ApplicationOutputs {"])
    for name, command in sorted(COMMANDS.items()):
        lines.append(f"  '{name}': {model_type(command.output_model,name,'command_output')}")
    lines.extend(["}", "", "export interface ApplicationQueryInputs {"])
    for name, query in sorted(QUERIES.items()):
        lines.append(f"  '{name}': {model_type(query.input_model,name,'query_input')}")
    lines.extend(["}", "", "export interface ApplicationQueryOutputs {"])
    for name,query in sorted(QUERIES.items()):
        lines.append(f"  '{name}': {model_type(query.output_model,name,'query_output')}")
    lines.extend(["}", "", "export type ApplicationQuery = keyof ApplicationQueryInputs", "",*definitions,""])
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
