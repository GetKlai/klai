"""The service answers on /retrieve and /health only, and accepts only fields it reads.

A route or request field that nothing calls or reads still has to be secured,
documented and kept working; adding one back is a deliberate change to this list.
"""

from retrieval_api.main import app
from retrieval_api.models import RetrieveRequest


def test_service_exposes_only_retrieve_and_health():
    assert sorted(app.openapi()["paths"]) == ["/health", "/retrieve"]


def test_retrieve_request_has_no_strict_mode_field():
    # The answer mode (strict or open) is decided by the caller's prompt; retrieval
    # ranks the same way in both modes, so the request does not carry it.
    assert "kb_narrow" not in RetrieveRequest.model_fields
