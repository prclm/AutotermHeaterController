#!/usr/bin/python3
# Filename: autoterm_heater.py

import logging
import serial
import serial.tools.list_ports as list_ports
import threading
import time

################
versionMajor = 0
versionMinor = 0
versionPatch = 6
################

status_text = {0:'heater off', 1:'starting', 2: 'warming up', 3:'running', 4:'shuting down'}

class Message:
    def __init__(self, preamble, device, length, msg_id1, msg_id2, payload = b''):
        self.preamble = preamble
        self.device = device
        self.length = length
        self.msg_id1 = msg_id1
        self.msg_id2 = msg_id2
        self.payload = payload

class AutotermUtils:
    def crc16(self, package : bytes):
        crc = 0xffff
        for byte in package:
            crc ^= byte
            for i in range(8):
                if (crc & 0x0001) != 0:
                    crc >>= 1
                    crc ^= 0xa001
                else:
                    crc >>= 1;
        return crc.to_bytes(2, byteorder='big')

    def parse(self, package : bytes, minPacketSize = 7):
        if len(package) < minPacketSize:
            self.logger.error('Parse: invalid lenght of package! ({})'.format(package.hex()))
            return 0
        while package[0] != 0xaa:
            if len(package) < minPacketSize:
                self.logger.error('Parse: invalid package! ({})'.format(package.hex()))
                return 0
            package = package[1:]
        if package[0] != 0xaa:
            self.logger.error('Parse: invalid bit 0 of package! ({})'.format(package.hex()))
            return 0
        if len(package) != int(package[2]) + minPacketSize:
            self.logger.error('Parse: invalid lenght of package! ({})'.format(package.hex()))
            return 0
        if package[1] not in [0x00, 0x02, 0x03, 0x04]:
            self.logger.error('Parse: invalid bit 1 of package! ({})'.format(package.hex()))
            return 0
        if package[-2:] != self.crc16(package[:-2]):
            self.logger.error('Parse: invalid crc of package! ({})'.format(package.hex()))
            return 0
        
        return Message(package[0], package[1], package[2], package[3], package[4], package[5:-2])

    def build(self, device, msg_id2, msg_id1=0x00, payload = b''):
        if device not in [0x00, 0x02, 0x03, 0x04]:
            self.logger.error('Built: invalid device! ({})'.format(device))
            return 0
        if msg_id1 not in range(256):
            self.logger.error('Built: invalid id1! ({})'.format(msg_id1))
            return 0
        if msg_id2 not in range(256):
            self.logger.error('Built: invalid id2! ({})'.format(msg_id1))
            return 0

        package = b'\xaa'+device.to_bytes(1, byteorder='big')+len(payload).to_bytes(1, byteorder='big')+msg_id1.to_bytes(1, byteorder='big')+msg_id2.to_bytes(1, byteorder='big')+payload
        
        return package + self.crc16(package)


