import os
import time
import sys
import subprocess
import datetime as dt
import ntplib
from colorama import Style, Fore, Back

import automationhat as ah

from network_names import stored_ssid_mapping_dict     # local file


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")


ALTERNATOR_OUTPUT_V_MIN = 12.7
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
        self.time_valid = False
        self._create_log_file()
        self._print_startup()

    def assert_time_valid(self):
        self.time_valid = True

    def _get_datestamp(self, ntp_reqd=True):
        if self.time_valid or not ntp_reqd:
            return dt.datetime.now().strftime("%Y%m%d")
        else:
            return "--------"

    def _get_timestamp(self):
        if self.time_valid:
            return dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        else:
            return "---------" + dt.datetime.now().strftime("%H%M%S")
            # Keep incorrect time displayed because relative differences still useful in log.

    def _create_log_file(self):
        datestamp = self._get_datestamp(ntp_reqd=False)
        # If time not yet updated via NTP, this will just append to most recent log - okay
        self.log_filepath = os.path.join(LOG_DIR, "%s.log" % datestamp)
        if not os.path.exists(self.log_filepath):
            # If multiple runs on same day, appends to existing file.
            # If program runs over midnight, after-midnight events will be in previous day's logs.
            with open(self.log_filepath, "w") as fd:
                pass

    def _add_to_log_file(self, print_str):
        self._create_log_file() # Ensures if date changes while program running, new log entries are written to next day's log.
        with open(self.log_filepath, "a") as log_file:
            log_file.write("%s\n" % print_str)

    def _print_and_log(self, message, color=Fore.WHITE, style=Style.BRIGHT, prompt=False):
        timestamp = self._get_timestamp()
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

    def _print_startup(self):
        self._add_to_log_file("")
        self._add_to_log_file("-"*23 + " PROGRAM START [PID: %d] " % os.getpid() + "-"*20)

    def print_temp(self, print_str, prompt_user=False):
        return self._print_and_log("[TEMP]  %s" % print_str, Fore.CYAN, prompt=prompt_user)

    def print_info(self, print_str, prompt_user=False):
        return self._print_and_log("[INFO]  %s" % print_str, Fore.WHITE, prompt=prompt_user)

    def print_debug(self, print_str, prompt_user=False):
        return self._print_and_log("[DEBUG] %s" % print_str, Fore.WHITE, Style.DIM, prompt=prompt_user)

    def print_warn(self, print_str, prompt_user=False):
        return self._print_and_log("[WARN]  %s" % print_str, Fore.YELLOW, prompt=prompt_user)

    def print_err(self, print_str, prompt_user=False):
        return self._print_and_log("[ERROR] %s" % print_str, Fore.RED, prompt=prompt_user)

    def print_shutdown(self, error_msg):
        self.print_err(error_msg)
        self.print_debug("[PID %d killed]" % os.getpid())


