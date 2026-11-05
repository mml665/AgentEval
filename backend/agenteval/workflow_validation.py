"""Parse source documents without losing JSONL line/field locations."""
import json
from types import SimpleNamespace as Obj
from pydantic import ValidationError
from fastapi import HTTPException
from agenteval.contracts import Case, ProfileCreate, digest, canonical
from agenteval.evaluators import metadata, validate_case


def array_lines(document):
    """Locations of case-object starts, including pretty JSON and a top-level cases wrapper."""
    decoder = json.JSONDecoder()
    def skip(i):
        while i < len(document) and document[i].isspace(): i += 1
        return i
    cursor, start = skip(0), None
    if document[cursor] == "[": start = cursor
    elif document[cursor] == "{":
        cursor = skip(cursor+1)
        while cursor < len(document) and document[cursor] != "}":
            key, cursor = decoder.raw_decode(document,cursor)
            cursor = skip(skip(cursor)+1)  # colon; syntax was already checked by json.loads
            begin = cursor; value,cursor = decoder.raw_decode(document,cursor)
            if key == "cases" and isinstance(value,list): start = begin
            cursor = skip(cursor)
            if document[cursor] == ",": cursor = skip(cursor+1)
    if start is None: return [1]
    cursor = start+1; line = document.count("\n",0,cursor)+1; locations = []
    while cursor < len(document):
        before = cursor; cursor = skip(cursor); line += document.count("\n",before,cursor)
        if document[cursor] == "]": break
        locations.append(line)
        _, end = decoder.raw_decode(document,cursor)
        line += document.count("\n",cursor,end); cursor = end
        before = cursor; cursor = skip(cursor); line += document.count("\n",before,cursor)
        if document[cursor] == ",": cursor += 1
    return locations


def inspect_document(document, specs=(), capabilities=None):
    errors, rows, locations = [], [], []
    try:
        parsed = json.loads(document)
        rows = parsed if isinstance(parsed, list) else parsed.get("cases", [parsed] if "case_key" in parsed else None) if isinstance(parsed, dict) else None
        if not isinstance(rows, list): return [], [{"line": 1, "field": "/cases", "code": "CASE_ARRAY_REQUIRED"}]
        locations = array_lines(document)
    except json.JSONDecodeError as exc:
        if document.lstrip().startswith(("[", "{")) and not any(line.strip().startswith("{") for line in document.splitlines()[1:]):
            return [], [{"line": exc.lineno, "column": exc.colno, "field": "/", "code": "INVALID_JSON"}]
        for line, value in enumerate(document.splitlines(), 1):
            if not value.strip(): continue
            try: rows.append(json.loads(value)); locations.append(line)
            except json.JSONDecodeError as error: errors.append({"line": line, "column": error.colno, "field": "/", "code": "INVALID_JSON"})
            except RecursionError: errors.append({"line": line, "field": "/", "code": "JSON_DEPTH_LIMIT"})
    except RecursionError:
        return [], [{"line": 1, "field": "/", "code": "JSON_DEPTH_LIMIT"}]
    if not 1 <= len(rows) <= 1000: errors.append({"field": "/cases", "code": "CASE_COUNT_RANGE"})
    cases, seen = [], set()
    for index, value in enumerate(rows[:1000]):
        location = {"row": index+1, "line": locations[index]}
        try: case = Case.model_validate(value).model_dump()
        except ValidationError as exc:
            errors += [{**location, "field": "/" + "/".join(map(str,e["loc"])), "code": e["type"]} for e in exc.errors()[:20]]
            continue
        try: canonical(case).encode("utf-8")
        except (ValueError, RecursionError):
            errors.append({**location, "field": "/", "code": "NON_FINITE_OR_TOO_DEEP_JSON"}); continue
        if case["case_key"] in seen: errors.append({**location, "case_key": case["case_key"], "field": "/case_key", "code": "DUPLICATE_CASE_KEY"})
        seen.add(case["case_key"])
        if not case["assertions"]: errors.append({**location, "case_key": case["case_key"], "field": "/assertions", "code": "ASSERTIONS_REQUIRED"})
        for spec in specs:
            try: validate_case(spec, case)
            except (ValueError, KeyError, TypeError, IndexError): errors.append({**location, "case_key": case["case_key"], "field": "/assertions", "evaluator": spec.instance_id, "code": "INVALID_ASSERTIONS"})
        cases.append(case)
    if capabilities is not None:
        for spec in specs:
            if set(metadata(spec)["required_capabilities"]) - set(capabilities): errors.append({"field": "/agent/capabilities", "evaluator": spec.instance_id, "code": "CAPABILITY_MISSING"})
    return cases, errors[:100]


def guided_profile(body):
    preset = {"answer": [{"instance_id": "answer", "id": "exact_match"}], "tools": [{"instance_id": "tools", "id": "tool_call"}],
        "trajectory": [{"instance_id": "trajectory", "id": "trajectory"}], "retrieval": [{"instance_id": metric, "id": "rag", "config": {"metric": metric, "k": 5}} for metric in ("recall", "mrr", "ndcg")],
        "state": [{"instance_id": "state", "id": "state_assertion"}]}
    return ProfileCreate(project_id=body.project_id or "new", name="向导评分方案", version="1",
        evaluators=[rule for goal in body.goals for rule in preset[goal]], execution=body.execution, gate=body.gate,
        environment={"adapter_id": body.environment_id} if body.environment_id else None)


def preview_guided(session, settings, body):
    from agenteval.adapters import validate_agent_config
    from agenteval.credentials import validate_reference
    from agenteval.preflight import check_material
    validate_agent_config(body.adapter_type, body.agent.configuration, settings)
    validate_reference(session, settings, body.project_id, body.agent.configuration)
    profile = guided_profile(body)
    project = body.project_id or "new"
    return check_material(settings, Obj(project_id=project, name=body.agent_name, adapter_type=body.adapter_type),
        Obj(id="new", **body.agent.model_dump()), Obj(name="新测试集"), Obj(id="new", version=body.benchmark.version),
        Obj(id="new", name="向导评分方案", version="1", definition=profile.model_dump()),
        [Obj(case_key=c.case_key, definition=c.model_dump()) for c in body.benchmark.cases])
