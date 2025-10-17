import os
import platform
import pwd
import time
import datetime as dt
import sys
import subprocess
import ntplib
from colorama import Style, Fore, Back

import sqlite3
import pandas as pd
from sqlalchemy import create_engine, text

HOSTNAME = platform.node()
if HOSTNAME.lower().startswith("rpi"):
    # RPi-specific things that aren't needed (or usually installed) when running from laptop
    import automationhat as ah
    from adafruit_pcf8523.pcf8523 import PCF8523
    import board

from network_names import stored_ssid_mapping_dict     # local file
from control_params import ALTERNATOR_OUTPUT_V_MIN, \
                           MAIN_V_MIN, MAIN_V_MAX, \
                           MAIN_V_CHARGED, \
                           AUX_V_MIN, AUX_V_MAX, \
                           MIN_CHARGE_CURRENT_A, \
                           RPI_SHUTDOWN_DELAY_SEC, \
                           STATE_CHANGE_DELAY_SEC, \
                           VOLTAGE_STABILIZATION_TIME_SEC, \
                           NTP_WAIT_TIME_SEC, RTC_LAG_THRESHOLD_SEC


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_DIR = os.path.join(SCRIPT_DIR, "logs")

DATE_FORMAT = "%Y%m%d"
TIME_FORMAT = "%H%M%S"
DATETIME_FORMAT = "%sT%s" % (DATE_FORMAT, TIME_FORMAT)
DATETIME_FORMAT_SQL = "%Y-%m-%d %H:%M:%S"

SHUNT_AMP_VOLTAGE_RATIO = 20/0.075

# Automation Hat pins
CHARGER_INPUT_SHUNT_LOW_PIN = 0   # labeled 1 on board
CHARGER_INPUT_SHUNT_HIGH_PIN = 1  # labeled 2 on board
CHARGER_OUTPUT_PIN = 2            # labeled 3 on board

ENGINE_ON_INPUT_PIN = 0           # labeled 1 on board
KEY_ACC_INPUT_PIN = 1             # labeled 2 on board
ENABLE_SWITCH_DETECT_PIN = 2      # labeled 3 on board

CHARGER_ENABLE_RELAY = 0          # labeled 1 on board
CHARGE_DIRECTION_RELAY = 1        # labeled 2 on board
KEEPALIVE_RELAY = 2               # labeled 3 on board



class ChargeControlError(Exception):
    pass

class SystemVoltageError(Exception):
    pass


class OutputHandler(object):
    def __init__(self):
        self.time_valid = False
        self.Clock = TimeKeeper(self)

        self._create_log_file()
        self._print_startup()

        self.Clock.check_rtc(log=False)
        self.Clock.check_rtc(log=True)
        # Call second time w/ logging after first call establishes what time source to use for output/log.

    def assert_time_valid(self):
        self.time_valid = True

    def _get_datestamp(self, valid_only=True):
        """Returns string.
        """
        if self.Clock is None:
            raise Exception("Tried to use _get_datestamp() but no Clock associated to OutputHandler.")

        if valid_only and (not self.time_valid):
            return "--------"
        else:
            return self.Clock.get_time_now(DATE_FORMAT)

    def _get_timestamp(self):
        if self.Clock is None:
            raise Exception("Tried to use _get_timestamp() but no Clock associated to OutputHandler.")
        if self.time_valid:
            return self.Clock.get_time_now(DATETIME_FORMAT)
        else:
            return self._get_datestamp(valid_only=True) + "-" + self.Clock.get_time_now(TIME_FORMAT)
            # Keep incorrect time displayed because relative differences still useful in log.

    def _create_log_file(self):
        datestamp = self._get_datestamp(valid_only=False)
        # If using sys time and not yet updated via NTP, this will just append to most recent log.
        self.log_filepath = os.path.join(LOG_DIR, "%s.log" % datestamp)
        if not os.path.exists(self.log_filepath):
            # If multiple runs on same day, appends to existing file.
            # If program runs over midnight, after-midnight events will be in previous day's logs.
            with open(self.log_filepath, "w") as fd:
                pass

    def _add_to_log_file(self, print_str):
        self._create_log_file() # Ensures that if date changes while program running,
                                # new log entries are written to next day's log.
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
        username = pwd.getpwuid(os.getuid()).pw_name
        # https://stackoverflow.com/a/2899055
        self._add_to_log_file("")
        self._add_to_log_file("-"*23 + " PROGRAM START [USER: %s, PID: %d] "
                              % (username, os.getpid()) + "-"*20)

    def print_rtc_and_sys_time(self, preface):
        self.print_debug("%s:" % preface)
        self.print_debug("\tRTC time: %s" % self.Clock.get_time_now(string_format=DATETIME_FORMAT, source="rtc"))
        self.print_debug("\tSys time: %s" % self.Clock.get_time_now(string_format=DATETIME_FORMAT, source="sys"))

    def print_network_status(self):
        """Outputs name defined in name-mapping dict (not SSID).
        """
        network_name = self.Clock.get_network_name()
        if network_name is not None:
            self.print_info("Connected to %s." % network_name)
        else:
            self.print_info("No network connection.")

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

    def print_exit(self, error_msg):
        self.print_err(error_msg)
        self.print_debug("[PID %d killed]" % os.getpid())


