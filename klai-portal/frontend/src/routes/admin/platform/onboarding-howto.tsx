import { createFileRoute, useNavigate } from '@tanstack/react-router'
import { ArrowLeft } from 'lucide-react'

export const Route = createFileRoute('/admin/platform/onboarding-howto')({
  component: OnboardingHowtoPage,
})

function Step({
  n,
  title,
  children,
}: {
  n: number
  title: string
  children: React.ReactNode
}) {
  return (
    <li className="relative rounded-xl border border-gray-200 bg-white py-5 pl-16 pr-5">
      <span
        className="absolute left-4 top-5 flex h-8 w-8 items-center justify-center rounded-full bg-[var(--color-rl-accent)] font-display-bold text-sm text-[var(--color-rl-dark)]"
        aria-hidden
      >
        {n}
      </span>
      <h3 className="text-[17px] font-display text-gray-900">{title}</h3>
      <div className="mt-1.5 space-y-2 text-sm text-gray-700">{children}</div>
    </li>
  )
}

function Expect({ children }: { children: React.ReactNode }) {
  return (
    <p className="mt-3 rounded-md border-l-[3px] border-[var(--color-success)] bg-[var(--color-success-bg)] px-3 py-2 text-sm text-[var(--color-success-text)]">
      {children}
    </p>
  )
}

