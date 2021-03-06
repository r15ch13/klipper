# TMC2130 configuration
#
# Copyright (C) 2018-2019  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import math, logging
import bus, tmc

TMC_FREQUENCY=13200000.

Registers = {
    "GCONF": 0x00, "GSTAT": 0x01, "IOIN": 0x04, "IHOLD_IRUN": 0x10,
    "TPOWERDOWN": 0x11, "TSTEP": 0x12, "TPWMTHRS": 0x13, "TCOOLTHRS": 0x14,
    "THIGH": 0x15, "XDIRECT": 0x2d, "MSLUT0": 0x60, "MSLUTSEL": 0x68,
    "MSLUTSTART": 0x69, "MSCNT": 0x6a, "MSCURACT": 0x6b, "CHOPCONF": 0x6c,
    "COOLCONF": 0x6d, "DCCTRL": 0x6e, "DRV_STATUS": 0x6f, "PWMCONF": 0x70,
    "PWM_SCALE": 0x71, "ENCM_CTRL": 0x72, "LOST_STEPS": 0x73,
}

ReadRegisters = [
    "GCONF", "GSTAT", "IOIN", "TSTEP", "XDIRECT", "MSCNT", "MSCURACT",
    "CHOPCONF", "DRV_STATUS", "PWM_SCALE", "LOST_STEPS",
]

Fields = {}
Fields["GCONF"] = {
    "I_scale_analog": 1<<0, "internal_Rsense": 1<<1, "en_pwm_mode": 1<<2,
    "enc_commutation": 1<<3, "shaft": 1<<4, "diag0_error": 1<<5,
    "diag0_otpw": 1<<6, "diag0_stall": 1<<7, "diag1_stall": 1<<8,
    "diag1_index": 1<<9, "diag1_onstate": 1<<10, "diag1_steps_skipped": 1<<11,
    "diag0_int_pushpull": 1<<12, "diag1_pushpull": 1<<13,
    "small_hysteresis": 1<<14, "stop_enable": 1<<15, "direct_mode": 1<<16,
    "test_mode": 1<<17
}
Fields["GSTAT"] = { "reset": 1<<0, "drv_err": 1<<1, "uv_cp": 1<<2 }
Fields["IOIN"] = {
    "STEP": 1<<0, "DIR": 1<<1, "DCEN_CFG4": 1<<2, "DCIN_CFG5": 1<<3,
    "DRV_ENN_CFG6": 1<<4, "DCO": 1<<5, "VERSION": 0xff << 24
}
Fields["IHOLD_IRUN"] = {
    "IHOLD": 0x1f << 0, "IRUN": 0x1f << 8, "IHOLDDELAY": 0x0f << 16
}
Fields["TPOWERDOWN"] = { "TPOWERDOWN": 0xff }
Fields["TSTEP"] = { "TSTEP": 0xfffff }
Fields["TPWMTHRS"] = { "TPWMTHRS": 0xfffff }
Fields["TCOOLTHRS"] = { "TCOOLTHRS": 0xfffff }
Fields["THIGH"] = { "THIGH": 0xfffff }
Fields["MSCNT"] = { "MSCNT": 0x3ff }
Fields["MSCURACT"] = { "CUR_A": 0x1ff, "CUR_B": 0x1ff << 16 }
Fields["CHOPCONF"] = {
    "toff": 0x0f, "hstrt": 0x07 << 4, "hend": 0x0f << 7, "fd3": 1<<11,
    "disfdcc": 1<<12, "rndtf": 1<<13, "chm": 1<<14, "TBL": 0x03 << 15,
    "vsense": 1<<17, "vhighfs": 1<<18, "vhighchm": 1<<19, "sync": 0x0f << 20,
    "MRES": 0x0f << 24, "intpol": 1<<28, "dedge": 1<<29, "diss2g": 1<<30
}
Fields["COOLCONF"] = {
    "semin": 0x0f, "seup": 0x03 << 5, "semax": 0x0f << 8, "sedn": 0x03 << 13,
    "seimin": 1<<15, "sgt": 0x7f << 16, "sfilt": 1<<24
}
Fields["DRV_STATUS"] = {
    "SG_RESULT": 0x3ff, "fsactive": 1<<15, "CS_ACTUAL": 0x1f << 16,
    "stallGuard": 1<<24, "ot": 1<<25, "otpw": 1<<26, "s2ga": 1<<27,
    "s2gb": 1<<28, "ola": 1<<29, "olb": 1<<30, "stst": 1<<31
}
Fields["PWMCONF"] = {
    "PWM_AMPL": 0xff, "PWM_GRAD": 0xff << 8, "pwm_freq": 0x03 << 16,
    "pwm_autoscale": 1<<18, "pwm_symmetric": 1<<19, "freewheel": 0x03 << 20
}
Fields["PWM_SCALE"] = { "PWM_SCALE": 0xff }
Fields["LOST_STEPS"] = { "LOST_STEPS": 0xfffff }

