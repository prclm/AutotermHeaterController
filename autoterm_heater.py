#!/usr/bin/python3
# Filename: autoterm_heater.py

import logging
import serial
import threading
import time

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
        if package[1] not in [0x00, 0x03, 0x04]:
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
    def __init__(self, serial_port1, baudrate1, serial_port2, baudrate2, log_path, log_level = logging.DEBUG):
        self.port1 = serial_port1
        self.baudrate1 = baudrate1
        self.port2 = serial_port2
        self.baudrate2 = baudrate2

        self.logger = logging.getLogger(__name__)
        self.logger.setLevel(log_level)
        handler = logging.FileHandler(log_path)
        formatter = logging.Formatter(fmt = '%(asctime)s  %(name)s %(levelname)s: %(message)s', datefmt='%d.%m.%Y %H:%M:%S')
        handler.setFormatter(formatter)
        handler.setLevel(logging.DEBUG)
        self.logger.addHandler(handler)

        self.__connect()
        self.__start_working()

    def __connect(self):
        self.__ser1 = serial.Serial(self.port1, self.baudrate1, bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=0.5, write_timeout=0.5)
        self.__ser2 = serial.Serial(self.port2, self.baudrate2, bytesize=serial.EIGHTBITS, parity=serial.PARITY_NONE, stopbits=serial.STOPBITS_ONE, timeout=0.5, write_timeout=0.5)

        self.__ser1.reset_input_buffer()
        self.__ser2.reset_input_buffer()

        self.__write_lock = False

        self.logger.info('Serial connection to '+self.port1+' established')
        self.logger.info('Serial connection to '+self.port2+' established')

    def __disconnect(self):
        self.__ser1.close()
        self.__ser2.close()

    def __start_working(self):
        self.__working = True
        self.__heater_on = False
        self.__heater_mode = False
        self.__heater_setpoint = False
        self.__heater_ventilation = False
        self.__heater_power_level = False
        
        self.__ventilation_on = False
        self.__heater_ser = None
        self.__controller_ser = None
        self.__heater_send = []

        self.__panel_temperature = None
        self.__heater_temperature = None
        self.__external_temperature = None
        self.__battery_voltage = None
        self.__heater_status = None
        self.__defined_rev = None
        self.__measured_rev = None

        #restart controller panel??
        
        self.__worker_thread = threading.Thread(target=self.__worker_thread, daemon=True)
        self.__worker_thread.start()

    def __stop_working(self):
        self.__working = False
        self.__worker_thread.join(10.0)

    def __process_message(self, message):
        new_message = self.parse(message)
        if new_message == 0:
            return 0

        if not self.__controller_ser or not self.__heater_ser:
            if new_message.device == 0x03:
                self.__controller_ser = self.__ser1
            elif new_message.device == 0x04:
                self.__heater_ser = self.__ser1

        if new_message.device == 0x00:
            self.logger.info('Inicialization message ({})'.format(message.hex()))
            
        elif new_message.device == 0x03:
            self.write_lock = True

            if   new_message.msg_id2 == 0x01:
                self.logger.info('Panel turns heater on with settings {}'.format(new_message.payload[2:].hex()))
            elif new_message.msg_id2 == 0x02:
                if new_message.length == 0:
                    self.logger.info('Panel asks for settings')
                else:
                    self.logger.info('Panel set new settings ({})'.format(new_message.payload[2:].hex()))
            elif new_message.msg_id2 == 0x03:
                self.logger.info('Panel turns off the heater')
            elif new_message.msg_id2 == 0x04:
                self.logger.info('Panel sends initialization message')
            elif new_message.msg_id2 == 0x06:
                self.logger.info('Panel sends initialization message')
            elif new_message.msg_id2 == 0x0f:
                self.logger.info('Panel asks for status')      
            elif new_message.msg_id2 == 0x11:
                self.__panel_temperature = new_message.payload[0]
                self.logger.info('Panel reports temperature {} °C'.format(new_message.payload[0]))
            elif new_message.msg_id2 == 0x23:
                self.logger.info('Panel turns ventilation on with settings {}'.format(new_message.payload[2:].hex()))     
            elif new_message.msg_id2 == 0x1c:
                self.logger.info('Panel sends initialization message')

            else:
                self.logger.warning('Unknown message: {}'.format(message.hex()))

        elif new_message.device == 0x04:
            self.write_lock = False
            
            if   new_message.msg_id2 == 0x01:
                self.__heater_on = True
                self.__heater_mode = new_message.payload[2]
                self.__heater_setpoint = new_message.payload[3]
                self.__heater_ventilation = new_message.payload[4]
                self.__heater_power_level = new_message.payload[5]
                self.logger.info('Heater confirms starting up ({})'.format(new_message.payload.hex()))
            elif new_message.msg_id2 == 0x02:    
                self.logger.info('Heater reports settings ({})'.format(new_message.payload.hex()))
            elif new_message.msg_id2 == 0x03:
                self.__heater_on = False
                self.__ventilation_on = False
                self.logger.info('Heater confirms turning off')
            elif new_message.msg_id2 == 0x04:
                self.logger.info('Heater responds to initialization message')
            elif new_message.msg_id2 == 0x06:
                self.logger.info('Heater responds to initialization message')
            elif new_message.msg_id2 == 0x0f:
                if len(new_message.payload) == 10:
                    self.__heater_status = new_message.payload[0]
                    self.__heater_temperature = new_message.payload[3]
                    self.__external_temperature = new_message.payload[4]
                    self.__battery_voltage = new_message.payload[6]
                self.logger.info('Heater reports status ({})'.format(new_message.payload.hex()))
            elif new_message.msg_id2 == 0x11:
                self.logger.info('Heater confirms panel temperature {} °C'.format(new_message.payload[0]))
            elif new_message.msg_id2 == 0x23:
                self.logger.info('Heater confirms turning ventilation on ({})'.format(new_message.payload.hex()))   
            elif new_message.msg_id2 == 0x1c:
                self.logger.info('Heater responds to initialization message')
            else:
              self.logger.warning('Unknown message {}'.format(message.hex()))
        
        return 1
                

        
    def __worker_thread(self):
        self.logger.info('Worker started')

        while True:
            if self.__ser1.inWaiting() > 0:
                message = self.__ser1.read(1)
                if message == b'\x1b':
                    self.__ser2.write(message)
                    self.logger.debug('Initialization message forwarded (1 >> 2: {})'.format(message.hex()))
                    continue
                if message != b'\xaa':
                    #self.__ser2.write(message)
                    #self.logger.warning('Unknown message forwarded (1 >> 2: {})'.format(message.hex()))
                    self.__ser1.reset_input_buffer()
                    self.logger.warning('Unknown message detected, disposed (1 >> 2: {})'.format(message.hex()))
                    continue
                message += self.__ser1.read(2)
                message += self.__ser1.read(message[-1]+4)

                self.__ser2.write(message)
                self.logger.debug('Message forwarded (1 >> 2: {})'.format(message.hex()))
                self.__process_message(message)
                    
            if self.__ser2.inWaiting() > 0:
                message = self.__ser2.read(1)
                if message == b'\x1b':
                    self.__ser1.write(message)
                    self.logger.debug('Initialization message forwarded (2 >> 1: {})'.format(message.hex()))
                    continue
                if message != b'\xaa':
                    #self.__ser1.write(message)
                    #self.logger.warning('Unknown message forwarded (2 >> 1: {})'.format(message.hex()))
                    self.__ser2.reset_input_buffer()
                    self.logger.warning('Unknown message detected, disposed (2 >> 1: {})'.format(message.hex()))
                    continue
                message += self.__ser2.read(2)
                message += self.__ser2.read(message[-1]+4)

                self.__ser1.write(message)
                self.logger.debug('Message forwarded (2 >> 1: {})'.format(message.hex()))
                self.__process_message(message)

            if len(self.__heater_send)>0 and not self.write_lock and self.__heater_ser != None:
                message = self.__heater_send.pop(0)
                self.__heater_ser.write(message)
                self.write_lock = True
                self.logger.info('Program sends message to heater ({})'.format(message.hex()))                


            #time.sleep(0.1)
            

    def get_panel_temperature(self):
        return self.__panel_temperature
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

    def shut_down(self):
        message = self.build(0x03,0x03)
        self.__heater_send.append(message)
        # opakované zprávy každých 10 s??

    def turn_on_ventilation(self, power):
        payload = b'\xff\xff' + power.to_bytes(1, byteorder='big') + b'\x00'
        message = self.build(0x03, 0x23, payload=payload)
        self.__heater_send.append(message)
        #poslat dvakrát??

    def turn_on_heater(self, mode, setpoint = 0x0f, ventilation = 0x00, power = 0x00):
        payload = b'\xff\xff' + mode.to_bytes(1, byteorder='big') + setpoint.to_bytes(1, byteorder='big') + ventilation.to_bytes(1, byteorder='big') + power.to_bytes(1, byteorder='big')
        message = self.build(0x03, 0x01, payload=payload)
        self.__heater_send.append(message)
        #poslat dvakrát??

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
        
        

        
    
