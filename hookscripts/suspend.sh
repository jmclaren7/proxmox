#!/bin/bash
#Suspends a VM by killing the shutdown task and starting a suspend task

if [ $2 = "pre-stop" ]; then
  echo [pre-stop] start
  setsid /var/lib/vz/snippets/suspend.sh $1 pre-stop-child &
  PID=$(pvenode task list --vmid $1 --source all --typefilter qmshutdown --limit 1 | grep UPID: | cut -d':' -f 3)
  PID=$(echo "obase=10; ibase=16; $PID" | bc)
  kill -9 $PID
  echo [pre-stop] end
elif [ $2 = "pre-stop-child" ]; then
  exec 0>&-
  exec 1>&-
  exec 2>&-
  sleep 3s
  echo suspend $1 --todisk
  qm suspend $1 --todisk

fi

exit 0