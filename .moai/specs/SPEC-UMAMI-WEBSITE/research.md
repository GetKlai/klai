# SPEC-UMAMI-WEBSITE: Research

## Website Structure Analysis

### Pages Discovered

| Page | Path | Type | Key Elements |
|------|------|------|-------------|
| Homepage | `/` and `/nl/` | Marketing landing | Hero CTA, ValueProps, Features (3 products), SocialProof, Comparison table, UseCases, Pricing (3 tiers), FAQ, FinalCTA |
| Blog index | `/blog/` and `/nl/blog/` | Content | Blog listing |
| Blog post | `/blog/[slug]` | Content | Individual article |
| Contact | `/contact` | Lead gen | Contact form (name, email, company, message) |
| Careers | `/careers` | Recruitment | Application form (name, email, role, message) |
| Company | `/company/` and `/company/[slug]` | Content | Company documentation pages |
| Docs | `/docs/` and `/docs/[...slug]` | Documentation | Product/legal documentation |
| CMS pages | `/[slug]` | Dynamic | Keystatic-managed pages |

### Conversion Points Identified

**Primary Conversion: Waitlist Signup (Modal)**
- Triggered by `[data-waitlist]` attribute on any button
- Collects: name, work email, company, team size (5 ranges)
- Hidden fields: product name, billing period
- Posts to `/api/waitlist`
- Used by: Hero CTA, Feature CTAs (Chat/Focus/Scribe), Pricing card CTAs, FinalCTA

**Secondary Conversion: Contact Form**
- Page: `/contact`
- Collects: name, email, company (optional), message
- Posts to `/api/contact`

**Tertiary: Careers Form**
- Page: `/careers`
- Collects: name, email, role (optional), message
- Posts to `/api/careers`

### Homepage Sections (scroll order)

1. **Nav** - Links: Why Klai, Product, Ownership, Pricing, FAQ + CTA button
2. **Hero** - Primary CTA ("Get started") + "Learn more" link
3. **ValueProps** - Problem/Answer positioning
4. **Features** - 3 product cards (Chat, Focus, Scribe) each with own CTA + billing toggle (monthly/yearly)
5. **SocialProof** - 3 pledges with external links (feedback.getklai.com, /company)
6. **Comparison** - Feature matrix: Klai vs ChatGPT vs Azure
7. **UseCases** - 3 personas: Compliance, IT, Management - with internal doc links
8. **Pricing** - 3 tiers (Core/Professional/Complete) with CTAs
9. **FAQ** - 6 questions
10. **FinalCTA** - Bottom conversion CTA
11. **Footer** - Links to Product, Company, Legal, Support sections

### Technical Stack

- Framework: Astro 5 (static site, TypeScript)
- Styling: Tailwind CSS v4
- CMS: Keystatic (git-based)
- i18n: Dutch (nl) + English (en)
- Deploy: Coolify on Hetzner CX42 (public-01)
- No existing analytics tracking installed

---

## B2B SaaS Website Analytics Standards

### Industry-Standard Conversion Funnel

For B2B SaaS marketing websites, the standard funnel is:

```
Visitor -> Engaged -> Intent -> Converted -> Retained
```

1. **Visitor**: Any pageview (homepage, blog, docs)
2. **Engaged**: Scroll depth > 50%, time on page > 30s, 2+ page views
3. **Intent**: Pricing page visit, comparison section view, FAQ interaction
4. **Converted**: Form submission (waitlist signup, contact form)
5. **Retained**: Return visitor, multiple sessions

### Key Metrics for B2B SaaS Websites

**Traffic Metrics:**
- Unique visitors per day/week/month
- Page views by page
- Traffic sources (referrer, UTM parameters)
- Device and browser breakdown
- Geographic distribution (important for EU B2B)
- Language preference (nl vs en)

**Engagement Metrics:**
- Bounce rate (single-page sessions)
- Average pages per session
- Scroll depth on key pages (homepage, pricing)
- Time on page for content pages (blog, docs)
- Section visibility (which homepage sections are actually seen)