class TimeKeeper(object):
    def __init__(self, Output):
        self.Output = Output
        self.state_change_delay_time = STATE_CHANGE_DELAY_SEC # default able to be overridden

        self.state_change_timer_start = None
        self.shutdown_timer_start = None
        self.charge_start_time = None

        self.rtc = PCF8523(board.I2C())
        self.rtc_time_valid = True # start w/ assumption that RTC up to date.
        self.sys_time_valid = False

    def check_rtc(self, log=True):
        """Check RTC time plausibility. Will fall behind sys time if coin-cell
        battery dies.
        """
        # if (time_now_sys - time_now_rtc) > dt.timedelta(seconds=lag_threshold):
        rtc_lag = self.get_rtc_lag()
        if rtc_lag > dt.timedelta(seconds=RTC_LAG_THRESHOLD_SEC):
            self.rtc_time_valid = False
            if log:
                self.Output.print_rtc_and_sys_time("Startup time compare")
                self.Output.print_err("RTC time behind sys time %ds, over %ds threshold. "
                                      "Falling back to sys time."
                                       % (rtc_lag, RTC_LAG_THRESHOLD_SEC))
            self.wait_for_ntp_update()
        else:
            self.rtc_time_valid = True
            self.Output.assert_time_valid()
            if log:
                self.Output.print_rtc_and_sys_time("Startup time compare")
                self.Output.print_info("Using RTC time.")

    def get_rtc_lag(self):
        """Returns datetime.timedelta object.
        """
        time_now_sys = self.get_time_now(source="sys")
        time_now_rtc = self.get_time_now(source="rtc")
        # self.Output.print_debug("sys time: %s" % self.get_time_now(string_format=DATETIME_FORMAT, source="sys"))
        # self.Output.print_debug("RTC time: %s" % self.get_time_now(string_format=DATETIME_FORMAT, source="rtc"))
        return (time_now_sys - time_now_rtc)

    def update_rtc(self, wait=False, log=True):
        if wait and not self.is_ntp_syncd(log=False):
            self.wait_for_ntp_update(log=True)

        if self.is_ntp_syncd(log=False):
            if (   (self.get_rtc_lag() >= dt.timedelta(seconds=1))
                or (self.get_rtc_lag() <= dt.timedelta(seconds=-1))):
                # Don't bother updating if <1s difference.
                prev_time = self.get_time_now(source="rtc")
                self.rtc.datetime = time.localtime(dt.datetime.now().timestamp())
                new_time = self.get_time_now(source="rtc")
                if log:
                    self.Output.print_debug("Updated RTC time (%s -> %s) from NTP-syncd sys time."
                                            % (prev_time, new_time))
            elif log:
                self.Output.print_rtc_and_sys_time("No RTC update needed")

        elif log:
            self.Output.print_debug("Not updating RTC time since sys time not syncd with NTP.")
        # Does not set self.rtc_time_valid to True. Will be False if check_rtc() failed.

    def get_time_now(self, string_format=None, source=None):
        """Returns date and time as datetime object or
        if string_format string arg passed, returns string.
        If source is set to "rtc" or "sys", will use that data source
        regardless of status.
        """
        if self.rtc_time_valid or source == "rtc":
            datetime_now = dt.datetime.fromtimestamp(time.mktime(self.rtc.datetime))
        else:
            datetime_now = dt.datetime.now()

        if string_format is not None:
            return datetime_now.strftime(string_format)
        else:
            return datetime_now

    def get_network_name(self, log=False):
        """Uses local file w/ SSID->name dict.
        Returns name of network as string, or None if not connected to any.
        """
        result = subprocess.run(["/usr/sbin/iwgetid", "-r"], capture_output=True, text=True)
        network_ssid = result.stdout.strip()
        # Can take a few seconds for network name to be returned after connection,
        # so might get false negative.
        # https://forums.raspberrypi.com/viewtopic.php?t=340058
        if log:
            self.Output.print_temp("Network SSID returned by iwgetid: %s" % network_ssid)
        return stored_ssid_mapping_dict.get(network_ssid)

    def is_ntp_syncd(self, log=False):
        # ntplib.NTPClient().request("pool.ntp.org", timeout=NTP_WAIT_TIME_SEC)
        result = subprocess.run(["/usr/bin/timedatectl", "show", "--property=NTPSynchronized", "--value"],
                                capture_output=True, text=True)
        updated = (result.stdout.strip() == "yes")
        if log and updated:
            self.Output.print_info("System date/time updated%s."
                                   % (" (connected to %s)"
                                      % (self.get_network_name(log=False)) if self.get_network_name(log=log) else ""))
        elif log:
            self.Output.print_info("System date/time updated%s."
                                   % (" (connected to %s)"
                                      % (self.get_network_name(log=False)) if self.get_network_name(log=log) else ""))
        return updated

    def wait_for_ntp_update(self, log=False):
        # Provide buffer time for OS to update sys time.
        if log:
            self.Output.print_debug("Checking if sys date/time synchronized to NTP server...")

        # Controller().turn_off_all_ind_leds()
        # Controller().light_red_led(0.5)
        # Controller().light_blue_led(0.5)
        start_time = self.get_time_now()
        while not self._has_time_elapsed(start_time, NTP_WAIT_TIME_SEC):
            if self.is_ntp_syncd(log=False):
                self.sys_time_valid = True
                self.Output.assert_time_valid()
                self.is_ntp_syncd(log=True) # Call again just for output
                break
        # Controller().turn_off_all_ind_leds()

        if log and not self.sys_time_valid:
            self.Output.print_warn("System date/time not yet updated since last power loss.")

    def set_charge_start_time(self):
        self.charge_start_time = self.get_time_now()

    def is_sys_voltage_stable(self):
        if self.charge_start_time is None:
            return True
        elif self._has_time_elapsed(self.charge_start_time, VOLTAGE_STABILIZATION_TIME_SEC):
            return True
        else:
            return False

    def get_seconds(self):
        """Returns seconds part of current time as int.
        """
        return int(self.get_time_now().strftime("%-S"))

    def get_minutes(self):
        """Returns minutes part of current time as int.
        """
        return int(self.get_time_now().strftime("%-M"))

    def start_shutdown_timer(self, log=True):
        """If called while timer already running, timer restarts.
        """
        Controller().turn_off_all_ind_leds()
        self.shutdown_timer_start = self.get_time_now()
        if log:
            self.Output.print_debug("RPi shutdown timer (%ds) started at %s."
                                    % (RPI_SHUTDOWN_DELAY_SEC, self.shutdown_timer_start.strftime("%H:%M:%S")))
        time.sleep(1) # Avoid catching multiple state transitions during some transient condition not yet characterized.

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

    def start_charge_delay_timer(self, state_change_desc, delay_s=STATE_CHANGE_DELAY_SEC, log=True):
        """If called while timer already running, timer restarts iff result is extended delay.
        If delay_s parameter specified (int representing seconds), overrides default delay
        unless it would shorten delay time. Longer delay takes precedence.
        """

        if (self.state_change_timer_start is None
              or (    self.get_time_now() + dt.timedelta(seconds=delay_s))
                  >= (self.state_change_timer_start + dt.timedelta(seconds=self.state_change_delay_time))):
            self.state_change_delay_time = delay_s
            Controller().turn_off_all_ind_leds()
            self.state_change_timer_start = self.charge_start_time = self.get_time_now()
            if log:
                self.Output.print_debug("Charge delay of %ds started (%s) at %s."
                                        % (self.state_change_delay_time,
                                           state_change_desc,
                                           self.state_change_timer_start.strftime("%H:%M:%S")))
            time.sleep(1) # Avoid catching multiple state transitions during voltage ripple.
        elif log:
            self.Output.print_debug("New charge delay of %ds ignored (%s) - inside existing %ds delay started at %s."
                                    % (delay_s, state_change_desc,
                                       self.state_change_delay_time,
                                       self.state_change_timer_start.strftime("%H:%M:%S")))

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
            is_time_up = self._has_time_elapsed(self.state_change_timer_start, self.state_change_delay_time)
            if is_time_up:
                self.state_change_timer_start = None
                Controller().turn_off_all_ind_leds()
            elif self.get_seconds() % 2 == 0:
                Controller().toggle_green_led()
                Controller().light_blue_led(brightness=int(Controller().is_green_led_lit()))
            return (is_time_up, is_time_up)

    def _get_time_elapsed(self, start_time):
        return (self.get_time_now() - start_time)

    def _has_time_elapsed(self, start_time, threshold_sec):
        if self._get_time_elapsed(start_time) >= dt.timedelta(seconds=threshold_sec):
            return True
        else:
            return False
        # https://www.tutorialspoint.com/How-can-we-do-date-and-time-math-in-Python


