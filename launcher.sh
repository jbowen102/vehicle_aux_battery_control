#!/bin/sh

. /home/user11/.virtualenvs/pimoroni/bin/activate
# https://stackoverflow.com/a/16011496

cd /home/user11/vehicle_aux_battery_control
python event_loop.py
cd -
