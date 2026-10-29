#!/usr/bin/env bash
# Fakes for datastore-rollback.test.sh and datastore-restore-rehearsal.test.sh,
# sourced by both. ds_fakes_install <bin> writes docker, sudo, df and flock into
# <bin>; they keep all state under $FAKE and log every docker call to $FAKE/calls.
#
# Postgres: fake/db/<store>/<db>/<schema.table> holds a row count. The
# pg_dump/pg_restore/psql command shapes were checked against a real
# postgres:18.6-alpine on 2026-10-28. For a major the data directories live in
# fake/pgdir/<store>/<path under /var/lib/postgresql, / as _>, and
# fake/db/<store> links to the one the running image opens; PG_MAJOR and
# PGDATA follow the image tag as the official images set them (18.6 and 19rc1
# checked on 2026-10-29). fake/db/<store>/<db>/.ext
# lists extensions besides plpgsql, fake/missing_ext the ones a new image lacks.
#
# Other engines: fake/eng/<container> is the store's data as key=value lines
# (points= for Qdrant, keys= and graphs= for Redis and FalkorDB, queues= for
# RabbitMQ, fcv=, dbs= and colls= for Mongo, indexes= for Meilisearch, buckets= for
# Garage, objects= per bucket). A compose service's container is cid-<service>. A cold copy, a
# BGSAVE and a definitions export copy that file into the snapshot; a restore
# copies it back. The fake answers the HTTP, redis-cli, mongosh and rabbitmqctl
# calls in the shapes real Qdrant 1.19/1.20, Redis 8.10, Meilisearch 1.55,
# Mongo 8.0, RabbitMQ 4.4 and Garage 2.4 produced on 2026-10-28.
# Switches: fake/probe_red (a live store's facts fail until it is restored),
# fake/corrupt_snapshot (a snapshot taken now does not read back),
# fake/stop_fails (docker compose stop exits 1), fake/fcv_fails
# (setFeatureCompatibilityVersion answers an error).

