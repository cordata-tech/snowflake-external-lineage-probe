# Snowflake external lineage — probe log

Notes taken while running, not reconstructed afterwards. For blog post #39.

## Account under test

| | |
|---|---|
| Account locator | `AB12345` |
| Org / account | `myorg` / `myacct` |
| Host | `https://myorg-myacct.snowflakecomputing.com` |
| Region | `AWS_EU_CENTRAL_1` (Frankfurt) |
| Edition | Enterprise |
| Version | `10.31.103` |
| Type | **30-day free trial**, no payment method attached |
| Date | 2026-09-06 |

## P0 — is external lineage reachable on a free trial at all?

**Yes.** This was the question that decided whether the post is viable, and the
answer is better than "yes for us": it is reachable by anyone, for nothing.

The documented trial exclusions list *"external network access"*, which is
Snowflake reaching **outbound** and is a different feature from external
lineage despite the colliding names. External lineage is not on the exclusion
list, and nothing about the trial blocked it.

```sql
CREATE ROLE IF NOT EXISTS lineage_probe;
GRANT ROLE lineage_probe TO USER PROBE_USER;
GRANT USAGE ON WAREHOUSE COMPUTE_WH TO ROLE lineage_probe;
GRANT INGEST LINEAGE ON ACCOUNT TO ROLE lineage_probe;   -- succeeded
GRANT DELETE LINEAGE ON ACCOUNT TO ROLE lineage_probe;   -- succeeded
SHOW GRANTS TO ROLE lineage_probe;
```

Result — both account-level privileges exist and are grantable:

| privilege | granted_on | name | grantee |
|---|---|---|---|
| `DELETE LINEAGE` | ACCOUNT | AB12345 | LINEAGE_PROBE |
| `INGEST LINEAGE` | ACCOUNT | AB12345 | LINEAGE_PROBE |
| `USAGE` | WAREHOUSE | COMPUTE_WH | LINEAGE_PROBE |

**Why this matters for the post.** Every claim we make can be reproduced by a
reader on a free trial. That is a stronger position than testing on a paid
account and asking to be believed.

## Setup notes worth keeping

- **Do not accept the notebook "Connect" prompt.** It provisions a Snowpark
  Container Services compute pool with a **24 hour idle timeout**, which bills
  while idle. Warehouses can be set to `AUTO_SUSPEND = 60`; that pool cannot.
  Cancelled it; `SHOW COMPUTE POOLS` shows both system pools `SUSPENDED` with
  `num_services 0`.
- Legacy Worksheets were removed from Snowsight in June 2026. The SQL surface
  is now **Workspaces**, and most tutorials online still describe the old one.
- Edition, cloud and region are fixed at signup and cannot be changed. Choosing
  Standard would have wasted the trial, since external lineage needs Enterprise.

## P0b — does the REST endpoint work on a trial, and does key-pair auth hold?

**Yes, HTTP 200.** A valid `COMPLETE` event with both sides external was
accepted. Auth is key-pair JWT (`X-Snowflake-Authorization-Token-Type:
KEYPAIR_JWT`); username/password is not accepted. The JWT is signed RS256 with
`iss = <LOCATOR>.<USER>.SHA256:<fp>` and `sub = <LOCATOR>.<USER>`. Minted with
stdlib plus `openssl` — see `jwt_token.py`; no SDK required.

Response body on success is **empty**. Not an ack, not an id, not a count.

## P2 — which eventTypes are accepted?  **Only COMPLETE.**

The documentation says *"Events with a different eventType, for example START
or FAIL, are rejected"*. Tested all four rather than the two named:

| eventType | HTTP | code |
|---|---|---|
| `FAIL` | **400** | 394902 |
| `START` | **400** | 394902 |
| `RUNNING` | **400** | 394902 |
| `ABORT` | **400** | 394902 |

Identical error each time:

```json
{"code":"394902",
 "message":"Unsupported event type, only COMPLETE event type can be ingested",
 "error_code":"394902"}
```

**This is the post's sharpest finding.** The graph cannot contain a failed run,
an in-flight run, or an aborted one — only successes. Part 2 § 3 makes
assertion results a data-quality facet and `docs/commit-signing.md` quotes
part 2 § 4: *"a run whose provenance points at an unsigned commit is itself a
finding."* A sink that rejects `FAIL` cannot hold the run that governance asks
about.

Note this is not a bug. A *lineage graph* records which datasets derive from
which, and a run that failed produced nothing to record. The error is treating
the graph as a run log or an evidence store.

