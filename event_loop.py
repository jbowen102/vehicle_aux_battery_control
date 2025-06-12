import class_def


Car = class_def.Vehicle(class_def.StarterBatt(), class_def.AuxBatt(), class_def.BatteryCharger())

key_acc_state =     Car.is_acc_powered()
key_on_state =      Car.is_key_on()
engine_on_state =   Car.is_engine_running()
sys_enabled_state = Car.is_enable_switch_closed()

while True:
    # Check for state changes
    if Car.is_acc_powered() and not key_acc_state:
        # Key switched from OFF to ACC
        key_acc_state = True

        # Wait and turn on FLA charging after some delay
        Car.charge_starter_batt()
        continue

    if not Car.is_acc_powered() and key_acc_state:
        # Key switched from ACC to OFF
        key_acc_state = False
        Car.keep_controller_on()

        continue

    if Car.is_key_on() and not key_on_state:
        # Key switched from ACC to ON
        key_on_state = True

        continue

    if not Car.is_key_on() and key_on_state:
        # Key switched from ON to ACC
        key_on_state = False
        Car.stop_charging()

        continue

    if Car.is_engine_running() and not engine_on_state:
        # Engine started
        engine_on_state = True

        # Wait and turn on Li charging after some delay.
        Car.charge_aux_batt()
        continue

    if not Car.is_engine_running() and engine_on_state:
        # Engine stopped
        engine_on_state = False

        Car.stop_charging()
        continue

    if Car.is_enable_switch_closed() and not sys_enabled_state:
        # Enable switch closed (during previous timeout)
        sys_enabled_state = True

        continue

    if not Car.is_enable_switch_closed() and sys_enabled_state:
        sys_enabled_state = False

        # Start system shutdown timer.
        # Allow canceling for initial amount of time.
        continue