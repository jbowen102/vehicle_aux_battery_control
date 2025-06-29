import time
import subprocess
# from datetime import datetime, timedelta
# from colorama import Style, Fore, Back
# import multiprocessing as mp

import automationhat as ah


ALTERNATOR_OUTPUT_V_MIN = 13
MAIN_V_MAX = 14.5
AUX_V_MAX = 13.5

# Automation Hat pins
AUX_BATT_V_MONITORING_PIN = 0   # labeled 1 on board
MAIN_BATT_V_MONITORING_PIN = 1  # labeled 2 on board

KEY_ACC_INPUT_PIN = 0           # labeled 1 on board
KEY_ON_INPUT_PIN = 1            # labeled 2 on board
ENABLE_SWITCH_DETECT_PIN = 2    # labeled 3 on board

CHARGER_ENABLE_RELAY = 0        # labeled 1 on board
CHARGE_DIRECTION_RELAY = 1     # labeled 2 on board
KEEPALIVE_RELAY = 2             # labeled 3 on board


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
        for relay in self.relay_list:
            self.open_relay(n)
        subprocess.run(["sudo", "shutdown", "-P", "--now"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
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

        Controller().close_relay(KEEPALIVE_RELAY) # Keep on whenever device is on.

    def is_acc_powered(self):
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

    def get_main_voltage(self):
        return self.StarterBatt.get_voltage()

    def get_aux_voltage(self):
        return self.AuxBatt.get_voltage()

    def charge_starter_batt(self):
        self.BattCharger.set_charge_direction_fwd()
        self.BattCharger.enable_charge()

    def charge_aux_batt(self):
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
        # Charge main battery with aux battery
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

