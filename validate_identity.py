#!/usr/bin/env python3
"""
validate_identity.py - run ON the bastion VM (vm-it-monitor-bastion).

Proves the VM's system-assigned managed identity can:
  1. read a secret from Key Vault   (RBAC: Key Vault Secrets User)
  2. log in to Azure SQL Database   (contained user + db_datareader)
No passwords or connection-string secrets anywhere: the VM asks the
Instance Metadata Service (IMDS) for a token, then presents that token.

Usage: python3 validate_identity.py <key-vault-secret-name>
"""
import json
import struct
import sys
import urllib.parse
import urllib.request

KEY_VAULT = "kv-aquacore"
SQL_SERVER = "sql-aquacore-water-ot.database.windows.net"
SQL_DATABASE = "db-aquacore-water-ot"

IMDS = "http://169.254.169.254/metadata/identity/oauth2/token"


def get_token(resource):
    """Ask IMDS (only reachable from inside the VM) for an access token."""
    qs = urllib.parse.urlencode({"api-version": "2018-02-01", "resource": resource})
    req = urllib.request.Request(f"{IMDS}?{qs}", headers={"Metadata": "true"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.load(r)["access_token"]


def check_key_vault(secret_name):
    print(f"[1/2] Key Vault: reading secret '{secret_name}' from {KEY_VAULT} ...")
    token = get_token("https://vault.azure.net")
    url = f"https://{KEY_VAULT}.vault.azure.net/secrets/{secret_name}?api-version=7.4"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            value = json.load(r)["value"]
        # Never print the secret itself - length is enough proof.
        print(f"      OK - secret retrieved ({len(value)} characters, value not shown)")
        return True
    except urllib.error.HTTPError as e:
        print(f"      FAILED - HTTP {e.code}: {e.reason}")
        if e.code == 403:
            print("      Check: Key Vault Secrets User assigned to the VM identity?")
        return False


def check_sql():
    print(f"[2/2] SQL: connecting to {SQL_DATABASE} with the identity token ...")
    try:
        import pyodbc
    except ImportError:
        print("      FAILED - pyodbc not installed (pip3 install pyodbc)")
        return False

    token = get_token("https://database.windows.net/")
    raw = token.encode("utf-16-le")
    token_struct = struct.pack(f"<I{len(raw)}s", len(raw), raw)
    SQL_COPT_SS_ACCESS_TOKEN = 1256

    conn_str = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{SQL_SERVER},1433;"
        f"Database={SQL_DATABASE};"
        "Encrypt=yes;TrustServerCertificate=no;Connection Timeout=30;"
    )
    try:
        conn = pyodbc.connect(conn_str, attrs_before={SQL_COPT_SS_ACCESS_TOKEN: token_struct})
        row = conn.cursor().execute("SELECT USER_NAME()").fetchone()
        print(f"      OK - connected as: {row[0]}")
        conn.close()
        return True
    except pyodbc.Error as e:
        print(f"      FAILED - {e}")
        return False


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("Usage: python3 validate_identity.py <key-vault-secret-name>")
    kv_ok = check_key_vault(sys.argv[1])
    sql_ok = check_sql()
    print("\nRESULT:", "PASS - managed identity works for both" if kv_ok and sql_ok else "FAIL - see messages above")
    sys.exit(0 if kv_ok and sql_ok else 1)
