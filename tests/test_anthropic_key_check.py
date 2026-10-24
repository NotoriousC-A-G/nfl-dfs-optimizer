"""check_anthropic_key: a rejected key must raise (loudly, up front); absent or unverifiable keys must not."""

from __future__ import annotations

import pytest
import requests

from nfl_dfs.analysis.circumstance.engine import AnthropicKeyError, check_anthropic_key


class _Resp:
    def __init__(self, code):
        self.status_code = code


class _Session:
    def __init__(self, code=None, exc=None):
        self.code, self.exc = code, exc

    def get(self, *a, **k):
        if self.exc:
            raise self.exc
        return _Resp(self.code)


def test_missing_key_is_reported_not_raised():
    assert check_anthropic_key(None) == "missing"
    assert check_anthropic_key("") == "missing"


def test_valid_key_is_ok():
    assert check_anthropic_key("k", session=_Session(200)) == "ok"


@pytest.mark.parametrize("code", [401, 403])
def test_rejected_key_raises(code):
    with pytest.raises(AnthropicKeyError):
        check_anthropic_key("k", session=_Session(code))


def test_network_failure_is_unreachable_not_a_verdict():
    assert check_anthropic_key("k", session=_Session(exc=requests.ConnectionError())) == "unreachable"
