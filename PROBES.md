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

## Still to run

- **P1** — does a custom `job.facets.processing` facet (`purpose`,
  `legal_basis`) survive ingest and come back from `GET_LINEAGE`?
- **P3** — is an unmodified `cordata-tech/pipeline-runtime` event accepted?
- **P4** — can the original OpenLineage JSON be read back anywhere?
