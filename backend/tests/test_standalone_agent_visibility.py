from types import SimpleNamespace
import pytest
from fastapi import HTTPException
from app.services.agent_visibility import require_agent_access


def test_only_active_account_can_use_subcontract_agent():
    user = SimpleNamespace(id=1, is_active=True)
    require_agent_access(SimpleNamespace(slug='blockchain-supervision'), user, None)
    with pytest.raises(HTTPException):
        require_agent_access(SimpleNamespace(slug='other-agent'), user, None)
    with pytest.raises(HTTPException):
        require_agent_access(SimpleNamespace(slug='blockchain-supervision'), SimpleNamespace(id=1, is_active=False), None)
