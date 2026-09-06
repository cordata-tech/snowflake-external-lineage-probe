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

### Still open on P1

Whether the custom `job.facets.processing` fields (`purpose`, `legal_basis`)
survived is **still unanswered**, and now for a different reason: there is no
SQL path to the external edge, so there is nowhere to read facets from. Next
step is to check whether the Snowsight UI surfaces any facet detail when the
external node or the edge is selected.

## Still to run
- **P3** — is an unmodified `cordata-tech/pipeline-runtime` event accepted?
- **P4** — can the original OpenLineage JSON be read back anywhere?
