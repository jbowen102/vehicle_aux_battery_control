
RPI_USER="user11"
RPI_PROGRAM_ROOT="/home/${RPI_USER}/vehicle_aux_battery_control"

# If running on RPi, push log BU to USB
KILLSWITCH_DEV="USB-01"
KILLSWITCH_DEV_ROOT=/media/${RPI_USER}/${KILLSWITCH_DEV}
KILLSWITCH_PATH=${KILLSWITCH_DEV_ROOT}/control_params.py
DEST_PATH_LOGS=${KILLSWITCH_DEV_ROOT}/logs
DEST_PATH_DATA=${KILLSWITCH_DEV_ROOT}
DEST_PATH_DATA_BU=${KILLSWITCH_DEV_ROOT}/datalogging_BU
if [ -d "/home/${RPI_USER}" ] && [ -e "${KILLSWITCH_PATH}" ]; then
    sleep 15 # need delay after system startup for drive to mount.
    rsync -azi \
          ${RPI_PROGRAM_ROOT}/logs/ \
          ${DEST_PATH_LOGS}/

    rsync -azi \
          --progress \
          ${RPI_PROGRAM_ROOT}/system_data_log.db \
          ${DEST_PATH_DATA}/

    rsync -azi \
          --progress \
          --delete-before \
          ${RPI_PROGRAM_ROOT}/datalogging_BU/ \
          ${DEST_PATH_DATA_BU}/
fi

# If running on laptop, pull log BU from RPi
LAPTOP_USER="user474"
REMOTE_HOSTNAME="rpi-04"
DEST_PATH_BASE=/home/${LAPTOP_USER}/Storage_Root/Tech/Projects/vehicle_aux_battery_control
DEST_PATH_LOGS=${DEST_PATH_BASE}/logs
DEST_PATH_DATA=${DEST_PATH_BASE}
DEST_PATH_DATA_BU=${DEST_PATH_BASE}/datalogging_BU
if [ -d "/home/${LAPTOP_USER}" ]; then
    rsync -azivh \
          --partial-dir="${DEST_PATH_BASE}/rsync_partials_buffer" \
          -e "ssh -i /home/${LAPTOP_USER}/.ssh/id_ed25519" \
          ${RPI_USER}@${REMOTE_HOSTNAME}:${RPI_PROGRAM_ROOT}/logs/ \
          ${DEST_PATH_LOGS}

    rsync -azivh \
          --progress \
          -e "ssh -i /home/${LAPTOP_USER}/.ssh/id_ed25519" \
          ${RPI_USER}@${REMOTE_HOSTNAME}:${RPI_PROGRAM_ROOT}/system_data_log.db \
          ${DEST_PATH_DATA}/

    rsync -azivh \
          --progress \
          --delete-before \
          -e "ssh -i /home/${LAPTOP_USER}/.ssh/id_ed25519" \
          ${RPI_USER}@${REMOTE_HOSTNAME}:${RPI_PROGRAM_ROOT}/datalogging_BU/ \
          ${DEST_PATH_DATA_BU}/
fi
