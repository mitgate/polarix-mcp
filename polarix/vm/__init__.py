"""VM / emulator lifecycle — the layer that gives Polarix control of the guest.

A desktop target is a virtual machine or emulator the host owns. This package
drives it from the outside through the hypervisor's CLI: power, snapshots
(reset to a known state before a test run), screenshots and raw input when the
guest has no Polarix agent yet (vision fallback).

Backends (detected at runtime, all thin subprocess wrappers)
  libvirt     — virsh (QEMU/KVM): Windows, Linux, macOS-on-KVM guests
  virtualbox  — VBoxManage
  android     — adb / emulator (Android Virtual Devices)
"""
