#!/usr/bin/env python3
"""Dry-run IPA migration or rehearse a production-mode import in an isolated clone."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import socket
import stat
import time

SPEC = importlib.util.spec_from_file_location(
    "atlas_freeipa", Path(__file__).with_name("atlas-freeipa.py")
)
assert SPEC and SPEC.loader
controller = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(controller)
infra = controller.infra

# Run beside IPA's own libraries; credentials enter Docker only through stdin.
WORKER = r"""
import base64
import configparser
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import ldap
import ldif
from ipapython.dn import DN

request = json.load(sys.stdin)
os.umask(0o077)
config = configparser.ConfigParser()
config.read('/etc/ipa/default.conf')
assert config['global']['host'] == 'ipa.home.arpa'
assert config['global']['realm'] == 'HOME.ARPA'
local_addresses = {'127.0.0.1', '::1'} | set(
    subprocess.check_output(['hostname', '-I'], text=True).split())
target_addresses = {entry[4][0] for entry in socket.getaddrinfo('ipa.home.arpa', 389)}
assert target_addresses and target_addresses <= local_addresses, \
    'ipa.home.arpa must resolve exclusively to this container'
marker = Path('/data/.atlas-ipa-migration-rehearsed')
assert not marker.exists(), 'Use a fresh clone for each rehearsal'

attributes = {
    'users': ['uid', 'uidNumber', 'gidNumber', 'ipaNTSecurityIdentifier'],
    'groups': ['cn', 'gidNumber', 'ipaNTSecurityIdentifier', 'member'],
    'hbac': ['cn', 'ipaEnabledFlag', 'memberUser', 'memberHost', 'memberService',
             'userCategory', 'hostCategory', 'serviceCategory'],
    'sudo': ['cn', 'ipaEnabledFlag', 'memberUser', 'memberHost', 'memberAllowCmd',
             'memberDenyCmd', 'userCategory', 'hostCategory', 'cmdCategory',
             'ipaSudoOpt', 'ipaSudoRunAs', 'ipaSudoRunAsGroup', 'sudoCmd',
             'member', 'externalUser', 'ipaSudoRunAsUserCategory',
             'ipaSudoRunAsGroupCategory'],
}
bases = {'users': 'cn=users,cn=accounts', 'groups': 'cn=groups,cn=accounts',
         'hbac': 'cn=hbac', 'sudo': 'cn=sudo'}

def connection(host, password, ca):
    conn = ldap.initialize('ldap://' + host)
    conn.set_option(ldap.OPT_NETWORK_TIMEOUT, 20)
    conn.set_option(ldap.OPT_X_TLS_CACERTFILE, ca)
    conn.set_option(ldap.OPT_X_TLS_REQUIRE_CERT, ldap.OPT_X_TLS_DEMAND)
    conn.set_option(ldap.OPT_X_TLS_NEWCTX, 0)
    conn.start_tls_s()
    conn.simple_bind_s('cn=Directory Manager', password)
    return conn

def snapshot(host, password, ca):
    conn = connection(host, password, ca)
    result = {}
    for kind, base in bases.items():
        entries = conn.search_s(base + ',dc=home,dc=arpa', ldap.SCOPE_SUBTREE,
                                '(objectClass=*)', attributes[kind])
        result[kind] = {
            dn.lower(): {attr.lower(): sorted(base64.b64encode(v).decode()
                         for v in values) for attr, values in attrs.items()}
            for dn, attrs in entries if dn and attrs
        }
    entries = conn.search_s('cn=certificates,cn=ipa,cn=etc,dc=home,dc=arpa',
                            ldap.SCOPE_SUBTREE, '(objectClass=*)', ['*'])
    result['trust'] = {
        dn.lower(): {attr.lower(): sorted(base64.b64encode(v).decode()
                     for v in values) for attr, values in attrs.items()}
        for dn, attrs in entries if dn
    }
    result['schema'] = {
        attr.lower(): sorted(base64.b64encode(v).decode() for v in values)
        for _, attrs in conn.search_s('cn=schema', ldap.SCOPE_BASE,
            '(objectClass=*)', ['attributeTypes', 'objectClasses'])
        for attr, values in attrs.items()
    }
    conn.unbind_s()
    return result

