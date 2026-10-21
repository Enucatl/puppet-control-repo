"""Check fresh PKI imports on disposable volumes of this machine's Docker daemon."""

import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import time

docker = ["docker", "--host", "unix:///var/run/docker.sock"]
daemon = subprocess.check_output(
    docker + ["info", "--format", "{{.Name}}"], text=True
).strip()
if daemon != socket.gethostname():
    raise SystemExit("Refusing Docker daemon whose name differs from this machine")
source = Path(__file__).resolve().parent
stage = Path(tempfile.mkdtemp(prefix="openvox-infra-public-"))
stage.chmod(0o755)
for path in source.iterdir():
    if path.is_file() and (
        path.name.startswith("infra")
        or path.name in ("openssl.cnf", "verify-crypto.rb")
    ):
        shutil.copyfile(path, stage / path.name)
        (stage / path.name).chmod(0o644)
prefix = f"openvox-infra-check-{os.getuid()}-{int(time.time())}"
volumes = {name: prefix + "-" + name for name in ("ssl", "ca", "data", "trust")}
image = "infra-openvox:24001"


def command(args, text=None, expected=0):
    result = subprocess.run(args, input=text, capture_output=True, text=True)
    if (expected == 0 and result.returncode != 0) or (
        expected != 0 and result.returncode == 0
    ):
        raise RuntimeError(
            f"Unexpected exit {result.returncode}: {result.stdout}\n{result.stderr}"
        )
    return result.stdout + result.stderr


base = docker + [
    "run",
    "--rm",
    "-i",
    "--network",
    "none",
    "--user",
    "999:999",
    "--read-only",
    "--cap-drop",
    "ALL",
    "--security-opt",
    "no-new-privileges:true",
    "--tmpfs",
    "/tmp:rw,nosuid,nodev,size=64m,uid=999,gid=999",
    "-e",
    "OPENSSL_CONF=/infra/openssl.cnf",
    "-v",
    f"{stage}:/infra:ro",
    "-v",
    f"{stage}/infra-puppet.conf:/etc/puppetlabs/puppet/puppet.conf:ro",
]
for name, target in {
    "ssl": "/etc/puppetlabs/puppet/ssl",
    "ca": "/etc/puppetlabs/puppetserver/ca",
    "data": "/opt/puppetlabs/server/data/puppetserver/server_data",
    "trust": "/trust",
}.items():
    base += ["-v", volumes[name] + ":" + target]
base += ["--entrypoint", "/opt/puppetlabs/puppet/bin/ruby", image]

