# SPEC-UMAMI-WEBSITE: Umami Website Analytics

---
id: SPEC-UMAMI-WEBSITE
title: Umami Website Analytics for Klai Website
created: 2026-03-24
status: Planned
priority: High
lifecycle: spec-anchored
assigned: expert-devops, expert-frontend
related: klai-website
---

## Environment

- **Server:** public-01 (Hetzner, EU, 15 GB RAM, 8 vCPU, 275 GB disk free)
- **Container management:** Coolify (already running on public-01)
- **Existing services on public-01:** Twenty CRM, Fider, Uptime Kuma
- **Existing database:** PostgreSQL instance managed by Coolify
- **Website framework:** Astro 5 (TypeScript, Tailwind CSS v4)
- **Website languages:** Dutch (nl) + English (en)
- **Website deploy:** Coolify on public-01
- **Domain DNS:** Cloud86 (ns1/ns2.cloud86.nl, ns3.cloud86.eu)
- **Analytics tool:** Umami (MIT license, open-source)
- **Docker image:** `docker.umami.is/umami-software/umami:postgresql-latest`

## Assumptions

- A1: The existing Coolify-managed PostgreSQL instance has capacity for an additional `umami` database. Umami's storage footprint for a single marketing website is minimal (~50 MB/month).
- A2: Coolify can deploy the Umami Docker image and manage reverse proxy + HTTPS for a `analytics.getklai.com` subdomain using its built-in SSL provisioning.
- A3: The klai-website is still in active development. Some pages (blog, docs, company) may have limited content. The SPEC defines tracking for all known pages and conversion points, with the understanding that event tracking attributes will be added as pages are built.
- A4: Umami operates in cookieless mode by default. No consent banner or cookie policy update is required.
- A5: The Umami admin interface will be accessible only to the Klai team. No public access to the dashboard.

## Requirements

### R1: Umami Deployment

**R1.1** The system shall deploy Umami as a Docker container on public-01 via Coolify.

**R1.2** The system shall use the existing Coolify-managed PostgreSQL instance with a dedicated `umami` database.

**R1.3** WHEN Umami is first deployed, THEN the default admin credentials (admin/umami) shall be changed immediately.

**R1.4** The system shall expose Umami at `https://analytics.getklai.com` with HTTPS via Coolify's automatic SSL provisioning.

**R1.5** The system shall configure a DNS A record for `analytics.getklai.com` pointing to public-01's IP address via Cloud86.

**R1.6** The system shall set the `APP_SECRET` environment variable to a cryptographically random value.

### R2: Tracker Script Installation

**R2.1** The system shall include the Umami tracking script in the website's `Base.astro` layout so it loads on every page.

**R2.2** The tracking script shall use the following configuration:
- `defer` attribute for non-blocking load
- `data-website-id` set to the Umami-generated website ID
- `data-domains` restricted to `getklai.com` (prevents tracking on localhost/staging)
- `data-performance` enabled for Core Web Vitals collection

**R2.3** The tracking script shall NOT be loaded in development or staging environments. IF the domain does not match `getklai.com`, THEN the tracking script shall not execute.

### R3: Automatic Pageview Tracking

**R3.1** The system shall automatically track pageviews for all pages including:
- Homepage (`/`, `/nl/`)
- Blog index and posts (`/blog/`, `/blog/[slug]`, `/nl/blog/`, `/nl/blog/[slug]`)
- Contact page (`/contact`)
- Careers page (`/careers`)
- Company pages (`/company/`, `/company/[slug]`)
- Documentation pages (`/docs/`, `/docs/[...slug]`)
- Dynamic CMS pages (`/[slug]`)

**R3.2** The system shall track the following automatic metrics (provided by Umami):
- Unique visitors
- Page views
- Referrer sources
- UTM parameters (source, medium, campaign, content, term)
- Browser, OS, and device type
- Country and language
- Screen resolution

### R4: Custom Event Tracking

**R4.1** WHEN a visitor clicks any CTA button (elements with `[data-waitlist]` attribute), THEN the system shall track a `cta-click` event with properties:
- `position`: location on page (hero, feature-chat, feature-focus, feature-scribe, pricing-core, pricing-professional, pricing-complete, final, nav)
- `product`: the product name from the `data-waitlist` attribute

**R4.2** WHEN the waitlist modal opens, THEN the system shall track a `waitlist-open` event with properties:
- `product`: the product name
- `billing`: the selected billing period (monthly/yearly)

**R4.3** WHEN the waitlist form is successfully submitted, THEN the system shall track a `waitlist-submit` event with properties:
- `product`: the product name
- `billing`: the billing period
- `team_size`: the selected team size range

**R4.4** WHEN the waitlist modal is closed without submitting, THEN the system shall track a `waitlist-close` event with property:
- `product`: the product name

**R4.5** WHEN the contact form is successfully submitted, THEN the system shall track a `contact-submit` event.

**R4.6** WHEN the careers form is successfully submitted, THEN the system shall track a `careers-submit` event.

**R4.7** WHEN the billing toggle (monthly/yearly) is clicked on the Features or Pricing section, THEN the system shall track a `billing-toggle` event with property:
- `period`: the newly selected billing period

**R4.8** WHEN a FAQ answer is expanded, THEN the system shall track a `faq-expand` event with property:
- `question`: the question index (1-6)

**R4.9** WHEN the language switcher is used, THEN the system shall track a `lang-switch` event with properties:
- `from`: the current language code
- `to`: the target language code

**R4.10** WHEN a visitor clicks an external link (links to domains other than getklai.com), THEN the system shall track an `outbound-link` event with property:
- `url`: the destination URL

