import os
import time
import subprocess
import datetime as dt
from colorama import Style, Fore, Back

import automationhat as ah


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")


ALTERNATOR_OUTPUT_V_MIN = 13
MAIN_V_MAX = 14.5
MAIN_V_CHARGED = 12.6
MAIN_V_MIN = 11.5

AUX_V_MAX = 13.5
AUX_V_MIN = 11.5      # Don't let aux batt drop below this.

# Automation Hat pins
AUX_BATT_V_MONITORING_PIN = 0   # labeled 1 on board
MAIN_BATT_V_MONITORING_PIN = 1  # labeled 2 on board

KEY_ACC_INPUT_PIN = 0           # labeled 1 on board
KEY_ON_INPUT_PIN = 1            # labeled 2 on board
ENABLE_SWITCH_DETECT_PIN = 2    # labeled 3 on board

CHARGER_ENABLE_RELAY = 0        # labeled 1 on board
CHARGE_DIRECTION_RELAY = 1     # labeled 2 on board
KEEPALIVE_RELAY = 2             # labeled 3 on board

STATE_CHANGE_DELAY_SEC = 30
RPI_SHUTDOWN_DELAY_SEC = 30


class ChargeControlError(Exception):
    pass

class SystemVoltageError(Exception):
    pass


class OutputHandler(object):
    def __init__(self):
        self._create_log_file()

    def _create_log_file(self):
        timestamp = dt.datetime.now().strftime("%Y%m%d")
        log_filename = "%s.log" % timestamp
        self.log_filepath = os.path.join(LOG_DIR, log_filename)
        if not os.path.exists(self.log_filepath):
            # If multiple runs on same day, appends to existing file.
            # If program runs over midnight, after-midnight events will be in previous day's logs.
            with open(self.log_filepath, "w") as fd:
                pass

    def _add_to_log_file(self, print_str):
        with open(self.log_filepath, "a") as log_file:
            log_file.write("%s\n" % print_str)

    def _print_and_log(self, message, color=Fore.WHITE, style=Style.BRIGHT, prompt=False):
        timestamp = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        print_str = Style.NORMAL + timestamp + " " + color + style + message
        log_str = timestamp + " " + message

        if prompt:
            print(print_str)
            self._add_to_log_file(log_str)

            user_input = input("> " + Style.RESET_ALL)
            self._add_to_log_file("\t> " + user_input)
            return user_input
        else:
            print(print_str + Style.RESET_ALL)
            self._add_to_log_file(log_str)
            return None

    def print_temp(self, print_str, prompt_user=False):
        return self._print_and_log("[TEMP]  %s" % print_str, Fore.CYAN, prompt=prompt_user)

    def print_debug(self, print_str, prompt_user=False):
        return self._print_and_log("[DEBUG] %s" % print_str, Fore.WHITE, Style.DIM, prompt=prompt_user)

    def print_warn(self, print_str, prompt_user=False):
        return self._print_and_log("[WARN]  %s" % print_str, Fore.YELLOW, prompt=prompt_user)

    def print_err(self, print_str, prompt_user=False):
        return self._print_and_log("[ERROR] %s" % print_str, Fore.RED, prompt=prompt_user)



class TimeKeeper(object):
    def __init__(self, Output):
        self.Output = Output
        self.state_change_timer_start = None
        self.shutdown_timer_start = None

    def start_shutdown_timer(self, log=True):
        """If called while timer already running, timer restarts.
        """
        self.shutdown_timer_start = dt.datetime.now()
        if log:
            self.Output.print_debug("RPi shutdown timer started at %s." % self.state_change_timer_start.strftime("%H:%M:%S"))

    def stop_shutdown_timer(self, log=True):
        self.shutdown_timer_start = None
        if log:
            self.Output.print_debug("RPi shutdown timer stopped.")

    def has_shutdown_delay_elapsed(self, log=False):
        """Evaluates if shutdown grace period has elapsed.
        Returns True or False.
        """
        if self.shutdown_timer_start is None:
            return False
        else:
            is_time_up = self._has_time_elapsed(self.shutdown_timer_start, RPI_SHUTDOWN_DELAY_SEC)
            if is_time_up and log:
                self.Output.print_debug("Shutdown-delay time has elapsed.")
            return is_time_up

    def start_charge_delay_timer(self, state_change_desc, log=True):
        """If called while timer already running, timer restarts.
        """
        self.state_change_timer_start = dt.datetime.now()
        if log:
            self.Output.print_debug("Charge-delay timer started (%s) at %s." % (state_change_desc, self.state_change_timer_start.strftime("%H:%M:%S")))

    def has_charge_delay_time_elapsed(self):
        """Evaluates if state-delay buffer time has elapsed since last state change.
        Returns two booleans - first indicates whether charge-delay time has elapsed.
        Second indicates if this is the first calling of the method that the response
        has been "True" since the timer started.
        """
        if self.state_change_timer_start is None:
            return (True, False)
        else:
            is_time_up = self._has_time_elapsed(self.state_change_timer_start, STATE_CHANGE_DELAY_SEC)
            if is_time_up:
                self.state_change_timer_start = None
            return (is_time_up, is_time_up)

    def _get_time_elapsed(self, start_time):
        return (dt.datetime.now() - start_time)

    def _has_time_elapsed(self, start_time, threshold_sec):
        if self._get_time_elapsed(start_time) >= dt.timedelta(seconds=threshold_sec):
            return True
        else:
            return False
        # https://www.tutorialspoint.com/How-can-we-do-date-and-time-math-in-Python


