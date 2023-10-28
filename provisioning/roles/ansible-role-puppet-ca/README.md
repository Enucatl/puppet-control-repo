# ansible-role-puppet-ca

This Ansible role is designed to manage Puppet certificate signing and cleaning for Puppet servers and clients. It performs the following tasks:

- Retrieves a list of all signed certificates on Puppet servers.
- Cleans Puppet certificates that are no longer defined.

## Requirements

Puppet server and clients must be set up and reachable.

## Role Variables

- `puppetserver`: A group in the inventory containing Puppet servers.
- `virt_infra_state`: A variable indicating the state of the Puppet infrastructure.

## Dependencies

This is a companion to [ansible-role-virt-infra](https://github.com/Enucatl/ansible-role-virt-infra).

## Example Playbook

Including an example of how to use your role (for instance, with variables passed in as parameters) is always nice for users too:

```yaml
    - hosts: puppetserver,vm
      gather_facts: no
      roles:
        - ansible-role-puppet-ca
```

## Example Inventory

```yaml
puppetserver:
  hosts:
    ## Put your KVM host connection and settings here
    vault.home.arpa:
      ansible_host: vault.home.arpa
  vars:
    ansible_python_interpreter: /usr/bin/python3

vm:
  children:
    deactivated:
      hosts:
        test-debian12-1.home.arpa:
        test-debian12-2.home.arpa:
        test-debian12-3.home.arpa:
        test-debian12-4.home.arpa:
        test-debian12-5.home.arpa:
      vars:
        kvmhost: ognongle.home.arpa
        puppetserver: vault.home.arpa
        virt_infra_state: undefined
```