class DataLogger(object):
    def __init__(self):
        self.sys_log_db = "system_data_log.db"
        self.voltage_table = "voltages"
        self.charging_table = "charging"
        self.signals_table = "signals"

        self.sql_engine = self._create_SQLite_engine()
        self._create_voltage_table() # idempotent
        self._create_charging_table() # idempotent
        self._create_signals_table() # idempotent

    def _create_SQLite_engine(self):
        return create_engine("sqlite:///%s" % self.sys_log_db, echo=False)

    def _execute_sql(self, stmt_str, query=False):
        with self.sql_engine.connect() as sql_conn:
            if query:
                return pd.read_sql(text(stmt_str), con=sql_conn, index_col="Timestamp", parse_dates=["Timestamp"])
            else:
                sql_conn.execute(text(stmt_str))
                sql_conn.commit()

    def _create_voltage_table(self, force=False):
        if force:
            sql_stmt = f"""DROP TABLE IF EXISTS {self.voltage_table};
                        """
            self._execute_sql(sql_stmt)
        sql_stmt = f"""CREATE TABLE IF NOT EXISTS {self.voltage_table} (
                           Timestamp TEXT,
                           Vmain FLOAT,
                           Vmain_raw FLOAT,
                           Vaux FLOAT,
                           Vaux_raw FLOAT,
                           PRIMARY KEY (Timestamp)
                       );
                    """
        self._execute_sql(sql_stmt)

    def _create_charging_table(self, force=False):
        if force:
            sql_stmt = f"""DROP TABLE IF EXISTS {self.charging_table};
                        """
            self._execute_sql(sql_stmt)
        sql_stmt = f"""CREATE TABLE IF NOT EXISTS {self.charging_table} (
                            Timestamp TEXT,
                            charge_enable BOOL,
                            charge_dir BOOL,
                            charge_current FLOAT,
                            shunt_V_in FLOAT,
                            shunt_V_out FLOAT,
                            PRIMARY KEY (Timestamp)
                       );
                    """
        self._execute_sql(sql_stmt)

    def _create_signals_table(self, force=False):
        if force:
            sql_stmt = f"""DROP TABLE IF EXISTS {self.signals_table};
                        """
            self._execute_sql(sql_stmt)
        # define schema
        sql_stmt = f"""CREATE TABLE IF NOT EXISTS {self.signals_table} (
                            Timestamp TEXT,
                            enable_sw BOOL,
                            key_ACC BOOL,
                            ecu_W BOOL,
                            engine_on BOOL,
                            network_conn TEXT,
                            HAT_analog_0 FLOAT,
                            HAT_analog_1 FLOAT,
                            HAT_analog_2 FLOAT,
                            HAT_input_0 BOOL,
                            HAT_input_1 BOOL,
                            HAT_input_2 BOOL,
                            HAT_relay_0 BOOL,
                            HAT_relay_1 BOOL,
                            HAT_relay_2 BOOL,
                            PRIMARY KEY (Timestamp)
                       );
                    """
        self._execute_sql(sql_stmt)

    def _log_data(self, table_name, timestamp_now, values_list):
        # Convert True and False to TRUE and FALSE for SQLite to properly interpret as BOOL.
        # Handle network-name string that needs extra quote wrap.
        values_str = ", ".join([str(x).upper() if not isinstance(x, str) else "'%s'" % x for x in values_list]).replace("NONE", "NULL")
        sql_stmt = f"""INSERT INTO {table_name}
                       VALUES ("{timestamp_now.strftime(DATETIME_FORMAT_SQL)}",
                               {values_str}
                              );
                    """
        self._execute_sql(sql_stmt)

    def _get_data(self, table_name, timestamp_now, trailing_seconds):
        cols = "*"

        if trailing_seconds is not None:
            timestamp_trail = timestamp_now - dt.timedelta(seconds=trailing_seconds)
            timestamp_trail_str = timestamp_trail.strftime(DATETIME_FORMAT_SQL)
            time_filter = "WHERE Timestamp >= '%s'" % timestamp_trail_str
        else:
            time_filter = ""

        sql_stmt = f"""SELECT {cols}
                       FROM {table_name}
                       {time_filter};
                    """
        return self._execute_sql(sql_stmt, query=True)

    def log_voltages(self, timestamp_now, values_list):
        self._log_data(self.voltage_table, timestamp_now, values_list)

    def get_voltages(self, timestamp_now, trailing_seconds=None):
        return self._get_data(self.voltage_table, timestamp_now, trailing_seconds)

    def log_signals(self, timestamp_now, values_list):
        self._log_data(self.signals_table, timestamp_now, values_list)

    def get_signals(self, timestamp_now, trailing_seconds=None):
        return self._get_data(self.signals_table, timestamp_now, trailing_seconds)

    def log_charging(self, timestamp_now, values_list):
        self._log_data(self.charging_table, timestamp_now, values_list)

    def get_charging(self, timestamp_now, trailing_seconds=None):
        return self._get_data(self.charging_table, timestamp_now, trailing_seconds)


