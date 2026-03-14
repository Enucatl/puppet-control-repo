# profile::nvidia_docker
#
# Configures a Docker host with NVIDIA GPU support:
#   - Creates a udev rule granting the 'input' group rw access to /dev/uinput
#     (required for Wolf/Moonlight virtual controller/keyboard/mouse passthrough)
#   - Manages a systemd oneshot service that applies nvidia-smi settings at boot
#     (persistence mode always; power cap, clock cap, temp target all optional)
#
# Prerequisites managed elsewhere:
#   - nvidia headless drivers: installed via ubuntu-drivers (run manually once)
#   - nvidia-container-toolkit package: packages::list in node data
#   - NVIDIA CTK apt source + key: packages::sources in node data
#   - uinput kernel module: kmod::list_of_loads in node data
#   - /etc/docker/daemon.json: managed by hand (nvidia default runtime + IPv6 config)
#
class profile::nvidia_docker (
  Optional[Integer] $power_limit_watts    = undef,
  Optional[String]  $clock_settings       = undef,  # "memory_mhz,graphics_mhz"
  Optional[Integer] $temp_target_celsius  = undef,
) {

  require profile::common

  $power_limit_exec = $power_limit_watts ? {
    undef   => '',
    default => "ExecStart=/usr/bin/nvidia-smi -pl ${power_limit_watts}",
  }

  # Disabling auto-boost is required before a fixed clock setting takes effect.
  $clock_exec = $clock_settings ? {
    undef   => '',
    default => "ExecStart=/usr/bin/nvidia-smi --auto-boost-default=0\nExecStart=/usr/bin/nvidia-smi -ac ${clock_settings}",
  }

  $temp_exec = $temp_target_celsius ? {
    undef   => '',
    default => "ExecStart=/usr/bin/nvidia-smi -gtt ${temp_target_celsius}",
  }

  systemd::unit_file { 'nvidia-power-management.service':
    content => @("END"),
      [Unit]
      Description=NVIDIA GPU power management
      After=multi-user.target

      [Service]
      Type=oneshot
      RemainAfterExit=yes
      ExecStart=/usr/bin/nvidia-smi -pm 1
      ${power_limit_exec}
      ${clock_exec}
      ${temp_exec}
      [Install]
      WantedBy=multi-user.target
      END
    enable  => true,
    active  => true,
  }

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
