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
    pass


def validate_agent_config(adapter_type, configuration, settings):
    if adapter_type in ("python", "langgraph"):
        registry = PLUGINS
        if adapter_type == "langgraph":
            # Do not import the framework in every API / ordinary Python process.
            registry = GRAPH_PLUGINS
        if set(configuration) != {"plugin_id"} or configuration["plugin_id"] not in registry:
            raise ValueError("Only deployment-registered plugin_id values are supported")
    else:
        if set(configuration) != {"endpoint"}:
            raise ValueError("HTTP configuration supports endpoint only; inline credentials are forbidden")
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


async def invoke(adapter_type, configuration, request, timeout, settings):
    validate_agent_config(adapter_type, configuration, settings)
    if adapter_type in ("python", "langgraph"):
        process = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "agenteval.plugin_host",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        )
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
    else:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False) as client:
            async with client.stream("POST", configuration["endpoint"], json=request) as response:
                if response.status_code != 200:
                    raise AgentFailure(f"HTTP agent returned status {response.status_code}")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_OUTPUT_BYTES:
                        raise AgentFailure("Agent output exceeded 1 MiB")
                raw = bytes(data)
    try:
        response = json.loads(raw)
        if not isinstance(response, dict) or "output" not in response:
            raise ValueError("Missing output")
        # Reject NaN/Infinity even if the remote serializer accepts them.
        json.dumps(response, allow_nan=False)
        return response
    except (ValueError, TypeError, UnicodeError) as exc:
        raise AgentFailure("Agent returned an invalid JSON result") from exc

