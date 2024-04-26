#!/bin/bash
# Removes the "You do not have a valid subscription for this server" popup message while logging in
# https://johnscs.com/remove-proxmox51-subscription-notice/
#
# Manual steps:
#   cd /usr/share/javascript/proxmox-widget-toolkit
#   cp proxmoxlib.js proxmoxlib.js.bak
#   nano proxmoxlib.js
#   search for "function(orig_cmd) {" and add "orig_cmd();" and "return;" just after it
#   systemctl restart pveproxy.service

sed -Ezi.bak "s/(function\(orig_cmd\) \{)/\1\n\torig_cmd\(\);\n\treturn;/g" /usr/share/javascript/proxmox-widget-toolkit/proxmoxlib.js && systemctl restart pveproxy.service


