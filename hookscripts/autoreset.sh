#!/bin/bash
# Automaticy resets a VM once after it starts in order to fix some firmware issues with unusual VMs like macOS

if [ $2 = "post-start" ]; then
  echo [post-start] start
  setsid /var/lib/vz/snippets/autoreset.sh $1 post-start-child &
  echo [post-start] end
elif [ $2 = "post-start-child" ]; then
  exec 0>&-
  exec 1>&-
  exec 2>&-
  sleep 6s
  echo reset $1
  qm reset $1

fi

exit 0