## P1 — CORRECTED. The edge landed. It is visible, and it is not queryable.

**My first hypothesis was wrong and is recorded here rather than quietly
dropped.** I concluded from `GET_LINEAGE` returning 0 rows that the events had
been accepted and discarded. The Snowsight lineage graph shows otherwise:

```
Amazon S3                    LINEAGE_TEST.PUBLIC          LINEAGE_TEST.PUBLIC
fraud_raw/transactions  -->  TRANSACTIONS_SCORED     -->  SCORED_COPY
                             TX_ID, FRAUD_SCORE
```

The external edge ingested correctly, first time, with the namespace
`snowflake://MYORG-MYACCT`. Nothing was dropped.

### The actual finding: the two read paths disagree

Same table, same function, same moment:

| query | result |
|---|---|
| `GET_LINEAGE(..., 'DOWNSTREAM', 3)` — native edge | **1 row** |
| `GET_LINEAGE(..., 'UPSTREAM', 3)` — external edge, **visible in the UI** | **0 rows** |
| `GET_LINEAGE('SCORED_COPY', ..., 'UPSTREAM', 5)` | stops at `TRANSACTIONS_SCORED`, never reaches S3 |

And there is no argument that turns it on. The deployed signature is
`GET_LINEAGE(object_name, object_domain, direction, max_distance,
object_version)` — confirmed by `SHOW FUNCTIONS` and by probing named
arguments until one was accepted. There is **no namespace parameter and no
include-external flag**, `'EXTERNAL'` returns *"Unknown domain"*, and
`SNOWFLAKE.ACCOUNT_USAGE` contains **no lineage view of any kind**.

So the external half of the lineage graph can be **looked at and not queried**.

### Why this is the post

Cordata's whole argument is that metadata earns its place by being
*queryable evidence* — something you can diff, review, project into an Art. 30
record, and fail a build on. A graph that renders in a console but cannot be
reached from SQL is a picture. You cannot generate a report from it, cannot
diff it between environments, cannot assert on it in CI, and cannot build a
control plane on top of it.

Combined with P2 — only `COMPLETE` accepted — the shape of what Snowflake
shipped is clear and defensible **for what it is**: a visualisation of where
data came from, for a human looking at a table in a console. That is a real
and useful thing. It is not an evidence store, and the mistake would be
treating "we emit OpenLineage to Snowflake" as discharging an obligation that
needs machine-readable, queryable provenance.

### Note on the documentation

The published docs describe a `namespace =>` named argument and an
`object_domain => 'EXTERNAL'` for querying external objects. **Neither exists
in the deployed function.** `SHOW FUNCTIONS` was the reliable source and the
documentation was not.

### P1 answered from the UI: some facets survive, the governance ones do not

Selecting the edge in Snowsight gives an **Edge details** panel:

| field | value |
|---|---|
| Type | **OpenLineage** |
| Run on | Sep 6, 2026, 8:21:42 AM |
| Run ID | `44444444-4444-4444-8444-444444444444` |
| From | Amazon S3 · `fraud_raw/transactions` · `s3://cordata-lake` |
| To | TABLE · `TRANSACTIONS_SCORED` · `LINEAGE_TEST.PUBLIC` |

**`columnLineage` survived.** The external S3 node renders columns `tx_id` and
`amount`, which appeared in no other part of the payload, and `TX_ID` is
highlighted as linked. The deliberately mismatched mapping — `amount` to
`FRAUD_SCORE`, differently named on each side — came back correctly, so this
is stored assertion rather than name matching.

**The governance facets did not.** Nothing in the panel carries `purpose`,
`legal_basis` or `sourceCodeLocation`, and the job's own name,
`transactions-scored-daily`, is absent too. What is kept is addressing —
where from, where to, which columns, which run, when.

Confirmed exhaustively afterwards: the edge panel ends at *Database and
schema* with nothing below it, and the external node's own panel carries three
fields only — `Source: Amazon S3`, `Type: External Node`,
`Namespace: s3://cordata-lake`.

### The complete inventory of what an external edge retains

| sent in the event | kept? | where it shows |
|---|---|---|
| `inputs[].namespace` / `name` | **yes** | node + edge *From* |
| `outputs[].namespace` / `name` | **yes** | edge *To* |
| `outputs[].facets.columnLineage` | **yes** | columns on both nodes, mapping preserved |
| `run.runId` | **yes** | edge *Run ID* |
| `eventTime` | **yes** | edge *Run on* |
| `job.name`, `job.namespace` | **no** | — |
| `job.facets.processing` (`purpose`, `legal_basis`) | **no** | — |
| `job.facets.sourceCodeLocation` | **no** | — |
| `run.facets.nominalTime` | **no** | — |
| `producer` | **no** | — |
| `outputs[].facets.schema` | not distinguishable from the table's own columns | — |

