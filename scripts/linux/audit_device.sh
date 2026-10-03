#!/usr/bin/env bash
# Lists every host-level setting that affects memory or the GPU on the device.
# Read-only. Nothing here changes the device.
set -u

echo "--- swap"
swapon --show 2>&1
grep -E 'swap' /etc/fstab 2>&1

echo "--- vm sysctls"
sysctl vm.swappiness vm.drop_caches vm.overcommit_memory 2>&1
grep -rhE '^\s*vm\.' /etc/sysctl.conf /etc/sysctl.d/ 2>/dev/null

echo "--- docker daemon.json"
cat /etc/docker/daemon.json 2>&1

echo "--- power mode and clocks"
nvpmodel -q 2>&1 | head -3
systemctl is-enabled jetson_clocks.service 2>&1 || true

echo "--- desktop"
systemctl get-default 2>&1
systemctl is-active gdm3 gdm 2>/dev/null | head -2

echo "--- network at boot"
# A connection that has user permissions, or a password held by the desktop
# keyring (psk-flags 1), does not start on a device that boots without the desktop.
nmcli -t -f NAME,TYPE,AUTOCONNECT connection show 2>&1 | while IFS=: read -r name type auto; do
    perms=$(nmcli -g connection.permissions connection show "$name" 2>/dev/null)
    flags=$(nmcli -g 802-11-wireless-security.psk-flags connection show "$name" 2>/dev/null)
    echo "$name ($type) autoconnect=$auto permissions=${perms:-all users} psk-flags=${flags:-n/a}"
done

echo "--- zram"
swapon --show=NAME,TYPE,SIZE 2>&1 | grep -i zram || echo "no zram"
systemctl is-enabled nvzramconfig 2>&1 || true

echo "--- iotedge config overrides"
sudo grep -nE 'memory|swap|shm' /etc/aziot/config.toml 2>&1 | head -5

echo "--- memory now"
free -m
