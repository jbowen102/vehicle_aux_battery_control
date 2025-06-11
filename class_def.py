import time
# from datetime import datetime, timedelta
# from colorama import Style, Fore, Back
# import multiprocessing as mp

import automationhat as ah


ALTERNATOR_OUTPUT_V_MIN = 13
MAIN_V_MAX = 14.5
AUX_V_MAX = 13.5

# Automation Hat pins
AUX_BATT_V_MONITORING_PIN = 0
MAIN_BATT_V_MONITORING_PIN = 1

KEY_ACC_INPUT_PIN = 0
KEY_ON_INPUT_PIN = 1

CHARGER_ENABLE_RELAY = 0
CHARGER_DIRECTION_RELAY = 1


class Vehicle(object):
    def __init__(self, StarterBattery, AuxBattery, BatteryCharger):
        self.StarterBatt = StarterBattery
        self.AuxBatt = AuxBattery

    def is_acc_powered(self):
        return ah.input[KEY_ACC_INPUT_PIN].read()

    def is_key_on(self):
        return ah.input[KEY_ON_INPUT_PIN].read()

    def is_engine_running(self):
        return self.StarterBatt.get_voltage() >= ALTERNATOR_OUTPUT_V_MIN

    def is_enable_switch_closed(self):
        # If manual switch opened, main system voltage sensing disabled.
        return self.StarterBatt.get_voltage() > 1


class Battery(object):
    def __init__(self, voltage_sensing_pin):
        assert voltage_sensing_pin in (0, 1, 2), "Tried to create battery object with invalid voltage-sensing pin %d" % voltage_sensing_pin
        self.v_pin = voltage_sensing_pin

    def get_voltage(self):
        return ah.analog[self.v_pin].read()


class StarterBatt(Battery):
    pass

class AuxBatt(Battery):
    pass


class BatteryCharger(object):
    def __init__(self):
        pass

    def is_charging(self):
        return ah.relay[CHARGER_ENABLE_RELAY].is_on()

    def _enable_charge(self):
        assert not self.is_charging(), "Tried to enable charging when already charging"
        ah.relay[CHARGER_ENABLE_RELAY].on()
        time.sleep(0.2)
        assert ah.relay[CHARGER_ENABLE_RELAY].is_on()

    def disable_charge(self):
        if self.is_charging():
            ah.relay[CHARGER_ENABLE_RELAY].off()
            time.sleep(2)
            # Allow system voltage to settle
        assert ah.relay[CHARGER_ENABLE_RELAY].is_off()

    def set_charge_direction_fwd(self):
        # Charge main battery with aux battery
        self.disable_charge()
        ah.relay[CHARGER_DIRECTION_RELAY].on()
        time.sleep(0.2)
        assert ah.relay[CHARGER_DIRECTION_RELAY].is_on()

    def set_charge_direction_rev(self):
        # Charge aux battery with alternator
        self.disable_charge()
        ah.relay[CHARGER_DIRECTION_RELAY].off()
        time.sleep(0.2)
        assert ah.relay[CHARGER_DIRECTION_RELAY].is_off()

    def charge_starter_batt(self):
        self.disable_charge()
        self.set_charge_direction_fwd()
        self._enable_charge()

    def charge_aux_batt(self):
        self.disable_charge()
        self.set_charge_direction_rev()
        self._enable_charge()

