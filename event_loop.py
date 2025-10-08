import os
import time
import signal
import traceback

from class_def import Vehicle, Controller, TimeKeeper, OutputHandler

def main(Output, Timer):
    time.sleep(4)                # Give time for system to stabilize.
    Car = Vehicle(Output, Timer)

    key_acc_powered   = Car.is_acc_powered()
    engine_on_state   = Car.is_engine_running()
    sys_enabled_state = Car.is_enable_switch_closed()
    Car.output_status()

    # Handle if program started w/ enable switch open (could have opened after boot initiated).
    if sys_enabled_state:
        Timer.start_charge_delay_timer("program startup", delay_s=15) # Treat RPi startup triggering as a state change.
    else:
        Car.is_enable_switch_closed(log=True) # Call again just for logging
        Timer.start_shutdown_timer(log=True)

    while True:

        if (Timer.get_minutes() % 10 == 0) and (Timer.get_seconds() == 43):
            Car.check_wiring() # periodically look for I/O issues.
            time.sleep(1)

        if (Timer.get_minutes() % 5 == 0) and (Timer.get_seconds() == 0):
            # Every 5 minutes, print/log system status info.
            Car.output_status()
            time.sleep(1)

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
            engine_on_state = False
            Car.stop_charging()
            Timer.start_charge_delay_timer("key ACC -> OFF", delay_s=5)
            continue

        elif not Car.is_engine_running() and engine_on_state:
            # Could happen independent of key -> ACC if engine stalls.
            Output.print_info("Engine stopped (main voltage raw: %.2f)." % Car.get_main_voltage_raw())
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
                    Output.print_info("State: Key ON; engine running.")
                if Car.is_aux_batt_full(log=first_time_ind):
                    Timer.start_charge_delay_timer("aux battery full already", delay_s=600)
                else:
                    Car.charge_aux_batt(log=first_time_ind, post_delay=first_time_ind)

            elif key_acc_powered:
                # Key in ACC or ON but engine off.
                if first_time_ind:
                    Output.print_info("State: Key in ACC or ON; engine off.")
                if Car.is_aux_batt_sufficient(log=first_time_ind):
                    Car.charge_starter_batt(log=first_time_ind, post_delay=first_time_ind)
                else:
                    # If Li batt V low, power down RPi.
                    Car.is_aux_batt_sufficient(log=True) # Call again just for logging
                    Car.shut_down_controller(delay=60)
                    break
                    # Will need to be manually turned back on either by key cycle or enable-switch cycle.

            else:
                # Key OFF
                if first_time_ind:
                    Output.print_info("State: Key OFF.")

                # if not Car.is_aux_batt_sufficient(log=first_time_ind):
                temp_threshold = 12 # temp measure until long-term key-off charge logic implemented.
                if not Car.is_aux_batt_sufficient(threshold_override=temp_threshold, log=False):
                    # If Li batt V low, power down RPi.
                    Car.is_aux_batt_sufficient(threshold_override=temp_threshold, log=True) # Call again just for logging
                    Output.print_warn("Li batt V low (%.2f); initiating RPi shutdown." % Car.get_aux_voltage(log=False))
                    Car.shut_down_controller(delay=60)
                    break
                    # Will turn back on next time key turned to ACC (assuming enable switch on)
                # elif not Car.does_starter_batt_need_charge(log=first_time_ind):
                #     if first_time_ind:
                #         Output.print_warn("Starter batt fully charged; initiating RPi shutdown.")
                #     Car.shut_down_controller()
                #     break
                else:
                    # Keep charging while FLA batt needs charge and Li batt V sufficient.
                    Car.charge_starter_batt(log=first_time_ind, post_delay=first_time_ind)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, Controller().sigterm_handler) # method that turns off LEDs and relays and exits Python script

    while True:
        Output = OutputHandler()
        Timer = Output.Clock     # TimeKeeper object created in OutputHandler.__init__()

        try:
            main(Output, Timer)
        except TimeoutError:
            # Thrown by AutomationHAT - "Timed out waiting for conversion."
            # Seems to be caused by system acquiring NTP sync, jumping system time, and some mechanics in AutomationHAT code infer an op timed out.
            Output.print_err(traceback.format_exc())
            Output.print_err("Restarting program (TimeoutError caught)." % delay)
            continue
        except OSError as e:
            # This block seems to catch other errors unintentionally, so have to be more specific.
            if e.errno == 16:
                # "OSError: [Errno 16] Device or resource busy" | Thrown by AutomationHAT. May be caused by system acquiring NTP sync,
                #                                                 jumping system time, and some mechanics in AutomationHAT code infer an op timed out.
                Output.print_err(traceback.format_exc())
                Output.print_err("Restarting program (OSError 16 caught)." % delay)
                continue
                # "OSError: [Errno 5] Input/output error" thrown when AutomationHAT absent. Handle below.
            else:
                Output.print_exit(traceback.format_exc())
                Controller().open_all_relays()
                break
        except KeyboardInterrupt:
            Output.print_exit("Keyboard interrupt.")
            Controller().open_all_relays()
            break
        except Exception:
            Output.print_exit(traceback.format_exc())
            Controller().open_all_relays()
            break
        except:
            Output.print_exit("Program killed by OS.")
            Controller().open_all_relays()
            break
