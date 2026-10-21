#!/usr/bin/env python3
"""Apply service certificates and renew OpenVox's native server identity."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
from pathlib import Path
import subprocess
import sys
import time

SOURCE = Path(__file__).resolve().parents[1]
VAULT_SECRETS = SOURCE / "docker/vault/secrets"
IPA_SECRETS = SOURCE / "docker/freeipa/secrets"
RUBY = "/opt/puppetlabs/puppet/bin/ruby"
DOCKER = ["docker", "--host", "unix:///var/run/docker.sock"]


def run(arguments: list[str], data: str | None = None) -> str:
    """Capture diagnostics without printing private installer input."""
    return subprocess.run(
        arguments, input=data, text=True, capture_output=True, check=True, timeout=120
    ).stdout


def check(service: str, *, renewal_due: bool = False) -> bool:
    """Compare the authenticated TLS leaf to disk and optionally check renewal due."""
    directory = VAULT_SECRETS if service == "vault" else IPA_SECRETS
    worker = """require 'openssl'; require 'socket'; require 'timeout'
expected = OpenSSL::X509::Certificate.new(File.read(ARGV.shift))
context = OpenSSL::SSL::SSLContext.new
context.ca_file = ARGV.shift
context.verify_mode = OpenSSL::SSL::VERIFY_PEER
hostname = ARGV.shift
renew_before = ARGV.shift.to_i
exit 1 if renew_before > 0 && expected.not_after <= Time.now + renew_before * 86400
ARGV.each do |port|
  Timeout.timeout(10) do
    Socket.tcp('127.0.0.1', port.to_i, connect_timeout: 5) do |socket|
      tls = OpenSSL::SSL::SSLSocket.new(socket, context)
      begin
        tls.hostname = hostname
        tls.connect
        tls.post_connection_check(hostname)
        exit 1 unless tls.peer_cert.to_der == expected.to_der
      ensure
        tls.close
      end
    end
  end
end
"""
    try:
        if service == "openvox":
            command = DOCKER + [
                "exec",
                "infra-openvox-1",
                RUBY,
                "-e",
                worker,
                "/etc/puppetlabs/puppet/ssl/certs/openvox.home.arpa.pem",
                "/trust/root.pem",
                "openvox.home.arpa",
                "30" if renewal_due else "0",
                "8140",
            ]
        else:
            command = [
                RUBY,
                "-e",
                worker,
                str(directory / "fullchain.pem"),
                str(VAULT_SECRETS / "ca.pem"),
                "vault.home.arpa" if service == "vault" else "ipa.home.arpa",
                "0",
                *(["8200"] if service == "vault" else ["443", "636"]),
            ]
        run(command)
    except subprocess.CalledProcessError, subprocess.TimeoutExpired:
        return False
    return True


def apply(service: str) -> None:
    """Apply canonical PEM files or renew the OpenVox identity through its native CLI."""
    if service == "openvox":
        run(
            DOCKER
            + [
                "exec",
                "infra-openvox-1",
                "/opt/puppetlabs/bin/puppet",
                "ssl",
                "renew_cert",
                "--if-expiring-in",
                "30d",
                "--config",
                "/etc/puppetlabs/puppet/puppet.conf",
            ]
        )
        if not check(service):
            run(DOCKER + ["restart", "--time", "60", "infra-openvox-1"])
        return
    if service == "vault":
        public = VAULT_SECRETS / "public/vault.pub.pem"
        public.parent.mkdir(mode=0o755, exist_ok=True)
        temporary = public.with_suffix(".tmp")
        temporary.write_text(
            run(
                [
                    RUBY,
                    "-ropenssl",
                    "-e",
                    "puts OpenSSL::PKey.read(File.read(ARGV[0])).public_to_pem",
                    str(VAULT_SECRETS / "key.pem"),
                ]
            )
        )
        temporary.chmod(0o644)
        os.chown(temporary, 100100, 100100)
        temporary.replace(public)
        run(DOCKER + ["kill", "--signal", "HUP", "infra-vault-1"])
        return
    worker = """import json, os, sys, tempfile
from pathlib import Path
from ipaserver.install.ipa_server_certinstall import ServerCertInstall
os.umask(0o077)
request = json.load(sys.stdin)
ServerCertInstall.make_parser()
with tempfile.TemporaryDirectory(prefix="ipa-certinstall-") as directory:
    chain = Path(directory) / "chain.pem"
    key = Path(directory) / "key.pem"
    chain.write_text(request["chain"])
    key.write_text(request["private_key"])
    options, args = ServerCertInstall.option_parser.parse_args([
        "--http", "--dirsrv", "--pin=", str(chain), str(key)])
    options.dirman_password = request["dm_password"]
    result = ServerCertInstall(options, args).execute()
raise SystemExit(result or 0)
"""
    run(
        DOCKER + ["exec", "-i", "infra-ipa-1", "python3", "-c", worker],
        json.dumps(
            {
                "chain": (IPA_SECRETS / "fullchain.pem").read_text(),
                "private_key": (IPA_SECRETS / "key.pem").read_text(),
                "dm_password": json.loads((IPA_SECRETS / "installer.json").read_text())[
                    "dm_password"
                ],
            }
        ),
    )
    run(
        DOCKER
        + [
            "exec",
            "infra-ipa-1",
            "systemctl",
            "restart",
            "httpd.service",
            "dirsrv@HOME-ARPA.service",
        ]
    )


def main() -> None:
    """Apply only when live certificates differ, retrying on every Puppet run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("service", choices=("vault", "ipa", "openvox"))
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    os.environ["OPENSSL_CONF"] = str(SOURCE / "docker/openvox/openssl.cnf")
    if args.check:
        raise SystemExit(
            0 if check(args.service, renewal_due=args.service == "openvox") else 1
        )
    with Path("/run/lock/atlas-openvox-maintenance.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        apply(args.service)
        deadline = time.monotonic() + (300 if args.service == "openvox" else 60)
        while not check(args.service):
            if time.monotonic() >= deadline:
                raise RuntimeError("Service has not loaded the Puppet certificate")
            time.sleep(2)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as error:
        print(
            error.stderr or f"Certificate command failed (exit {error.returncode}).",
            file=sys.stderr,
            end="",
        )
        raise SystemExit(error.returncode) from None
