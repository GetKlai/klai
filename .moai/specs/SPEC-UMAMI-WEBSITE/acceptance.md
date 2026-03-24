# SPEC-UMAMI-WEBSITE: Acceptance Criteria

## AC-1: Umami Deployment

### AC-1.1: Container Running

```gherkin
Given Umami is deployed via Coolify on public-01
When I check the container status
Then the Umami container is running and healthy
And it is using the image docker.umami.is/umami-software/umami:postgresql-latest
```

### AC-1.2: Database Connection

```gherkin
Given the existing Coolify PostgreSQL instance is running
When Umami starts
Then it connects to a dedicated "umami" database
And database tables are created automatically on first start
```

### AC-1.3: HTTPS Access

```gherkin
Given DNS is configured for analytics.getklai.com
When I visit https://analytics.getklai.com
Then the Umami login page loads over HTTPS
And the SSL certificate is valid (Let's Encrypt via Coolify)
```

### AC-1.4: Default Credentials Changed

```gherkin
Given Umami is deployed for the first time
When the admin logs in
Then the default password "umami" has been changed to a strong unique password
And the password is stored in the team password manager
```

### AC-1.5: Telemetry Disabled

```gherkin
Given the Umami container is configured
When I check the environment variables
Then DISABLE_TELEMETRY is set to 1
And APP_SECRET is set to a random string of at least 64 characters
```

---

## AC-2: Tracker Script Installation

### AC-2.1: Script Present in Production

```gherkin
Given the website is built for production
When I view the HTML source of any page
Then the Umami tracking script is present in the <head>
And the script has defer attribute
And data-website-id is set to the correct Umami website ID
And data-domains is set to "getklai.com"
And data-performance is present
And data-do-not-track is present
```

### AC-2.2: Script Absent in Development

```gherkin
Given the website is running in development mode (localhost)
When I view the HTML source of any page
Then the Umami tracking script is NOT present
```

### AC-2.3: Non-Blocking Load

```gherkin
Given a visitor loads any page on getklai.com
When the page renders
Then the Umami script loads asynchronously (defer)
And the page is fully interactive before the analytics script executes
```

---

## AC-3: Automatic Pageview Tracking

### AC-3.1: Homepage Pageview

```gherkin
Given a visitor navigates to getklai.com
When the page loads
Then a pageview is recorded in the Umami dashboard for path "/"
```

### AC-3.2: Dutch Homepage Pageview

```gherkin
Given a visitor navigates to getklai.com/nl/
When the page loads
Then a pageview is recorded in the Umami dashboard for path "/nl/"
```

### AC-3.3: Blog Pageview

```gherkin
Given a visitor navigates to a blog post at /blog/some-post
When the page loads
Then a pageview is recorded with the correct path
```

### AC-3.4: Traffic Source Tracking

```gherkin
Given a visitor arrives via a link with UTM parameters
  (e.g., ?utm_source=linkedin&utm_medium=social&utm_campaign=launch)
When the pageview is recorded
Then the UTM source, medium, and campaign are visible in the Umami dashboard
```

---

## AC-4: Custom Event Tracking

### AC-4.1: CTA Click Tracking

```gherkin
Given a visitor is on the homepage
When they click the Hero CTA button ("Get started")
Then a "cta-click" event is recorded
And the event has property position="hero"
And the event has the correct product property
```

### AC-4.2: Waitlist Modal Open

```gherkin
Given a visitor is on the homepage
When they click any CTA button with [data-waitlist] attribute
Then the waitlist modal opens
And a "waitlist-open" event is recorded
And the event has the product name as property
And the event has the current billing period as property
```

### AC-4.3: Waitlist Form Submission

```gherkin
Given the waitlist modal is open
And the visitor has filled in all required fields
When they submit the form successfully
Then a "waitlist-submit" event is recorded
And the event has product, billing, and team_size properties
And the team_size value matches the selected dropdown option
And no PII (name, email, company) is included in the event properties
```

### AC-4.4: Waitlist Modal Abandonment

```gherkin
Given the waitlist modal is open
When the visitor closes the modal without submitting
  (via close button, backdrop click, or Escape key)
Then a "waitlist-close" event is recorded
And the event has the product name as property
```

### AC-4.5: Contact Form Submission

```gherkin
Given a visitor is on the /contact page
When they submit the contact form successfully
Then a "contact-submit" event is recorded
And no PII is included in the event properties
```

### AC-4.6: Careers Form Submission

```gherkin
Given a visitor is on the /careers page
When they submit the careers form successfully
Then a "careers-submit" event is recorded
And no PII is included in the event properties
```