ds_fakes_install() {
  local bin="$1"
  mkdir -p "$bin"
  cat > "$bin/docker" <<'FAKE'
#!/usr/bin/env bash
F="$FAKE"
echo "docker $*" >> "$F/calls"
mkdir -p "$F/eng" "$F/vol"
img_of() { awk -v s="$1" '/^  [a-z]/ { svc = $1; sub(/:$/, "", svc) } /^    image:/ && svc == s { print $2 }' "$KLAI_COMPOSE_DIR/docker-compose.yml"; }
name_of() { local c="${1#cid-}"; echo "${c%%:*}"; }
get() { sed -n "s/^$2=//p" "$F/eng/$1" 2>/dev/null; }
put() { local f="$F/eng/$1"; touch "$f"; grep -v "^$2=" "$f" > "$f.tmp"; echo "$2=$3" >> "$f.tmp"; mv "$f.tmp" "$f"; }
live() { [[ -e "$F/compose_svc_$1" ]]; }
red() { live "$1" && [[ -e "$F/probe_red" ]]; }
pgmajor() { local t="${1##*:}"; [[ "$t" == *-pg* ]] && t="${t##*-pg}"; echo "${t%%[.-]*}"; }
pgkey() { local k="${1#/var/lib/postgresql/}"; echo "${k//\//_}"; }
pgdata() { pgenv "$1" | sed -n 's/^PGDATA=//p'; }
# The store's data as a link into pgdir, moving a plain fixture directory there first.
pgnorm() {
  [[ -d "$F/db/$1" && ! -L "$F/db/$1" ]] || return 0
  local k; k="$(pgkey "$(pgdata "$(cat "$F/running/$1")")")"
  mkdir -p "$F/pgdir/$1" && mv "$F/db/$1" "$F/pgdir/$1/$k" && ln -s "$F/pgdir/$1/$k" "$F/db/$1"
}
pgenv() { local m; m="$(pgmajor "$1")"; echo "PG_MAJOR=$m"; if (( m >= 18 )); then echo "PGDATA=/var/lib/postgresql/$m/docker"; else echo "PGDATA=/var/lib/postgresql/data"; fi; }

in_store() {
  local t="$1" db="" sql="" a; shift
  local cmd="$1"; shift
  case "$cmd" in
    pg_isready) [[ ! -e "$F/pg_down" ]] ;;
    pg_dumpall)
      [[ "$1" == -U ]] && echo "CREATE ROLE $2;" && echo "ALTER ROLE $2 WITH SUPERUSER LOGIN;"
      echo "CREATE ROLE grafana_reader;" ;;
    pg_dump)
      while (( $# )); do [[ "$1" == -d ]] && db="$2"; shift; done
      echo "FAKEDUMP $db"
      for a in "$F/db/$t/$db"/*; do [[ -e "$a" ]] && echo "T $(basename "$a") $(cat "$a")"; done ;;
    pg_restore)
      local data=0 create=0
      while (( $# )); do
        case "$1" in --data-only) data=1 ;; --create) create=1 ;; esac
        shift
      done
      if (( data )); then
        awk '$1 == "T" { print "COPY " $2 " (id) FROM stdin;"; for (i = 0; i < $3; i++) print "row"; print "\\." }'
        # A dump that does not read back: pg_restore stops with exit 1.
        if [[ -e "$F/corrupt_dump" ]]; then exit 1; fi
      elif (( create )); then
        local first=1 k name cnt
        while read -r k name cnt; do
          if [[ "$k" == FAKEDUMP ]]; then db="$name"; mkdir -p "$F/db/$t/$db"; continue; fi
          if [[ -e "$F/restore_short" && "$first" == 1 ]]; then cnt=$((cnt - 1)); fi
          first=0
          echo "$cnt" > "$F/db/$t/$db/$name"
        done
        rm -f "$F/probe_red"
      fi ;;
    psql)
      while (( $# )); do case "$1" in -d) db="$2" ;; -c|-Atc) sql="$2" ;; esac; shift; done
      case "$sql" in
        "") cat > "$F/globals_applied_$t" ;;
        "select extname"*) echo plpgsql; cat "$F/db/$t/$db/.ext" 2>/dev/null; true ;;
        "select 1 from pg_available_extensions"*)
          a="${sql##*name = \'}"; grep -qx "${a%\'}" "$F/missing_ext" 2>/dev/null || echo 1 ;;
        "select datname"*) for a in "$F/db/$t"/*; do [[ -e "$a" && "$(basename "$a")" != postgres ]] && basename "$a"; done; true ;;
        "DROP DATABASE"*) a="${sql#*\"}"; rm -rf "$F/db/$t/${a%%\"*}" ;;
        "select count(*)"*)
          [[ -e "$F/probe_red" ]] && exit 1
          a="${sql#*from \"}"; a="${a//\"/}"
          cat "$F/db/$t/$db/$a" 2>/dev/null ;;
      esac ;;
    redis-cli)
      [[ "$1" == --no-auth-warning ]] && shift
      case "$*" in
        PING) echo PONG ;;
        "INFO persistence") printf 'rdb_bgsave_in_progress:0\r\nrdb_last_bgsave_status:ok\r\n' ;;
        LASTSAVE) cat "$F/lastsave_$t" 2>/dev/null || echo 100 ;;
        BGSAVE)
          cp "$F/eng/$t" "$F/eng/$t.rdb" 2>/dev/null || : > "$F/eng/$t.rdb"
          echo $(( $(cat "$F/lastsave_$t" 2>/dev/null || echo 100) + 1 )) > "$F/lastsave_$t"
          echo "Background saving started" ;;
        "INFO keyspace")
          red "$t" && exit 1
          # Three keys with a TTL on top of the persistent ones.
          printf '# Keyspace\r\ndb0:keys=%s,expires=3,avg_ttl=1000\r\n' "$(( $(get "$t" keys || echo 0) + 3 ))" ;;
        GRAPH.LIST) for a in $(get "$t" graphs); do echo "$a"; done ;;
      esac ;;
    rabbitmq-diagnostics) : ;;
    rabbitmqctl)
      [[ "$1" == -q ]] && shift
      case "$1" in
        export_definitions)
          python3 -c 'import json, sys; print(json.dumps({"queues": [{"name": q, "durable": True} for q in sys.argv[1:]]}))' $(get "$t" queues) ;;
        list_queues) red "$t" && exit 1; for a in $(get "$t" queues); do echo "$a"; done ;;
      esac ;;
    sh)
      local script="$2"
      case "$script" in
        *import_definitions*)
          put "$t" queues "$(python3 -c 'import json, sys; print(" ".join(q["name"] for q in json.load(sys.stdin)["queues"]))')" ;;
        *mongosh*)
          red "$t" && [[ "$JS" == *listDatabases* ]] && exit 1
          case "$JS" in
            *setFeatureCompatibilityVersion*)
              [[ -e "$F/fcv_fails" ]] && { echo "MongoServerError: cannot set"; exit 0; }
              a="${JS#*setFeatureCompatibilityVersion: \"}"; put "$t" fcv "${a%%\"*}"; echo 1 ;;
            *db.version*) a="$(get "$t" version)"; echo "${a%.*} $(get "$t" fcv)" ;;
            *listDatabases*)
              echo "fcv $(get "$t" fcv)"
              for a in $(get "$t" dbs); do echo "db $a"; done
              for a in $(get "$t" colls); do echo "collection ${a%%:*}"; echo "count ${a%%:*} ${a##*:}"; done ;;
            *featureCompatibilityVersion*) get "$t" fcv ;;
            *ping*) echo 1 ;;
          esac ;;
        *wget*)
          case "$4" in
            /health) echo '{"status":"available"}' ;;
            /tasks*) echo '{"total":0}' ;;
            /stats)
              red "$t" && exit 1
              python3 -c 'import json, sys; print(json.dumps({"indexes": {i.split(":")[0]: {"numberOfDocuments": int(i.split(":")[1])} for i in sys.argv[1:]}}))' $(get "$t" indexes) ;;
          esac ;;
      esac ;;
  esac
}

# docker run: options, then the image, then its command.
run() {
  local name="" vf="" net="" detach=0 vols=() img="" a
  while (( $# )); do
    case "$1" in
      --name) name="$2"; shift 2 ;;
      --volumes-from) vf="$(name_of "$2")"; shift 2 ;;
      --network) net="$2"; shift 2 ;;
      -v) vols+=("$2"); shift 2 ;;
      -e|--label|--memory|--cpus|--entrypoint|-u) shift 2 ;;
      -d) detach=1; shift ;;
      --rm|-i) shift ;;
      *) img="$1"; shift; break ;;
    esac
  done
  local dir="" vol=""
  for a in "${vols[@]}"; do
    case "$a" in
      */in:ro|*:/out) dir="${a%%:*}" ;;
      /*) ;;
      *) [[ -n "$vol" ]] || vol="${a%%:*}" ;;
    esac
  done
  if [[ "$img" == curlimages/* ]]; then
    local t port path
    t="$(name_of "${net#container:}")"; port="$3"; path="$5"
    case "$port$path" in
      6333/readyz) echo "all shards are ready" ;;
      6333/collections)
        red "$t" && exit 22
        grep -q CORRUPT "$F/eng/$t" 2>/dev/null && exit 22
        if [[ -n "$(get "$t" points)" ]]; then echo '{"result":{"collections":[{"name":"klai_knowledge"}]}}'; else echo '{"result":{"collections":[]}}'; fi ;;
      6333/collections/*/points/count) echo "{\"result\":{\"count\":$(get "$t" points)}}" ;;
      3903/health) echo ok ;;
      3903/v2/GetBucketInfo*)
        a=" $(get "$t" objects) "; a="${a#* "${path#*id=}":}"; echo "{\"objects\":${a%% *}}" ;;
      3903/v2/ListBuckets)
        python3 -c 'import json, sys; print(json.dumps([{"id": b, "globalAliases": [b + "-alias"], "localAliases": []} for b in sys.argv[1:]]))' $(get "$t" buckets) ;;
    esac
    return
  fi
  if [[ "$img" == alpine:* ]]; then
    case "$*" in
      *"rm -rf /var/lib/postgresql/"*) pgnorm "$vf"; rm -rf "$F/pgdir/$vf/$(pgkey "${!#}")" ;;
      *'mv "$1" "$2"'*)
        pgnorm "$vf"; a="$F/pgdir/$vf/$(pgkey "${*: -2:1}")"
        [[ -d "$a" && ! -e "$F/pgdir/$vf/$(pgkey "${!#}")" ]] || exit 1
        mv "$a" "$F/pgdir/$vf/$(pgkey "${!#}")" ;;
      *"tar -cf"*)
        if [[ -e "$F/corrupt_snapshot" ]]; then echo CORRUPT > "$dir/volume.tar"; else cp "$F/eng/$vf" "$dir/volume.tar"; fi ;;
      *"tar -xf"*) if [[ -n "$vf" ]]; then cp "$dir/volume.tar" "$F/eng/$vf"; rm -f "$F/probe_red"; else cp "$dir/volume.tar" "$F/vol/$vol"; fi ;;
      *appendonlydir*) cp "$dir/dump.rdb" "$F/eng/$vf"; rm -f "$F/probe_red" ;;
      *"cp /in/dump.rdb"*) cp "$dir/dump.rdb" "$F/vol/$vol" ;;
    esac
    return
  fi
  if (( detach )); then
    [[ -n "$name" ]] || return 1
    # A Postgres throwaway keeps its databases under db/, any other under eng/.
    mkdir -p "$F/db/$name"
    if [[ -n "$vol" && -e "$F/vol/$vol" ]]; then cp "$F/vol/$vol" "$F/eng/$name"; else : > "$F/eng/$name"; fi
    echo "$img" > "$F/running/$name"
    echo ctr-id
  fi
}

