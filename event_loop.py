import time
import datetime as dt

from class_def import Vehicle, StarterBatt, AuxBatt, BatteryCharger, Controller


STATE_CHANGE_DELAY_SEC = 30
LAST_STATE_CHANGE = None

RPI_SHUTDOWN_DELAY = 30


def start_charge_delay_timer():
    """If called while timer already running, timer restarts.
    """
    global LAST_STATE_CHANGE
    LAST_STATE_CHANGE = dt.datetime.now()

def charge_delay_time_elapsed():
    """Evaluates if state-delay buffer time has elapsed since last state change.
    Returns True or False.
    """
    global LAST_STATE_CHANGE, STATE_CHANGE_DELAY_SEC

    if LAST_STATE_CHANGE is None:
        return True
    elif (dt.datetime.now() - LAST_STATE_CHANGE) >= dt.timedelta(seconds=STATE_CHANGE_DELAY_SEC):
        return True
    else:
        return False
    # https://www.tutorialspoint.com/How-can-we-do-date-and-time-math-in-Python



def main():
    time.sleep(5) # Give time for system to stabilize.

    Car = Vehicle()

    key_acc_powered =   Car.is_acc_powered()
    key_on_pos =        Car.is_key_on()
    engine_on_state =   Car.is_engine_running()
    sys_enabled_state = Car.is_enable_switch_closed()


    while True:

        # Check for enable-switch state change
        if not Car.is_enable_switch_closed() and sys_enabled_state:
            # Switch opened for the first time.
            sys_enabled_state = False
            # TODO Start system shutdown timer.
            continue
        elif Car.is_enable_switch_closed() and not sys_enabled_state:
            # Enable switch closed (during previous timeout)
            sys_enabled_state = True
            # TODO Stop shutdown timer
            continue


        # Check for key state changes
        if Car.is_acc_powered() and not key_acc_powered:
            # Key switched from OFF to ACC
            key_acc_powered = True
            Car.stop_charging()
            start_charge_delay_timer()
            continue

        elif not Car.is_acc_powered() and key_acc_powered:
            # Key switched from ACC to OFF
            key_acc_powered = False
            # Okay to continue charging across this transition.
            start_charge_delay_timer()
            continue

        elif Car.is_key_on() and not key_on_pos:
            # Key switched from ACC to ON
            key_on_pos = True
            Car.stop_charging()
            start_charge_delay_timer()
            continue

        elif not Car.is_key_on() and key_on_pos:
            # Key switched from ON to ACC
            key_on_pos = False
            Car.stop_charging()
            start_charge_delay_timer()
            continue

        elif Car.is_engine_running() and not engine_on_state:
            # Engine started
            engine_on_state = True
            Car.stop_charging()
            start_charge_delay_timer()
            continue

        elif not Car.is_engine_running() and engine_on_state:
            # Engine stopped
            engine_on_state = False
            Car.stop_charging()
            start_charge_delay_timer()
            continue


        # Enter new charging mode based on current state.
        if charge_delay_time_elapsed():
            if Car.is_engine_running():
                # Key ON, engine running.
                Car.charge_aux_batt()
            elif Car.is_key_on():
                # Key ON but engine off.
                Car.charge_starter_batt()
            elif Car.is_acc_powered():
                # Key in ACC
                Car.charge_starter_batt()
            elif Car.is_key_off():
                # Key OFF
                Car.charge_starter_batt()
                # TODO Determine how long system should keep itself on and charge FLA batt
                # (based on FLA voltage measured after settling or some other
                # function that should continue)

                # RPi power-down at some point
                Controller.shut_down()


if __name__ == "__main__":
    main()