class AutotermPassthrough(AutotermUtils):
    def __init__(self, log_path, serial_port1 = None, baudrate1 = 2400, serial_port2 = None, baudrate2 = 2400, serial_num = None, log_level = logging.DEBUG):
        self.port1 = serial_port1
        self.baudrate1 = baudrate1
        self.port2 = serial_port2
        self.baudrate2 = baudrate2
        self.serial_num = serial_num

        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)
        handler = logging.FileHandler(log_path)
        formatter = logging.Formatter(fmt = '%(asctime)s  %(name)s %(levelname)s: %(message)s', datefmt='%d.%m.%Y %H:%M:%S')
        handler.setFormatter(formatter)
        handler.setLevel(logging.DEBUG)
        self.logger.addHandler(handler)

        self.logger.info('AutotermPassthrough v {}.{}.{} is starting.'.format(versionMajor, versionMinor, versionPatch))

        self.__connected = False
        self.__connect()

        self.__working = False
        self.__start_working()

    def __write_message(self, ser_port, message):
        try:
            if ser_port.write(message) != len(message):
                self.logger.critical('Cannot send whole message to serial port {}!'.format(ser_port.port))
        except serial.serialutil.SerialException:
            self.__connected = False
            self.logger.critical('Cannot write to serial port {}!'.format(ser_port.port))

    def __message_waiting(self, ser_port):
        try:
            return ser_port.in_waiting
        except OSError:
            self.__connected = False
            self.logger.critical('Cannot check serial port {} for incomming messages!'.format(ser_port.port))
            return 0
        self.__ser2.close()
        
    def __connect(self):
        while not self.__connected:
            if self.serial_num:
                # Search for USB devices based on serial number
                ports = [port.device for port in list_ports.comports() if port.serial_number == self.serial_num]
                if len(ports) == 2:
                    self.port1 = ports[0]
                    self.port2 = ports[1]
            if not self.port1 or not self.port2:
                # Raise error!
                pass
                    
            try:
                self.__ser1 = serial.Serial(self.port1, self.baudrate1, bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=0.5, write_timeout=0.5)
                self.__ser2 = serial.Serial(self.port2, self.baudrate2, bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=0.5, write_timeout=0.5)

                self.__ser1.reset_input_buffer()
                self.__ser2.reset_input_buffer()

                self.__write_lock_timer = time.time()
                self.__write_lock_delay = 10

                self.__connected = True

                self.__ser_heater = None
                self.__ser_controller = None

                self.logger.info('Serial connection to '+self.port1+' established')
                self.logger.info('Serial connection to '+self.port2+' established')

            except serial.serialutil.SerialException:
                self.logger.critical('Cannot connect to serial port!')
                time.sleep(10)

    def __disconnect(self):
        self.__ser1.close()
        self.__ser2.close()

    def __reconnect(self):
        self.__disconnect()
        self.__connect()

    def __start_working(self):
        self.__working = True

        # Buffer for message 
        self.__send_to_heater = []

        self.__heater_timer = None
        self.__shutdown_request = False
        self.__shutdown_timer = time.time()
        self.__shutdown_delay = 10              # Sets how often raspberry sends messages to turn heater off

        # Heater info value
        # Following values are stored in tuples with timestamp
        self.__heater_software_version = (None, None, None, None, None)

        # Heater settings values
        self.__settings_timer = time.time()
        self.__settings_delay = 5               # Sets how often raspberry asks for settings        
        # Following values are stored in tuples with timestamp
        self.__heater_mode = (None, None)
        self.__heater_setpoint = (None, None)
        self.__heater_ventilation = (None, None)
        self.__heater_power_level = (None, None)

        # Heater status values
        self.__status_timer = time.time()
        self.__status_delay = 5                 # Sets how often raspberry asks for status
        # Following values are stored in tuples with timestamp
        self.__heater_status1 = (None, None)
        self.__heater_status2 = (None, None)
        self.__heater_errors = (None, None)
        self.__heater_temperature = (None, None)
        self.__external_temperature = (None, None)
        self.__battery_voltage = (None, None)
        self.__flame_temperature = (None, None)

        # Controller temperature value
        # Following values are stored in tuples with timestamp
        self.__controller_temperature = (None, None)

        # Diagnostic values
        # Following values are stored in tuples with timestamp
        self.__d_status1 = (None, None)
        self.__d_status2 = (None, None)
        self.__d_counter1 = (None, None)
        self.__d_counter2 = (None, None)
        self.__d_defined_rev = (None, None)
        self.__d_measured_rev = (None, None)
        self.__d_fuel_pump1 = (None, None)
        self.__d_fuel_pump2 = (None, None)
        self.__d_chamber_temperature = (None, None)
        self.__d_flame_temperature = (None, None)
        self.__d_external_temperature = (None, None)
        self.__d_heater_temperature = (None, None)
        self.__d_battery_voltage = (None, None)

        # Create and start worker thread
        self.__worker_thread = threading.Thread(target=self.__worker_thread, daemon=True)
        self.__worker_thread.start()

    def __stop_working(self):
        self.__working = False
        self.__worker_thread.join(10.0)

    def __process_message(self, message, ser_message):
        new_message = self.parse(message)

        if new_message == 0:
            return 0

        # Heater and controller port assignment
        if not self.__ser_controller and new_message.device == 0x03:
            self.__ser_controller = ser_message
        if not self.__ser_heater and new_message.device == 0x04:
            self.__ser_heater = ser_message

        # Initialization message received
        if new_message.device == 0x00:
            self.logger.info('Inicialization message ({})'.format(message.hex()))

        # Disgnostic message received
        elif new_message.device == 0x02:
            if   new_message.msg_id2 == 0x00:
                self.logger.info('PC sends initialization diagnostic message')
            # Diagnostic message from heater received
            elif new_message.msg_id2 == 0x01:
                if len(new_message.payload) == 72:
                    self.__d_status1 = (new_message.payload[0], time.time())
                    self.__d_status2 = (new_message.payload[1], time.time())
                    self.__d_counter1 = (int.from_bytes(new_message.payload[7:9],'big'), time.time())
                    self.__d_counter2 = (int.from_bytes(new_message.payload[10:12],'big'), time.time())
                    self.__d_defined_rev = (new_message.payload[12], time.time())
                    self.__d_measured_rev = (new_message.payload[13], time.time())
                    self.__d_fuel_pump1 = (new_message.payload[15], time.time())
                    self.__d_fuel_pump2 = (new_message.payload[17], time.time())
                    self.__d_chamber_temperature = (int.from_bytes(new_message.payload[19:21],'big'), time.time())
                    self.__d_flame_temperature = (int.from_bytes(new_message.payload[21:23],'big'), time.time())
                    self.__d_external_temperature = (new_message.payload[25], time.time())
                    self.__d_heater_temperature = (new_message.payload[26], time.time())
                    self.__d_battery_voltage = (new_message.payload[28], time.time())
                    self.logger.info('Heater sends diagnostic message ({})'.format(new_message.payload.hex()))
                else:
                    self.logger.warning('Heater sends diagnostic message, wrong payload length ({})'.format(new_message.payload.hex()))
            
        # New message is from controller    
        elif new_message.device == 0x03:
            # Do not send messages, waiting for response from the heater
            self.__write_lock_timer = time.time() + self.__write_lock_delay
            # 01 - Controller turns heater on
            if   new_message.msg_id2 == 0x01:
                self.__heater_timer = None
                self.logger.info('Controller turns heater on with settings {}'.format(new_message.payload[2:].hex()))
            # 02 - Controller asks for settings
            elif new_message.msg_id2 == 0x02:
                if new_message.length == 0:
                    pass
                    #self.logger.info('Controller asks for settings')
                else:
                    self.__heater_timer = None
                    self.logger.info('Controller set new settings ({})'.format(new_message.payload[2:].hex()))
            # 03 - Controller turns off the heater
            elif new_message.msg_id2 == 0x03:
                self.__heater_timer = None
                self.logger.info('Controller turns off the heater')
            # 04 - Controller sends initialization message
            elif new_message.msg_id2 == 0x04:
                self.logger.info('Controller sends initialization message')
            # 06 - Controller asks for software version
            elif new_message.msg_id2 == 0x06:
                self.logger.info('Controller asks for software version')
            # 07 - Controller asks for status
            elif new_message.msg_id2 == 0x0f:
                #self.logger.info('Controller asks for status')
                pass
            # 11 - Controller reports temperature
            elif new_message.msg_id2 == 0x11:
                if len(new_message.payload) == 1: 
                    self.__controller_temperature = (new_message.payload[0], time.time())
                    #self.logger.info('Controller reports temperature {} °C'.format(new_message.payload[0]))
                else:
                    self.logger.warning('Controller reports temperature, wrong payload length ({})'.format(new_message.payload.hex()))
            # 1c - Controller sends initialization message
            elif new_message.msg_id2 == 0x1c:
                self.logger.info('Controller sends initialization message')
            # 23 - Controller turns ventilation
            elif new_message.msg_id2 == 0x23:
                self.logger.info('Controller turns ventilation on with settings {}'.format(new_message.payload[2:].hex()))
            # Unknown message
            else:
                self.logger.warning('Unknown message from controller: {}'.format(message.hex()))

        # New message is from heater
        elif new_message.device == 0x04:
            # Response from heater received, can send other messages
            self.__write_lock_timer = time.time()
            # 01 - Heater confirms starting up
            if   new_message.msg_id2 == 0x01:
                if len(new_message.payload) == 6:
                    self.__heater_mode = (new_message.payload[2], time.time())
                    self.__heater_setpoint = (new_message.payload[3], time.time())
                    self.__heater_ventilation = (new_message.payload[4], time.time())
                    self.__heater_power_level = (new_message.payload[5], time.time())
                    self.logger.info('Heater confirms starting up ({})'.format(new_message.payload.hex()))
                else:
                    self.logger.warning('Heater confirms starting up, wrong payload length ({})'.format(new_message.payload.hex()))
                # Reset settings timer
                self.__settings_timer = time.time()
            # 02 - Heater reports settings
            elif new_message.msg_id2 == 0x02:
                if len(new_message.payload) == 6:
                    self.__heater_mode = (new_message.payload[2], time.time())
                    self.__heater_setpoint = (new_message.payload[3], time.time())
                    self.__heater_ventilation = (new_message.payload[4], time.time())
                    self.__heater_power_level = (new_message.payload[5], time.time())
                    self.logger.info('Heater reports settings ({})'.format(new_message.payload.hex()))
                else:
                    self.logger.warning('Heater reports settings, wrong payload length ({})'.format(new_message.payload.hex()))
                # Reset settings timer
                self.__settings_timer = time.time()
            # 03 - Heater confirms turn off request
            elif new_message.msg_id2 == 0x03:
                self.logger.info('Heater confirms turn off request')
            # 04 - Heater responds to initialization message
            elif new_message.msg_id2 == 0x04:
                self.logger.info('Heater responds to initialization message')
            # 06 - Heater reports software version
            elif new_message.msg_id2 == 0x06:
                if len(new_message.payload) == 5:
                    self.__heater_software_version = (new_message.payload[0], new_message.payload[1], new_message.payload[2], new_message.payload[3], time.time())
                    self.logger.info('Heater reports software version ({})'.format(new_message.payload.hex()))
                else:
                    self.logger.warning('Heater reports software version, wrong payload length ({})'.format(new_message.payload.hex()))
            # 0f - Heater reports status
            elif new_message.msg_id2 == 0x0f:
                if len(new_message.payload) == 10:
                    self.__heater_status1 = (new_message.payload[0], time.time())
                    self.__heater_status2 = (new_message.payload[1], time.time())
                    self.__heater_errors = (new_message.payload[2], time.time())
                    self.__heater_temperature = (new_message.payload[3], time.time())
                    self.__external_temperature = (new_message.payload[4], time.time())
                    self.__battery_voltage = (new_message.payload[6]/10, time.time())
                    self.__flame_temperature = (int.from_bytes(new_message.payload[7:9],'big'), time.time())
                    self.logger.info('Heater reports status ({})'.format(new_message.payload.hex()))
                else:
                    self.logger.warning('Heater reports status, wrong payload length ({})'.format(new_message.payload.hex()))              
                # Reset staus timer
                self.__status_timer = time.time()
            # 11 - Heater confirms controller temperature
            elif new_message.msg_id2 == 0x11:
                if len(new_message.payload) == 1:
                    #self.logger.info('Heater confirms controller temperature {} °C'.format(new_message.payload[0]))
                    pass
                else:
                    self.logger.warning('Heater confirms controller temperature, wrong payload length ({})'.format(new_message.payload.hex()))
            # 1c - Heater responds to initialization message
            elif new_message.msg_id2 == 0x1c:
                self.logger.info('Heater responds to initialization message')
            # 23 - Heater confirms turning ventilation on
            elif new_message.msg_id2 == 0x23:
                self.logger.info('Heater confirms turning ventilation on ({})'.format(new_message.payload.hex()))   
            # Unknown message
            else:
                self.logger.warning('Unknown message from heater ({})'.format(message.hex()))
        # Unknown device id
        else:
            self.logger.warning('Unknown device id in message ({})'.format(message.hex()))
        # Message processed
        return 1


    def __worker_thread(self):
        self.logger.info('Worker started')

        while self.__working:
            if not self.__connected:
                self.__reconnect()
            else:
                if self.__message_waiting(self.__ser1) > 0:
                    message = self.__ser1.read(1)
                    if message == b'\x1b':
                        self.__write_message(self.__ser2, message)
                        self.logger.debug('Initialization message forwarded (1 >> 2: {})'.format(message.hex()))
                        continue
                    if message != b'\xaa':
                        self.__ser1.reset_input_buffer()
                        self.logger.warning('Unknown message detected, disposed (1 >> 2: {})'.format(message.hex()))
                        continue
                    message += self.__ser1.read(2)
                    message += self.__ser1.read(message[-1]+4)

                    self.__write_message(self.__ser2, message)
                    self.logger.debug('Message forwarded (1 >> 2: {})'.format(message.hex()))
                    self.__process_message(message, self.__ser1)
                        
                if self.__message_waiting(self.__ser2) > 0:
                    message = self.__ser2.read(1)
                    if message == b'\x1b':
                        self.__write_message(self.__ser1, message)
                        self.logger.debug('Initialization message forwarded (2 >> 1: {})'.format(message.hex()))
                        continue
                    if message != b'\xaa':
                        self.__ser2.reset_input_buffer()
                        self.logger.warning('Unknown message detected, disposed (2 >> 1: {})'.format(message.hex()))
                        continue
                    message += self.__ser2.read(2)
                    message += self.__ser2.read(message[-1]+4)

                    self.__write_message(self.__ser1, message)
                    self.logger.debug('Message forwarded (2 >> 1: {})'.format(message.hex()))
                    self.__process_message(message, self.__ser2)

                if len(self.__send_to_heater) > 0 and self.__write_lock_timer <= time.time():
                    message = self.__send_to_heater.pop(0)
                    if self.__ser_heater:
                        self.__write_message(self.__ser_heater, message)
                        self.logger.info('Program sends message to heater ({})'.format(message.hex()))
                    else:
                        self.__write_message(self.__ser1, message)
                        self.__write_message(self.__ser2, message)
                        self.logger.warning('Program sends message to both adapters ({})'.format(message.hex()))
                    self.__write_lock_timer = time.time() + self.__write_lock_delay
                    
                if self.__heater_timer:
                    if time.time() >= self.__heater_timer:
                        self.shutdown()

                if self.__shutdown_request:
                    if self.__heater_status1[0] == 0:
                        self.__shutdown_request = False
                    elif time.time() > self.__shutdown_timer + self.__shutdown_delay:
                        message = self.build(0x03,0x03)
                        if message != 0:
                            self.__send_to_heater.append(message)
                        self.__shutdown_timer = time.time()

                if time.time() >= self.__status_timer + self.__status_delay and self.__write_lock_timer <= time.time():
                    self.asks_for_status()

                if time.time() >= self.__settings_timer + self.__settings_delay and self.__write_lock_timer <= time.time():
                    self.asks_for_settings()


    # Heater and ventilation controlling
    def get_heater_timer(self):
        return self.__heater.timer
    def set_heater_timer(self, timer):
        self.__heater_timer = time.time() + (timer * 60) 
    def shutdown(self):
        self.__shutdown_request = True
    def turn_on_ventilation(self, power, timer = None):
        if timer:
            self.__heater_timer = time.time() + (timer * 60)
        payload = b'\xff\xff' + power.to_bytes(1, byteorder='big') + b'\x0f'
        message = self.build(0x03, 0x23, payload=payload)
        if message != 0:
            self.__send_to_heater.append(message)
            self.__send_to_heater.append(message)
            # Message is sent twice as from the controller
    def turn_on_heater(self, mode, setpoint = 0x0f, ventilation = 0x00, power = 0x00, timer = None):
        if timer:
            self.__heater_timer = time.time() + (timer * 60)
        payload = b'\xff\xff' + mode.to_bytes(1, byteorder='big') + setpoint.to_bytes(1, byteorder='big') + ventilation.to_bytes(1, byteorder='big') + power.to_bytes(1, byteorder='big')
        message = self.build(0x03, 0x01, payload=payload)
        if message != 0:
            self.__send_to_heater.append(message)
            self.__send_to_heater.append(message)
            # Message is sent twice as from the controller
    def change_settings(self, mode, setpoint = 0x0f, ventilation = 0x00, power = 0x00, timer = None):
        if timer:
            self.__heater_timer = time.time() + (timer * 60)
        payload = b'\xff\xff' + mode.to_bytes(1, byteorder='big') + setpoint.to_bytes(1, byteorder='big') + ventilation.to_bytes(1, byteorder='big') + power.to_bytes(1, byteorder='big')
        message = self.build(0x03, 0x02, payload=payload)
        if message != 0:
            self.__send_to_heater.append(message)
            self.__send_to_heater.append(message)
            # Message is sent twice as from the controller

    # Heater info
    def asks_for_heater_software_version(self):
        message = self.build(0x03, 0x06)
        if message != 0:
            self.__send_to_heater.append(message)
    def get_heater_software_version(self):
        return self.__heater_software_version

    # Heater settings
    def asks_for_settings(self):
        message = self.build(0x03, 0x02)
        if message != 0:
            self.__send_to_heater.append(message)
    def get_heater_mode(self):
        return self.__heater_mode
    def get_heater_setpoint(self):
        return self.__heater_setpoint
    def get_heater_ventilation(self):
        return self.__heater_ventilation
    def get_heater_power_level(self):
        return self.__heater_power_level
    
    # Heater status
    def asks_for_status(self):
        message = self.build(0x03, 0x0f)
        if message != 0:
            self.__send_to_heater.append(message)
    def get_heater_status(self):
        return (self.__heater_status1, self.__heater_status2)
    def get_heater_status_text(self):
        if self.__heater_status1[0] in status_text.keys():
            return status_text[self.__heater_status1[0]]
        else:
            return 'unknown status'
    def get_heater_errors(self):
        return self.__heater_errors
    def get_heater_temperature(self):
        return self.__heater_temperature
    def get_external_temperature(self):
        return self.__external_temperature
    def get_battery_voltage(self):
        return self.__battery_voltage
    def get_flame_temperature(self):
        return self.__flame_temperature

    # Controller temperature
    def report_controller_temperature(self, temperature):
        payload = temperature.to_bytes(1, byteorder='big')
        message = self.build(0x03, 0x11, payload=payload)
        if message != 0:
            self.__send_to_heater.append(message)
    def get_controller_temperature(self):
        return self.__controller_temperature
            
    def get_defined_rev(self):
        return self.__defined_rev
    def get_measured_rev(self):
        return self.__measured_rev

    # Diagnostic
    def diagnostic_on(self):
        payload = b'\x01'
        message = self.build(0x03, 0x07, payload = payload)
        if message != 0:
            self.__send_to_heater.append(message)
    def diagnostic_off(self):
        payload = b'\x00'
        message = self.build(0x03, 0x07, payload = payload)
        if message != 0:
            self.__send_to_heater.append(message)
    def unblock(self):
        message = self.build(0x03, 0x0d)
        if message != 0:
            self.__send_to_heater.append(message)
    def get_d_status(self):	
        return (self.__d_status1, self.__d_status2)
    def get_d_counter1(self):
        return self.__d_counter1
    def get_d_counter2(self):
        return self.__d_counter2
    def get_d_defined_rev(self):
        return self.__d_defined_rev
    def get_d_measured_rev(self):
        return self.__d_measured_rev
    def get_d_fuel_pump1(self):
        return self.__d_fuel_pump1
    def get_d_fuel_pump2(self):
        return self.__d_fuel_pump2
    def get_d_chamber_temperature(self):
        return self.__d_chamber_temperature
    def get_d_flame_temperature(self):
        return self.__d_flame_temperature
    def get_d_external_temperature(self):
        return self.__d_external_temperature
    def get_d_heater_temperature(self):
        return self.__d_heater_temperature
    def get_d_battery_voltage(self):
        return self.__d_battery_voltage