class Controller(object):
    def __init__(self):
        self.input_list = [0, 1, 2]
        self.relay_list = [0, 1, 2]
        self.analog_list = [0, 1, 2]
        self.ind_led_list = [0, 1, 2]

    def _light_led(self, led_num, brightness):
        ah.light[led_num].write(brightness)

    def light_green_led(self, brightness=1):
        self._light_led(0, brightness=brightness)

    def light_blue_led(self, brightness=1):
        self._light_led(1, brightness=brightness)

    def light_red_led(self, brightness=1):
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
        # Make sure charge-enable relay opened first (e.g., before charge-direction one)
        self.open_relay(CHARGER_ENABLE_RELAY)
        for relay_num in self.relay_list:
            self.open_relay(relay_num)

    def reboot(self, delay_s):
        try:
            self.turn_off_all_ind_leds()
            self.light_red_led(1)
            self.open_all_relays()
        except:
            # If AutomationHAT errored out, skip nice-to-have feature of LED indication.
            pass
        finally:
            time.sleep(delay_s) # give time for user to connect over SSH and stop boot loop.
            subprocess.run(["/usr/bin/sudo", "/usr/sbin/reboot", "-h", "now"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            # The below line won't normally run, but in case there's a problem w/
            # the subprocess call, this at least makes sure the program exits.
            sys.exit(250) # https://medium.com/@himanshurahangdale153/list-of-exit-status-codes-in-linux-f4c00c46c9e0

    def shut_down(self, delay_s):
        try:
            self.turn_off_all_ind_leds()
            self.light_red_led(1)
            self.open_all_relays()
        except:
            # If AutomationHAT errored out, skip nice-to-have feature of LED indication.
            pass
        finally:
            time.sleep(delay_s) # give time for user to connect over SSH and stop boot loop.
            subprocess.run(["/usr/bin/sudo", "/usr/sbin/shutdown", "-h", "now"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            # https://learn.sparkfun.com/tutorials/raspberry-pi-safe-reboot-and-shutdown-button/all
            # The below line won't normally run, but in case there's a problem w/
            # the subprocess call, this at least makes sure the program exits.
            sys.exit(250)

    def sigterm_handler(self, _signo, _stack_frame):
        """Pipe kill signal from Linux to this Python script to allow graceful exit.
        """
        self.turn_off_all_ind_leds()
        self.open_all_relays()
        sys.exit(0)
        # https://stackoverflow.com/questions/18499497/how-to-process-sigterm-signal-gracefully


class Vehicle(object):
    def __init__(self, Output, Timer):
        self.Output = Output
        self.Timer = Timer
        self.DataLogger = DataLogger()
        self.BattCharger = BatteryCharger(self.Output, self.Timer)

        self.key_acc_detect_pin = KEY_ACC_INPUT_PIN
        self.engine_on_detect_pin = ENGINE_ON_INPUT_PIN

        self.enable_sw_detect_pin = ENABLE_SWITCH_DETECT_PIN
        self.keepalive_relay_num = KEEPALIVE_RELAY

        self.led_level = 0

        Controller().open_all_relays()
        time.sleep(1)                # Give time for automationhat inputs to stabilize.
        self.check_wiring()
        Controller().close_relay(self.keepalive_relay_num) # Keep on whenever device is on.

    def log_data(self):
        self.DataLogger.log_voltages(self.Timer.get_time_now(),
                                     self.get_main_voltage(log=False),
                                     self.get_main_voltage_raw(log=False),
                                     self.get_aux_voltage(log=False),
                                     self.get_aux_voltage_raw(log=False)
                                    )
        self.DataLogger.log_charging(self.Timer.get_time_now(),
                                     self.BattCharger.is_charging(),
                                     self.BattCharger.is_charge_direction_fwd(),
                                     self.BattCharger.get_charge_current(),
                                     Controller().read_voltage(CHARGER_INPUT_SHUNT_HIGH_PIN),
                                     Controller().read_voltage(CHARGER_INPUT_SHUNT_LOW_PIN)
                                    )
        self.DataLogger.log_signals(self.Timer.get_time_now(),
                                    self.is_enable_switch_closed(log=False),
                                    self.is_acc_powered(),
                                    Controller().is_input_high(self.engine_on_detect_pin),
                                    self.is_engine_running(log=False),
                                    self.Timer.get_network_name(log=False),
                                    Controller().read_voltage(0),
                                    Controller().read_voltage(1),
                                    Controller().read_voltage(2),
                                    Controller().is_input_high(0),
                                    Controller().is_input_high(1),
                                    Controller().is_input_high(2),
                                    Controller().is_relay_on(0),
                                    Controller().is_relay_on(1),
                                    Controller().is_relay_on(2)
                                   )

    def check_wiring(self):
        if self.get_main_voltage_raw() < 5:
            # No FLA voltage detected
            output_str = "No main voltage detected (reading %.2fV)." % self.get_main_voltage_raw(log=False)
            self.Output.print_err(output_str)
            raise SystemVoltageError(output_str)
        if self.get_aux_voltage_raw() < 5:
            # No Li voltage detected
            output_str = "No aux voltage detected (reading %.2fV)." % self.get_aux_voltage_raw(log=False)
            self.Output.print_err(output_str)
            raise SystemVoltageError(output_str)

        # TODO - FIX
        # if self.BattCharger.is_charging() and self.BattCharger.get_charge_current() < MIN_CHARGE_CURRENT_A:
        #     output_str = "No charge current detected despite charging (reading %.2fA)." % self.BattCharger.get_charge_current()
        #     self.Output.print_err(output_str)
        #     raise ChargeControlError(output_str)
        if self.is_key_off() and self.is_engine_running():
            output_str = "Inferred engine running but key OFF."
            self.Output.print_err(output_str)
            raise SystemVoltageError(output_str)

    def is_acc_powered(self):
        # Returns True when key in either ACC or ON position
        return Controller().is_input_high(self.key_acc_detect_pin)

    def is_key_off(self):
        return not self.is_acc_powered()

    def is_engine_running(self, log=False):
        if not self.is_acc_powered():
            return False
        else:
            # W signal not consistent enough.
            ecu_w_signal_high = Controller().is_input_high(self.engine_on_detect_pin)
            main_voltage_elevated = (self.get_main_voltage_raw(log=log) >= ALTERNATOR_OUTPUT_V_MIN)
            dc_charger_elevating = (self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_fwd())
            if log:
                self.Output.print_debug("ECU W signal %s" % ("HIGH" if ecu_w_signal_high else "LOW"))
            return (ecu_w_signal_high or (main_voltage_elevated and not dc_charger_elevating))

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
        if self.BattCharger.is_charge_direction_fwd():
            voltage = Controller().read_voltage(CHARGER_OUTPUT_PIN)
        else:
            voltage = Controller().read_voltage(CHARGER_INPUT_SHUNT_HIGH_PIN)
        if log:
            self.Output.print_debug("Main voltage (raw): %.2fV" % voltage)
        return voltage

    def get_aux_voltage_raw(self, log=False):
        if self.BattCharger.is_charge_direction_rev():
            voltage = Controller().read_voltage(CHARGER_OUTPUT_PIN)
        else:
            voltage = Controller().read_voltage(CHARGER_INPUT_SHUNT_HIGH_PIN)
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
                self.Output.print_warn("Starter battery being charged (by aux batt) "
                                       "during FLA battery-voltage reading (elevating value).")
        elif self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_rev():
            # Currently charging aux battery, depressing main voltage.
            # This should only happen while engine running, but if engine shut off after above condition eval'd, this block may run.
            # But in that case, probably not elevated.
            if log:
                self.Output.print_warn("Charging aux battery during FLA battery-voltage "
                                       "reading (engine should have just stopped).")

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
                self.Output.print_warn("Aux batt charging starter battery during "
                                       "Li battery-voltage reading (depressing value).")
        elif self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_rev():
            # Currently being charged, elevating aux-batt voltage.
            if log:
                self.Output.print_warn("Aux batt being charged during Li "
                                       "battery-voltage reading (elevating value).")

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
        if not self.Timer.is_sys_voltage_stable():
            return False
        est_voltage = self.get_main_voltage(log=log)
        is_low = est_voltage < MAIN_V_MIN
        if log and is_low:
            self.Output.print_warn("Starter-batt voltage %.2fV below min allowed %.2fV." % (est_voltage, MAIN_V_MIN))
        return is_low

    def is_starter_batt_charged(self, log=False):
        if not self.Timer.is_sys_voltage_stable():
            return True
        else:
            return (self.get_main_voltage(log=log) >= MAIN_V_CHARGED)

    def does_starter_batt_need_charge(self, log=False):
        return not self.is_starter_batt_charged(log=log)

    def is_aux_batt_empty(self, threshold_override=None, log=True):
        if not self.Timer.is_sys_voltage_stable():
            return False

        if threshold_override is not None:
            threshold = threshold_override
        else:
            threshold = AUX_V_MIN

        est_voltage = self.get_aux_voltage(log=log)
        is_low = est_voltage < threshold
        if log and is_low:
            self.Output.print_warn("Aux-batt voltage %.2fV below min allowed %.2fV."
                                   % (est_voltage, threshold))
        elif log:
            self.Output.print_debug("Aux-batt voltage %.2fV sufficient (min allowed: %.2fV)."
                                    % (est_voltage, threshold))
        return is_low

    def is_aux_batt_sufficient(self, threshold_override=None, log=False):
        return not self.is_aux_batt_empty(threshold_override=threshold_override, log=log)

    def is_aux_batt_full(self, log=False):
        if not self.Timer.is_sys_voltage_stable():
            return False
        est_voltage = self.get_aux_voltage(log=log)
        is_full = est_voltage >= AUX_V_MAX
        if log and is_full:
            self.Output.print_debug("Aux batt full (%.2fV)" % est_voltage)
        return is_full

    def charge_starter_batt(self, log=True, post_delay=False):
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
        if post_delay:
            time.sleep(VOLTAGE_STABILIZATION_TIME_SEC)

    def charge_aux_batt(self, log=False, post_delay=False):
        if not self.is_starter_batt_charged():
            # Only want to charge with engine running. Sometimes engine stops after
            # event loop already called this method (when engine was running), so
            # instead just make sure FLA batt sufficient.
            main_voltage = self.get_main_voltage(log=True)
            output_str = "Called Vehicle.charge_aux_batt(), but engine not running."
            self.Output.print_err(output_str)
            raise ChargeControlError(output_str)
        if self.is_aux_batt_full(log=False):
            output_str = "Called Vehicle.charge_aux_batt(), " \
                         "and aux batt already full (%.2fV > %.2fV max." \
                         % (self.get_aux_voltage(log=False), AUX_V_MAX)
            self.Output.print_warn(output_str)

        self.BattCharger.set_charge_direction_rev()
        self.BattCharger.enable_charge()
        # self.roll_indicator_light(Controller().light_green_led)
        Controller().toggle_green_led()

        if log:
            self.Output.print_info("Charging auxiliary battery.")
        if post_delay:
            time.sleep(VOLTAGE_STABILIZATION_TIME_SEC)

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

    def shut_down_controller(self, delay=5):
        self.Timer.update_rtc(wait=False, log=True) # Use system time to update RTC if sync'd w/ NTP.
        Controller().turn_off_all_ind_leds()
        self.Output.print_warn("Shutting down controller in %d seconds." % delay)
        Controller().shut_down(delay_s=delay)

    def output_status(self):
        self.Output.print_info("Status:")
        key_acc_powered = self.is_acc_powered()
        engine_on_state = self.is_engine_running()
        charging_fla = (self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_fwd())
        charging_li = (self.BattCharger.is_charging() and self.BattCharger.is_charge_direction_rev())
        # charge_current = self.BattCharger.get_charge_current() if self.BattCharger.is_charging() else None
        ecu_w_signal_high = Controller().is_input_high(self.engine_on_detect_pin)

        self.Output.print_info("\tKey %s." % ("@ ACC/ON" if key_acc_powered else "OFF"))
        self.Output.print_info("\tEngine %s (W signal %s)."
                               % (("ON" if engine_on_state else "OFF"), ("HIGH" if ecu_w_signal_high else "LOW")))
        self.Output.print_info("\tMain (raw): %.2f" % self.get_main_voltage_raw())
        self.Output.print_info("\tAux  (raw): %.2f" % self.get_aux_voltage_raw())
        # self.Output.print_info("\t%s" % (      ("Charging -> FLA (%.2fA)." % charge_current) if charging_fla
        #                                  else (("Charging -> Li (%.2fA)." % charge_current) if charging_li
        self.Output.print_info("\t%s" % (      ("Charging -> FLA.") if charging_fla
                                         else (("Charging -> Li.") if charging_li
                                         else  "Not charging.")))
        self.Output.print_temp("\tShunt high-side voltage: %.2f"
                               % Controller().read_voltage(CHARGER_INPUT_SHUNT_HIGH_PIN))
        self.Output.print_temp("\tShunt low-side voltage:  %.2f"
                               % Controller().read_voltage(CHARGER_INPUT_SHUNT_LOW_PIN))
        self.Output.print_network_status()
        self.Output.print_rtc_and_sys_time("Time compare (periodic)")


class BatteryCharger(object):
    def __init__(self, Output, Timer):
        self.Output = Output
        self.Timer = Timer
        self.charger_enable_relay = CHARGER_ENABLE_RELAY
        self.charge_direction_relay = CHARGE_DIRECTION_RELAY

    def is_charging(self):
        return Controller().is_relay_on(self.charger_enable_relay)

    def enable_charge(self):
        if not self.is_charging():
            Controller().close_relay(self.charger_enable_relay)
            time.sleep(0.5)
            self.Timer.set_charge_start_time()
        if not self.is_charging():
            self.Output.print_err("BatteryCharger.enable_charge() failed to start charging.")
            raise ChargeControlError("BatteryCharger.enable_charge() failed to start charging.")

    def disable_charge(self):
        if self.is_charging():
            Controller().open_relay(self.charger_enable_relay)
            # Allow system voltage to settle
            time.sleep(0.5)
            self.Timer.set_charge_start_time()
            # Also release charge-direction relay to avoid wasting energy through its coil.
            Controller().open_relay(self.charge_direction_relay)
            time.sleep(0.2)

        if self.is_charging():
            self.Output.print_err("BatteryCharger.disable_charge() failed to stop charging.")
            raise ChargeControlError("BatteryCharger.disable_charge() failed to stop charging.")
        if not Controller().is_relay_off(self.charge_direction_relay):
            self.Output.print_err("BatteryCharger.disable_charge() failed to open charge-direction relay.")
            raise ChargeControlError("BatteryCharger.disable_charge() failed to open charge-direction relay.")

    def get_charge_current(self):
        if not self.is_charging():
            raise ChargeControlError("BatteryCharger.get_charge_current() called when not charging.")
        voltage_diff = (  Controller().read_voltage(CHARGER_INPUT_SHUNT_HIGH_PIN)
                        - Controller().read_voltage(CHARGER_INPUT_SHUNT_LOW_PIN) )
        return voltage_diff * SHUNT_AMP_VOLTAGE_RATIO

    def is_charge_direction_fwd(self):
        return Controller().is_relay_off(self.charge_direction_relay)

    def is_charge_direction_rev(self):
        return Controller().is_relay_on(self.charge_direction_relay)

    def set_charge_direction_fwd(self):
        # Charge starter battery with aux battery
        if self.is_charge_direction_rev():
            self.disable_charge()
            Controller().open_relay(self.charge_direction_relay)
            time.sleep(0.5)
        if not self.is_charge_direction_fwd():
            self.Output.print_err("BatteryCharger.set_charge_direction_fwd() failed to set direction.")
            raise ChargeControlError("BatteryCharger.set_charge_direction_fwd() failed to set direction.")

    def set_charge_direction_rev(self):
        # Charge aux battery with alternator
        if self.is_charge_direction_fwd():
            self.disable_charge()
            Controller().close_relay(self.charge_direction_relay)
            time.sleep(0.5)
        if not self.is_charge_direction_rev():
            self.Output.print_err("BatteryCharger.set_charge_direction_rev() failed to set direction.")
            raise ChargeControlError("BatteryCharger.set_charge_direction_rev() failed to set direction.")

