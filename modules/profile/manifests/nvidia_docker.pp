# profile::nvidia_docker
#
# Configures a Docker host with NVIDIA GPU support:
#   - Creates a udev rule granting the 'input' group rw access to /dev/uinput
#     (required for Wolf/Moonlight virtual controller/keyboard/mouse passthrough)
#
# Prerequisites managed elsewhere:
#   - nvidia headless drivers: installed via ubuntu-drivers (run manually once)
#   - nvidia-container-toolkit package: packages::list in node data
#   - NVIDIA CTK apt source + key: packages::sources in node data
#   - uinput kernel module: kmod::list_of_loads in node data
#   - /etc/docker/daemon.json: managed by hand (nvidia default runtime + IPv6 config)
#
class profile::nvidia_docker {

  require profile::common

  # Grant the 'input' group rw access to /dev/uinput so Wolf containers can
  # create virtual input devices for Moonlight clients.
  # Wolf compose services must include:
  #   group_add: [input]
  # OPTIONS+=static_node ensures the device node exists before first use.
  systemd::udev::rule { '99-uinput.rules':
    rules => [
      'KERNEL=="uinput", SUBSYSTEM=="misc", MODE="0660", GROUP="input", OPTIONS+="static_node=uinput"',
    ],
  }

}
