"""Check the Brother driverless queue without sending a print job."""

from pathlib import Path

from puppet_catalog import compile_catalog, resources


def test_driverless_printing_orders_cups_queue_and_default(tmp_path: Path) -> None:
    """Manage the discovered IPP printer through CUPS with idempotent guards."""
    result = resources(
        compile_catalog(
            tmp_path,
            "include profile::desktop_printing",
            hostname="aperture",
            node_type="desktop",
        )
    )
    assert result["Package[cups]"]["ensure"] == "installed"
    assert result["Service[cups]"] == {
        "ensure": "running",
        "enable": True,
        "require": "Package[cups]",
    }
    queue = result["Exec[configure-brother-printer]"]
    assert queue["require"] == "Service[cups]"
    assert "-p Brother_MFC_L2750DW -E" in queue["command"]
    assert "-v ipp://printer.home.arpa/ipp/print -m everywhere" in queue["command"]
    assert "printer-is-shared=false" in queue["command"]
    assert (
        "device for Brother_MFC_L2750DW: ipp://printer.home.arpa/ipp/print"
        in (queue["unless"])
    )
    default = result["Exec[default-brother-printer]"]
    assert default["require"] == "Exec[configure-brother-printer]"
    assert default["command"] == "/usr/sbin/lpadmin -d Brother_MFC_L2750DW"
    assert "system default destination: Brother_MFC_L2750DW" in default["unless"]
    for action in (queue, default):
        assert action["provider"] == "shell"
        assert action["environment"] == ["LC_ALL=C"]
    assert not any("scan" in resource.lower() for resource in result)
