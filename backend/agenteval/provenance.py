"""Platform-owned provenance. Instrumentation records are not independent business probes."""
def describe(response, environment=None):
    observations = (response or {}).get("observations", {})
    if not isinstance(observations, dict):
        observations = {}
    return {"schema_version": "1", "output": {"source": "agent_self_report", "independent": False},
        "observations": {key: {"source": "agent_self_report", "independent": False} for key in observations},
        "trace": {"source": "execution_wrapper_and_agent_instrumentation", "independent_business_state": False},
        "environment": {"source": "platform_environment_adapter", "independent": True,
                        "status": (environment or {}).get("status", "unavailable")}}
