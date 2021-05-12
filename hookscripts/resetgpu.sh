#!/bin/bash
#Gives GPU back to host after a VM is shutdown

if [ $2 = "post-stop" ]; then
  echo [post-stop] start
  PCIE=$(grep -oP '(?<=hostpci0: ).*?(?=,)' /etc/pve/nodes/`hostname -s`/qemu-server/$1.conf)
  echo -n 0000:$PCIE.0 > /sys/bus/pci/drivers/vfio-pci/unbind

  #The specific driver you want to use need to be specifed, nouveau for nvidia usualy
  echo -n 0000:$PCIE.0 > /sys/bus/pci/drivers/nouveau/bind 

  # Alternative method if I can figure out how to prevent vfio-pci from automaticly binding, 
  # this would mean the host driver doesn't need to be specified
  #echo 1 > /sys/bus/pci/devices/0000:$PCIE.0/remove 
  #echo 1 > /sys/bus/pci/rescan

  echo [post-stop] end
fi

exit 0