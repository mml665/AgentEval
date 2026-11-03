import pytest

from agenteval.contracts import pointer
from agenteval.provenance import describe


@pytest.mark.parametrize("path", ["/items/-1", "/items/+1", "/items/01", "/items/1.0", "/items/-", "/items/~2", "/items/~"])
def test_json_pointer_rejects_invalid_array_indices_and_escapes(path):
    with pytest.raises((ValueError, KeyError, IndexError, TypeError)):
        pointer({"items": ["first", "second"]}, path)


def test_json_pointer_accepts_rfc6901_escapes_and_canonical_array_indices():
    payload = {"a/b": {"~key": [11, 22]}}
    assert pointer(payload, "/a~1b/~0key/0") == 11
    assert pointer(payload, "/a~1b/~0key/1") == 22


def test_environment_provenance_requires_observed_hashed_snapshot():
    for evidence in (None, {}, {"status": "unavailable"}, {"status": "observed"}, {"status": "observed", "state_hash": None}):
        assert describe({"output": "ok"}, evidence)["environment"]["independent"] is False
    observed = {"status": "observed", "state_hash": "sha256", "state": {"done": True}}
    assert describe({"output": "ok"}, observed)["environment"]["independent"] is True
