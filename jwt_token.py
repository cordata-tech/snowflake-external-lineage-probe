#!/usr/bin/env python3
"""Mint a Snowflake key-pair JWT. stdlib + openssl only, nothing to install.

Snowflake wants iss = <ACCOUNT_LOCATOR>.<USER>.SHA256:<public key fingerprint>
and sub = <ACCOUNT_LOCATOR>.<USER>, both uppercased, signed RS256.
"""
import base64, json, subprocess, time, pathlib

import os

# Account-specific values come from the environment so this file carries no
# identifiers. See README.md; source them from .env.local, which is ignored.
ACCOUNT = os.environ["SF_ACCOUNT_LOCATOR"]   # e.g. AB12345, from CURRENT_ACCOUNT()
USER = os.environ["SF_USER"]
FP = os.environ["SF_KEY_FP"]                 # RSA_PUBLIC_KEY_FP from DESC USER
KEY = pathlib.Path(os.environ.get("SF_PRIVATE_KEY", pathlib.Path(__file__).parent / "rsa_key.p8"))

def b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")

def token(lifetime=3600) -> str:
    now = int(time.time())
    header = {"alg": "RS256", "typ": "JWT"}
    payload = {
        "iss": f"{ACCOUNT}.{USER}.{FP}",
        "sub": f"{ACCOUNT}.{USER}",
        "iat": now,
        "exp": now + lifetime,
    }
    signing_input = (
        b64u(json.dumps(header, separators=(",", ":")).encode())
        + "."
        + b64u(json.dumps(payload, separators=(",", ":")).encode())
    )
    sig = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", str(KEY)],
        input=signing_input.encode(), capture_output=True, check=True,
    ).stdout
    return signing_input + "." + b64u(sig)

if __name__ == "__main__":
    print(token())
