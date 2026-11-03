"""OTel SDK recording plus a bounded, validated storage contract."""
from contextlib import contextmanager
from contextvars import ContextVar
import json
import os
import re
import sys
import threading
import time

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

active_recorder = ContextVar("agenteval_recorder", default=None)


def started_span(span):
    context = span.get_span_context()
    return {"trace_id": f"{context.trace_id:032x}", "span_id": f"{context.span_id:016x}",
            "parent_span_id": f"{span.parent.span_id:016x}" if span.parent else None,
            "name": span.name, "kind": span.attributes.get("agenteval.kind", "internal"),
            "start_ns": span.start_time, "end_ns": span.start_time, "duration_ms": 0,
            "status": "interrupted", "attributes": {"span.open": True}, "events": []}


class IPCProcessor(SpanProcessor):
    """Send starts and completions while the plugin is still running."""
    def __init__(self):
        self.lock = threading.Lock()

    def send(self, row):
        with self.lock:
            sys.stderr.write(json.dumps({"agenteval_span": row}, ensure_ascii=True) + "\n")
            sys.stderr.flush()

    def on_start(self, span, parent_context=None):
        self.send(started_span(span))

    def on_end(self, span):
        self.send(serialize_span(span))


def serialize_span(span):
    return {"trace_id": f"{span.context.trace_id:032x}", "span_id": f"{span.context.span_id:016x}",
            "parent_span_id": f"{span.parent.span_id:016x}" if span.parent else None,
            "name": span.name, "kind": span.attributes.get("agenteval.kind", "internal"),
            "start_ns": span.start_time, "end_ns": span.end_time,
            "duration_ms": (span.end_time - span.start_time) / 1_000_000,
            "status": "error" if span.status.status_code == trace.StatusCode.ERROR else "ok",
            "dropped_events": getattr(span, "dropped_events", 0), "attributes": dict(span.attributes),
            "events": [{"name": e.name, "time_ns": e.timestamp, "attributes": dict(e.attributes)} for e in span.events]}


class Recorder:
    def __init__(self):
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider(shutdown_on_exit=False)
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        if os.getenv("AGENTEVAL_STREAM_TRACE") == "1":
            self.provider.add_span_processor(IPCProcessor())
        self.tracer = self.provider.get_tracer("agenteval", "0.4.0")
        self.calls = []

    @contextmanager
    def span(self, name, kind, input=None, traceparent=None):
        context = TraceContextTextMapPropagator().extract({"traceparent": traceparent}) if traceparent else None
        token = active_recorder.set(self)
        try:
            with self.tracer.start_as_current_span(name, context=context, attributes={"agenteval.kind": kind}) as span:
                if input is not None:
                    raw = json.dumps(input, ensure_ascii=False)
                    span.add_event("input", {"value": raw[:48000], "truncated": len(raw) > 48000})
                    if len(raw) > 48000:
                        span.set_attribute("data.truncated", True)
                try:
                    yield span
                except BaseException as exc:
                    span.set_status(trace.Status(trace.StatusCode.ERROR, type(exc).__name__))
                    span.add_event("operation.error", {"error.type": type(exc).__name__})
                    raise
        finally:
            active_recorder.reset(token)

    def traceparent(self):
        carrier = {}
        TraceContextTextMapPropagator().inject(carrier)
        return carrier["traceparent"]

    def records(self):
        rows = []
        for span in self.exporter.get_finished_spans():
            rows.append({"trace_id": f"{span.context.trace_id:032x}", "span_id": f"{span.context.span_id:016x}",
                         "parent_span_id": f"{span.parent.span_id:016x}" if span.parent else None,
                         "name": span.name, "kind": span.attributes.get("agenteval.kind", "internal"),
                         "start_ns": span.start_time, "end_ns": span.end_time,
                         "duration_ms": (span.end_time - span.start_time) / 1_000_000,
                         "status": "error" if span.status.status_code == trace.StatusCode.ERROR else "ok",
                         "dropped_events": getattr(span, "dropped_events", 0),
                         "attributes": dict(span.attributes), "events": [
                             {"name": event.name, "time_ns": event.timestamp, "attributes": dict(event.attributes)} for event in span.events]})
        return rows


def record_tool(name, arguments, operation, call_id):
    recorder = active_recorder.get()
    if recorder is None:
        return operation(**arguments), None
    with recorder.span(name, "tool", arguments) as span:
        span.set_attribute("tool.call_id", call_id)
        started = time.time_ns()
        call = {"call_id": call_id, "name": name, "arguments": arguments,
                "span_id": f"{span.get_span_context().span_id:016x}", "start_ns": started}
        try:
            output = operation(**arguments)
            call.update(output=output, status="ok")
            event("output", output)
            return output, call
        except Exception as exc:
            call.update(status="error", error=type(exc).__name__)
            raise
        finally:
            call["end_ns"] = time.time_ns()
            recorder.calls.append(call)


