VMID=$1

if [ ! -z VMID ] then
echo "Adding TUN option to VM VMID"
echo "lxc.cgroup.devices.allow = c 10:200 rwm" >> /etc/pve/lxc/VMID.conf
else
echo "Missing option VMID"
fi