import autoterm_heater
import time
import logging


heater_log_path = '/home/pi/AutotermHeaterController/appdata/logs/PlanarHeater.log'


heater = autoterm_heater.AutotermPassthrough(serial_num = 'A50285BI', log_path = heater_log_path, log_level = logging.INFO)

print('Connection with heater succesfully initialized.')
    
while True:
    request = input('Enter your request: ')

    if   request == 'ast':
        heater.asks_for_status()
    elif request == 'ase':
        heater.asks_for_settings()
    elif 'rpt' in request:
        heater.report_panel_temperature(int(request.split(' ')[1]))
    elif request == 'pt':
        print(heater.get_controller_temperature())
    elif request == 'ht':
        print(heater.get_heater_temperature())
    elif request == 'et':
        print(heater.get_external_temperature())
    elif request == 'bv':
        print(heater.get_battery_voltage())
    elif request == 'hs':
        print(heater.get_heater_status())
    elif request == 'hst':
        print(heater.get_heater_status_text())
    elif request == 'dr':
        print(heater.get_defined_rev())
    elif request == 'mr':
        print(heater.get_measured_rev())

    elif request == 'hmd':
        print(heater.get_heater_mode())
    elif request == 'hsp':
        print(heater.get_heater_setpoint())
    elif request == 'hvt':
        print(heater.get_heater_ventilation())
    elif request == 'hpl':
        print(heater.get_heater_power_level())

    elif 'vent_on' in request:
        heater.turn_on_ventilation(int(request[-1]))
    elif 'heat_on' in request:
        heater.turn_on_heater(mode = 4, power = int(request.split(' ')[1]))
    elif 'heat_set' in request:
        heater.change_settings(mode = 4, power = int(request.split(' ')[1]))
    elif 'off' in request:
        heater.shutdown()
    else:
        print('Unknown request!')