def export_database(conn, path):
    suffix = DN('dc=home,dc=arpa')
    excluded = [DN('cn=certificates,cn=ipa,cn=etc', suffix), DN('cn=ca', suffix)]
    entries = conn.search_s(str(suffix), ldap.SCOPE_SUBTREE,
                            '(objectClass=*)', ['*', 'nsaccountlock'])
    entries = sorted((entry for entry in entries if entry[0]),
                     key=lambda entry: (len(DN(entry[0])), entry[0].lower()))
    counts = {'exported': 0, 'excluded_authority': 0}
    with path.open('w') as stream:
        writer = ldif.LDIFWriter(stream)
        for dn, attrs in entries:
            if any(tree in DN(dn) for tree in excluded):
                counts['excluded_authority'] += 1
                continue
            writer.unparse(dn, attrs)
            counts['exported'] += 1
    assert entries and DN(entries[0][0]) == suffix, 'Export must start at domain suffix'
    return counts

with tempfile.TemporaryDirectory(prefix='ipa-migrate-', dir='/run') as work:
    work = Path(work)
    for name, value in [('source-password', request['source_password']),
                        ('source-ca.pem', request['source_ca']),
                        ('target-ca.pem', request['target_ca'])]:
        (work / name).write_text(value)
    source = snapshot('freeipa.home.arpa', request['source_password'],
                      str(work / 'source-ca.pem'))
    before = snapshot('ipa.home.arpa', request['target_password'],
                      str(work / 'target-ca.pem'))
    if request['action'] == 'rehearse':
        assert set(before['users']) == {'uid=admin,cn=users,cn=accounts,dc=home,dc=arpa'}, \
            'Rehearsal requires a fresh bootstrap-only target'
    conn = connection('freeipa.home.arpa', request['source_password'],
                      str(work / 'source-ca.pem'))
    export_counts = export_database(conn, work / 'source.ldif')
    conn.unbind_s()
    command = ['ipa-migrate', 'prod-mode', 'freeipa.home.arpa', '--no-prompt',
               '--skip-config', '--db-ldif', str(work / 'source.ldif'),
               '--bind-pw-file', str(work / 'source-password'),
               '--cacertfile', str(work / 'source-ca.pem'),
               '--log-file', str(work / 'migration.log')]
    if request['action'] == 'dry-run':
        command += ['--dryrun', '--dryrun-record', str(work / 'proposed.ldif')]
    (work / 'krb5.conf').write_text('[libdefaults]\n default_ccache_name = FILE:' +
        str(work / 'krb5cc') + '''
 default_realm = HOME.ARPA
 forwardable = true
 dns_lookup_kdc = false
 rdns = false
 dns_canonicalize_hostname = false
[realms]
 HOME.ARPA = {
  kdc = ipa.home.arpa
 }
''')
    environment = dict(os.environ, KRB5CCNAME='FILE:' + str(work / 'krb5cc'),
                       KRB5_CONFIG=str(work / 'krb5.conf'))
    subprocess.run(['kinit', 'admin@HOME.ARPA'],
                   input=request['target_admin_password'] + '\n',
                   text=True, capture_output=True, check=True, env=environment)
    process = subprocess.run(command, text=True, capture_output=True,
                             env=environment)
    login = {}
    login_error = None
    if request['action'] == 'rehearse' and process.returncode == 0:
        try:
            conn = connection('ipa.home.arpa', request['target_password'],
                              str(work / 'target-ca.pem'))
            enabled = conn.search_s('cn=ipaconfig,cn=etc,dc=home,dc=arpa',
                ldap.SCOPE_BASE, '(objectClass=*)', ['ipaMigrationEnabled'])[0][1]
            assert enabled.get('ipaMigrationEnabled', [b'FALSE'])[0].upper() == b'TRUE'
            user_dn = 'uid=puppet-enroller,cn=users,cn=accounts,dc=home,dc=arpa'
            keys_before = bool(conn.search_s(user_dn, ldap.SCOPE_BASE,
                '(objectClass=*)', ['krbPrincipalKey'])[0][1].get('krbPrincipalKey'))
            assert not keys_before, 'Migrated principal must regenerate its Kerberos keys'
            conn.simple_bind_s(user_dn, request['login_password'])
            conn.simple_bind_s('cn=Directory Manager', request['target_password'])
            keys_after = bool(conn.search_s(user_dn, ldap.SCOPE_BASE,
                '(objectClass=*)', ['krbPrincipalKey'])[0][1].get('krbPrincipalKey'))
            assert keys_after, 'Password LDAP bind did not generate target Kerberos keys'
            conn.unbind_s()
            login_environment = dict(environment, KRB5CCNAME='FILE:' + str(work / 'login-cc'))
            subprocess.run(['kinit', 'puppet-enroller@HOME.ARPA'],
                input=request['login_password'] + '\n', text=True,
                capture_output=True, check=True, env=login_environment)
            subprocess.run(['kinit', 'admin@HOME.ARPA'],
                input=request['target_admin_password'] + '\n', text=True,
                capture_output=True, check=True, env=environment)
            subprocess.run(['ipa', 'server-show', 'ipa.home.arpa'], text=True, capture_output=True,
                           check=True, env=environment)
            login = {'migration_enabled': True, 'ldap_bind': True,
                     'kerberos_login': True, 'keys_were_absent': not keys_before,
                     'keys_generated': keys_after, 'target_admin_api': True}
        except Exception as error:
            login = {'success': False}
            login_error = {'type': type(error).__name__, 'message': str(error),
                           'stdout': getattr(error, 'stdout', None),
                           'stderr': getattr(error, 'stderr', None)}
    after = snapshot('ipa.home.arpa', request['target_password'],
                     str(work / 'target-ca.pem'))
    mismatches = {}
    for kind, entries in source.items():
        if kind in {'trust', 'schema'}:
            continue
        mismatches[kind] = sum(after[kind].get(dn) != values
                               for dn, values in entries.items())
    report = {'action': request['action'], 'returncode': process.returncode,
              'source_export': export_counts,
              'source_counts': {kind: len(values) for kind, values in source.items()},
              'target_counts': {kind: len(values) for kind, values in after.items()},
              'mismatches': mismatches, 'unchanged': before == after,
              'trust_unchanged': before['trust'] == after['trust'],
              'schema_unchanged': before['schema'] == after['schema'],
              'login': login, 'login_error': login_error,
              'source': source, 'before': before, 'after': after,
              'proposed_ldif': (work / 'proposed.ldif').read_text()
                               if (work / 'proposed.ldif').exists() else '',
              'stdout': process.stdout, 'stderr': process.stderr,
              'log': (work / 'migration.log').read_text()
                     if (work / 'migration.log').exists() else ''}
    if request['action'] == 'rehearse':
        marker.write_text('Imported rehearsal; discard clone before repeating.\n')
    print(json.dumps(report))