### AC-4.7: Billing Toggle

```gherkin
Given a visitor is viewing the Features or Pricing section
When they click the monthly/yearly billing toggle
Then a "billing-toggle" event is recorded
And the event has period property set to the newly selected period
```

### AC-4.8: FAQ Expansion

```gherkin
Given a visitor is viewing the FAQ section
When they expand a FAQ answer
Then a "faq-expand" event is recorded
And the event has question property set to the question index (1-6)
```

### AC-4.9: Language Switch

```gherkin
Given a visitor is viewing the website in English
When they use the language switcher to switch to Dutch
Then a "lang-switch" event is recorded
And the event has from="en" and to="nl" properties
```

### AC-4.10: Outbound Link Click

```gherkin
Given a visitor is on any page
When they click a link to an external domain
  (e.g., feedback.getklai.com, github.com)
Then an "outbound-link" event is recorded
And the event has the destination URL as property
```

### AC-4.11: Scroll Depth Tracking

```gherkin
Given a visitor is on the homepage
When they scroll past the 50% mark of the page
Then a "scroll-depth" event is recorded with depth="50"
And the 50% milestone fires only once per page load
And the 25% milestone has already fired once
```

---

## AC-5: Privacy Compliance

### AC-5.1: No Cookies

```gherkin
Given a visitor loads any page on getklai.com
When I inspect the browser cookies
Then no cookies have been set by the Umami tracker
And no localStorage entries have been created by the tracker
```

### AC-5.2: No PII in Events

```gherkin
Given custom events are being tracked
When I inspect the event data in the Umami dashboard
Then no event properties contain email addresses, names, or company names
And event properties contain only categorical data
  (product names, team size ranges, page positions, language codes)
```

### AC-5.3: Do Not Track Respected

```gherkin
Given a visitor has Do Not Track enabled in their browser
When they load a page on getklai.com
Then no tracking data is sent to the Umami server
```

### AC-5.4: EU Data Residency

```gherkin
Given the Umami instance is deployed on public-01
When analytics data is collected
Then all data is stored on the Hetzner server in the EU
And no data is transmitted to servers outside the EU
```

---

## AC-6: Dashboard and Reporting

### AC-6.1: Overview Dashboard

```gherkin
Given I am logged into the Umami dashboard
When I view the website overview
Then I can see unique visitors, page views, bounce rate, and average visit duration
And I can filter by date range
```

### AC-6.2: Custom Events Visible

```gherkin
Given custom events have been tracked
When I view the Events section in the Umami dashboard
Then I can see event counts for each event type
And I can drill into event properties for segmentation
```

### AC-6.3: Conversion Funnel Report

```gherkin
Given the conversion funnel report is configured
When I view the funnel
Then I can see the drop-off between:
  Homepage view -> Pricing view -> CTA click -> Waitlist open -> Waitlist submit
```

---

## AC-7: Operational Requirements

### AC-7.1: Auto-Restart

```gherkin
Given the Umami container crashes
When Coolify detects the container is unhealthy
Then it automatically restarts the container
And the analytics service recovers without manual intervention
```

### AC-7.2: Graceful Degradation

```gherkin
Given the Umami server at analytics.getklai.com is unreachable
When a visitor loads any page on getklai.com
Then the website loads and functions normally
And no JavaScript errors appear in the browser console
And the tracking script fails silently
```

### AC-7.3: Performance Impact

```gherkin
Given the Umami tracking script is loaded
When I measure the website's Largest Contentful Paint (LCP)
Then the LCP is not measurably affected (within 50ms variance)
And the tracking script does not block the main thread
```

### AC-7.4: Database Backup

```gherkin
Given the umami database exists in the Coolify PostgreSQL instance
When the regular PostgreSQL backup runs
Then the umami database is included in the backup
```

---

## Definition of Done

- [ ] Umami container deployed and running on public-01 via Coolify
- [ ] DNS A record configured for analytics.getklai.com
- [ ] HTTPS working with valid certificate
- [ ] Default admin password changed, stored in password manager
- [ ] Tracking script added to Base.astro with production-only rendering
- [ ] Website registered in Umami with correct domain
- [ ] All 11 custom events implemented and verified
- [ ] No PII leaks in any custom event
- [ ] No cookies set by tracker (verified in browser)
- [ ] Do Not Track respected (verified with DNT header)
- [ ] Conversion funnel report configured
- [ ] Website loads normally when Umami is unreachable
- [ ] Umami container auto-restarts on failure
- [ ] Database included in backup strategy
