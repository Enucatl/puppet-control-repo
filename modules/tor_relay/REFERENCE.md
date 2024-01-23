# Reference

<!-- DO NOT EDIT: This document was generated by Puppet Strings -->

## Table of Contents

### Classes

* [`tor_relay`](#tor_relay): Class: tor_relay  This class configures a Tor relay with specified parameters using the tor module.  Parameters:   - $orport: The ORPort for 

## Classes

### <a name="tor_relay"></a>`tor_relay`

Class: tor_relay

This class configures a Tor relay with specified parameters using the tor module.

Parameters:
  - $orport: The ORPort for the Tor relay.
  - $nickname: The nickname for the Tor relay.
  - $relay_bandwidth_rate: The relay bandwidth rate for the Tor relay.
  - $relay_bandwidth_burst: The relay bandwidth burst for the Tor relay.
  - $contact_info: The contact information for the Tor relay.
  - $dir_port: The directory port for the Tor relay.
  - $socks_port: The SOCKS port for the Tor relay.
  - $metrics_port: The metrics port for the Tor relay.

Example Usage:
  class { 'tor_relay':
    orport                => '9001',
    nickname              => 'mytorrelay',
    relay_bandwidth_rate  => '100 KB',
    relay_bandwidth_burst => '200 KB',
    contact_info          => 'admin@example.com',
    dir_port              => '9030',
    socks_port            => '9050',
    metrics_port          => '9151',
  }

#### Parameters

The following parameters are available in the `tor_relay` class:

* [`orport`](#-tor_relay--orport)
* [`nickname`](#-tor_relay--nickname)
* [`relay_bandwidth_rate`](#-tor_relay--relay_bandwidth_rate)
* [`relay_bandwidth_burst`](#-tor_relay--relay_bandwidth_burst)
* [`contact_info`](#-tor_relay--contact_info)
* [`dir_port`](#-tor_relay--dir_port)
* [`socks_port`](#-tor_relay--socks_port)
* [`metrics_port`](#-tor_relay--metrics_port)

##### <a name="-tor_relay--orport"></a>`orport`

Data type: `Any`



##### <a name="-tor_relay--nickname"></a>`nickname`

Data type: `Any`



##### <a name="-tor_relay--relay_bandwidth_rate"></a>`relay_bandwidth_rate`

Data type: `Any`



##### <a name="-tor_relay--relay_bandwidth_burst"></a>`relay_bandwidth_burst`

Data type: `Any`



##### <a name="-tor_relay--contact_info"></a>`contact_info`

Data type: `Any`



##### <a name="-tor_relay--dir_port"></a>`dir_port`

Data type: `Any`



##### <a name="-tor_relay--socks_port"></a>`socks_port`

Data type: `Any`



##### <a name="-tor_relay--metrics_port"></a>`metrics_port`

Data type: `Any`


