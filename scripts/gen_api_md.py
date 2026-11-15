"""docs/api.md 생성: docs/api-guide.md(설명) + OpenAPI의 엔드포인트·스키마 표.

사용: python scripts/gen_api_md.py docs/openapi.json docs/api.md
"""

import json
import sys
from pathlib import Path
from typing import Any

GUIDE = Path(__file__).resolve().parent.parent / "docs" / "api-guide.md"

TAG_TITLES = {
    "admin-auth": "관리자 · 인증",
    "admin-posts": "관리자 · 카테고리·게시물",
    "admin-photos": "관리자 · 사진 업로드",
    "admin-stats": "관리자 · 통계",
    "public": "방문자 (공개)",
    "health": "헬스 체크",
}
METHOD_ORDER = ("get", "post", "put", "patch", "delete")

Schema = dict[str, Any]  # Any: OpenAPI JSON


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def _type_of(schema: Schema) -> str:
    if "$ref" in schema:
        return f"[{_ref_name(schema['$ref'])}](#schema-{_ref_name(schema['$ref']).lower()})"
    if "anyOf" in schema:
        parts = [_type_of(s) for s in schema["anyOf"]]
        return " \\| ".join(parts)
    if "enum" in schema:
        return " \\| ".join(f"`{v}`" for v in schema["enum"])
    t = schema.get("type", "object")
    if t == "array":
        return f"{_type_of(schema.get('items', {}))}[]"
    if t == "null":
        return "null"
    fmt = schema.get("format")
    return f"{t} ({fmt})" if fmt else str(t)


def _cell(text: object) -> str:
    return str(text).replace("\n", " ").replace("|", "\\|")


def _constraints(schema: Schema) -> str:
    keys = {
        "minLength": "최소 길이",
        "maxLength": "최대 길이",
        "minimum": "≥",
        "exclusiveMinimum": ">",
        "maximum": "≤",
        "minItems": "최소 개수",
        "maxItems": "최대 개수",
    }
    found = [f"{label} {schema[k]}" for k, label in keys.items() if k in schema]
    for sub in schema.get("anyOf", []):
        found += [f"{label} {sub[k]}" for k, label in keys.items() if k in sub]
    return ", ".join(found)


def _schema_table(schema: Schema) -> list[str]:
    props: dict[str, Schema] = schema.get("properties", {})
    if not props:
        return []
    required = set(schema.get("required", []))
    lines = ["| 필드 | 타입 | 필수 | 설명 |", "|---|---|---|---|"]
    for name, prop in props.items():
        desc = prop.get("description", "")
        extra = _constraints(prop)
        if extra:
            desc = f"{desc} ({extra})" if desc else extra
        if "default" in prop and prop["default"] is not None:
            desc = f"{desc} 기본값 `{json.dumps(prop['default'], ensure_ascii=False)}`".strip()
        if prop.get("examples"):
            desc = f"{desc} 예: `{json.dumps(prop['examples'][0], ensure_ascii=False)}`".strip()
        lines.append(f"| `{name}` | {_type_of(prop)} | {'✔' if name in required else ''} | {_cell(desc)} |")
    return lines


def _body_schema(content: dict[str, Schema]) -> Schema | None:
    for media in ("application/json", "application/problem+json"):
        if media in content:
            schema: Schema | None = content[media].get("schema")
            return schema
    return None


def _operation(method: str, path: str, op: Schema) -> list[str]:
    out = [f"### `{method.upper()} {path}`", ""]
    if op.get("summary"):
        out += [f"**{op['summary']}**", ""]
    if op.get("description"):
        out += [op["description"], ""]

    params = op.get("parameters", [])
    if params:
        out += ["| 파라미터 | 위치 | 타입 | 필수 | 설명 |", "|---|---|---|---|---|"]
        for p in params:
            schema = p.get("schema", {})
            desc = p.get("description") or schema.get("description", "")
            extra = _constraints(schema)
            if extra:
                desc = f"{desc} ({extra})" if desc else extra
            if "default" in schema and schema["default"] is not None:
                desc = f"{desc} 기본값 `{schema['default']}`".strip()
            out.append(
                f"| `{p['name']}` | {p['in']} | {_type_of(schema)} | {'✔' if p.get('required') else ''} "
                f"| {_cell(desc)} |"
            )
        out.append("")

    body = op.get("requestBody")
    if body:
        schema = _body_schema(body.get("content", {}))
        if schema:
            out += [f"요청 본문: {_type_of(schema)}", ""]

    out += ["| 응답 | 설명 | 본문 |", "|---|---|---|"]
    for code, resp in op.get("responses", {}).items():
        schema = _body_schema(resp.get("content", {}))
        out.append(
            f"| {code} | {_cell(resp.get('description', ''))} | {_type_of(schema) if schema else '-'} |"
        )
    out.append("")
    return out


def render(openapi: Schema) -> str:
    lines = [GUIDE.read_text(encoding="utf-8").rstrip(), "", "## 9. 엔드포인트", ""]

    by_tag: dict[str, list[tuple[str, str, Schema]]] = {}
    for path, item in openapi["paths"].items():
        for method in METHOD_ORDER:
            if method in item:
                op = item[method]
                tag = (op.get("tags") or ["기타"])[0]
                by_tag.setdefault(tag, []).append((method, path, op))

    ordered = [t for t in TAG_TITLES if t in by_tag] + [t for t in by_tag if t not in TAG_TITLES]
    lines += ["| 메서드 | 경로 | 설명 |", "|---|---|---|"]
    for tag in ordered:
        for method, path, op in by_tag[tag]:
            lines.append(f"| {method.upper()} | `{path}` | {_cell(op.get('summary', ''))} |")
    lines.append("")

    for tag in ordered:
        lines += [f"### {TAG_TITLES.get(tag, tag)}", ""]
        for method, path, op in by_tag[tag]:
            lines += _operation(method, path, op)

    lines += ["## 10. 스키마", ""]
    for name, schema in sorted(openapi.get("components", {}).get("schemas", {}).items()):
        if name in {"HTTPValidationError", "ValidationError"}:
            continue  # 422는 Problem 형식으로 변환된다
        lines += [f'<a id="schema-{name.lower()}"></a>', f"### {name}", ""]
        if schema.get("description"):
            lines += [schema["description"], ""]
        if "enum" in schema:
            lines += ["값: " + ", ".join(f"`{v}`" for v in schema["enum"]), ""]
        table = _schema_table(schema)
        if table:
            lines += [*table, ""]
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/openapi.json")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "docs/api.md")
    out.write_text(render(json.loads(src.read_text(encoding="utf-8"))), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
