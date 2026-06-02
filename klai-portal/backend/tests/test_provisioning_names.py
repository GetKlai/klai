import pytest


def test_deepblue_slug_stays_readable_and_valid() -> None:
    from app.core.provisioning_names import validate_slug_for_provisioning

    names = validate_slug_for_provisioning("deepblue-security-intelligence-37491434", domain="getklai.com")

    assert names.chat_label == "chat-deepblue-security-intelligence-37491434"
    assert names.librechat_container == "librechat-deepblue-security-intelligence-37491434"
    assert names.mongodb_database == names.librechat_container


def test_slug_at_chat_dns_label_limit_passes() -> None:
    from app.core.provisioning_names import DNS_LABEL_MAX_LENGTH, TENANT_SLUG_MAX_LENGTH, validate_slug_for_provisioning

    slug = "a" * TENANT_SLUG_MAX_LENGTH
    names = validate_slug_for_provisioning(slug, domain="getklai.com")

    assert len(names.chat_label) == DNS_LABEL_MAX_LENGTH


def test_slug_beyond_chat_dns_label_limit_fails() -> None:
    from app.core.provisioning_names import ProvisioningNameError, TENANT_SLUG_MAX_LENGTH, validate_slug_for_provisioning

    slug = "a" * (TENANT_SLUG_MAX_LENGTH + 1)

    with pytest.raises(ProvisioningNameError, match="LibreChat DNS label"):
        validate_slug_for_provisioning(slug, domain="getklai.com")


@pytest.mark.parametrize(
    "import_path",
    [
        "app.api.signup",
        "app.api.admin.platform_manage",
    ],
)
def test_to_slug_preserves_zitadel_suffix_inside_dns_label_limit(import_path: str) -> None:
    from importlib import import_module

    from app.core.provisioning_names import TENANT_SLUG_MAX_LENGTH

    module = import_module(import_path)
    slug = module._to_slug("A" * 120, "375686912108658705")

    assert len(slug) == TENANT_SLUG_MAX_LENGTH
    assert slug.endswith("-37568691")
