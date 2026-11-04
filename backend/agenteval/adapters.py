import asyncio
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from agenteval.plugins import GRAPH_PLUGINS, PLUGINS

MAX_OUTPUT_BYTES = 1_048_576


class AgentFailure(Exception):
    def __init__(self, message, response=None, code="AGENT_PROTOCOL_OR_EXECUTION_ERROR"):
        super().__init__(message)
        self.response = response
        self.code = code


def validate_agent_config(adapter_type, configuration, settings):
    if adapter_type in ("python", "langgraph", "component"):
        registry = PLUGINS
        if adapter_type == "langgraph":
            # Do not import the framework in every API / ordinary Python process.
            registry = GRAPH_PLUGINS
        if adapter_type == "component":
            from agenteval.components import COMPONENTS
            registry = COMPONENTS
        if set(configuration) != {"plugin_id"} or configuration["plugin_id"] not in registry:
            raise ValueError("Only deployment-registered plugin_id values are supported")
    else:
        if "endpoint" not in configuration or set(configuration) - {"endpoint", "credential_version_id"}:
            raise ValueError("HTTP accepts endpoint and optional credential_version_id; inline secrets are forbidden")
        if "credential_version_id" in configuration and (not isinstance(configuration["credential_version_id"], str) or not configuration["credential_version_id"]):
            raise ValueError("credential_version_id must be a registered immutable reference")
        validate_url(configuration["endpoint"], settings)


def validate_url(url, settings):
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("An HTTP(S) endpoint without embedded credentials is required")
    if parsed.query or parsed.fragment:
        raise ValueError("Query strings and fragments are not allowed in registered endpoints")
    address = f"{parsed.hostname.lower()}:{parsed.port or (443 if parsed.scheme == 'https' else 80)}"
    if address not in settings.http_allowlist:
        raise ValueError("Endpoint is not in AGENTEVAL_HTTP_ALLOWLIST")


async def bounded_read(stream):
    data = bytearray()
    while chunk := await stream.read(65536):
        data.extend(chunk)
        if len(data) > MAX_OUTPUT_BYTES:
            raise AgentFailure("Agent output exceeded 1 MiB")
    return bytes(data)


def plugin_environment(settings):
    required = {"PATH", "SystemRoot", "WINDIR", "TEMP", "TMP", "TMPDIR", "HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "SYSTEMDRIVE",
        "COMSPEC", "PATHEXT", "LANG", "LC_ALL", "LC_CTYPE", "TZ", "SSL_CERT_FILE", "SSL_CERT_DIR"}
    permitted = required | {"AGENTEVAL_RETRIEVER_ENDPOINT", "AGENTEVAL_RERANKER_ENDPOINT", "AGENTEVAL_HTTP_ALLOWLIST"} | {key for key in settings.plugin_env_allowlist if not key.upper().startswith("AGENTEVAL_")}
    normalize = str.upper if os.name == "nt" else lambda key: key
    permitted = {normalize(key) for key in permitted}
    return {**{key: value for key,value in os.environ.items() if normalize(key) in permitted},
        "PYTHONPATH": str(Path(__file__).resolve().parents[1]), "AGENTEVAL_STREAM_TRACE": "1"}


async def invoke(adapter_type, configuration, request, timeout, settings, trace_buffer=None, headers=None):
    validate_agent_config(adapter_type, configuration, settings)
    if adapter_type in ("python", "langgraph", "component"):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "agenteval.plugin_host",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, limit=262144,
            env=plugin_environment(settings),
        )
        async def collect():
            try:
                while line := await process.stderr.readline():
                    if trace_buffer is not None and len(trace_buffer) <= 1000:
                        try:
                            row = json.loads(line).get("agenteval_span")
                            if isinstance(row, dict) and "span_id" in row:
                                trace_buffer[row["span_id"]] = row
                        except (ValueError, AttributeError):
                            pass
            except (ValueError, OSError):
                pass
        collecting = asyncio.create_task(collect())
        try:
            async with asyncio.timeout(timeout):
                process.stdin.write(json.dumps({"adapter_type": adapter_type, "plugin_id": configuration["plugin_id"], "request": request}).encode())
                await process.stdin.drain()
                process.stdin.close()
                raw = await bounded_read(process.stdout)
                await process.wait()
                if process.returncode:
                    raise AgentFailure("Trusted Python plugin failed")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()
            try:
                await asyncio.wait_for(collecting, .5)
            except TimeoutError:
                collecting.cancel()
    else:
        if configuration.get("credential_version_id") and not headers:
            raise AgentFailure("Credential reference was not resolved", code="CREDENTIAL_UNAVAILABLE")
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            async with client.stream("POST", configuration["endpoint"], json=request, headers=headers or {}) as response:
                if response.status_code != 200:
                    code = "HTTP_AUTH_REJECTED" if response.status_code == 401 else "HTTP_PERMISSION_DENIED" if response.status_code == 403 else "HTTP_REDIRECT_REJECTED" if 300 <= response.status_code < 400 else "HTTP_AGENT_STATUS_ERROR"
                    raise AgentFailure("HTTP Agent rejected the request", code=code)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_OUTPUT_BYTES:
                        raise AgentFailure("Agent output exceeded 1 MiB")
                raw = bytes(data)
    try:
        response = json.loads(raw)
        if headers:
            from agenteval.credentials import contains_secret
            if contains_secret(response, headers):
                raise AgentFailure("Agent echoed a credential; result discarded", code="HTTP_SENSITIVE_RESPONSE_REJECTED")
        if not isinstance(response, dict) or "output" not in response:
            raise AgentFailure("Missing output", code="HTTP_PROTOCOL_ERROR" if adapter_type == "http" else "AGENT_PROTOCOL_OR_EXECUTION_ERROR")
        # Reject NaN/Infinity even if the remote serializer accepts them.
        json.dumps(response, allow_nan=False)
        if response.get("status") == "agent_failed":
            raise AgentFailure("Agent reported business failure", response, code="HTTP_BUSINESS_FAILURE" if adapter_type == "http" else "AGENT_PROTOCOL_OR_EXECUTION_ERROR")
        return response
    except (ValueError, TypeError, UnicodeError) as exc:
        raise AgentFailure("Agent returned an invalid JSON result", code="HTTP_INVALID_JSON" if adapter_type == "http" else "AGENT_PROTOCOL_OR_EXECUTION_ERROR") from None