def event(name, value):
    if active_recorder.get():
        raw = json.dumps(value, ensure_ascii=False)
        span = trace.get_current_span()
        span.add_event(name, {"value": raw[:48000], "truncated": len(raw) > 48000})
        if len(raw) > 48000:
            span.set_attribute("data.truncated", True)


def validated(rows, trace_id, external_parent=None):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("Trace must contain at most 1000 spans")
    ids = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Span must be an object")
        if row.get("trace_id") != trace_id or not re.fullmatch(r"[0-9a-f]{16}", row.get("span_id", "")):
            raise ValueError("Invalid trace/span identity")
        if row["span_id"] in ids or type(row.get("start_ns")) is not int or type(row.get("end_ns")) is not int or not 0 <= row["start_ns"] <= row["end_ns"] < 2 ** 63:
            raise ValueError("Invalid span duration or duplicate identity")
        parent = row.get("parent_span_id")
        if parent is not None and not re.fullmatch(r"[0-9a-f]{16}", parent):
            raise ValueError("Invalid parent identity")
        if not isinstance(row.get("events", []), list) or not isinstance(row.get("attributes", {}), dict):
            raise ValueError("Invalid span event/attribute shape")
        for event in row.get("events", []):
            if not isinstance(event, dict) or not isinstance(event.get("name"), str) or not isinstance(event.get("attributes", {}), dict) or type(event.get("time_ns")) is not int:
                raise ValueError("Invalid event")
        row.setdefault("events", [])
        row.setdefault("attributes", {})
        row.setdefault("name", "external")
        row.setdefault("kind", "internal")
        row.setdefault("status", "unknown")
        if any(not isinstance(row[key], str) for key in ("name", "kind", "status")):
            raise ValueError("Invalid span label")
        row["duration_ms"] = (row["end_ns"] - row["start_ns"]) / 1_000_000
        ids.add(row["span_id"])
    parents = {r["span_id"]: r.get("parent_span_id") for r in rows}
    for key in parents:
        seen, node = set(), key
        while node in parents:
            if node in seen:
                raise ValueError("Cyclic trace")
            seen.add(node)
            node = parents[node]
        if node is not None and node != external_parent:
            raise ValueError("Orphan span")
    return sorted(rows, key=lambda r: (r["start_ns"], r["span_id"]))


async def invoke_traced(adapter, configuration, request, timeout, settings, capture):
    from agenteval.adapters import invoke, AgentFailure
    recorder = Recorder()
    response = None
    class StreamBuffer(dict):
        def __setitem__(self, key, value):
            super().__setitem__(key, value)
            callback = capture.get("on_span")
            if callback:
                try:
                    callback(value)
                except Exception:
                    capture["storage_warning"] = True
    streamed = StreamBuffer()
    try:
        with recorder.span("execution.invoke", "execution", request["input"]) as root_span:
            request["traceparent"] = recorder.traceparent()
            callback = capture.get("on_span")
            if callback:
                callback(started_span(root_span))
            try:
                response = await invoke(adapter, configuration, request, timeout, settings, streamed)
            except AgentFailure as exc:
                response = exc.response
                raise
            event("final.output", response["output"])
            if response.get("usage"):
                event("token.usage", response["usage"])
            return response
    finally:
        roots = recorder.records()
        if roots:
            root = roots[-1]
            capture["trace_id"] = root["trace_id"]
            try:
                received = list(streamed.values())
                supplied = response.get("trace_spans", []) if response else []
                if not isinstance(supplied, list):
                    raise ValueError("Invalid external trace")
                child = list({r["span_id"]: r for r in received + supplied}.values())
                capture["spans"] = validated(child + roots, root["trace_id"])
                capture["completeness"] = "instrumented" if child else "wrapper_only"
                if any(r.get("dropped_events") or r.get("attributes", {}).get("data.truncated") or r.get("attributes", {}).get("span.open") for r in capture["spans"]):
                    capture["completeness"] = "partial"
            except (ValueError, TypeError, KeyError):
                capture["spans"], capture["completeness"] = roots, "invalid"


def persist_trace(session, execution_id, rows):
    from sqlalchemy import select
    from agenteval.db import TraceSpan
    existing = {s.span_id: s for s in session.scalars(select(TraceSpan).filter_by(execution_id=execution_id))}
    for row in rows:
        previous = existing.get(row["span_id"])
        if previous is None:
            session.add(TraceSpan(execution_id=execution_id, trace_id=row["trace_id"], span_id=row["span_id"],
                                 parent_span_id=row.get("parent_span_id"), record=row))
        elif previous.record.get("attributes", {}).get("span.open") and not row.get("attributes", {}).get("span.open"):
            previous.record = row