**Addressing is kept. Meaning is discarded.** Where from, where to, which
columns, which run, when — all retained. Why the processing happened, under
what legal basis, from which commit, and even what the job was called — none
of it.

That is a coherent design for a lineage viewer and useless as an Art. 30
input. Part 2 § 1 puts `purpose` and `legal_basis` on every event precisely so
the RoPA can be projected from them; those fields go in and cannot be got
back, from SQL or from the console.

### A 200 does not mean stored

The Run ID on the surviving edge is the **fourth** namespace variant,
`snowflake://myorg-myacct.snowflakecomputing.com`. The fifth variant —
`MYORG-MYACCT`, no scheme, not a valid OpenLineage namespace by any reading
— also returned **HTTP 200 with an empty body**, and is not the edge shown.

So the endpoint returns success for events it does not resolve, and the
producer has no way to tell the difference. There is no ack, no id, no count,
no error, and no SQL surface to check against afterwards.

## P5 — could dbt or Airflow get more back? **No.**

Raised as a review question: the documentation says dbt and Airflow send
lineage to this endpoint, so perhaps their integrations carry data a manual
client cannot. Two answers.

**The documentation says they cannot.** On configuring dbt: *"Configuring dbt
to emit OpenLineage events isn't unique to Snowflake; the only thing specific
to Snowflake is the endpoint and base URL of external lineage."* Same wording
for Airflow. There is no privileged channel — it is JSON over REST either way.

**Tested rather than argued.** Sent one `COMPLETE` event carrying eleven
standard OpenLineage facets, with `producer` set to the dbt integration's own
URL and `User-Agent: OpenLineage-dbt/1.20.0`, so Snowflake had every signal it
would need to treat it as a dbt event.

**One correction to earlier findings: `datasetType` IS retained.** The node
now reports `Type: FILE` where the previous probe's node said
`Type: External Node`. Both facets the documentation names — `columnLineage`
and `datasetType` — work exactly as described. I had not tested the second
one, and the earlier "what is kept" list was incomplete.

The `subType` of `PARQUET` did not surface; only the top-level type.

Everything else was ignored, and the edge panel is identical to the previous
probe's:

| standard facet sent | kept |
|---|---|
| `datasetType` | **yes** |
| `columnLineage` | **yes** |
| `sql` | no |
| `sourceCode`, `sourceCodeLocation` | no |
| `documentation` (job and dataset) | no |
| `ownership` (job and dataset) | no |
| `jobType` (`BATCH` / `DBT` / `MODEL`) | no |
| `parent` (the Airflow DAG and task) | no |
| `processing_engine`, `nominalTime` | no |
| `dataSource`, `storage` | no |
| `dataQualityMetrics`, `outputStatistics` | no |

### The rule, stated exactly

Snowflake keeps **addressing** — namespaces, names, columns, run id, event
time — plus **the two facets it documents**. Everything else in the spec is
discarded, whoever sent it.

### The finding this produced that is better than the original

`parent` is how Airflow's OpenLineage integration says **which DAG and which
task** produced a run. It is dropped. So an Airflow user looking at an edge in
Snowsight cannot tell which pipeline wrote it — the graph knows a run
happened, and not what ran.

That is stronger than our own `processing` facet being ignored. A custom facet
being dropped is unremarkable; the flagship integration's own parentage being
dropped is a statement about what this store is for.

### Grading the claim honestly

The post should not present these as one undifferentiated list:

1. **Our custom `processing` facet** (`purpose`, `legal_basis`) — dropped, and
   Snowflake never promised otherwise. Weakest form of the claim.
2. **Standard OpenLineage facets** — `sql`, `parent`, `ownership`,
   `sourceCodeLocation` and the rest. Dropped. Notable, because these are the
   spec, not our invention.
3. **Core spec fields** — `job.name`, `job.namespace`, `producer`. Dropped.
   These are required properties of a RunEvent, not "additional properties",
   so the documentation's *"Snowflake ignores them"* does not cover them.

## Still to run
- **P3** — is an unmodified `cordata-tech/pipeline-runtime` event accepted?
- **P4** — can the original OpenLineage JSON be read back anywhere?
