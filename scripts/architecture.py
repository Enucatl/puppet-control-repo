from diagrams import Cluster, Diagram
from diagrams.generic.os import Ubuntu, Debian, RedHat, Windows
from diagrams.onprem.container import Docker


with Diagram("Architecture", show=False):
    ubuntu_desktop = Ubuntu("ognongle")
    ubuntu_nuc = Ubuntu("nuc10i7fnh")

    with Cluster(f"{ubuntu_desktop.label} kvm"):
        win10_vm = Windows("win10")

    with Cluster(f"{ubuntu_nuc.label} kvm"):
        nuc_vms = [
            Debian("pihole"),
            Debian("vault"),
            RedHat("ipa"),
        ]
    with Cluster(f"{ubuntu_nuc.label} docker"):
        nuc_containers = [
            Docker("portainer"),
            Docker("paperless"),
            Docker("traefik"),
        ]

    ubuntu_desktop >> win10_vm
    ubuntu_nuc >> nuc_vms
    ubuntu_nuc >> nuc_containers