signer = r"""
require 'openssl'
require 'digest'
mode=ARGV[0]
key=OpenSSL::PKey.generate_key('ML-DSA-44')
root=OpenSSL::X509::Certificate.new
root.version=2;root.serial=1001
root.subject=OpenSSL::X509::Name.parse('/CN=Disposable infra helper test root')
root.issuer=root.subject;root.public_key=key
root.not_before=Time.now-60;root.not_after=Time.now+180*86400
factory=OpenSSL::X509::ExtensionFactory.new
factory.subject_certificate=root;factory.issuer_certificate=root
root.add_extension(factory.create_extension('basicConstraints','CA:TRUE,pathlen:1',true))
root.add_extension(factory.create_extension('keyUsage','keyCertSign,cRLSign',true))
root.add_extension(factory.create_extension('subjectKeyIdentifier','hash'))
root.sign(key,nil)
request=OpenSSL::X509::Request.new(File.read('/opt/puppetlabs/server/data/puppetserver/server_data/bootstrap/intermediate.csr'))
raise unless request.verify(request.public_key)
intermediate=OpenSSL::X509::Certificate.new
intermediate.version=2;intermediate.serial=1002
intermediate.subject=request.subject;intermediate.issuer=root.subject
intermediate.public_key=mode=='wrong-intermediate-key' ? OpenSSL::PKey.generate_key('ML-DSA-44') : request.public_key
intermediate.not_before=Time.now-60;intermediate.not_after=Time.now+(mode=='short-issuer' ? 59*86400 : 90*86400)
factory.subject_certificate=intermediate;factory.issuer_certificate=root
intermediate.add_extension(factory.create_extension('basicConstraints','CA:TRUE,pathlen:0',true))
intermediate.add_extension(factory.create_extension('keyUsage','keyCertSign,cRLSign',true))
intermediate.add_extension(factory.create_extension('subjectKeyIdentifier','hash'))
intermediate.add_extension(factory.create_extension('authorityKeyIdentifier','keyid'))
intermediate.sign(key,nil)
crl=OpenSSL::X509::CRL.new
crl.version=1;crl.issuer=root.subject
crl.last_update=Time.now-120;crl.next_update=Time.now+86400
crl.add_extension(OpenSSL::X509::Extension.new('crlNumber',OpenSSL::ASN1::Integer(1).to_der))
crl.sign(key,nil)
File.write('/trust/root.pem',root.to_pem*(mode=='multiple-roots' ? 2 : 1))
File.write('/trust/openvox-chain.pem',intermediate.to_pem+root.to_pem)
File.write('/trust/root.crl',crl.to_pem)
puts Digest::SHA256.hexdigest(root.to_der)
"""
checks = []
try:
    for volume in volumes.values():
        command(docker + ["volume", "create", volume])
    command(base + ["/infra/infra-pki.rb", "prepare"])
    hashes = (
        "require 'digest';puts ARGV.map{|p| Digest::SHA256.file(p).hexdigest}.join(':')"
    )
    keypaths = [
        "/opt/puppetlabs/server/data/puppetserver/server_data/bootstrap/intermediate.key",
        "/etc/puppetlabs/puppet/ssl/private_keys/openvox.home.arpa.pem",
    ]
    before = command(base + ["-e", hashes] + keypaths)
    command(base + ["/infra/infra-pki.rb", "prepare"])
    assert command(base + ["-e", hashes] + keypaths) == before
    checks.append("prepare retry retains both seed-only key identities")
    pin = command(base + ["-", "valid"], text=signer).strip()
    output = command(
        base + ["/infra/infra-pki.rb", "import", "--root-sha256", "0" * 64], expected=1
    )
    assert "fingerprint does not match" in output
    checks.append("wrong operator root pin rejected before CA import")
    for mode, error in [
        ("multiple-roots", "exactly one pinned offline root"),
        ("wrong-intermediate-key", "intermediate key pair"),
        ("short-issuer", "issuer lifetime"),
    ]:
        pin = command(base + ["-", mode], text=signer).strip()
        output = command(
            base + ["/infra/infra-pki.rb", "import", "--root-sha256", pin], expected=1
        )
        assert error in output, output
        checks.append(mode + " rejected before CA import")
    pin = command(base + ["-", "valid"], text=signer).strip()
    output = command(base + ["/infra/infra-pki.rb", "import", "--root-sha256", pin])
    assert "Imported pinned offline-root-signed" in output
    checks.append("actual packaged CA import and seed-only chain verification passed")
    command(
        base
        + [
            "-e",
            "require 'openssl';"
            "raise 'Import must leave the native leaf file unbundled' unless "
            "File.read(ARGV[0]).scan(/-----BEGIN CERTIFICATE-----/).length == 1;"
            "cert=OpenSSL::X509::Certificate.new(File.read(ARGV[0]));"
            "raise 'Imported leaf does not last 60 days' unless "
            "(cert.not_after-Time.now-60*86400).abs < 120",
            "/etc/puppetlabs/puppet/ssl/certs/openvox.home.arpa.pem",
        ]
    )
    checks.append("imported server leaf lasts 60 days")
    for operation in [("prepare",), ("import", "--root-sha256", pin)]:
        output = command(base + ["/infra/infra-pki.rb", *operation], expected=1)
        assert "Existing authority must be restored/renewed" in output
    checks.append("prepare and import refuse to replace established authority")
    assert command(base + ["-e", hashes] + keypaths) == before
    checks.append("import retained original host and intermediate key identities")
    print(
        json.dumps(
            {
                "passed": len(checks),
                "checks": checks,
                "root_key_persisted": False,
                "network": "none",
                "rootfs": "read-only",
            },
            indent=2,
        )
    )
finally:
    for volume in volumes.values():
        command(docker + ["volume", "rm", volume])
    shutil.rmtree(stage)
