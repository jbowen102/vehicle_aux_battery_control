#!/bin/sh

USERNAME="user11"

# Activate virtual environment
. /home/${USERNAME}/.virtualenvs/pimoroni/bin/activate
# https://stackoverflow.com/a/16011496

# Kill program if already running.
sudo kill $(pgrep -f "python event_loop.py") > /dev/null 2>&1
# https://stackoverflow.com/a/40652908
# https://stackoverflow.com/questions/43724467/what-is-the-difference-between-kill-and-kill-9
# https://stackoverflow.com/a/617184

# Create log file if it doesn't exist already.
PROGRAM_ROOT=/home/${USERNAME}/vehicle_aux_battery_control
TODAY="$(date "+%Y%m%d")"
LOG_PATH="${PROGRAM_ROOT}/logs/${TODAY}.log"
touch "${LOG_PATH}"
# Allow program running from user session to edit same file.
chown "${USERNAME}" "${LOG_PATH}"

cd "${PROGRAM_ROOT}"
python event_loop.py
