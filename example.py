import autoterm_heater
import time


heater_log_path = '/home/pi/pythonFiles/appdata/logs/PlanarHeater.log'
heater = autoterm_heater.AutotermPassthrough('/dev/ttyUSB0',2400,'/dev/ttyUSB1',2400,heater_log_path)

while True:
    request = input('Enter your request: ')

    if request == 'pt':
        print(plr.get_panel_temperature())
    elif request == 'ht':
        print(plr.get_heater_temperature())
    elif request == 'et':
        print(plr.get_external_temperature())
    elif request == 'bv':
        print(plr.get_battery_voltage())
    elif request == 'hs':
        print(plr.get_heater_status())
    elif request == 'dr':
        print(plr.get_defined_rev())
    elif request == 'mr':
        print(plr.get_measured_rev())

    elif 'vent_on' in request:
        plr.turn_on_ventilation(int(request[-1]))
    elif 'off' in request:
        plr.shut_down()
