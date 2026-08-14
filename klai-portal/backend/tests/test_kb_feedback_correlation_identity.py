"""Feedback must be looked up under the identity the retrieval log was written with.

Production evidence 2026-08-14: 55 of 58 knowledge.feedback events had
correlated=false, going back to April. The retrieval log is keyed
rl:{org_id}:{user_id} where retrieval-api supplies the Zitadel subject
(368883971322282015), while the LibreChat feedback patch sent req.user.id --
the Mongo ObjectId (69e13d3f41c7d65c4c70cd2b). Two different namespaces, so the
lookup could never hit.

The data was there the whole time: for the 12:56:54 message, a retrieval entry
sat at epoch 1786712209.8, four seconds earlier and well inside the
[msg-60s, msg+10s] window, carrying 20 chunk_ids.

LibreChat stores the Zitadel subject on the user document as `openidId`, so the
patch can send both and the correlation can prefer the one that matches.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.api.internal import KbFeedbackIn, _correlation_user_id

ZITADEL = "368883971322282015"
MONGO = "69e13d3f41c7d65c4c70cd2b"


def _body(**kw) -> KbFeedbackIn:
    return KbFeedbackIn(
        conversation_id="c1",
        message_id="m1",
        message_created_at=datetime.now(UTC),
        rating="thumbsUp",
        librechat_tenant_id="librechat-voys",
        **kw,
    )


def test_prefers_the_identity_id_when_present():
    body = _body(librechat_user_id=MONGO, identity_user_id=ZITADEL)
    assert _correlation_user_id(body) == ZITADEL


def test_falls_back_to_the_librechat_id_when_absent():
    # Older LibreChat images do not send identity_user_id. Falling back keeps
    # their behaviour unchanged instead of erroring during a staged rollout.
    body = _body(librechat_user_id=MONGO)
    assert _correlation_user_id(body) == MONGO


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_blank_identity_id_falls_back(empty):
    # An empty string is not a usable key; `or` alone would already do this,
    # but whitespace would sneak through and produce rl:8:   .
    body = _body(librechat_user_id=MONGO, identity_user_id=empty)
    assert _correlation_user_id(body) == MONGO


def test_identity_id_is_optional_on_the_contract():
    # The field must stay optional: the LiteLLM hook and partner paths post to
    # this endpoint too and do not know about openidId.
    assert KbFeedbackIn.model_fields["identity_user_id"].default is None
