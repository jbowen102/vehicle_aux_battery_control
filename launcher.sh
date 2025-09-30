#!/bin/sh

SCRIPT_DIR="$(realpath "$(dirname "${0}")")"
SCRIPT_NAME="$(basename "${0}")"

USERNAME="user11"
# Activate virtual environment
. /home/${USERNAME}/.virtualenvs/pimoroni/bin/activate
# https://stackoverflow.com/a/16011496

PROGRAM_ROOT="/home/${USERNAME}/vehicle_aux_battery_control"
# Kill program if already running.
${PROGRAM_ROOT}/kill_event_loop.sh

# Create log file if it doesn't exist already.
DATESTAMP="$(date "+%Y%m%d")"
LOG_PATH="${PROGRAM_ROOT}/logs/${DATESTAMP}.log"
touch "${LOG_PATH}"
# Allow program running from user session to edit same file.
chown "${USERNAME}" "${LOG_PATH}"
# Will fail if script hasn't been run by root since date updated to today.

# If USB unplugged from RPi, don't run program.
KILLSWITCH_USB="USB-01"
if [ -d "/media/${USERNAME}/${KILLSWITCH_USB}" ]; then
    cd "${PROGRAM_ROOT}"
    python event_loop.py
else
    # NTP_SYNCD="$(/usr/bin/timedatectl show --property=NTPSynchronized --value)"
    # if [ "$NTP_SYNCD" = "yes" ]; then
    #     # Returned empty string
    #     TIMESTAMP="$(date "+%Y%m%dT%H%M%S")"
    # else
    #     TIMESTAMP="---------$(date "+%H%M%S")"
    # fi
    TIMESTAMP="$(date "+%Y%m%dT%H%M%S")" # Assume RTC time valid

    echo "${SCRIPT_NAME}: ${KILLSWITCH_USB} not present."

    echo "\n----------------------- ABORT " >> "${LOG_PATH}"
    echo "${TIMESTAMP} [ERROR] ${SCRIPT_NAME}: ${KILLSWITCH_USB} not present." >> "${LOG_PATH}"
    echo "-----------------------" >> "${LOG_PATH}"
    exit 254
    # Don't shut down RPi.
    # https://medium.com/@himanshurahangdale153/list-of-exit-status-codes-in-linux-f4c00c46c9e0
fi
