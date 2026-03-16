#!/bin/bash
# Shared configuration constants for Proxmox provisioning scripts

DOMAIN_SUFFIX="home.arpa"
PUPPET_SERVER="docker.${DOMAIN_SUFFIX}"
CMK_DOMAIN="checkmk.docker.${DOMAIN_SUFFIX}"
CMK_SITE="cmk"
CMK_REG_USER="agent_registration"

DEFAULT_STORAGE="local-zfs"
SNIPPET_STORAGE="local"
TEMPLATE_ID=9000
