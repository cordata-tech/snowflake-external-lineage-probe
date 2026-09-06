#!/bin/bash
# Run a statement via the Snowflake SQL API using the same key-pair JWT.
T=$(python3 "$(dirname "$0")/jwt_token.py")
python3 - "$1" <<'PY' > /tmp/stmt.json
import json,sys
print(json.dumps({"statement":sys.argv[1],"warehouse":"COMPUTE_WH",
                  "role":"ACCOUNTADMIN","timeout":60}))
PY
curl -s -X POST "${SF_HOST}/api/v2/statements" \
  -H "Content-Type: application/json" -H "Accept: application/json" \
  -H "User-Agent: cordata-lineage-probe/1.0" \
  -H "X-Snowflake-Authorization-Token-Type: KEYPAIR_JWT" \
  -H "Authorization: Bearer $T" --data @/tmp/stmt.json
