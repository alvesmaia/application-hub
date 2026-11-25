"""Read access to the apps' Postgres from other machines (dashboards, DBeaver): the configuration, the `pg_hba.conf`
the hub writes and the TLS certificate.

Off by default. When on (`HUB_DB_PUBLIC=1`, port published by the compose), only members of `hub_readers` (the read
accounts made in Administration) come from outside, always over TLS, from `HUB_DB_ALLOWED_CIDRS`; the hub's and the
apps' service roles stay reachable only from the Docker networks. Traffic whose source address was lost (it shows up
as a network gateway) is refused: fail closed."""

from __future__ import annotations

import datetime
import hashlib
import ipaddress
import os
from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import urlsplit

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

READERS = "hub_readers"
NEVER = ("hub", "postgres", "template0", "template1")  # never open to a read account
Net = tuple[str, str]  # (subnet CIDR, gateway IP) of a Docker network the database is on


@dataclass(frozen=True)
class DbAccessCfg:
    public: bool
    port: int
    host: str
    cidrs: tuple[str, ...]
    bad_cidrs: tuple[str, ...]
    cert_file: str
    key_file: str


def from_env(env: Mapping[str, str] = os.environ) -> DbAccessCfg:
    cidrs, bad = [], []
    for raw in (env.get("HUB_DB_ALLOWED_CIDRS") or "").replace(";", ",").split(","):
        if not (s := raw.strip()):
            continue
        try:
            cidrs.append(str(ipaddress.ip_network(s, strict=False)))
        except ValueError:
            bad.append(s)
    host = env.get("HUB_DB_PUBLIC_HOST") or urlsplit(env.get("HUB_PUBLIC_URL") or "").hostname or "localhost"
    port = env.get("HUB_DB_PUBLIC_PORT") or "5432"
    return DbAccessCfg(env.get("HUB_DB_PUBLIC") == "1", int(port) if port.isdigit() else 5432, host, tuple(cidrs),
                       tuple(bad), env.get("HUB_DB_TLS_CERT_FILE", ""), env.get("HUB_DB_TLS_KEY_FILE", ""))  # fmt: skip


def hba(cfg: DbAccessCfg, nets: list[Net]) -> str:
    """The whole `pg_hba.conf` (first matching line wins). Off = the image's default (password from anywhere it
    is reachable: the port is only published on 127.0.0.1)."""
    head = ["# Managed by Application Hub (hub/admin/dbaccess_net.py): edits here are overwritten.",
            "local all all trust", "host all all 127.0.0.1/32 scram-sha-256", "host all all ::1/128 scram-sha-256"]  # fmt: skip
    if not cfg.public:
        return "\n".join([*head, "host all all all scram-sha-256", ""])
    lines = [*head, *(f"host all all {gw}/32 reject" for _, gw in nets if gw)]
    lines.append(f"host {','.join(NEVER)} +{READERS} all reject")
    lines += [f"hostssl all +{READERS} {c} scram-sha-256" for c in cfg.cidrs]
    lines.append(f"host all +{READERS} all reject")
    lines += [f"host all all {subnet} scram-sha-256" for subnet, _ in nets]
    lines.append("host all all all reject")
    return "\n".join([*lines, ""])


def self_signed(host: str) -> tuple[bytes, bytes]:
    """(certificate PEM, key PEM) for the database when the installation brought none (clients use sslmode=require
    or pin the fingerprint shown in Administration)."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, host[:64])])
    try:
        san: x509.GeneralName = x509.IPAddress(ipaddress.ip_address(host))
    except ValueError:
        san = x509.DNSName(host)
    now = datetime.datetime.now(datetime.UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(days=1))
            .not_valid_after(now + datetime.timedelta(days=3650))
            .add_extension(x509.SubjectAlternativeName([san]), critical=False).sign(key, hashes.SHA256()))  # fmt: skip
    pem_key = key.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
    )
    return cert.public_bytes(serialization.Encoding.PEM), pem_key


def fingerprint(cert_pem: bytes) -> str:
    """SHA-256 of the certificate (what DBeaver and psql show when asked to trust it)."""
    der = x509.load_pem_x509_certificate(cert_pem).public_bytes(serialization.Encoding.DER)
    return ":".join(f"{b:02X}" for b in hashlib.sha256(der).digest())
