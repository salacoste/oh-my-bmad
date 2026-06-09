"""Violation fixture: hardcoded cert/key paths — MUST surface SECRETS001."""

# Absolute path to a cert file — VIOLATION.
_SERVER_CERT = "/etc/ssl/server.pem"

# Absolute path under /certs/ — VIOLATION.
_CLIENT_KEY = "/certs/client.key"

# Absolute path with .crt extension — VIOLATION.
_CA_CHAIN = "/etc/ssl/ca-chain.crt"
