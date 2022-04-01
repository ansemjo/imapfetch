#!/bin/ash

# Copyright (c) 2018 Anton Semjonov
# Licensed under the MIT License

# assemble cron schedule from env
# define the schedule with '-e SCHEDULE=...'
export SCHEDULE="${SCHEDULE:-"*/15 * * * *"}"

# command is given as parameter
if [[ $1 = cron ]]; then

  # assemble cron command
  shift 1;
  export COMMAND="imapfetch ${*:?command arguments required}"

  # install crontab
  echo "$SCHEDULE $COMMAND" | crontab -

  # run command once before schedule
  sh -c "$COMMAND" || {
    echo "command error. exiting."
    exit 1
  }

  # exec crontab in foreground
  exec crond -f

else

  # if the first parameter isn't "cron", run imapfetch directly
  imapfetch "$@"

fi