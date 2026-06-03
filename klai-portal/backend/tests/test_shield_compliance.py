"""Shield deterministic compliance checks."""


def test_email_blocks_prompt():
    from app.services.shield_compliance import check_compliance

    result = check_compliance("Mail de offerte naar jane@example.com")
    assert result["status"] == "red"
    assert result["should_block"] is True
    assert any(w["id"] == "privacy-email" for w in result["warnings"])


def test_bsn_elfproef_blocks_prompt():
    from app.services.shield_compliance import check_compliance

    result = check_compliance("Controleer BSN 123456782 voor deze klant.")
    assert result["status"] == "red"
    assert result["should_block"] is True
    assert any(w["id"] == "privacy-bsn" for w in result["warnings"])


def test_transparency_warning_does_not_block():
    from app.services.shield_compliance import check_compliance

    result = check_compliance("We plaatsen een chatbot op de website.")
    assert result["status"] == "yellow"
    assert result["should_block"] is False
    assert result["should_warn"] is True


def test_extended_level_flags_high_risk_domain():
    from app.services.shield_compliance import check_compliance

    result = check_compliance("Gebruik AI voor kredietwaardigheid van klanten.", level="extended")
    assert result["status"] == "orange"
    assert result["should_block"] is False
    assert any(w["id"] == "ai-act-high-risk" for w in result["warnings"])


def test_strict_level_blocks_social_scoring():
    from app.services.shield_compliance import check_compliance

    result = check_compliance("Maak een social scoring profiel voor burgers.", level="strict")
    assert result["status"] == "red"
    assert result["should_block"] is True
    assert any(w["id"] == "ai-act-prohibited-social-scoring" for w in result["warnings"])


def test_clean_prompt_is_green():
    from app.services.shield_compliance import check_compliance

    result = check_compliance("Vat de openbare productdocumentatie samen.")
    assert result["status"] == "green"
    assert result["risk_score"] == 0
    assert result["warnings"] == []
