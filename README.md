# Snowflake external lineage — probe kit

Snowflake moved **external lineage to GA on 2026-09-03**. It ingests
OpenLineage events over REST, shows external objects in Snowsight and exposes
them through `GET_LINEAGE()`.

This repository is the evidence behind a Cordata post about what that does and
does not buy you. Findings live in [`PROBES.md`](./PROBES.md), written while
running rather than reconstructed afterwards, including the ones that went the
other way.

**Everything here was run on a 30-day free trial with no payment method**, so
any claim in the post can be reproduced for nothing.

## Reproducing

Requires an Enterprise-or-higher Snowflake account. Edition, cloud and region
are fixed at signup and cannot be changed afterwards — choosing Standard wastes
the trial, because external lineage needs Enterprise.

```bash
# 1. key pair — the private half is gitignored and must stay that way
openssl genrsa 2048 | openssl pkcs8 -topk8 -inform PEM -out rsa_key.p8 -nocrypt
openssl rsa -in rsa_key.p8 -pubout -out rsa_key.pub
chmod 600 rsa_key.p8
```

```sql
-- 2. register the public half and grant the privileges
ALTER USER <you> SET RSA_PUBLIC_KEY='<contents of rsa_key.pub, no header lines>';
DESC USER <you>;                     -- read RSA_PUBLIC_KEY_FP from the output

GRANT INGEST LINEAGE ON ACCOUNT TO ROLE <role>;
GRANT DELETE LINEAGE ON ACCOUNT TO ROLE <role>;
```

```bash
# 3. point the scripts at your account
cp .env.local.example .env.local && $EDITOR .env.local && source .env.local

python3 jwt_token.py      # signs a key-pair JWT, stdlib + openssl, no SDK
./sql.sh "SELECT CURRENT_VERSION()"
```

## Layout

| | |
|---|---|
| `PROBES.md` | the findings, with raw requests and responses |
| `jwt_token.py` | signs a key-pair JWT — RS256 via `openssl`, no dependencies |
| `sql.sh` | run a statement through the SQL API with the same JWT |
| `p*.json` | the event payloads sent to `/api/v2/lineage/external-lineage` |

## Related

- [A pipeline is a descriptor, not a program](https://cordata.tech/en/blog/pipelines-as-descriptors)
- [The pipeline half — OpenLineage, Great Expectations, and metadata-first ETL](https://cordata.tech/en/blog/pipeline-half-openlineage-gx)
- [`cordata-tech/pipeline-runtime`](https://github.com/cordata-tech/pipeline-runtime) — the emitter these events imitate