if [[ "$1" == compose ]]; then
  shift; [[ "$1" == --project-directory ]] && shift 2
  case "$1" in
    exec) shift; [[ "$1" == -T ]] && shift; t="$1"; shift; in_store "$t" "$@" ;;
    ps) for a in "$@"; do [[ "$a" == -* || "$a" == ps ]] || { echo "cid-$a"; : > "$F/compose_svc_$a"; }; done ;;
    stop) [[ ! -e "$F/stop_fails" ]] ;;
    start) : ;;
    up)
      svc="${!#}"; img="$(img_of "$svc")"
      # A Postgres store opens the directory of its image's major; an empty one is a fresh initdb.
      if [[ -e "$F/db/$svc" || -L "$F/db/$svc" ]]; then
        pgnorm "$svc"; rm "$F/db/$svc"
        mkdir -p "$F/pgdir/$svc/$(pgkey "$(pgdata "$img")")"
        ln -s "$F/pgdir/$svc/$(pgkey "$(pgdata "$img")")" "$F/db/$svc"
      fi
      echo "$img" > "$F/running/$svc" ;;
  esac
elif [[ "$1" == exec ]]; then
  shift
  while [[ "$1" == -* ]]; do case "$1" in -e|-u) shift 2 ;; *) shift ;; esac; done
  t="$(name_of "$1")"; shift; in_store "$t" "$@"
