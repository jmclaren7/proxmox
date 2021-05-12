#!/bin/bash
#Resets GPU after a VM is shutdown so that it may be used by the host again or another VM

if [ $2 = "post-stop" ]; then
  echo [post-stop] start
  PCIE=$(grep -oP '(?<=hostpci0: ).*?(?=,)' /etc/pve/nodes/pve50/qemu-server/$1.conf)
  echo 1 > /sys/bus/pci/devices/0000:$PCIE.0/remove
  echo 1 > /sys/bus/pci/rescan
  echo [post-stop] end
fi

exit 0

