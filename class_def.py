import time
import subprocess
import datetime as dt
# from colorama import Style, Fore, Back
# import multiprocessing as mp

import automationhat as ah


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


class TimeKeeper(object):
    def __init__(self):
        self.state_change_timer_start = None
        self.shutdown_timer_start = None

    def start_charge_delay_timer(self):
        """If called while timer already running, timer restarts.
        """
        self.state_change_timer_start = dt.datetime.now()

    def has_charge_delay_time_elapsed(self):
        """Evaluates if state-delay buffer time has elapsed since last state change.
        Returns True or False.
        """
        if self.state_change_timer_start is None:
            return True
        else:
            return self._has_time_elapsed(self.state_change_timer_start, STATE_CHANGE_DELAY_SEC)

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
        # TODO set up to run automatically w/ superuser priveleges


class Vehicle(object):
    def __init__(self):
        self.StarterBatt = StarterBattery(MAIN_BATT_V_MONITORING_PIN)
        self.AuxBatt = AuxBattery(AUX_BATT_V_MONITORING_PIN)
        self.BattCharger = BatteryCharger()

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

    def is_enable_switch_closed(self):
        # Either ACC power present or keepalive relay should always be powering switch.
        # If key in OFF position, only way to get signal here is with keepalive relay enabled.
        enable_detect = Controller().is_input_high(self.enable_sw_detect_pin)
        if (not enable_detect) and self.is_key_off():
            # If key off and enable not detected, could be because keepalive relay off too.
            # In this state, can't tell if enable switch on or off.
            # Indeterminate reading
            Controller().close_relay(self.keepalive_relay_num) # Should have been on already, but if not, turn on.
            time.sleep(0.2) # Give time for propagation delay
            return self.is_enable_switch_closed()

        if enable_detect:
            return True
        else:
            return False

    def _get_main_voltage(self):
        return self.StarterBatt.get_voltage()

    def _get_aux_voltage(self):
        return self.AuxBatt.get_voltage()

    def get_main_oc_voltage_est(self):
        # Needs hysteresis to avoid bang-bang ctrl
        if self.is_engine_running():
            return 13
            # Too hard to estimate
        elif self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_rev():
            # Currently charging aux battery
            offset = 0.5
            # Offset depends on charge current, but no way to infer that currently.
            # TODO experimentally determine more accurate value.
            # Should this be reworked to automatically shut down charging, wait and then measure?
        elif self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_fwd():
            # Currently being charged, elevating voltage
            offset = -0.5
            # Offset depends on charge current, but no way to infer that currently.
            # TODO experimentally determine more accurate value.
            # Should this be reworked to automatically shut down charging, wait and then measure?
        else:
            offset = 0
        return (self._get_aux_voltage() + offset)

    def get_aux_oc_voltage_est(self):
        # Needs hysteresis to avoid bang-bang ctrl
        if self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_fwd():
            # Currently charging starter battery
            offset = 0.5
            # TODO experimentally determine more accurate value.
            # Offset depends on charge current, but no way to infer that currently.
        elif self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_rev():
            # Currently being charged, elevating voltage
            offset = -0.5
            # TODO experimentally determine more accurate value.
            # Offset depends on charge current, but no way to infer that currently.
        else:
            offset = 0
        return (self._get_aux_voltage() + offset)

    def is_starter_batt_low(self):
        return (self.get_main_oc_voltage_est() <= MAIN_V_MIN)

    def is_starter_batt_charged(self):
        return (self.get_aux_oc_voltage_est() >= MAIN_V_CHARGED)

    def does_starter_batt_need_charge(self):
        return not self.is_starter_batt_charged()

    def is_aux_batt_empty(self):
        return (self.get_aux_oc_voltage_est() <= AUX_V_MIN)

    def is_aux_batt_sufficient(self):
        return not self.is_aux_batt_empty()

    def is_aux_batt_full(self):
        return (self.get_aux_oc_voltage_est() >= AUX_V_MAX)

    def charge_starter_batt(self):
        assert not self.is_aux_batt_empty(), "Called Vehicle.charge_starter_batt(), " \
                                             "but aux batt V (%.2fV) is below min threshold %.2f." \
                                             % (self.get_aux_oc_voltage_est(), AUX_V_MIN)
        assert self.get_main_oc_voltage_est() < MAIN_V_MAX, "Called Vehicle.charge_starter_batt(), " \
                                            "but starter batt V (%.2fV) is over max threshold %.2f." \
                                            % (self.get_main_oc_voltage_est(), MAIN_V_MAX)
        self.BattCharger.set_charge_direction_fwd()
        self.BattCharger.enable_charge()

    def charge_aux_batt(self):
        assert self.is_engine_running(), "Called Vehicle.charge_aux_batt(), but engine not running."
        assert not self.is_aux_batt_full(), "Called Vehicle.charge_aux_batt(), " \
                                            "but batt V (%.2fV) is over max threshold %.2f." \
                                            % (self.get_aux_oc_voltage_est(), AUX_V_MAX)
        self.BattCharger.set_charge_direction_rev()
        self.BattCharger.enable_charge()

    def stop_charging(self):
        self.BattCharger.disable_charge()

    def shut_down_controller(self):
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
    def __init__(self):
        self.charger_enable_relay = CHARGER_ENABLE_RELAY
        self.charge_direction_relay = CHARGE_DIRECTION_RELAY

    def is_charging(self):
        return Controller().is_relay_on(self.charger_enable_relay)

    def enable_charge(self):
        if not self.is_charging():
            Controller().close_relay(self.charger_enable_relay)
            time.sleep(0.5)
        assert self.is_charging(), "BatteryCharger.enable_charge() failed to start charging."

    def disable_charge(self):
        if self.is_charging():
            Controller().open_relay(self.charger_enable_relay)
            # Allow system voltage to settle
            time.sleep(0.5)
            # Also release charge-direction relay to avoid wasting energy through its coil.
            Controller().open_relay(self.charge_direction_relay)
        assert not self.is_charging(), "BatteryCharger.disable_charge() failed to stop charging."
        assert Controller().is_relay_off(self.charge_direction_relay), "BatteryCharger.disable_charge() failed to open charge-direction relay."

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
        assert self.is_charge_direction_fwd(), "BatteryCharger.set_charge_direction_fwd() failed to set direction."

    def set_charge_direction_rev(self):
        # Charge aux battery with alternator
        if self.is_charge_direction_fwd():
            self.disable_charge()
            Controller().open_relay(self.charge_direction_relay)
            time.sleep(0.5)
        assert self.is_charge_direction_rev(), "BatteryCharger.set_charge_direction_rev() failed to set direction."

