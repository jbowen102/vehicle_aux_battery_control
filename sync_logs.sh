
RPI_USER="user11"
RPI_PROGRAM_ROOT="/home/${RPI_USER}/vehicle_aux_battery_control"

# If running on RPi, push log BU to USB
KILLSWITCH_DEV="USB-01"
DEST_PATH_LOGS="/media/${RPI_USER}/${KILLSWITCH_DEV}/logs_BU"
DEST_PATH_DATALOGGING="/media/${RPI_USER}/${KILLSWITCH_DEV}"
if [ -d "${RPI_PROGRAM_ROOT}" ]; then
    sleep 15 # need delay after system startup for drive to mount.
    rsync -azi \
          ${RPI_PROGRAM_ROOT}/logs/ \
          ${DEST_PATH_LOGS}/

    rsync -azi --progress \
          ${RPI_PROGRAM_ROOT}/system_data_log.db \
          ${DEST_PATH_DATALOGGING}/
fi

# If running on laptop, pull log BU from RPi
LAPTOP_USER="user474"
REMOTE_HOSTNAME="rpi-04"
DEST_PATH_LOGS=/home/${LAPTOP_USER}/Storage_Root/Tech/Projects/vehicle_aux_battery_control/logs_BU
DEST_PATH_DATALOGGING=/home/${LAPTOP_USER}/Storage_Root/Tech/Projects/vehicle_aux_battery_control/datalogging_BU
if [ -d "${DEST_PATH}" ]; then
    rsync -azivh \
          -e "ssh -i /home/${LAPTOP_USER}/.ssh/id_ed25519" \
          ${RPI_USER}@${REMOTE_HOSTNAME}:${RPI_PROGRAM_ROOT}/logs/ \
          ${DEST_PATH_LOGS}

    rsync -azivh --progress \
          -e "ssh -i /home/${LAPTOP_USER}/.ssh/id_ed25519" \
          ${RPI_USER}@${REMOTE_HOSTNAME}:${RPI_PROGRAM_ROOT}/system_data_log.db \
          ${DEST_PATH_DATALOGGING}/
fi
