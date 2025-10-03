
RPI_USER="user11"
RPI_PROGRAM_ROOT="/home/${RPI_USER}/vehicle_aux_battery_control"

# If running on RPi, push log BU to USB
KILLSWITCH_DEV="USB-01"
DEST_PATH="/media/${RPI_USER}/${KILLSWITCH_DEV}/logs_BU"
if [ -d "${RPI_PROGRAM_ROOT}" ]; then
    sleep 15 # need delay for drive to mount.
    rsync -azi ${RPI_PROGRAM_ROOT}/logs/ ${DEST_PATH}/
fi

# If running on laptop, pull log BU from RPi
LAPTOP_USER="user474"
REMOTE_HOSTNAME="rpi-04"
DEST_PATH=/home/${LAPTOP_USER}/Storage_Root/Tech/Projects/vehicle_aux_battery_control/logs_BU
if [ -d "${DEST_PATH}" ]; then
    rsync -azi -e "ssh -i /home/${LAPTOP_USER}/.ssh/id_ed25519" \
    ${RPI_USER}@${REMOTE_HOSTNAME}:${RPI_PROGRAM_ROOT}/logs/ \
    ${DEST_PATH}
fi