**R4.11** WHEN a visitor scrolls past 25%, 50%, 75%, or 100% of the homepage, THEN the system shall track a `scroll-depth` event with property:
- `depth`: the percentage milestone reached

Each milestone shall fire only once per page load.

### R5: Privacy Configuration

**R5.1** The system shall operate in cookieless mode (Umami default). The system shall NOT set any cookies or use localStorage for visitor identification.

**R5.2** The system shall NOT store IP addresses. Umami does not store raw IP addresses by design.

**R5.3** The system shall NOT use browser fingerprinting for visitor identification.

**R5.4** The system shall NOT track visitors across different websites.

**R5.5** The system shall respect the browser's Do Not Track setting by enabling the `data-do-not-track` attribute on the tracker script.

**R5.6** The system shall NOT collect any personally identifiable information (PII) in custom events. Event properties shall contain only categorical data (product names, team size ranges, page positions), never email addresses, names, or other PII.

### R6: Dashboard and Reporting

**R6.1** The Umami dashboard shall be configured with the following default views:
- Overview: unique visitors, page views, bounce rate, average visit duration
- Pages: top pages by views
- Referrers: traffic sources
- Events: custom event counts and breakdowns
- UTM: campaign performance

**R6.2** WHERE Umami supports custom reports, the following reports shall be created:
- **Conversion funnel:** Homepage view -> Pricing section view -> CTA click -> Waitlist open -> Waitlist submit
- **Product interest:** Breakdown of waitlist-open and waitlist-submit by product (Chat/Focus/Scribe)
- **Content engagement:** Blog post views with scroll depth

**R6.3** The Umami admin account shall use a strong, unique password stored in the team password manager (not in environment variables or code).

### R7: Operational Requirements

**R7.1** IF the Umami container becomes unhealthy or stops, THEN Coolify shall automatically restart it.

**R7.2** The system shall NOT affect website loading performance. The tracking script shall load asynchronously with the `defer` attribute and shall not block page rendering.

**R7.3** IF the Umami server is unreachable, THEN the website shall continue to function normally without errors. The tracking script shall fail silently.

**R7.4** The Umami database shall be included in the existing PostgreSQL backup strategy.

## Specifications

### S1: Umami Container Configuration

```
Image: docker.umami.is/umami-software/umami:postgresql-latest
Port: 3000 (internal)
Environment:
  DATABASE_URL: postgresql://umami:<password>@<postgres-host>:5432/umami
  APP_SECRET: <random-64-char-string>
  DISABLE_TELEMETRY: 1
Resources:
  Memory limit: 512 MB
  CPU limit: 0.5 cores
```

### S2: Tracker Script Tag

```html
<script
  defer
  src="https://analytics.getklai.com/script.js"
  data-website-id="<GENERATED-ID>"
  data-domains="getklai.com"
  data-performance
  data-do-not-track
></script>
```

This script tag shall be placed in `src/layouts/Base.astro` inside the `<head>` element, conditionally rendered only when building for production.

### S3: Custom Event Implementation Strategy

Events shall be implemented using a combination of:

1. **HTML `data-umami-event` attributes** for simple click tracking (CTA buttons, FAQ expansion, billing toggle, nav links, outbound links)
2. **JavaScript `umami.track()` calls** for events requiring dynamic properties (waitlist open/submit/close, form submissions, scroll depth, language switch)

### S4: DNS Configuration

```
analytics.getklai.com  A  <public-01-IP>
```

Add via Cloud86 DNS management interface.

### S5: File Changes Required

| File | Change |
|------|--------|
| `src/layouts/Base.astro` | Add Umami tracking script in `<head>` |
| `src/components/ui/WaitlistModal.astro` | Add `umami.track()` calls for waitlist-open, waitlist-submit, waitlist-close events |
| `src/components/sections/Hero.astro` | Add `data-umami-event` to CTA button |
| `src/components/sections/FinalCTA.astro` | Add `data-umami-event` to CTA button |
| `src/components/sections/PricingCards.astro` | Add `data-umami-event` to each plan CTA |
| `src/components/sections/Features.astro` | Add `data-umami-event` to product CTAs and billing toggle |
| `src/components/sections/FAQ.astro` | Add `data-umami-event` to FAQ expand triggers |
| `src/components/sections/Nav.astro` | Add `data-umami-event` to nav CTA |
| `src/components/ui/LangSwitcher.astro` | Add `umami.track()` for language switch |
| `src/pages/contact.astro` | Add `umami.track()` for contact-submit |
| `src/pages/careers.astro` | Add `umami.track()` for careers-submit |
| `src/pages/index.astro` (and `/nl/`) | Add scroll depth tracking script |

### S6: Scroll Depth Tracking Implementation

Use an Intersection Observer on the homepage to detect when sentinel elements at 25%, 50%, 75%, and 100% scroll positions enter the viewport. Each fires once per page load.

### S7: Outbound Link Tracking

Use event delegation on the document to detect clicks on anchor elements with `href` values pointing to external domains (not `getklai.com`). Track via `umami.track('outbound-link', { url: href })`.

## Traceability

| Requirement | Implementation | Acceptance |
|------------|---------------|------------|
| R1 (Deployment) | Coolify container setup | AC-1 |
| R2 (Tracker script) | Base.astro modification | AC-2 |
| R3 (Pageviews) | Automatic via Umami | AC-3 |
| R4 (Custom events) | Component modifications per S5 | AC-4 |
| R5 (Privacy) | Umami defaults + tracker attributes | AC-5 |
| R6 (Dashboard) | Umami admin configuration | AC-6 |
| R7 (Operations) | Coolify restart policy + graceful degradation | AC-7 |
