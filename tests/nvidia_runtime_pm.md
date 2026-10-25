# NVIDIA runtime power management

The Linux collector enumerates NVIDIA display functions through PCI sysfs before
loading NVML. It reserves stable slots for sleeping GPUs so Intel/AMD indices do
not shift when NVIDIA wakes. NVML handles are matched by PCI address. If sysfs
shows no NVIDIA function (WSL2, some VMs and containers), the collector falls
back to NVML's own device count and index order; there is no runtime PM to
respect in that case.

When any discovered NVIDIA GPU reports something other than `active` or
`unsupported` (runtime PM disabled, so it cannot sleep) in
`power/runtime_status`, no NVML initialization or collection takes place.
Suspended GPUs show `Sleeping`; transitional or unreadable status shows
`Monitoring paused`. This applies on AC and battery. Because legacy NVML
initialization can attach multiple GPUs, monitoring pauses for all NVIDIA GPUs
when one is asleep. No zero-watt measurement is claimed while asleep.

For active GPUs, each sample initializes NVML, collects data, joins its PCIe
threads, and shuts NVML down. btop keeps no NVIDIA attachment between refreshes.
The kernel-state check and NVML call are not atomic; this does not prove that a
GPU cannot suspend between the check and the first driver call. Real suspend
behavior also depends on the driver and other clients.

Run `python3 tests/nvidia_runtime_pm.py` after building. The test runs the real
binary in a PTY with an isolated config and a mock NVIDIA library. It redirects
power-status reads without changing sysfs and checks suspended startup, two
wake/sleep cycles, missing/transitional/unsupported state, and balanced
init/shutdown calls. The runtime PM checks need a NVIDIA PCI device for discovery
and are skipped without one. A second run hides all PCI vendors to check the
NVML-only fallback. No real NVIDIA API calls are made.

Also smoke-tested against a hybrid laptop's real NVIDIA and Intel drivers: normal UI
and metrics work, and `/proc/PID/fd` contains no NVIDIA device files between
samples. Physical D3 suspension remains to be checked after other GPU clients exit.