SignedFields = ["CUR_A", "CUR_B", "sgt"]

FieldFormatters = {
    "I_scale_analog":   (lambda v: "1(ExtVREF)" if v else ""),
    "shaft":            (lambda v: "1(Reverse)" if v else ""),
    "drv_err":          (lambda v: "1(ErrorShutdown!)" if v else ""),
    "uv_cp":            (lambda v: "1(Undervoltage!)" if v else ""),
    "VERSION":          (lambda v: "%#x" % v),
    "MRES":             (lambda v: "%d(%dusteps)" % (v, 0x100 >> v)),
    "otpw":             (lambda v: "1(OvertempWarning!)" if v else ""),
    "ot":               (lambda v: "1(OvertempError!)" if v else ""),
    "s2ga":             (lambda v: "1(ShortToGND_A!)" if v else ""),
    "s2gb":             (lambda v: "1(ShortToGND_B!)" if v else ""),
    "ola":              (lambda v: "1(OpenLoad_A!)" if v else ""),
    "olb":              (lambda v: "1(OpenLoad_B!)" if v else ""),
}


######################################################################
# TMC stepper current config helper
######################################################################

MAX_CURRENT = 2.000

class TMCCurrentHelper:
    def __init__(self, config, mcu_tmc):
        self.printer = config.get_printer()
        self.name = config.get_name().split()[-1]
        self.mcu_tmc = mcu_tmc
        self.fields = mcu_tmc.get_fields()
        run_current = config.getfloat('run_current',
                                      above=0., maxval=MAX_CURRENT)
        hold_current = config.getfloat('hold_current', run_current,
                                       above=0., maxval=MAX_CURRENT)
        self.sense_resistor = config.getfloat('sense_resistor', 0.110, above=0.)
        vsense, irun, ihold = self._calc_current(run_current, hold_current)
        self.fields.set_field("vsense", vsense)
        self.fields.set_field("IHOLD", ihold)
        self.fields.set_field("IRUN", irun)
        gcode = self.printer.lookup_object("gcode")
        gcode.register_mux_command(
            "SET_TMC_CURRENT", "STEPPER", self.name,
            self.cmd_SET_TMC_CURRENT, desc=self.cmd_SET_TMC_CURRENT_help)
    def _calc_current_bits(self, current, vsense):
        sense_resistor = self.sense_resistor + 0.020
        vref = 0.32
        if vsense:
            vref = 0.18
        cs = int(32. * current * sense_resistor * math.sqrt(2.) / vref
                 - 1. + .5)
        return max(0, min(31, cs))
    def _calc_current(self, run_current, hold_current):
        vsense = False
        irun = self._calc_current_bits(run_current, vsense)
        ihold = self._calc_current_bits(hold_current, vsense)
        if irun < 16 and ihold < 16:
            vsense = True
            irun = self._calc_current_bits(run_current, vsense)
            ihold = self._calc_current_bits(hold_current, vsense)
        return vsense, irun, ihold
    def _calc_current_from_field(self, field_name):
        bits = self.fields.get_field(field_name)
        sense_resistor = self.sense_resistor + 0.020
        vref = 0.32
        if self.fields.get_field("vsense"):
            vref = 0.18
        current = (bits + 1) * vref / (32 * sense_resistor * math.sqrt(2.))
        return round(current, 2)
    cmd_SET_TMC_CURRENT_help = "Set the current of a TMC driver"
    def cmd_SET_TMC_CURRENT(self, params):
        gcode = self.printer.lookup_object('gcode')
        if 'HOLDCURRENT' in params:
            hold_current = gcode.get_float(
                'HOLDCURRENT', params, above=0., maxval=MAX_CURRENT)
        else:
            hold_current = self._calc_current_from_field("IHOLD")
        if 'CURRENT' in params:
            run_current = gcode.get_float(
                'CURRENT', params, minval=hold_current, maxval=MAX_CURRENT)
        else:
            run_current = self._calc_current_from_field("IRUN")
        if 'HOLDCURRENT' not in params and 'CURRENT' not in params:
            # Query only
            gcode.respond_info("Run Current: %0.2fA Hold Current: %0.2fA"
                               % (run_current, hold_current))
            return
        print_time = self.printer.lookup_object('toolhead').get_last_move_time()
        vsense, irun, ihold = self._calc_current(run_current, hold_current)
        if vsense != self.fields.get_field("vsense"):
            val = self.fields.set_field("vsense", vsense)
            self.mcu_tmc.set_register("CHOPCONF", val, print_time)
        self.fields.set_field("IHOLD", ihold)
        val = self.fields.set_field("IRUN", irun)
        self.mcu_tmc.set_register("IHOLD_IRUN", val, print_time)


