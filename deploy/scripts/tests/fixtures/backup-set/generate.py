#!/usr/bin/env python3
"""Writes a synthetic nightly backup set for backup-restore.test.sh.

    generate.py <output dir> <seed>

The set has the file names backup.sh produces, filled from a seeded generator
with invented names and example.com addresses; nothing is read from any real
source. The engine files are the plain-text stand-ins the fake docker in
datastore-fakes.sh understands (keys=, indexes=, points=, one "db.collection n"
line per Mongo collection). The engine facts backup.sh records while it makes a
set go into backup-facts.txt here; the file facts (checksums, tar listings) are
added by the test through backup.sh's own record_facts, so the two scripts are
shown to agree.
"""
import gzip
import io
import os
import random
import sys
import tarfile

out, seed = sys.argv[1], int(sys.argv[2])
rng = random.Random(seed)
header = f"synthetic-data: generator=deploy/scripts/tests/fixtures/backup-set/generate.py seed={seed}"
os.makedirs(out, exist_ok=True)


def write(name, text):
    with open(os.path.join(out, name), "w") as f:
        f.write(text)


def sql_dump(databases):
    lines = [f"-- {header}", "--", "-- PostgreSQL database cluster dump", "--", "CREATE ROLE app_owner;"]
    for db, tables in databases.items():
        lines.append(f"\\connect {db}")
        for table in tables:
            lines.append(f"COPY {table} (id, label, contact) FROM stdin;")
            for i in range(rng.randint(2, 9)):
                lines.append(f"{i}\tExample label {rng.randint(100, 999)}\tuser{i}@example.com")
            lines.append("\\.")
    lines += ["--", "-- PostgreSQL database cluster dump complete", "--"]
    return "\n".join(lines) + "\n"


dumps = {
    "postgres-all.sql": {"portal": ["public.orgs", 'public."KnowledgeBases"'], "tickets": ["public.tickets"]},
    "firecrawl-postgres-all.sql": {"crawl": ["nuq.queue_scrape"]},
    "listmonk-postgres-all.sql": {"mail": ["public.subscribers", "public.lists"]},
    "crm-postgres-all.sql": {"crm": ["core.people"]},
    "fider-postgres-all.sql": {"ideas": ["public.posts"]},
    "umami-postgres-all.sql": {"stats": ["public.website_event"]},
}
for name, databases in dumps.items():
    write(name, sql_dump(databases))


def tarball(name, files):
    raw = io.BytesIO()
    with gzip.GzipFile(fileobj=raw, mode="wb", mtime=0) as gz:
        with tarfile.open(fileobj=gz, mode="w") as tar:
            for path, size in files.items():
                data = bytes(rng.randrange(256) for _ in range(size))
                info = tarfile.TarInfo(path)
                info.size = len(data)
                info.mtime = 0
                tar.addfile(info, io.BytesIO(data))
    with open(os.path.join(out, name), "wb") as f:
        f.write(raw.getvalue())


for name in ("gitea-repos.tar.gz", "garage-meta.tar.gz", "garage-data.tar.gz", "crm-storage.tar.gz",
             "listmonk-uploads.tar.gz", "scribe-audio.tar.gz"):
    stem = name.split(".")[0]
    tarball(name, {f"./{stem}/file-{i}.bin": rng.randint(20, 200) for i in range(rng.randint(3, 8))})

research = os.path.join(out, "research-uploads")
for i in range(rng.randint(3, 6)):
    os.makedirs(os.path.join(research, f"user-{i}"), exist_ok=True)
    with open(os.path.join(research, f"user-{i}", "note.txt"), "w") as f:
        f.write(f"{header}\nExample upload {i}\n")

redis_keys, vexa_keys, falkor_keys = rng.randint(20, 90), rng.randint(2, 9), rng.randint(10, 60)
write("redis-dump.rdb", f"{header}\nkeys={redis_keys}\n")
write("vexa12-redis-dump.rdb", f"{header}\nkeys={vexa_keys}\n")
write("falkordb-dump.rdb", f"{header}\nkeys={falkor_keys}\ngraphs=example_graph_a example_graph_b\n")

mongo = {"tenant_a.messages": rng.randint(5, 50), "tenant_b.messages": rng.randint(1, 9)}
write("mongodb-all.archive", header + "\n" + "".join(f"{k} {v}\n" for k, v in mongo.items()))

meili = {"example_index_a": rng.randint(5, 50), "example_index_b": rng.randint(1, 9)}
write("meilisearch.snapshot", f"{header}\nindexes=" + " ".join(f"{k}:{v}" for k, v in meili.items()) + "\n")
points = rng.randint(50, 300)
write("qdrant-klai_knowledge.snapshot", f"{header}\npoints={points}\n")

facts = [
    f"facts redis count keys {redis_keys}",
    f"facts vexa_redis count keys {vexa_keys}",
    f"facts falkordb count keys {falkor_keys}",
    "facts falkordb graph example_graph_a",
    "facts falkordb graph example_graph_b",
    "facts qdrant collection klai_knowledge",
    f"facts qdrant count klai_knowledge {points}",
]
for k, v in meili.items():
    facts += [f"facts meilisearch index {k}", f"facts meilisearch count {k} {v}"]
write("backup-facts.txt", header + "\n" + "\n".join(facts) + "\n")
