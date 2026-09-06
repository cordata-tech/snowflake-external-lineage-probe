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

## P1 — facet survival: **BLOCKED, and the blocker may be the finding**

Sent a full `COMPLETE` event: S3 input, the Snowflake table
`LINEAGE_TEST.PUBLIC.TRANSACTIONS_SCORED` as output, carrying a custom
`job.facets.processing` (`purpose`, `legal_basis`), `sourceCodeLocation`,
`schema` and `columnLineage`. **HTTP 200, empty body.**

`GET_LINEAGE` on the table returns **0 rows**, checked repeatedly over roughly
twenty minutes. So the facet question cannot be answered yet — no edge exists
to read facets from.

**What was ruled out**

- Namespace spelling. `CURRENT_ORGANIZATION_NAME()`/`CURRENT_ACCOUNT_NAME()`
  confirm `MYORG`/`MYACCT`, matching the documented
  `snowflake://ORG-ACCOUNT` form.
- My own query being wrong on the Snowflake side — the table anchor is a plain
  `'TABLE'` domain query and compiles fine, returning the documented columns.

**What is NOT yet ruled out**

- Ingestion latency. Nothing in the documentation states a visibility SLA.
- Whether the read path for external-sourced edges needs something else again.

**The observation worth keeping either way.** Five namespace variants were
sent, including `MYORG-MYACCT` with **no scheme at all**, which is not a
valid OpenLineage namespace by any reading:

| namespace sent | HTTP |
|---|---|
| `snowflake://MYORG-MYACCT` | 200 |
| `snowflake://myorg-myacct` | 200 |
| `snowflake://AB12345` (locator) | 200 |
| `snowflake://myorg-myacct.snowflakecomputing.com` | 200 |
| `MYORG-MYACCT` (malformed) | 200 |

All five accepted, all with an empty body. **A producer gets no signal
distinguishing an event that linked from one that was accepted and dropped.**
If that holds after latency is excluded it is a finding in its own right, and
it bears directly on the post's thesis: a sink you cannot verify wrote
anything is not an evidence store.

### The control that isolates it

Ran a native Snowflake lineage edge through the *same* read path at the *same*
moment, to separate "read path or latency" from "external ingest did not land":

```sql
CREATE OR REPLACE TABLE LINEAGE_TEST.PUBLIC.SCORED_COPY AS
  SELECT tx_id, fraud_score FROM LINEAGE_TEST.PUBLIC.TRANSACTIONS_SCORED;
```

| query | result |
|---|---|
| native edge, ~10 seconds after the CTAS | **1 row** — `TRANSACTIONS_SCORED -> SCORED_COPY`, distance 1 |
| external edge, ~25 minutes after ingest | **0 rows** |

That rules out three explanations at once: the read path works, `GET_LINEAGE`
is not generally lagged, and the query shape is right — an identical query
returns rows for the native edge.

What remains is either an external-ingest visibility lag far longer than
native, or events accepted and dropped. **Both support the post's thesis.** If
it is a lag, a 200 tells a producer nothing about whether or when its lineage
lands; if it is a drop, the sink discards silently. Either way the sink cannot
be verified from outside, which is the property an evidence store needs.

**Still to do before publishing this:** a re-check hours later, and the
Snowsight lineage UI as an independent read path.
The Topcoat pilot recorded a false finding this way once — a stale build that
looked like a framework bug — and the rule since is that an environmental
explanation gets excluded before a vendor one gets published.

### My own errors while probing, for the record

- `SYSTEM$GET_LINEAGE` does not exist; it is `SNOWFLAKE.CORE.GET_LINEAGE`, a
  table function.
- The published docs describe a `namespace =>` named argument for querying
  external objects with `object_domain => 'EXTERNAL'`. **Neither exists in the
  deployed function.** `SHOW FUNCTIONS` gives the real signature —
  `GET_LINEAGE(VARCHAR, VARCHAR, VARCHAR, DEFAULT NUMBER, DEFAULT VARCHAR)` —
  and `'EXTERNAL'` returns *"Unknown domain: EXTERNAL."* Introspecting the
  account beat reading the documentation, which is worth a line in the post.

## Still to run
- **P3** — is an unmodified `cordata-tech/pipeline-runtime` event accepted?
- **P4** — can the original OpenLineage JSON be read back anywhere?
