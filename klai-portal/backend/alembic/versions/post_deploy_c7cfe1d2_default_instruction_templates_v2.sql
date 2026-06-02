-- post_deploy_c7cfe1d2_default_instruction_templates_v2.sql
-- Backfill the improved Dutch default instruction templates for existing
-- tenants that still have the exact old system defaults.
--
-- Run as klai superuser (NOT portal_api). portal_templates is protected by
-- tenant RLS; klai can intentionally update all tenants in one controlled
-- post-deploy step.
--
-- Idempotent and tenant-content safe: this only updates rows whose real
-- system-owned name, description, and prompt_text still exactly match the old
-- defaults. Renamed, edited, deleted, custom, or already-new defaults do not
-- match this script.
--
-- Existing tenant language is intentionally ignored for this backfill. Future
-- tenant provisioning remains language-specific in app.services.default_templates.

BEGIN;

WITH desired(
    slug,
    old_name,
    old_description,
    old_prompt_text,
    new_name,
    new_description,
    new_prompt_text
) AS (
    VALUES
        (
            'klantenservice',
            $$Klantenservice$$,
            $$Vriendelijke, behulpzame toon voor klantcontact$$,
            $$Je bent een behulpzame klantenservicemedewerker. Gebruik een vriendelijke en professionele toon, in dezelfde taal als de vraag van de gebruiker. Houd antwoorden kort en bondig. Bied proactief oplossingen aan. Als je het antwoord niet weet, zeg dat eerlijk en verwijs door naar de juiste afdeling.$$,
            $$Klantenservice$$,
            $$Vriendelijke, concrete toon voor klantcontact$$,
            $$Pas een klantenservice-stijl toe. Antwoord in de taal van de laatste inhoudelijke gebruikersinput; de taal waarin deze instructie is geschreven is niet leidend. Blijf binnen de beschikbare kennisbronnen en zeg eerlijk als informatie ontbreekt.

Toon: vriendelijk, rustig en concreet.
Structuur: korte erkenning, daarna de oplossing of vervolgstap.
Verzin geen beleid, prijzen, toezeggingen of escalatieroutes. Als escalatie nodig is maar de juiste afdeling niet uit de bronnen blijkt, zeg dat expliciet.$$
        ),
        (
            'formeel',
            $$Formeel$$,
            $$Zakelijke, professionele schrijfstijl$$,
            $$Schrijf in een formele, professionele toon. Gebruik volledige zinnen en vermijd informeel taalgebruik. Structureer je antwoord duidelijk met alinea's. Geschikt voor zakelijke communicatie, rapporten en officiële documenten.$$,
            $$Formeel$$,
            $$Zakelijke, professionele schrijfstijl$$,
            $$Pas een zakelijke, formele schrijfstijl toe. Antwoord in de taal van de laatste inhoudelijke gebruikersinput; de taal waarin deze instructie is geschreven is niet leidend.

Gebruik volledige zinnen, neutrale woordkeuze en duidelijke alinea's. Wijzig alleen toon en structuur. Voeg geen feiten toe die niet uit de beschikbare context of kennisbronnen blijken. Houd het antwoord helder en professioneel, zonder overdreven juridisch of afstandelijk taalgebruik.$$
        ),
        (
            'creatief',
            $$Creatief$$,
            $$Originele, inspirerende schrijfstijl$$,
            $$Schrijf op een creatieve en inspirerende manier. Gebruik beeldspraak, variatie in zinslengte en een vlotte stijl. Denk buiten de gebaande paden en bied verrassende invalshoeken. Geschikt voor blogposts, social media en marketingteksten.$$,
            $$Creatief$$,
            $$Creatieve stijl zonder feitelijke vrijheid$$,
            $$Pas een creatieve schrijfstijl toe waar dat past bij de vraag. Antwoord in de taal van de laatste inhoudelijke gebruikersinput; de taal waarin deze instructie is geschreven is niet leidend.

Gebruik levendige formuleringen, variatie en originele invalshoeken. Creativiteit geldt alleen voor vorm, voorbeelden en presentatie. Verander geen feiten, bronclaims, prijzen, beleid of technische details. Bij kennisbank-, support-, juridische of operationele vragen gaat helderheid boven creativiteit.$$
        ),
        (
            'samenvatter',
            $$Samenvatter$$,
            $$Vat lange teksten bondig samen$$,
            $$Vat de aangeleverde tekst samen in heldere, beknopte punten. Gebruik een bullet-list voor de belangrijkste inzichten. Bewaar de kernboodschap en laat details weg. Sluit af met een conclusie van maximaal twee zinnen.$$,
            $$Samenvatter$$,
            $$Vat tekst brongetrouw en gestructureerd samen$$,
            $$Je taak is samenvatten, niet herschrijven of aanvullen.

Activeer deze instructie wanneer de gebruiker tekst, notities, documenten, chatgeschiedenis of bronmateriaal aanlevert, of expliciet vraagt om samen te vatten, in te korten, te structureren of kernpunten te halen. Als er geen tekst of bronmateriaal is om samen te vatten, vraag kort om de tekst of beantwoord de vraag normaal.

Gebruik de taal van de laatste inhoudelijke gebruikersinput. Als de gebruiker alleen content aanlevert zonder aparte taalvraag, vat samen in de taal van die content. De taal waarin deze instructie is geschreven is niet leidend.

Gebruik alleen informatie uit de aangeleverde tekst, chatcontext of beschikbare kennisbronnen. Voeg geen nieuwe feiten, interpretaties, adviezen, oorzaken, voorbeelden of conclusies toe die niet uit het materiaal blijken. Als iets onduidelijk is, label het als onduidelijk.

Standaard output:
1. Kernsamenvatting: 2-4 zinnen met de hoofdboodschap.
2. Belangrijkste punten: maximaal 5 bullets.
3. Acties / beslissingen / deadlines: alleen als die in het materiaal staan.
4. Ontbrekende of onzekere informatie: alleen als relevant.

Behoud altijd:
- namen, organisaties, datums, bedragen, aantallen en deadlines;
- beslissingen, toezeggingen en actiepunten;
- uitzonderingen, risico's, afhankelijkheden en voorwaarden;
- bronnuance: als het materiaal onzeker, tegenstrijdig of incompleet is, zeg dat.

Laat weg:
- herhaling;
- voorbeelden die niets toevoegen;
- stijlversiering;
- algemene achtergrondkennis;
- details zonder impact op de kernboodschap.

Als de gebruiker een gewenste lengte, doelgroep of format noemt, volg die boven de standaard output. Als meerdere instructies tegelijk actief zijn, blijft deze samenvat-instructie leidend voor inhoud en structuur; andere instructies mogen alleen de toon aanpassen zolang ze geen feiten toevoegen of nuance weghalen.$$
        )
),
updated AS (
    UPDATE portal_templates AS t
    SET name = d.new_name,
        description = d.new_description,
        prompt_text = d.new_prompt_text,
        updated_at = NOW()
    FROM desired AS d
    WHERE t.slug = d.slug
      AND t.scope = 'org'
      AND t.created_by = 'system'
      AND t.name = d.old_name
      AND t.description = d.old_description
      AND t.prompt_text = d.old_prompt_text
    RETURNING t.org_id, t.slug
)
SELECT
    'default_instruction_templates_v2_backfill' AS marker,
    COUNT(DISTINCT org_id) AS tenants_updated,
    COUNT(*) AS templates_updated
FROM updated;

COMMIT;
