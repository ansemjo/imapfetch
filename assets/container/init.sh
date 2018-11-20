#!/bin/ash

# Copyright (c) 2018 Anton Semjonov
# Licensed under the MIT License

# command is given as parameter
export COMMAND="${*:?command required}"

# assemble cron schedule from env
# either use '-e MINUTES=n' to run test every n minutes
# or define the complete schedule part with '-e SCHEDULE=...'
export SCHEDULE="${SCHEDULE:-"*/$MINUTES * * * *"}"

# install crontab
echo "$SCHEDULE $COMMAND" | crontab -

# run command once before schedule
${SHELL:-sh} -c "$COMMAND" || {
  echo "command error. exiting."
  exit 1
}

# exec crontab in foreground
exec crond -f