class Controller(object):
    def __init__(self):
        self.input_list = [0, 1, 2]
        self.relay_list = [0, 1, 2]
        self.analog_list = [0, 1, 2]

    def read_voltage(self, analog_pin_num):
        assert analog_pin_num in self.analog_list, "Called Controller.read_voltage() with invalid analog_pin_num %d" % analog_pin_num
        return ah.analog[analog_pin_num].read()

    def is_input_high(self, input_pin_num):
        assert input_pin_num in self.input_list, "Called Controller.is_input_high() with invalid input_pin_num %d" % input_pin_num
        return ah.input[input_pin_num].is_on()

    def is_input_low(self, input_pin_num):
        assert input_pin_num in self.input_list, "Called Controller.is_input_low() with invalid input_pin_num %d" % input_pin_num
        return ah.input[input_pin_num].is_off()

    def is_relay_on(self, relay_num):
        assert relay_num in self.relay_list, "Called Controller.is_relay_on() with invalid relay_num %d" % relay_num
        return ah.relay[relay_num].is_on()

    def is_relay_off(self, relay_num):
        assert relay_num in self.relay_list, "Called Controller.is_relay_off() with invalid relay_num %d" % relay_num
        return ah.relay[relay_num].is_off()

    def close_relay(self, relay_num):
        assert relay_num in self.relay_list, "Called Controller.close_relay() with invalid relay_num %d" % relay_num
        ah.relay[relay_num].on()
        assert self.is_relay_on(relay_num), "Tried to close relay %d but follow-up check failed." % relay_num

    def open_relay(self, relay_num):
        assert relay_num in self.relay_list, "Called Controller.open_relay() with invalid relay_num %d" % relay_num
        ah.relay[relay_num].off()
        assert self.is_relay_off(relay_num), "Tried to open relay %d but follow-up check failed." % relay_num

    def shut_down(self):
        # First open all relays
        for relay_num in self.relay_list:
            self.open_relay(relay_num)
        subprocess.run(["/usr/bin/sudo", "/sbin/shutdown", "-h", "now"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        # https://learn.sparkfun.com/tutorials/raspberry-pi-safe-reboot-and-shutdown-button/all


class Vehicle(object):
    def __init__(self, Output):
        self.Output = Output
        self.StarterBatt = StarterBattery(MAIN_BATT_V_MONITORING_PIN)
        self.AuxBatt = AuxBattery(AUX_BATT_V_MONITORING_PIN)
        self.BattCharger = BatteryCharger(self.Output)

        self.key_acc_detect_pin = KEY_ACC_INPUT_PIN
        self.key_on_detect_pin = KEY_ON_INPUT_PIN

        self.enable_sw_detect_pin = ENABLE_SWITCH_DETECT_PIN
        self.keepalive_relay_num = KEEPALIVE_RELAY

        Controller().close_relay(self.keepalive_relay_num) # Keep on whenever device is on.

    def is_acc_powered(self):
        # Returns True when key in either ACC or ON position
        return Controller().is_input_high(self.key_acc_detect_pin)

    def is_key_on(self):
        return Controller().is_input_high(self.key_on_detect_pin)

    def is_key_off(self):
        return not self.is_acc_powered()

    def is_engine_running(self):
        return self.StarterBatt.get_voltage() >= ALTERNATOR_OUTPUT_V_MIN

    def is_enable_switch_closed(self, log=False):
        # Either ACC power present or keepalive relay should always be powering switch.
        # If key in OFF position, only way to get signal here is with keepalive relay enabled.
        if Controller().is_relay_off(self.keepalive_relay_num) and self.is_key_off():
            # If key off and enable not detected, could be because keepalive relay off too.
            # In this state, can't tell if enable switch on or off. Indeterminate reading.
            self.Output.log_err("Keepalive relay off when expected to be held on during enable-switch state check.")
            Controller().close_relay(self.keepalive_relay_num) # Should have been on already, but if not, turn on.
            time.sleep(0.2) # Give time for propagation delay
            return self.is_enable_switch_closed(log=log)

        enable_detect = Controller().is_input_high(self.enable_sw_detect_pin)
        if log:
            self.Output.print_debug("Enable switch %s" % ("ON" if enable_detect else "OFF"))
        return enable_detect

    def get_main_voltage_raw(self, log=False):
        voltage = self.StarterBatt.get_voltage()
        if log:
            self.Output.print_debug("Main voltage (raw): %.2fV" % voltage)
        return voltage

    def get_aux_voltage_raw(self, log=False):
        voltage = self.AuxBatt.get_voltage()
        if log:
            self.Output.print_debug("Aux voltage (raw): %.2fV" % voltage)
        return voltage

    def get_main_oc_voltage_est(self, log=False):
        # Needs hysteresis to avoid bang-bang ctrl
        if self.is_engine_running():
            return 13
            # Too hard to estimate
            if log:
                self.Output.print_warn("Engine running during main open-circuit voltage estimation.")
        elif self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_rev():
            # Currently charging aux battery
            offset = 0.5
            # Offset depends on charge current, but no way to infer that currently.
            # TODO experimentally determine more accurate value.
            # Should this be reworked to automatically shut down charging, wait and then measure?
            if log:
                self.Output.print_warn("Charging aux battery during main open-circuit voltage estimation.")
        elif self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_fwd():
            # Currently being charged, elevating voltage
            offset = -0.5
            # Offset depends on charge current, but no way to infer that currently.
            # TODO experimentally determine more accurate value.
            # Should this be reworked to automatically shut down charging, wait and then measure?
            if log:
                self.Output.print_warn("Main battery being charged by aux batt during main open-circuit voltage estimation.")
        else:
            offset = 0

        voltage_est = self.get_main_voltage_raw(log=log) + offset
        if log:
            self.Output.print_debug("Main open-circuit voltage est: %.2fV (includes %.1fV offset)" % (voltage_est, offset))
        return voltage_est

    def get_aux_oc_voltage_est(self, log=False):
        # Needs hysteresis to avoid bang-bang ctrl
        if self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_fwd():
            # Currently charging starter battery
            offset = 0.5
            # TODO experimentally determine more accurate value.
            # Offset depends on charge current, but no way to infer that currently.
            if log:
                self.Output.print_warn("Charging starter battery during aux-batt open-circuit voltage estimation.")
        elif self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_rev():
            # Currently being charged, elevating voltage
            offset = -0.5
            # TODO experimentally determine more accurate value.
            # Offset depends on charge current, but no way to infer that currently.
            if log:
                self.Output.print_warn("Aux battery being charged during aux-batt open-circuit voltage estimation.")
        else:
            offset = 0

        voltage_est = self.get_aux_voltage_raw(log=log) + offset
        if log:
            self.Output.print_debug("Aux open-circuit voltage est: %.2fV (includes %.1fV offset)" % (voltage_est, offset))
        return voltage_est

    def is_starter_batt_low(self, log=True):
        est_voltage = self.get_main_oc_voltage_est(log=log)
        is_low = est_voltage <= MAIN_V_MIN
        if log and is_low:
            self.Output.print_warn("Starter-batt voltage %.2f below min allowed %.2f." % (est_voltage, MAIN_V_MIN))
        return is_low

    def is_starter_batt_charged(self, log=False):
        return (self.get_aux_oc_voltage_est(log=log) >= MAIN_V_CHARGED)

    def does_starter_batt_need_charge(self, log=False):
        return not self.is_starter_batt_charged(log=log)

    def is_aux_batt_empty(self, log=True):
        est_voltage = self.get_aux_oc_voltage_est(log=log)
        is_low = est_voltage <= AUX_V_MIN
        if log and is_low:
            self.Output.print_warn("Aux-batt voltage %.2f below min allowed %.2f." % (est_voltage, AUX_V_MIN))
        elif log:
            self.Output.print_debug("Aux-batt voltage %.2f sufficient (min allowed: %.2f)." % (est_voltage, AUX_V_MIN))
        return is_low

    def is_aux_batt_sufficient(self, log=False):
        return not self.is_aux_batt_empty(log=log)

    def is_aux_batt_full(self, log=False):
        est_voltage = self.get_aux_oc_voltage_est(log=log)
        is_full = est_voltage >= AUX_V_MAX
        if log and is_full:
            self.Output.print_debug("Aux batt full (%.2fV)" % est_voltage)
        return is_full

    def charge_starter_batt(self, log=True):
        if self.is_aux_batt_empty(log=log):
            output_str = "Called Vehicle.charge_starter_batt(), " \
                         "but aux batt V (%.2fV) is below min threshold %.2f." \
                         % (self.get_aux_oc_voltage_est(log=False), AUX_V_MIN)
            self.Output.print_err(output_str)
            raise ChargeControlError(output_str)
        if self.get_main_oc_voltage_est(log=log) > MAIN_V_MAX:
            output_str = "Called Vehicle.charge_starter_batt(), " \
                         "but starter batt V (%.2fV) is over max threshold %.2f." \
                         % (self.get_main_oc_voltage_est(log=False), MAIN_V_MAX)
            self.Output.print_err(output_str)
            raise SystemVoltageError(output_str)

        self.BattCharger.set_charge_direction_fwd()
        self.BattCharger.enable_charge()
        if log:
            self.Output.print_debug("Charging starter batt.")

    def charge_aux_batt(self, log=False):
        if not self.is_engine_running():
            output_str = "Called Vehicle.charge_aux_batt(), but engine not running."
            self.Output.print_err(output_str)
            raise ChargeControlError(output_str)
        if self.is_aux_batt_full(log=log):
            output_str = "Called Vehicle.charge_aux_batt(), " \
                         "and aux batt already full (%.2fV > %.2fV." \
                         % (self.get_aux_oc_voltage_est(log=False), AUX_V_MAX)
            self.Output.print_debug(output_str)

        self.BattCharger.set_charge_direction_rev()
        self.BattCharger.enable_charge()
        if log:
            self.Output.print_debug("Charging starter batt.")

    def stop_charging(self, log=True):
        self.BattCharger.disable_charge()
        if log:
            self.Output.print_debug("Stopped charging.")

    def shut_down_controller(self):
        self.Output.print_warn("Shutting down controller.")
        Controller().shut_down()


class Battery(object):
    def __init__(self, voltage_sensing_pin):
        assert voltage_sensing_pin in (0, 1, 2), "Tried to create battery object with invalid voltage-sensing pin %d" % voltage_sensing_pin
        self.v_pin = voltage_sensing_pin

    def get_voltage(self):
        return Controller().read_voltage(self.v_pin)


class StarterBattery(Battery):
    pass

class AuxBattery(Battery):
    pass


class BatteryCharger(object):
    def __init__(self, Output):
        self.Output = Output
        self.charger_enable_relay = CHARGER_ENABLE_RELAY
        self.charge_direction_relay = CHARGE_DIRECTION_RELAY

    def is_charging(self):
        return Controller().is_relay_on(self.charger_enable_relay)

    def enable_charge(self):
        if not self.is_charging():
            Controller().close_relay(self.charger_enable_relay)
            time.sleep(0.5)
        if not self.is_charging():
            self.Output.print_err("BatteryCharger.enable_charge() failed to start charging.")
            raise ChargeControlError("BatteryCharger.enable_charge() failed to start charging.")

    def disable_charge(self):
        if self.is_charging():
            Controller().open_relay(self.charger_enable_relay)
            # Allow system voltage to settle
            time.sleep(0.5)
            # Also release charge-direction relay to avoid wasting energy through its coil.
            Controller().open_relay(self.charge_direction_relay)

        if self.is_charging():
            self.Output.print_err("BatteryCharger.disable_charge() failed to stop charging.")
            raise ChargeControlError("BatteryCharger.disable_charge() failed to stop charging.")
        if not Controller().is_relay_off(self.charge_direction_relay):
            self.Output.print_err("BatteryCharger.disable_charge() failed to open charge-direction relay.")
            raise ChargeControlError("BatteryCharger.disable_charge() failed to open charge-direction relay.")

    def is_charge_direction_fwd(self):
        return Controller().is_relay_on(self.charge_direction_relay)

    def is_charge_direction_rev(self):
        return Controller().is_relay_off(self.charge_direction_relay)

    def set_charge_direction_fwd(self):
        # Charge starter battery with aux battery
        if self.is_charge_direction_rev():
            self.disable_charge()
            Controller().close_relay(self.charge_direction_relay)
            time.sleep(0.5)
        if not self.is_charge_direction_fwd():
            self.Output.print_err("BatteryCharger.set_charge_direction_fwd() failed to set direction.")
            raise ChargeControlError("BatteryCharger.set_charge_direction_fwd() failed to set direction.")

    def set_charge_direction_rev(self):
        # Charge aux battery with alternator
        if self.is_charge_direction_fwd():
            self.disable_charge()
            Controller().open_relay(self.charge_direction_relay)
            time.sleep(0.5)
        if not self.is_charge_direction_rev():
            self.Output.print_err("BatteryCharger.set_charge_direction_rev() failed to set direction.")
            raise ChargeControlError("BatteryCharger.set_charge_direction_rev() failed to set direction.")