**Conversion Metrics:**
- Waitlist form opens (modal shown)
- Waitlist form submissions (by product: Chat/Focus/Scribe)
- Waitlist form abandonment (modal opened but not submitted)
- Contact form submissions
- Careers form submissions
- CTA click rates by position (hero, feature, pricing, final)

**Content Performance:**
- Blog post views and engagement
- Documentation page views
- Company page views
- Most popular content by language

### Standard Custom Events for B2B Marketing Sites

| Event Name | Trigger | Properties |
|-----------|---------|------------|
| `cta-click` | Any CTA button click | position (hero/feature/pricing/final), product |
| `waitlist-open` | Waitlist modal opens | product, billing |
| `waitlist-submit` | Waitlist form submitted | product, billing, team_size |
| `waitlist-close` | Modal closed without submit | product |
| `contact-submit` | Contact form submitted | - |
| `careers-submit` | Careers form submitted | - |
| `pricing-view` | Pricing section enters viewport | - |
| `billing-toggle` | Monthly/yearly toggle clicked | billing_period |
| `comparison-view` | Comparison table enters viewport | - |
| `faq-expand` | FAQ answer expanded | question_index |
| `outbound-link` | External link clicked | url, text |
| `blog-read` | Blog post scrolled >75% | slug |
| `lang-switch` | Language switched | from, to |
| `nav-click` | Navigation link clicked | target |
| `scroll-depth` | Page scroll milestones | depth (25/50/75/100) |

### Umami Capabilities Summary

**Version:** Latest stable (Docker image: `docker.umami.is/umami-software/umami:postgresql-latest`)

**Privacy Features:**
- Cookieless by design (no consent banner required)
- No cross-site tracking
- No fingerprinting
- GDPR compliant out of the box
- Can respect Do Not Track browser setting
- No PII stored in analytics data

**Tracking Features:**
- Automatic pageview tracking
- Custom events via `data-umami-event` HTML attributes (no JS needed)
- Custom events via `umami.track()` JavaScript API
- Event properties for segmentation
- Core Web Vitals collection (`data-performance`)
- Domain restriction (`data-domains`)
- UTM parameter tracking built-in
- Referrer tracking built-in

**Dashboard Features:**
- Real-time visitors
- Page views and unique visitors over time
- Top pages, referrers, browsers, OS, devices, countries
- Custom event reporting
- UTM campaign tracking
- Retention analysis
- Funnel reports (paid/self-hosted)
- Custom reports

**Deployment:**
- Docker image available for PostgreSQL
- Minimal resource requirements
- Can share existing PostgreSQL instance (separate database recommended)
- Default port: 3000
- Default admin: admin/umami (must change immediately)

**Tracker Script:**
```html
<script defer src="https://analytics.getklai.com/script.js" data-website-id="WEBSITE-ID"></script>
```

Key attributes:
- `data-website-id`: Required, identifies the website
- `data-domains`: Restrict to production domain only
- `data-host-url`: Override data collection endpoint
- `data-auto-track`: Enable/disable automatic pageview tracking
- `data-performance`: Enable Core Web Vitals
- `data-do-not-track`: Respect browser DNT setting

**Custom Event Implementation:**

HTML attribute method (preferred for simple clicks):
```html
<button data-umami-event="cta-click" data-umami-event-position="hero">
  Get started
</button>
```

JavaScript method (for dynamic events):
```javascript
umami.track('waitlist-submit', { product: 'chat', team_size: '2-10' });
```

### Infrastructure Considerations

**public-01 server resources:**
- 15 GB RAM, 8 vCPU, 275 GB disk free
- Already running: Coolify, Twenty CRM, Fider, Uptime Kuma
- Existing PostgreSQL available via Coolify

**Database strategy:**
- Create a new database (`umami`) in the existing Coolify-managed PostgreSQL instance
- Umami's data footprint is minimal for a single marketing website
- No need for a separate PostgreSQL container

**DNS/Proxy:**
- Subdomain: `analytics.getklai.com`
- Route through Coolify's built-in reverse proxy (Caddy/Traefik)
- HTTPS via Let's Encrypt (automatic with Coolify)

**Resource estimate:**
- Umami container: ~200 MB RAM, minimal CPU
- Database growth: ~50 MB/month for a marketing site with moderate traffic