elif [[ "$1" == inspect ]]; then
  fmt="$3"; shift 3
  for c in "$@"; do
    case "$fmt" in
      *Mounts*) echo "/var/lib/docker/volumes/${c#cid-}" ;;
      *Config.Image*) cat "$F/running/${c#cid-}" ;;
      *Config.Cmd*) [[ "$c" == cid-redis ]] && printf 'redis-server\n--requirepass\nfake-redis-pw\n' ;;
      *Config.Env*)
        [[ "$c" == cid-qdrant ]] && echo "QDRANT__SERVICE__API_KEY=fake-qdrant-key"
        [[ -e "$F/db/${c#cid-}" ]] && pgenv "$(cat "$F/running/${c#cid-}")" ;;
    esac
  done
  true
elif [[ "$1" == image ]]; then
  pgenv "${!#}"
elif [[ "$1" == cp ]]; then
  src="${2%%:*}"; cp "$F/eng/$(name_of "$src").rdb" "$3"
elif [[ "$1" == run ]]; then
  shift; run "$@"
elif [[ "$1" == volume ]]; then
  [[ "$2" == create ]] && : > "$F/vol/${!#}"
  true
fi
FAKE
  cat > "$bin/sudo" <<'FAKE'
#!/usr/bin/env bash
# sudo -n du -sb <path>
printf '%s\t%s\n' "$(cat "$FAKE/du_bytes")" "${!#}"
FAKE
  cat > "$bin/df" <<'FAKE'
#!/usr/bin/env bash
echo "Filesystem 1-blocks Used Available Capacity Mounted"
# Only the backup root has the fixture's free space; any other path plenty, so
# a check against the wrong filesystem passes and the test notices.
if [[ "${!#}" == */backups ]]; then avail="$(cat "$FAKE/df_avail")"; else avail=999999999999; fi
echo "/dev/fake 1000000000000 1 $avail 1% /"
FAKE
  printf '#!/usr/bin/env bash\nexit 0\n' > "$bin/flock"
  chmod +x "$bin"/*
}

# ds_fakes_engines <fake-dir>: synthetic data for every non-Postgres store.
ds_fakes_engines() {
  local f="$1/eng"
  mkdir -p "$f"
  printf 'points=120\n' > "$f/qdrant"
  printf 'indexes=tenant_a_messages:40 tenant_a_convos:9\n' > "$f/meilisearch"
  printf 'version=8.0.34\nfcv=8.0\ndbs=admin config tenant_a tenant_b\ncolls=tenant_a.messages:40 tenant_b.messages:7\n' > "$f/mongodb"
  printf 'keys=57\n' > "$f/redis"
  printf 'keys=4\n' > "$f/vexa12-redis"
  printf 'keys=0\n' > "$f/crm-redis"
  printf 'keys=31\ngraphs=tenant_a tenant_b\n' > "$f/falkordb"
  printf 'buckets=b1 b2\nobjects=b1:12 b2:3\n' > "$f/garage"
  printf 'queues=nuq_scrape nuq_crawl\n' > "$f/firecrawl-rabbitmq"
}