function OnboardingHowtoPage() {
  const navigate = useNavigate()

  return (
    <div className="mx-auto max-w-3xl px-6 py-10">
      <div className="mb-2 flex items-center justify-between">
        <h1 className="page-title text-[26px] font-display-bold text-gray-900">
          Een nieuwe tenant onboarden
        </h1>
        <button
          type="button"
          onClick={() => void navigate({ to: '/admin/platform' })}
          className="inline-flex items-center gap-1.5 rounded-full border border-gray-200 bg-white px-3 py-2 text-sm font-medium text-gray-700 klai-hover"
        >
          <ArrowLeft className="h-4 w-4" />
          Terug naar platform
        </button>
      </div>
      <p className="mb-6 text-sm text-gray-400">
        Stap-voor-stap how-to om een klant (tenant) aan te maken en te
        controleren dat alles werkt: provisioning, chat, kennisbank, templates
        en mensen uitnodigen.
      </p>

      {/* Voor je begint */}
      <section className="space-y-2">
        <h2 className="text-[19px] font-display-bold text-gray-900">
          Voor je begint
        </h2>
        <ul className="list-disc space-y-1 pl-5 text-sm text-gray-700">
          <li>
            Je hebt <b>platform-admin</b>-toegang nodig (de tenant-aanmaakknop
            staat alleen daar).
          </li>
          <li>
            Je hebt het <b>e-mailadres van de owner</b> van de nieuwe tenant
            nodig (diegene krijgt de eigenaarsrechten).
          </li>
          <li>
            Het e-mailadres mag nog niet als gebruiker bestaan. Bestaat het al?
            Eerst de oude tenant/gebruiker laten opruimen door een dev.
          </li>
        </ul>
      </section>

      {/* Stappen */}
      <ol className="mt-6 list-none space-y-3.5 p-0">
        <Step n={1} title="Tenant aanmaken">
          <p>
            Ga naar{' '}
            <button
              type="button"
              onClick={() => void navigate({ to: '/admin/platform/new' })}
              className="text-[var(--color-rl-accent-dark)] underline decoration-[var(--color-rl-accent)]/60 underline-offset-2"
            >
              platform-admin &rarr; nieuwe tenant
            </button>{' '}
            en vul in:
          </p>
          <ul className="list-disc space-y-1 pl-5">
            <li>
              <b>Bedrijfsnaam</b> (wordt de organisatie + subdomein, bv.{' '}
              <code className="rounded border border-gray-200 bg-[var(--color-rl-cream)] px-1 py-0.5 font-mono text-xs">
                bedrijf-1234.getklai.com
              </code>
              )
            </li>
            <li>
              <b>Owner e-mail</b>, <b>Voornaam</b>, <b>Achternaam</b>
            </li>
          </ul>
          <p>
            Klik op aanmaken. De provisioning start automatisch: organisatie,
            een org- en personal-kennisbank, de chat-omgeving (LibreChat), het
            AI-team (LiteLLM) en de subdomein-route.
          </p>
          <Expect>
            Verwacht: status gaat naar <b>ready</b> (ca. 10&ndash;30s). De owner
            krijgt automatisch <b>één</b> e-mail: &ldquo;Activeer je
            Klai-account&rdquo;.
          </Expect>
        </Step>

        <Step n={2} title="Owner activeert het account">
          <ul className="list-disc space-y-1 pl-5">
            <li>
              Open de e-mail <b>&ldquo;Activeer je Klai-account&rdquo;</b>.
            </li>
            <li>
              Klik de link en kies een <b>wachtwoord</b> (min. 8 tekens).
            </li>
            <li>
              Log in op{' '}
              <a
                href="https://my.getklai.com"
                target="_blank"
                rel="noreferrer"
                className="text-[var(--color-rl-accent-dark)] underline decoration-[var(--color-rl-accent)]/60 underline-offset-2"
              >
                my.getklai.com
              </a>{' '}
              met e-mail + wachtwoord.
            </li>
          </ul>
          <Expect>
            Verwacht: <b>precies één</b> mail (géén aparte &ldquo;Confirm your
            email&rdquo;), wachtwoord zetten lukt direct, en je komt in de eigen
            tenant terecht.
          </Expect>
        </Step>

        <Step n={3} title="Chat testen">
          <p>
            In de tenant: tab <b>Chat</b> &rarr; kies bron{' '}
            <b>&ldquo;Persoonlijk, Organisatiekennis&rdquo;</b> &rarr; stel een
            vraag.
          </p>
          <Expect>
            Verwacht: de chat antwoordt. Geen &ldquo;An unknown error
            occurred&rdquo;.
          </Expect>
        </Step>

        <Step n={4} title="Kennis toevoegen (kennisbank)">
          <p>
            Tab <b>Knowledge</b> &rarr; open een collectie &rarr;{' '}
            <b>Bron toevoegen</b>. Drie manieren:
          </p>
          <ul className="list-disc space-y-1 pl-5">
            <li>
              <b>Bestand</b> uploaden (PDF, Word, Excel, PowerPoint, Markdown,
              TXT, CSV, JSON, XML, ZIP, TAR)
            </li>
            <li>
              <b>URL</b> van een openbare webpagina plakken
            </li>
            <li>
              <b>Tekst</b> plakken
            </li>
          </ul>
          <p>
            Voor externe bronnen: <b>Koppeling toevoegen</b> &rarr; kies type
            (Google Drive, Notion, Microsoft 365, GitHub, Website, &hellip;) en
            autoriseer.
          </p>
          <Expect>
            Verwacht: na verwerking status <b>Gesynct</b>, en de bron toont z'n{' '}
            <b>echte naam</b> (bv.{' '}
            <code className="rounded border border-gray-200 bg-[var(--color-rl-cream)] px-1 py-0.5 font-mono text-xs">
              CV.pdf
            </code>
            ), niet een{' '}
            <code className="rounded border border-gray-200 bg-[var(--color-rl-cream)] px-1 py-0.5 font-mono text-xs">
              file:sha256:&hellip;
            </code>
            -code.
          </Expect>
        </Step>

        <Step n={5} title="Templates bekijken">
          <p>
            Tab <b>Templates</b> &rarr; bekijk of maak antwoord-templates voor
            de chat.
          </p>
          <Expect>
            Verwacht: de templates-pagina laadt (leeg is normaal bij een nieuwe
            tenant).
          </Expect>
        </Step>

        <Step n={6} title="Mensen uitnodigen">
          <p>
            Als owner/admin: <b>Beheer &rarr; Gebruikers &rarr; Gebruiker
            uitnodigen</b>. Vul e-mail + rol in.
          </p>
          <Expect>
            Verwacht: de uitgenodigde krijgt <b>één</b> &ldquo;Activeer je
            Klai-account&rdquo;-mail en doorloopt dezelfde activatie als de owner
            (stap 2).
          </Expect>
        </Step>
      </ol>

      {/* Rollen */}
      <section className="mt-10 space-y-2">
        <h2 className="text-[19px] font-display-bold text-gray-900">
          Rollen kort
        </h2>
        <table className="w-full table-fixed border-t border-b border-gray-200 text-sm">
          <thead>
            <tr className="border-b border-gray-200">
              <th className="w-28 py-3 pr-4 text-left text-xs font-medium text-gray-400 tracking-wide">
                Rol
              </th>
              <th className="py-3 text-left text-xs font-medium text-gray-400 tracking-wide">
                Mag
              </th>
            </tr>
          </thead>
          <tbody>
            <tr className="border-b border-gray-200">
              <td className="py-4 pr-4 align-top font-medium text-gray-900">
                Admin
              </td>
              <td className="py-4 align-top text-gray-700">
                Alles binnen de tenant: gebruikers uitnodigen, instellingen,
                kennisbanken, templates.
              </td>
            </tr>
            <tr className="border-b border-gray-200">
              <td className="py-4 pr-4 align-top font-medium text-gray-900">
                Company
              </td>
              <td className="py-4 align-top text-gray-700">
                Werken met gedeelde org-kennis + eigen persoonlijke kennisbank.
              </td>
            </tr>
            <tr className="border-b border-gray-200 last:border-b-0">
              <td className="py-4 pr-4 align-top font-medium text-gray-900">
                Personal
              </td>
              <td className="py-4 align-top text-gray-700">
                Eigen persoonlijke kennisbank en chat.
              </td>
            </tr>
          </tbody>
        </table>
      </section>

      {/* Troubleshooting */}
      <section className="mt-10 space-y-2">
        <h2 className="text-[19px] font-display-bold text-gray-900">
          Als er iets misgaat
        </h2>
        <div className="rounded-xl border border-gray-200 border-l-[3px] border-l-[var(--color-warning)] bg-[var(--color-warning-bg)] px-4 py-3">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-gray-200">
                <th className="w-2/5 py-2 pr-4 text-left text-xs font-medium text-gray-500 tracking-wide">
                  Symptoom
                </th>
                <th className="py-2 text-left text-xs font-medium text-gray-500 tracking-wide">
                  Wat te doen
                </th>
              </tr>
            </thead>
            <tbody>
              <tr className="border-b border-gray-200">
                <td className="py-3 pr-4 align-top text-gray-900">
                  Chat zegt &ldquo;An unknown error occurred&rdquo; vlak na
                  aanmaken
                </td>
                <td className="py-3 align-top text-gray-700">
                  Zeldzaam &mdash; de chat-container hoort zichzelf bij
                  provisioning goed op te starten. Blijft het? Laat een dev de
                  tenant-chat-container herstarten.
                </td>
              </tr>
              <tr className="border-b border-gray-200">
                <td className="py-3 pr-4 align-top text-gray-900">
                  Owner krijgt twee mails of &ldquo;Code is invalid / link
                  expired&rdquo;
                </td>
                <td className="py-3 align-top text-gray-700">
                  Hoort niet meer voor te komen. Gebeurt het tóch? Meld het bij
                  een dev (regressie).
                </td>
              </tr>
              <tr className="border-b border-gray-200">
                <td className="py-3 pr-4 align-top text-gray-900">
                  Bron blijft op &ldquo;Bezig&rdquo; staan
                </td>
                <td className="py-3 align-top text-gray-700">
                  Even wachten (PDF-verwerking duurt iets). Langer dan ~5 min?
                  Meld het.
                </td>
              </tr>
              <tr className="last:border-b-0">
                <td className="py-3 pr-4 align-top text-gray-900">
                  Aanmaken faalt op een bestaand e-mailadres
                </td>
                <td className="py-3 align-top text-gray-700">
                  Het adres heeft al een account. Laat de oude tenant/gebruiker
                  eerst opruimen.
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </section>

      <p className="mt-12 border-t border-gray-200 pt-4 text-xs text-gray-400">
        Intern document &middot; Klai platform. Houd dit in lijn met de echte
        UI; werk het bij als labels of stappen wijzigen.
      </p>
    </div>
  )
}
