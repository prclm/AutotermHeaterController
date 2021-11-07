import autoterm_heater
import time


heater_log_path = '/home/pi/AutotermHeaterController/appdata/logs/AutotermHeater.log'
heater = autoterm_heater.AutotermPassthrough('/dev/ttyUSB0',2400,'/dev/ttyUSB1',2400,heater_log_path)

while True:
    request = input('Enter your request: ')

    if request == 'pt':
        print(heater.get_controller_temperature())
    elif request == 'ht':
        print(heater.get_heater_temperature())
    elif request == 'et':
        print(heater.get_external_temperature())
    elif request == 'bv':
        print(heater.get_battery_voltage())
    elif request == 'hs':
        print(heater.get_heater_status())
    elif request == 'dr':
        print(heater.get_defined_rev())
    elif request == 'mr':
        print(heater.get_measured_rev())

    elif 'vent_on' in request:
        heater.turn_on_ventilation(int(request[-1]))
    elif 'off' in request:
        heater.shutdown()
