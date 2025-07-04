import time

from class_def import Vehicle, TimeKeeper



def main():
    time.sleep(5) # Give time for system to stabilize.

    Car = Vehicle()
    Timer = TimeKeeper()

    key_acc_powered =   Car.is_acc_powered()
    key_on_pos =        Car.is_key_on()
    engine_on_state =   Car.is_engine_running()
    sys_enabled_state = Car.is_enable_switch_closed()

    Timer.start_charge_delay_timer() # Treat RPi startup triggering as a state change.

    while True:

        # Check for enable-switch state change
        if not Car.is_enable_switch_closed() and sys_enabled_state:
            # Switch opened for the first time.
            sys_enabled_state = False
            Car.stop_charging()
            # TODO Start system shutdown timer.
            continue
        elif Car.is_enable_switch_closed() and not sys_enabled_state:
            # Enable switch closed (during previous timeout)
            sys_enabled_state = True
            # TODO Stop shutdown timer
            Timer.start_charge_delay_timer()  # Re-enter appropriate operating mode below after delay.
            continue


        # Check for key state changes
        if Car.is_acc_powered() and not key_acc_powered:
            # Key switched from OFF to ACC
            key_acc_powered = True
            Car.stop_charging()
            Timer.start_charge_delay_timer()
            continue

        elif not Car.is_acc_powered() and key_acc_powered:
            # Key switched from ACC to OFF
            key_acc_powered = False
            # Okay to continue charging across this transition.
            Timer.start_charge_delay_timer()
            continue

        elif Car.is_key_on() and not key_on_pos:
            # Key switched from ACC to ON
            key_on_pos = True
            Car.stop_charging()
            Timer.start_charge_delay_timer()
            continue

        elif not Car.is_key_on() and key_on_pos:
            # Key switched from ON to ACC
            key_on_pos = False
            Car.stop_charging()
            Timer.start_charge_delay_timer()
            continue

        elif Car.is_engine_running() and not engine_on_state:
            # Engine started
            engine_on_state = True
            Car.stop_charging()
            Timer.start_charge_delay_timer()
            continue

        elif not Car.is_engine_running() and engine_on_state:
            # Engine stopped
            engine_on_state = False
            Car.stop_charging()
            continue


        # Enter new charging mode based on current state.
        if Timer.has_charge_delay_time_elapsed():
            if Car.is_engine_running():
                # Key ON, engine running.
                if Car.is_aux_batt_full():
                    Timer.start_charge_delay_timer()
                else:
                    Car.charge_aux_batt()

            elif Car.is_acc_powered():
                # Key in ACC or ON but engine off.
                if Car.is_aux_batt_sufficient():
                    Car.charge_starter_batt()
                else:
                    # If Li batt V low, power down RPi.
                    Car.shut_down_controller()
                    # Will need to be manually turned back on either by key cycle or enable-switch cycle.

            elif Car.is_key_off():
                # Key OFF
                if Car.does_starter_batt_need_charge() and Car.is_aux_batt_sufficient():
                    # Keep charging while FLA batt needs charge and Li batt V sufficient.
                    Car.charge_starter_batt()

                else:
                    # If Li batt V low, power down RPi.
                    Car.shut_down_controller()
                    # Will turn back on next time key turned to ACC (assuming enable switch on)


if __name__ == "__main__":
    main()