######################################################################
# TMC2130 SPI
######################################################################

# Helper code for working with TMC devices via SPI
class MCU_TMC_SPI:
    def __init__(self, config, name_to_reg, fields):
        self.printer = config.get_printer()
        self.mutex = self.printer.get_reactor().mutex()
        self.spi = bus.MCU_SPI_from_config(config, 3, default_speed=4000000)
        self.name_to_reg = name_to_reg
        self.fields = fields
    def get_fields(self):
        return self.fields
    def get_register(self, reg_name):
        reg = self.name_to_reg[reg_name]
        with self.mutex:
            self.spi.spi_send([reg, 0x00, 0x00, 0x00, 0x00])
            if self.printer.get_start_args().get('debugoutput') is not None:
                return 0
            params = self.spi.spi_transfer([reg, 0x00, 0x00, 0x00, 0x00])
        pr = bytearray(params['response'])
        return (pr[1] << 24) | (pr[2] << 16) | (pr[3] << 8) | pr[4]
    def set_register(self, reg_name, val, print_time=None):
        minclock = 0
        if print_time is not None:
            minclock = self.spi.get_mcu().print_time_to_clock(print_time)
        reg = Registers[reg_name]
        data = [(reg | 0x80) & 0xff, (val >> 24) & 0xff, (val >> 16) & 0xff,
                (val >> 8) & 0xff, val & 0xff]
        with self.mutex:
            self.spi.spi_send(data, minclock)


######################################################################
# TMC2130 printer object
######################################################################

class TMC2130:
    def __init__(self, config):
        # Setup mcu communication
        self.fields = tmc.FieldHelper(Fields, SignedFields, FieldFormatters)
        self.mcu_tmc = MCU_TMC_SPI(config, Registers, self.fields)
        # Allow virtual pins to be created
        diag1_pin = config.get('diag1_pin', None)
        tmc.TMCVirtualPinHelper(config, self.mcu_tmc, diag1_pin)
        # Register commands
        cmdhelper = tmc.TMCCommandHelper(config, self.mcu_tmc)
        cmdhelper.setup_register_dump(ReadRegisters)
        # Setup basic register values
        TMCCurrentHelper(config, self.mcu_tmc)
        mh = tmc.TMCMicrostepHelper(config, self.mcu_tmc)
        self.get_microsteps = mh.get_microsteps
        self.get_phase = mh.get_phase
        tmc.TMCStealthchopHelper(config, self.mcu_tmc, TMC_FREQUENCY)
        # Allow other registers to be set from the config
        set_config_field = self.fields.set_config_field
        set_config_field(config, "toff", 4)
        set_config_field(config, "hstrt", 0)
        set_config_field(config, "hend", 7)
        set_config_field(config, "TBL", 1)
        set_config_field(config, "IHOLDDELAY", 8)
        set_config_field(config, "TPOWERDOWN", 0)
        set_config_field(config, "PWM_AMPL", 128)
        set_config_field(config, "PWM_GRAD", 4)
        set_config_field(config, "pwm_freq", 1)
        set_config_field(config, "pwm_autoscale", True)
        set_config_field(config, "sgt", 0)

def load_config_prefix(config):
    return TMC2130(config)
