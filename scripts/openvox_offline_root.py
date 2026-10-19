#!/usr/bin/env python3
"""Rehearse an offline-root ceremony without replacing established authorities."""

from __future__ import annotations

import argparse
import base64
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import socket
import ssl
import uuid


def controller():
    """Load the existing fixed local-Docker controller without running it."""
    spec = importlib.util.spec_from_file_location(
        "openvox_rehearsal", Path(__file__).with_name("openvox-rehearsal.py")
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VERIFY = r"""
require 'openssl'
require 'json'
require 'time'
data = JSON.parse(STDIN.read)
root = OpenSSL::X509::Certificate.new(data.fetch('root'))
crl = OpenSSL::X509::CRL.new(data.fetch('crl'))
abort 'Root signature invalid' unless root.verify(root.public_key)
abort 'Root algorithm changed' unless root.signature_algorithm == 'ML-DSA-44' && OpenSSL::ASN1.decode(root.public_key.public_to_der).value[0].value[0].oid == '2.16.840.1.101.3.4.3.17'
abort 'Root CRL issuer mismatch' unless crl.issuer == root.subject
abort 'Root CRL signature invalid' unless crl.verify(root.public_key)
abort 'Root CRL expired or not yet valid' unless crl.last_update <= Time.now && crl.next_update > Time.now
result = {root_signature: true, root_crl_signature: true,
          root_crl_valid: true, crl_next_update: crl.next_update.utc.iso8601}
if data.key?('initial')
  initial = OpenSSL::X509::Certificate.new(data.fetch('initial'))
  renewed = OpenSSL::X509::Certificate.new(data.fetch('renewed'))
  [initial, renewed].each do |cert|
    abort 'Intermediate signature invalid' unless cert.verify(root.public_key)
    abort 'Intermediate issuer mismatch' unless cert.issuer == root.subject
    abort 'Intermediate algorithm changed' unless cert.signature_algorithm == 'ML-DSA-44' && OpenSSL::ASN1.decode(cert.public_key.public_to_der).value[0].value[0].oid == '2.16.840.1.101.3.4.3.17'
    abort 'Intermediate outlives root' unless cert.not_after <= root.not_after
    constraints = cert.extensions.find { |ext| ext.oid == 'basicConstraints' }
    abort 'Intermediate path length incorrect' unless constraints && constraints.value == 'CA:TRUE, pathlen:0'
  end
  abort 'Renewal replaced intermediate key' unless initial.public_key.public_to_der == renewed.public_key.public_to_der
  abort 'Renewal did not extend validity' unless renewed.not_after > initial.not_after
  abort 'Renewal reused serial' unless renewed.serial != initial.serial
  abort 'Wrong disposable intermediate revoked' unless crl.revoked.any? { |entry| entry.serial == initial.serial }
  abort 'Renewed certificate unexpectedly revoked' if crl.revoked.any? { |entry| entry.serial == renewed.serial }
  store = OpenSSL::X509::Store.new
  store.add_cert(root)
  store.add_crl(crl)
  store.flags = OpenSSL::X509::V_FLAG_CRL_CHECK
  abort 'Revoked intermediate accepted by verifier' if store.verify(initial)
  abort 'Wrong revocation error' unless store.error == OpenSSL::X509::V_ERR_CERT_REVOKED
  abort 'Renewed intermediate rejected' unless store.verify(renewed)
  result.merge!(intermediate_signatures: true, same_key_renewal: true,
                renewal_validity_extended: true, revoked_intermediate_denied: true,
                renewed_intermediate_accepted: true)
end
puts JSON.generate(result)
"""

GENERATE = r"""
require 'openssl'
require 'fileutils'
File.umask(0o077)
directory = ARGV.fetch(0)
abort 'Probe directory already exists' if File.exist?(directory)
FileUtils.mkdir_p(directory)
key = OpenSSL::PKey.generate_key('ML-DSA-44')
csr = OpenSSL::X509::Request.new
csr.version = 0
csr.subject = OpenSSL::X509::Name.parse('/CN=Disposable offline root ceremony probe')
csr.public_key = key
csr.sign(key, nil)
abort 'CSR signature invalid' unless csr.verify(key)
File.write("#{directory}/intermediate.key", key.private_to_pem)
File.write("#{directory}/intermediate.csr", csr.to_pem)
puts csr.to_pem
"""


def ceremony(action: str, expected: str, runtime=None) -> dict:
    """Refresh a root CRL or exercise a disposable intermediate lifecycle.

    The established offline root is pinned before any signing or revocation.
    Rehearsal revokes only a certificate it created in this invocation.
    """
    if not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("Expected root fingerprint must be 64 lowercase hex digits")
    if action not in ("rehearse", "publish"):
        raise ValueError("Unknown ceremony action")
    runtime = runtime or controller()
    daemon = runtime.execute(runtime.DOCKER + ["info", "--format", "{{.Name}}"])
    if daemon.strip() != socket.gethostname():
        raise RuntimeError("Offline ceremony requires the local Docker daemon")
    container = f"{runtime.PROJECT}-root-1"
    network = runtime.execute(
        runtime.DOCKER
        + ["inspect", "--format", "{{.HostConfig.NetworkMode}}", container]
    ).strip()
    if network != "none":
        raise RuntimeError("Established offline root must have network_mode none")
    identifier = uuid.uuid4().hex
    output = runtime.WORK / "public" / "offline-root" / identifier
    report = {
        "action": action,
        "root_sha256": expected,
        "public_artifacts": str(output),
    }
    try:
        runtime.start_vault("root")
        root = runtime.api("root", "GET", "pki/cert/ca")["data"]["certificate"]
        actual = hashlib.sha256(ssl.PEM_cert_to_DER_cert(root)).hexdigest()
        if actual != expected:
            raise RuntimeError(
                "Established root fingerprint mismatch; ceremony refused"
            )
        public = {"root.pem": root}
        verification = {"root": root}
        if action == "rehearse":
            csr = runtime.inside(
                "root",
                [
                    runtime.RUBY,
                    "-e",
                    GENERATE,
                    f"/state/offline-root-probes/{identifier}",
                ],
            )
            issued = []
            for lifetime in ("48h", "72h"):
                signed = runtime.api(
                    "root",
                    "POST",
                    "pki/root/sign-intermediate",
                    {
                        "csr": csr,
                        "format": "pem",
                        "ttl": lifetime,
                        "max_path_length": 0,
                    },
                )["data"]
                issued.append(signed)
            # Only the fresh disposable serial is eligible for this operation.
            runtime.api(
                "root",
                "POST",
                "pki/revoke",
                {"serial_number": issued[0]["serial_number"]},
            )
            public["intermediate-initial.pem"] = issued[0]["certificate"]
            public["intermediate-renewed.pem"] = issued[1]["certificate"]
            verification.update(
                initial=issued[0]["certificate"], renewed=issued[1]["certificate"]
            )
        rotation = runtime.api("root", "GET", "pki/crl/rotate")["data"]
        if not rotation["success"]:
            raise RuntimeError("Root CRL rotation failed")
        crl = runtime.api("root", "GET", "pki/cert/crl")["data"]["certificate"]
        public["root.crl.pem"] = crl
        verification["crl"] = crl
        report["checks"] = json.loads(
            runtime.inside(
                "root", [runtime.RUBY, "-e", VERIFY], json.dumps(verification)
            )
        )
        report["crl_next_update"] = report["checks"].pop("crl_next_update")
        report["issuer_urls"] = runtime.api("root", "GET", "pki/config/urls")["data"]
        output.mkdir(parents=True, mode=0o755)
        for name, contents in public.items():
            target = output / name
            target.write_text(contents + "\n")
            target.chmod(0o644)
        der = runtime.inside(
            "root",
            [
                runtime.RUBY,
                "-e",
                "require 'openssl'; require 'base64'; puts Base64.strict_encode64(OpenSSL::X509::CRL.new(STDIN.read).to_der)",
            ],
            crl,
        )
        (output / "root.crl").write_bytes(base64.b64decode(der.strip(), validate=True))
        (output / "root.crl").chmod(0o644)
    finally:
        runtime.compose("stop", "root")
    state = runtime.execute(
        runtime.DOCKER + ["inspect", "--format", "{{.State.Running}}", container]
    ).strip()
    if state != "false":
        raise RuntimeError("Offline root did not stop after ceremony")
    report["checks"]["offline_root_stopped"] = True
    (output / "evidence.json").write_text(json.dumps(report, indent=2) + "\n")
    (output / "evidence.json").chmod(0o644)
    (runtime.WORK / "offline-root-lifecycle.json").write_text(
        json.dumps(report, indent=2) + "\n"
    )
    return report


def main() -> None:
    """Run an explicit ceremony against the existing disposable authority."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("rehearse", "publish"))
    parser.add_argument("--expected-root-sha256", required=True)
    arguments = parser.parse_args()
    report = ceremony(arguments.action, arguments.expected_root_sha256)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
