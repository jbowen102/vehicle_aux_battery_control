#!/bin/sh

PROGRAM_ROOT=/home/${USERNAME}/vehicle_aux_battery_control

USERNAME=$(cat ${PROGRAM_ROOT}/user)
# Activate virtual environment
. /home/${USERNAME}/.virtualenvs/pimoroni/bin/activate
# https://stackoverflow.com/a/16011496


# Kill program if already running.
${PROGRAM_ROOT}/kill_event_loop.sh

# Create log file if it doesn't exist already.
TODAY="$(date "+%Y%m%d")"
LOG_PATH="${PROGRAM_ROOT}/logs/${TODAY}.log"
touch "${LOG_PATH}"
# Allow program running from user session to edit same file.
chown "${USERNAME}" "${LOG_PATH}"

cd "${PROGRAM_ROOT}"
python event_loop.py