class TimeKeeper(object):
    def __init__(self, Output, ntp_wait_time):
        self.Output = Output
        self.state_change_timer_start = None
        self.shutdown_timer_start = None

        self.valid_sys_time = False
        self.wait_for_ntp_update(ntp_wait_time, log=True)

    def get_network_name(self):
        """Uses local file w/ SSID->name dict. Returns name of network as string.
        """
        result = subprocess.run("iwgetid -r", capture_output=True, text=True, shell=True)
        network_ssid = result.stdout.strip()
        self.Output.print_temp("network_ssid returned by iwgetid: %s" % network_ssid)
        return stored_ssid_mapping_dict.get(network_ssid)

    def wait_for_ntp_update(self, wait_time, log=False):
        # Establish whether OS has correct time before starting logging.
        if log:
            self.Output.print_debug("Checking if sys date/time synchronized to NTP server...")

        # ntplib.NTPClient().request("pool.ntp.org", timeout=wait_time)
        start_time = dt.datetime.now()
        Controller().light_red_led(0.5)
        Controller().light_blue_led(0.5)
        while not self._has_time_elapsed(start_time, wait_time):
            result = subprocess.run(["timedatectl", "show", "--property=NTPSynchronized"], capture_output=True, text=True)
            if result.stdout.strip() == "NTPSynchronized=yes":
                self.valid_sys_time = True
                self.Output.assert_time_valid()
                self.Output.print_info("System date/time NTP-synchronized.")
                break
        Controller().light_red_led(0)
        Controller().light_blue_led(0)

        if log and not self.valid_sys_time:
            self.Output.print_warn("System date/time not yet updated since last power loss.")

    def is_sys_time_valid(self):
        return self.valid_sys_time

    def get_seconds(self):
        """Returns seconds part of current time as int.
        """
        return int(dt.datetime.now().strftime("%-S"))

    def start_shutdown_timer(self, log=True):
        """If called while timer already running, timer restarts.
        """
        self.shutdown_timer_start = dt.datetime.now()
        if log:
            self.Output.print_debug("RPi shutdown timer started at %s." % self.shutdown_timer_start.strftime("%H:%M:%S"))

    def is_shutdown_pending(self):
        if self.shutdown_timer_start is None:
            return False
        else:
            return True

    def stop_shutdown_timer(self, log=True):
        self.shutdown_timer_start = None
        Controller().turn_off_all_ind_leds()
        if log:
            self.Output.print_debug("RPi shutdown timer stopped.")

    def has_shutdown_delay_elapsed(self, log=False):
        """Evaluates if shutdown grace period has elapsed.
        Returns True or False.
        """
        if self.shutdown_timer_start is None:
            return False
        else:
            if self.get_seconds() % 3 == 0:
                Controller().toggle_red_led()
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
            # Timer not running
            return (True, False)
        elif self.is_shutdown_pending():
            # Don't allow charge initiation while shutdown timer active.
            return (False, False)
        else:
            is_time_up = self._has_time_elapsed(self.state_change_timer_start, STATE_CHANGE_DELAY_SEC)
            if is_time_up:
                self.state_change_timer_start = None
                Controller().turn_off_all_ind_leds()
            elif self.get_seconds() % 2 == 0:
                Controller().toggle_green_led()
                Controller().light_blue_led(brightness=int(Controller().is_green_led_lit()))
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
        self.ind_led_list = [0, 1, 2]

    def _light_led(self, led_num, brightness=None):
        if brightness is None:
            brightness = 1
        ah.light[led_num].write(brightness)

    def light_green_led(self, brightness=None):
        self._light_led(0, brightness=brightness)

    def light_blue_led(self, brightness=None):
        self._light_led(1, brightness=brightness)

    def light_red_led(self, brightness=None):
        self._light_led(2, brightness=brightness)

    def toggle_green_led(self):
        ah.light[0].toggle()

    def toggle_blue_led(self):
        ah.light[1].toggle()

    def toggle_red_led(self):
        ah.light[2].toggle()

    def is_green_led_lit(self):
        return (ah.light[0].read() == 0)

    def is_blue_led_lit(self):
        return (ah.light[1].read() == 0)

    def is_red_led_lit(self):
        return (ah.light[2].read() == 0)

    def turn_off_all_ind_leds(self):
        for led_num in self.ind_led_list:
            ah.light[led_num].off()

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

    def open_all_relays(self):
        for relay_num in self.relay_list:
            self.open_relay(relay_num)

    def reboot(self):
        self.turn_off_all_ind_leds()
        self.open_all_relays()
        subprocess.run(["/usr/bin/sudo", "usr/sbin/reboot"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    def shut_down(self):
        self.turn_off_all_ind_leds()
        self.open_all_relays()
        subprocess.run(["/usr/bin/sudo", "usr/sbin/shutdown", "-h", "now"],
                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        # https://learn.sparkfun.com/tutorials/raspberry-pi-safe-reboot-and-shutdown-button/all

    def sigterm_handler(self, _signo, _stack_frame):
        """Pipe kill signal from Linux to this Python script to allow graceful exit.
        """
        self.turn_off_all_ind_leds()
        self.open_all_relays()
        sys.exit(0)
        # https://stackoverflow.com/questions/18499497/how-to-process-sigterm-signal-gracefully


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

        self.led_level = 0

        Controller().open_all_relays()
        Controller().close_relay(self.keepalive_relay_num) # Keep on whenever device is on.

    def is_acc_powered(self):
        # Returns True when key in either ACC or ON position
        return Controller().is_input_high(self.key_acc_detect_pin)

    def is_key_on(self):
        return Controller().is_input_high(self.key_on_detect_pin)

    def is_key_off(self):
        return not self.is_acc_powered()

    def is_engine_running(self):
        return (self.is_key_on() and (self.get_main_voltage_raw(log=False) >= ALTERNATOR_OUTPUT_V_MIN))

    def is_enable_switch_closed(self, log=False):
        # Either ACC power present or keepalive relay should always be powering switch.
        # If key in OFF position, only way to get signal here is with keepalive relay enabled.
        if Controller().is_relay_off(self.keepalive_relay_num) and self.is_key_off():
            # If key off and enable not detected, could be because keepalive relay off too.
            # In this state, can't tell if enable switch on or off. Indeterminate reading.
            self.Output.print_err("Keepalive relay off when expected to be held on during enable-switch state check.")
            Controller().close_relay(self.keepalive_relay_num) # Should have been on already, but if not, turn on.
            time.sleep(0.2) # Give time for propagation delay
            return self.is_enable_switch_closed(log=log)

        enable_detect = Controller().is_input_high(self.enable_sw_detect_pin)
        if log and enable_detect:
            self.Output.print_info("Enable switch ON")
        elif log:
            self.Output.print_warn("Enable switch OFF")
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

    def get_main_voltage(self, log=False):
        elevated = False
        if self.is_engine_running():
            # Currently being charged, elevating voltage
            elevated = True
            if log:
                self.Output.print_warn("Engine running during FLA battery-voltage reading (elevating value).")
        elif self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_fwd():
            # Currently being charged, elevating FLA voltage
            elevated = True
            if log:
                self.Output.print_warn("Starter battery being charged (by aux batt) during FLA battery-voltage reading (elevating value).")
        elif self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_rev():
            # Currently charging aux battery, depressing main voltage.
            # This should only happen while engine running (and so be caught above)
            output_str = "Aux batt being charged w/o engine running."
            self.Output.print_err(output_str)
            raise ChargeControlError(output_str)

        voltage_est = self.get_main_voltage_raw(log=log)
        if log:
            self.Output.print_debug("FLA battery-voltage reading: %.2fV%s."
                                    % (voltage_est, (" (assumed elevated)" if elevated else "")))
        return voltage_est

        # # INACTIVE (needs further dev):
        # # Needs hysteresis to avoid bang-bang ctrl
        # if self.is_engine_running():
        #     if log:
        #         self.Output.print_warn("Engine running during main open-circuit voltage estimation.")
        #     # Too hard to estimate
        #     if self.get_main_voltage_raw >= 13:
        #         voltage_est = 13
        #         if log:
        #             self.Output.print_debug("Main open-circuit voltage est: %.2fV (pegged while engine running)" % voltage_est)
        #     else:
        #         voltage_est = 12.5
        #         if log:
        #             self.Output.print_debug("Main open-circuit voltage est: %.2fV (pegged while engine running)" % voltage_est)

        # else:
        #     if self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_rev():
        #         # Currently charging aux battery
        #         offset = 0.5
        #         # Offset depends on charge current, but no way to infer that currently.
        #         # TODO experimentally determine more accurate value.
        #         # Should this be reworked to automatically shut down charging, wait and then measure?
        #         if log:
        #             self.Output.print_warn("Charging aux battery during main open-circuit voltage estimation.")
        #     elif self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_fwd():
        #         # Currently being charged, elevating voltage
        #         offset = -0.5
        #         # Offset depends on charge current, but no way to infer that currently.
        #         # TODO experimentally determine more accurate value.
        #         # Should this be reworked to automatically shut down charging, wait and then measure?
        #         if log:
        #             self.Output.print_warn("Main battery being charged by aux batt during main open-circuit voltage estimation.")
        #     else:
        #         offset = 0
        #     voltage_est = self.get_main_voltage_raw(log=log) + offset

        #     if log:
        #         self.Output.print_debug("Main open-circuit voltage est: %.2fV (includes %.1fV offset)" % (voltage_est, offset))
        # return voltage_est

    def get_aux_voltage(self, log=False):
        elevated = False
        depressed = False
        if self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_fwd():
            # Currently charging starter battery, depressing aux-batt voltage.
            depressed = True
            if log:
                self.Output.print_warn("Aux batt charging starter battery during Li battery-voltage reading (depressing value).")
        elif self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_rev():
            # Currently being charged, elevating aux-batt voltage.
            if log:
                self.Output.print_warn("Aux batt being charged during Li battery-voltage reading (elevating value).")

        if elevated and depressed:
            output_str = "Vehicle.get_aux_voltage() indicating voltage both elevated and depressed (mutually exclusive)."
            self.Output.print_err(output_str)
            raise SystemVoltageError(output_str)

        voltage_est = self.get_aux_voltage_raw(log=log)
        if log:
            self.Output.print_debug("Li battery-voltage reading: %.2fV%s."
                                    % (voltage_est, (" (assumed elevated)" if elevated
                                               else (" (assumed depressed)" if depressed else ""))))
        return voltage_est

        # # INACTIVE (needs further dev):
        # # Needs hysteresis to avoid bang-bang ctrl
        # if self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_fwd():
        #     # Currently charging starter battery
        #     offset = 0.5
        #     # TODO experimentally determine more accurate value.
        #     # Offset depends on charge current, but no way to infer that currently.
        #     if log:
        #         self.Output.print_warn("Charging starter battery during aux-batt open-circuit voltage estimation.")
        # elif self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_rev():
        #     # Currently being charged, elevating voltage
        #     offset = -0.5
        #     # TODO experimentally determine more accurate value.
        #     # Offset depends on charge current, but no way to infer that currently.
        #     if log:
        #         self.Output.print_warn("Aux battery being charged during aux-batt open-circuit voltage estimation.")
        # else:
        #     offset = 0

        # voltage_est = self.get_aux_voltage_raw(log=log) + offset
        # if log:
        #     self.Output.print_debug("Aux open-circuit voltage est: %.2fV (includes %.1fV offset)" % (voltage_est, offset))
        # return voltage_est

    def is_starter_batt_low(self, log=True):
        est_voltage = self.get_main_voltage(log=log)
        is_low = est_voltage < MAIN_V_MIN
        if log and is_low:
            self.Output.print_warn("Starter-batt voltage %.2fV below min allowed %.2fV." % (est_voltage, MAIN_V_MIN))
        return is_low

    def is_starter_batt_charged(self, log=False):
        return (self.get_main_voltage(log=log) >= MAIN_V_CHARGED)

    def does_starter_batt_need_charge(self, log=False):
        return not self.is_starter_batt_charged(log=log)

    def is_aux_batt_empty(self, threshold_override=None, log=True):
        if threshold_override is not None:
            threshold = threshold_override
        else:
            threshold = AUX_V_MIN

        est_voltage = self.get_aux_voltage(log=log)
        is_low = est_voltage < threshold
        if log and is_low:
            self.Output.print_warn("Aux-batt voltage %.2fV below min allowed %.2fV." % (est_voltage, threshold))
        elif log:
            self.Output.print_debug("Aux-batt voltage %.2fV sufficient (min allowed: %.2fV)." % (est_voltage, threshold))
        return is_low

    def is_aux_batt_sufficient(self, threshold_override=None, log=False):
        return not self.is_aux_batt_empty(threshold_override=threshold_override, log=log)

    def is_aux_batt_full(self, log=False):
        est_voltage = self.get_aux_voltage(log=log)
        is_full = est_voltage >= AUX_V_MAX
        if log and is_full:
            self.Output.print_debug("Aux batt full (%.2fV)" % est_voltage)
        return is_full

    def charge_starter_batt(self, log=True):
        if self.is_aux_batt_empty(log=False):
            output_str = "Called Vehicle.charge_starter_batt(), " \
                         "but aux batt V (%.2fV) is below min threshold %.2fV." \
                         % (self.get_aux_voltage(log=False), AUX_V_MIN)
            self.Output.print_err(output_str)
            raise ChargeControlError(output_str)
        if self.get_main_voltage(log=False) > MAIN_V_MAX:
            output_str = "Called Vehicle.charge_starter_batt(), " \
                         "but starter batt V (%.2fV) is over max threshold %.2fV." \
                         % (self.get_main_voltage(log=False), MAIN_V_MAX)
            self.Output.print_err(output_str)
            raise SystemVoltageError(output_str)

        self.BattCharger.set_charge_direction_fwd()
        self.BattCharger.enable_charge()
        # self.roll_indicator_light(Controller().light_blue_led)
        Controller().toggle_blue_led()
        if log:
            self.Output.print_info("Charging starter battery.")

    def charge_aux_batt(self, log=False):
        if not self.is_starter_batt_charged():
            # Only want to charge with engine running. Sometimes alternator output
            # varies to where this was incorrectly inferring engine off momentarily
            # and shutting program down. Switched to just making sure battery charged.
            main_voltage = self.get_main_voltage(log=True)
            output_str = "Called Vehicle.charge_aux_batt(), but starter battery insufficiently charged (%.2fV)." % main_voltage
            self.Output.print_err(output_str)
            raise ChargeControlError(output_str)
        if self.is_aux_batt_full(log=False):
            output_str = "Called Vehicle.charge_aux_batt(), " \
                         "and aux batt already full (%.2fV > %.2fV max." \
                         % (self.get_aux_voltage(log=False), AUX_V_MAX)
            self.Output.print_warn(output_str)

        self.BattCharger.set_charge_direction_rev()

        # # TEMP disabled while DPDT relay not yet installed.
        # self.BattCharger.enable_charge()
        # self.roll_indicator_light(Controller().light_green_led)
        Controller().toggle_green_led()
        # # TEMP

        if log:
            self.Output.print_info("Charging auxiliary battery.")

    def roll_indicator_light(self, led_fxn):
        """Increment brightness to produce glowing effect.
        Pass LED function like Controller().light_blue_led
        """
        # self.led_level = int(dt.datetime.now().strftime("%f")[:2])/100
        if int(dt.datetime.now().strftime("%f")[0]) % 3 == 0:
            self.led_level = (self.led_level + 0.23) % 1
            led_fxn(self.led_level)

    def stop_charging(self, log=True):
        charging_was_active = self.BattCharger.is_charging()
        self.BattCharger.disable_charge()
        Controller().turn_off_all_ind_leds()
        if log and charging_was_active:
            self.Output.print_info("Stopped charging.")

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

