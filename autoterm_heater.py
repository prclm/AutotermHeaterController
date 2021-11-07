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
versionPatch = 3
################

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
        if device not in [0x03, 0x04]:
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

        # Following values are stored in tuples with timestamp
        self.__heater_mode = (None, None)
        self.__heater_setpoint = (None, None)
        self.__heater_ventilation = (None, None)
        self.__heater_power_level = (None, None)

        self.__shutdown_request = False
        self.__shutdown_timer = time.time()
        self.__shutdown_delay = 10              # Sets how often raspberry sends messages to turn heater off
        
        self.__send_to_heater = []

        # Following values are stored in tuples with timestamp
        self.__controller_temperature = (None, None)
        self.__heater_temperature = (None, None)
        self.__external_temperature = (None, None)
        self.__battery_voltage = (None, None)
        self.__heater_status = (None, None)
        self.__defined_rev = (None, None)
        self.__measured_rev = (None, None)
        
        self.__worker_thread = threading.Thread(target=self.__worker_thread, daemon=True)
        self.__worker_thread.start()

    def __stop_working(self):
        self.__working = False
        self.__worker_thread.join(10.0)

    def __process_message(self, message, ser_message):
        new_message = self.parse(message)

        if new_message == 0:
            return 0

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
            elif new_message.msg_id2 == 0x01:
                self.logger.info('Heater sends diagnostic message')
                # Get values from message
            
        # New message is from controller    
        elif new_message.device == 0x03:
            # Do not send messages, waiting for response from the heater
            self.__write_lock_timer = time.time() + self.__write_lock_delay

            if   new_message.msg_id2 == 0x01:
                self.logger.info('Controller turns heater on with settings {}'.format(new_message.payload[2:].hex()))
            elif new_message.msg_id2 == 0x02:
                if new_message.length == 0:
                    self.logger.info('Controller asks for settings')
                else:
                    self.logger.info('Controller set new settings ({})'.format(new_message.payload[2:].hex()))
            elif new_message.msg_id2 == 0x03:
                self.logger.info('Controller turns off the heater')
            elif new_message.msg_id2 == 0x04:
                self.logger.info('Controller sends initialization message')
            elif new_message.msg_id2 == 0x06:
                self.logger.info('Controller sends initialization message')
            elif new_message.msg_id2 == 0x0f:
                self.logger.info('Controller asks for status')
            elif new_message.msg_id2 == 0x11:
                self.__controller_temperature = (new_message.payload[0], time.time())
                self.logger.info('Controller reports temperature {} °C'.format(new_message.payload[0]))
            elif new_message.msg_id2 == 0x23:
                self.logger.info('Controller turns ventilation on with settings {}'.format(new_message.payload[2:].hex()))     
            elif new_message.msg_id2 == 0x1c:
                self.logger.info('Controller sends initialization message')

            else:
                self.logger.warning('Unknown message from controller: {}'.format(message.hex()))

        # New message is from heater
        elif new_message.device == 0x04:
            # Response from heater received, can send other messages
            self.__write_lock_timer = time.time()
            
            if   new_message.msg_id2 == 0x01:
                self.__heater_mode = (new_message.payload[2], time.time())
                self.__heater_setpoint = (new_message.payload[3], time.time())
                self.__heater_ventilation = (new_message.payload[4], time.time())
                self.__heater_power_level = (new_message.payload[5], time.time())
                self.logger.info('Heater confirms starting up ({})'.format(new_message.payload.hex()))
            elif new_message.msg_id2 == 0x02:
                self.__heater_mode = (new_message.payload[2], time.time())
                self.__heater_setpoint = (new_message.payload[3], time.time())
                self.__heater_ventilation = (new_message.payload[4], time.time())
                self.__heater_power_level = (new_message.payload[5], time.time())
                self.logger.info('Heater reports settings ({})'.format(new_message.payload.hex()))
            elif new_message.msg_id2 == 0x03:
                self.logger.info('Heater confirms turn off request')
            elif new_message.msg_id2 == 0x04:
                self.logger.info('Heater responds to initialization message')
            elif new_message.msg_id2 == 0x06:
                self.logger.info('Heater responds to initialization message')
            elif new_message.msg_id2 == 0x0f:
                if len(new_message.payload) == 10:
                    self.__heater_status = (new_message.payload[0], time.time())
                    self.__heater_temperature = (new_message.payload[3], time.time())
                    self.__external_temperature = (new_message.payload[4], time.time())
                    self.__battery_voltage = (new_message.payload[6]/10, time.time())
                self.logger.info('Heater reports status ({})'.format(new_message.payload.hex()))
            elif new_message.msg_id2 == 0x11:
                self.logger.info('Heater confirms controller temperature {} °C'.format(new_message.payload[0]))
            elif new_message.msg_id2 == 0x23:
                self.logger.info('Heater confirms turning ventilation on ({})'.format(new_message.payload.hex()))   
            elif new_message.msg_id2 == 0x1c:
                self.logger.info('Heater responds to initialization message')
            else:
                self.logger.warning('Unknown message from heater: {}'.format(message.hex()))
        else:
            self.logger.warning('Unknown device id: {}'.format(message.hex()))

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
                    self.__process_message(message, self.__ser2)
                        
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
                    

                if self.__shutdown_request:
                    if self.__heater_status[0] == 0:
                        self.__shutdown_request = False
                    elif time.time() > self.__shutdown_timer + self.__shutdown_delay:
                        message = self.build(0x03,0x03)
                        if message != 0:
                            self.__send_to_heater.append(message)
                        self.__shutdown_timer = time.time()            

    # Status requests
    def asks_for_status(self):
        message = self.build(0x03, 0x0f)
        if message != 0:
            self.__send_to_heater.append(message)
    def report_panel_temperature(self, temperature):
        payload = temperature.to_bytes(1, byteorder='big')
        message = self.build(0x03, 0x11, payload=payload)
        if message != 0:
            self.__send_to_heater.append(message)
    def get_controller_temperature(self):
        return self.__controller_temperature
    def get_heater_temperature(self):
        return self.__heater_temperature
    def get_external_temperature(self):
        return self.__external_temperature
    def get_battery_voltage(self):
        return self.__battery_voltage
    def get_heater_status(self):
        return self.__heater_status
    def get_defined_rev(self):
        return self.__defined_rev
    def get_measured_rev(self):
        return self.__measured_rev

    # Settings request
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
    
    def shutdown(self):
        self.__shutdown_request = True

    def turn_on_ventilation(self, power):
        payload = b'\xff\xff' + power.to_bytes(1, byteorder='big') + b'\x0f'
        message = self.build(0x03, 0x23, payload=payload)
        if message != 0:
            self.__send_to_heater.append(message)
            self.__send_to_heater.append(message)
            # Message is sent twice as from the controller

    def turn_on_heater(self, mode, setpoint = 0x0f, ventilation = 0x00, power = 0x00):
        payload = b'\xff\xff' + mode.to_bytes(1, byteorder='big') + setpoint.to_bytes(1, byteorder='big') + ventilation.to_bytes(1, byteorder='big') + power.to_bytes(1, byteorder='big')
        message = self.build(0x03, 0x01, payload=payload)
        if message != 0:
            self.__send_to_heater.append(message)
            self.__send_to_heater.append(message)
            # Message is sent twice as from the controller

    def change_settings(self, mode, setpoint = 0x0f, ventilation = 0x00, power = 0x00):
        payload = b'\xff\xff' + mode.to_bytes(1, byteorder='big') + setpoint.to_bytes(1, byteorder='big') + ventilation.to_bytes(1, byteorder='big') + power.to_bytes(1, byteorder='big')
        message = self.build(0x03, 0x02, payload=payload)
        if message != 0:
            self.__send_to_heater.append(message)
            self.__send_to_heater.append(message)
            # Message is sent twice as from the controller

class AutotermController(AutotermUtils):
    def __init__(self, serial_port, baudrate, log_path):
        self.port = serial_port
        self.baudrate = baudrate

        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(logging.DEBUG)
        handler = logging.FileHandler(log_path)
        formatter = logging.Formatter(fmt = '%(asctime)s  %(name)s %(levelname)s: %(message)s', datefmt='%d.%m.%Y %H:%M:%S')
        handler.setFormatter(formatter)
        handler.setLevel(logging.DEBUG)
        self.logger.addHandler(handler)
        
