#!/bin/bash
# Shared configuration constants for Proxmox provisioning scripts

DOMAIN_SUFFIX="home.arpa"
PUPPET_SERVER="docker.${DOMAIN_SUFFIX}"

DEFAULT_STORAGE="local-zfs"
SNIPPET_STORAGE="local"
TEMPLATE_ID=9000