"""


def private_json(path: Path) -> dict:
    """Reject credentials readable by other accounts before loading them."""
    metadata = path.stat()
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise RuntimeError("Credential JSON must belong to root with mode 0600")
    return json.loads(path.read_text())


def target(action: str, container: str) -> None:
    """Allow live dry-runs and restrict imports to the dedicated isolated clone."""
    metadata = json.loads(infra.run(infra.DOCKER + ["inspect", container]))[0]
    if metadata["Config"]["Hostname"] != controller.HOSTNAME:
        raise RuntimeError("Target hostname must be ipa.home.arpa")
    if action == "dry-run" and container == "infra-ipa-1":
        return
    if (
        container != "atlas-ipa-rehearsal"
        or metadata["Config"].get("Labels", {}).get("home.arpa.rehearsal")
        != "ipa-migration"
        or metadata["HostConfig"].get("PortBindings")
        or set(metadata["NetworkSettings"]["Networks"]) != {"atlas-ipa-rehearsal"}
    ):
        raise RuntimeError("Import requires the isolated atlas-ipa-rehearsal clone")
    mounts = metadata.get("Mounts", [])
    if any(mount.get("Name") == controller.VOLUME for mount in mounts):
        raise RuntimeError("Rehearsal cannot mount the authoritative IPA volume")


def migrate(
    action: str,
    container: str,
    source_file: Path,
    source_ca: Path,
    login_file: Path | None = None,
) -> Path:
    """Keep migration evidence private and report only aggregate verification."""
    source = private_json(source_file)
    if set(source) != {"dm_password"} or not isinstance(source["dm_password"], str):
        raise RuntimeError("Source JSON requires only dm_password")
    if action == "rehearse" and login_file is None:
        raise RuntimeError(
            "Rehearsal requires --login-password-file for puppet-enroller"
        )
    settings = private_json(controller.STATE / "installer.json")
    target(action, container)
    request = {
        "action": action,
        "source_password": source["dm_password"],
        "source_ca": source_ca.read_text(),
        "target_password": settings["dm_password"],
        "target_admin_password": settings["admin_password"],
        "target_ca": (controller.STATE / "root.pem").read_text(),
    }
    if login_file:
        login = private_json(login_file)
        if set(login) != {"password"} or not isinstance(login["password"], str):
            raise RuntimeError("Login JSON requires only password")
        request["login_password"] = login["password"]
    report = json.loads(
        infra.run(
            infra.DOCKER + ["exec", "-i", container, "python3", "-c", WORKER],
            json.dumps(request),
        )
    )
    path = controller.STATE / f"migration-{action}-{time.time_ns()}.json"
    infra.save(path, json.dumps(report))
    if report["returncode"]:
        raise RuntimeError(f"Migration failed; private evidence: {path}")
    if not report["trust_unchanged"]:
        raise RuntimeError(f"Migration changed target trust; private evidence: {path}")
    if action == "dry-run" and not report["unchanged"]:
        raise RuntimeError(f"Dry-run changed identity state; private evidence: {path}")
    if action == "rehearse" and report.get("login_error"):
        raise RuntimeError(
            f"Migrated password verification failed; private evidence: {path}"
        )
    if action == "rehearse" and any(report["mismatches"].values()):
        raise RuntimeError(
            f"Imported identity comparison differs; private evidence: {path}"
        )
    print(
        json.dumps(
            {
                name: report[name]
                for name in (
                    "action",
                    "source_export",
                    "source_counts",
                    "target_counts",
                    "mismatches",
                    "unchanged",
                    "trust_unchanged",
                    "schema_unchanged",
                    "login",
                )
            }
        )
    )
    return path


def main() -> None:
    """Run only on Atlas under the existing authority maintenance lock."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("dry-run", "rehearse"))
    parser.add_argument("--container", default="infra-ipa-1")
    parser.add_argument("--source-password-file", type=Path, required=True)
    parser.add_argument("--source-ca", type=Path, required=True)
    parser.add_argument("--login-password-file", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    if os.geteuid() != 0 or socket.getfqdn() != "atlas.home.arpa":
        raise RuntimeError("Run as root on atlas.home.arpa")
    if (
        Path("/etc/openvox-infra/promoted").read_text().strip()
        != "openvox-authority:atlas.home.arpa"
    ):
        raise RuntimeError("Atlas is not the promoted authority")
    if (
        infra.run(infra.DOCKER + ["info", "--format", "{{.Name}}"]).strip()
        != socket.gethostname()
    ):
        raise RuntimeError("Refusing nonlocal Docker daemon")
    with infra.maintenance():
        evidence = migrate(
            args.action,
            args.container,
            args.source_password_file,
            args.source_ca,
            args.login_password_file,
        )
    print(f"Private migration evidence: {evidence}")


if __name__ == "__main__":
    main()
