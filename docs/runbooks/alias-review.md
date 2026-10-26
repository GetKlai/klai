# Alias review: keeping a knowledge base's names and synonyms current

A knowledge base can carry alias groups (`portal_knowledge_bases.aliases`, plan step 6): names
that mean the same thing, such as a product's official name next to what employees and visitors
call it. The ingest path and `kb_patch` give a chunk that names one term the group's other names
("Also known as"), and retrieval adds the other names to a query that names one. This runbook is
the review cycle: once a month, or when the gap report shows a new word, a person proposes groups
from real traffic, the knowledge-base owner decides, and the patch applies them. Nothing in it
runs on its own, and nothing here calls a model.

## 1. Candidates from real traffic

Gap events (`portal_retrieval_gaps`) hold the questions that found nothing or only weak passages;
the question text is kept for seven days (`telemetry_level` full), so the review reads at most a
week. List the week's gap questions of one organisation and read them for words the knowledge
base does not use:

```sql
select g.gap_type, g.nearest_kb_slug, left(g.query_text, 120)
from portal_retrieval_gaps g join portal_orgs o on o.id = g.org_id
where o.slug = '<org-slug>' and g.occurred_at > now() - interval '7 days'
order by g.occurred_at desc;
```

A candidate is a word the question uses that the article does not (check with a title search over
`knowledge.artifacts.extra->>'title'` or the retrieval log), paired with the article's own word.
Typos that recur (a product name spelled as it sounds) count too. A word that is a language or
section code, or that names another product of the same organisation, is not a candidate: a wrong
alias makes every right passage look like another subject.

## 2. The owner decides

Propose the groups in the conversation as "question says X, article says Y, n times this week".
The owner accepts, edits or rejects each group. Accepted groups go on the knowledge base through
the existing endpoint (`PATCH /api/app/knowledge-bases/{slug}` with `aliases`: up to 50 groups of
2 to 10 terms, a term at most once); rejected pairs are noted at the bottom of this file's
counterpart in the private infra repo so they are not proposed again.

## 3. Apply

Wait a minute (the ingest caches settings for 60 s), then follow `docs/runbooks/kb-patch.md`: a dry
run that counts, the user's yes, `INGEST_OK=1`, the run on that one knowledge base. Only chunks that
name a term are re-embedded, on our own GPU.

## 4. Did it help

Two weeks later, compare the share of gap events among questions that contain a term of the group
with the two weeks before the change:

```sql
select (occurred_at > '<change date>') as after, count(*)
from portal_retrieval_gaps g join portal_orgs o on o.id = g.org_id
where o.slug = '<org-slug>' and g.occurred_at > '<change date>'::timestamptz - interval '14 days'
  and g.query_text ~* '(<term1>|<term2>|...)'
group by 1;
```

Read it next to the total number of questions in the same windows (the chat's turn events), because
fewer gap events can also mean fewer questions. A group whose words no question used in thirty days,
or whose gap share did not fall, is removed at the next review. The meetlat (`docs/runbooks/meetlat.md`)
is the regression check for a change, not the proof: its cases rarely contain alias words.
