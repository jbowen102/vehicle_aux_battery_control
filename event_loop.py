import os
import time
import signal
import traceback

import automationhat as ah

from class_def import Vehicle, Controller, TimeKeeper, OutputHandler


def main(Output):
    time.sleep(4)                # Give time for system to stabilize.
    Car    = Vehicle(Output)
    time.sleep(1)                # Give time for automationhat inputs to stabilize.
    Timer  = TimeKeeper(Output, ntp_wait_time=20)

    key_acc_powered   = Car.is_acc_powered()
    key_on_pos        = Car.is_key_on()
    engine_on_state   = Car.is_engine_running()
    sys_enabled_state = Car.is_enable_switch_closed()
    Output.print_info("Key @ %s." % ("ON" if key_on_pos else ("ACC" if key_acc_powered else "OFF")))
    Output.print_info("Engine %s." % ("ON" if engine_on_state else "OFF"))
    Output.print_info("System %s." % ("enabled" if sys_enabled_state else "disabled"))

    Timer.start_charge_delay_timer("program startup") # Treat RPi startup triggering as a state change.

    while True:
        # Check for enable-switch state change
        if not Car.is_enable_switch_closed() and sys_enabled_state:
            # Switch opened for the first time.
            Car.is_enable_switch_closed(log=True) # Call again just for logging
            sys_enabled_state = False
            Car.stop_charging(log=True)
            Timer.start_shutdown_timer(log=True)
            continue
        elif Car.is_enable_switch_closed() and not sys_enabled_state:
            # Enable switch closed (during previous timeout)
            Car.is_enable_switch_closed(log=True) # Call again just for logging
            sys_enabled_state = True
            Timer.stop_shutdown_timer(log=True)
            Timer.start_charge_delay_timer("enable switch closed")  # Re-enter appropriate operating mode below after delay.
            continue

        # Shut down if shutdown countdown has ended.
        if Timer.has_shutdown_delay_elapsed(log=False):
            Timer.has_shutdown_delay_elapsed(log=True) # Call again just for logging
            Car.shut_down_controller()
            break


        # Check for vehicle operating-state changes
        if Car.is_acc_powered() and not key_acc_powered:
            Output.print_info("Key switched from OFF to ACC.")
            key_acc_powered = True
            Car.stop_charging()
            Timer.start_charge_delay_timer("key OFF -> ACC")
            continue

        elif not Car.is_acc_powered() and key_acc_powered:
            Output.print_info("Key switched from ACC to OFF.")
            key_acc_powered = False
            Car.stop_charging()
            Timer.start_charge_delay_timer("key ACC -> OFF")
            continue

        elif Car.is_key_on() and not key_on_pos:
            Output.print_info("Key switched from ACC to ON.")
            key_on_pos = True
            Car.stop_charging()
            Timer.start_charge_delay_timer("key ACC -> ON")
            continue

        elif not Car.is_key_on() and key_on_pos:
            Output.print_info("Key switched from ON to ACC.")
            key_on_pos = False
            if engine_on_state:
                Output.print_info("Engine stopped (main voltage raw: %.2f)" % Car.get_main_voltage_raw())
                engine_on_state = False
                timer_str = "engine stopped, key ON -> ACC"
            else:
                timer_str = "key ON -> ACC"
            Car.stop_charging()
            Timer.start_charge_delay_timer(timer_str)
            continue

        elif not Car.is_engine_running() and engine_on_state:
            # Could happen independent of key -> ACC if engine stalls.
            Output.print_info("Engine stopped (main voltage raw: %.2f)" % Car.get_main_voltage_raw())
            engine_on_state = False
            Car.stop_charging()
            Timer.start_charge_delay_timer("engine stopped")
            continue

        elif Car.is_engine_running() and not engine_on_state:
            Output.print_info("Engine started. (main voltage raw: %.2f)" % Car.get_main_voltage_raw())
            engine_on_state = True
            Car.stop_charging()
            Timer.start_charge_delay_timer("engine started")
            continue


        shutdown_pending = Timer.is_shutdown_pending()
        ready, first_time_ind = Timer.has_charge_delay_time_elapsed()
        # Enter new charging mode (if first_time_ind is True) based on current state.
        # or continue with current mode.
        # Don't enter if shutdown pending.
        if ready and not shutdown_pending:
            if first_time_ind:
                Output.print_debug("Charge-delay time has elapsed.")

            if engine_on_state:
                # Key ON, engine running.
                if first_time_ind:
                    Output.print_info("State: Key ON, engine running.")
                if Car.is_aux_batt_full(log=first_time_ind):
                    Timer.start_charge_delay_timer("aux battery full already")
                else:
                    Car.charge_aux_batt(log=first_time_ind)

            elif key_acc_powered:
                # Key in ACC or ON but engine off.
                if first_time_ind:
                    Output.print_info("State: Key %s." % ("ON, engine off" if Car.is_key_on() else "in ACC (engine off)"))
                if Car.is_aux_batt_sufficient(log=first_time_ind):
                    Car.charge_starter_batt(log=first_time_ind)
                else:
                    # If Li batt V low, power down RPi.
                    Car.is_aux_batt_sufficient(log=True) # Call again just for logging
                    Car.shut_down_controller()
                    break
                    # Will need to be manually turned back on either by key cycle or enable-switch cycle.

            else:
                # Key OFF
                if first_time_ind:
                    Output.print_info("State: Key OFF.")

                # if not Car.is_aux_batt_sufficient(log=first_time_ind):
                temp_threshold = 13
                if not Car.is_aux_batt_sufficient(threshold_override=temp_threshold, log=False):
                    # If Li batt V low, power down RPi.
                    Car.is_aux_batt_sufficient(threshold_override=temp_threshold, log=True) # Call again just for logging
                    Output.print_warn("Li batt V low (%.2f); initiating RPi shutdown." % Car.get_aux_voltage(log=False))
                    Car.shut_down_controller()
                    break
                    # Will turn back on next time key turned to ACC (assuming enable switch on)
                # elif not Car.does_starter_batt_need_charge(log=first_time_ind):
                #     if first_time_ind:
                #         Output.print_warn("Starter batt fully charged; initiating RPi shutdown.")
                #     Car.shut_down_controller()
                #     break
                else:
                    # Keep charging while FLA batt needs charge and Li batt V sufficient.
                    Car.charge_starter_batt(log=first_time_ind)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, Controller().sigterm_handler) # method that turns off LEDs and relays and exits Python script
    Output = OutputHandler()
    try:
        main(Output)
    except TimeoutError:
        # Thrown by automationhat - "Timed out waiting for conversion."
        # Not sure what's causing it yet. Doesn't usually persist across reboot though.
        Controller().reboot()
    except KeyboardInterrupt:
        Output.print_shutdown("Keyboard interrupt.")
    except Exception:
        Output.print_shutdown(traceback.format_exc())
    except:
        Output.print_shutdown("Program killed by OS.")
    finally:
        Controller().open_all_relays() # Does this run when program shut down by SIGTERM?
        # Output.print_warn("Shutting down controller.")
        # Controller().shut_down()
