import asyncio
import builtins
import gc
import sys
import time
import types


class _SimState:
    start_monotonic = time.monotonic()
    ap_active = False
    ap_config = {
        "essid": "FPV_Gamification_Pico_SIM",
        "password": "",
        "pm": 0,
    }
    ap_ifconfig = ("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1")


_original_open = builtins.open


def _install_time_compat():
    def ticks_ms():
        return int((time.monotonic() - _SimState.start_monotonic) * 1000)

    def ticks_diff(a, b):
        return int(a - b)

    def sleep_ms(ms):
        time.sleep(max(0, ms) / 1000.0)

    if not hasattr(time, "ticks_ms"):
        setattr(time, "ticks_ms", ticks_ms)
    if not hasattr(time, "ticks_diff"):
        setattr(time, "ticks_diff", ticks_diff)
    if not hasattr(time, "sleep_ms"):
        setattr(time, "sleep_ms", sleep_ms)


def _install_gc_compat():
    if not hasattr(gc, "mem_free"):
        setattr(gc, "mem_free", lambda: 256 * 1024)
    if not hasattr(gc, "mem_alloc"):
        setattr(gc, "mem_alloc", lambda: 64 * 1024)


def _install_open_compat():
    def open_compat(file, mode="r", buffering=-1, encoding=None, errors=None, newline=None, closefd=True, opener=None):
        # Keep exact newlines when opening text files without explicit newline
        # to match os.stat-based content-length logic used by firmware.
        if "b" not in mode and newline is None:
            newline = ""
        return _original_open(
            file,
            mode,
            buffering=buffering,
            encoding=encoding,
            errors=errors,
            newline=newline,
            closefd=closefd,
            opener=opener,
        )

    builtins.open = open_compat


def _install_asyncio_compat(sim_port):
    if not hasattr(asyncio, "sleep_ms"):

        async def sleep_ms(ms):
            await asyncio.sleep(max(0, ms) / 1000.0)

        setattr(asyncio, "sleep_ms", sleep_ms)

    original_start_server = asyncio.start_server

    async def start_server_compat(client_connected_cb, host=None, port=None, *args, **kwargs):
        listen_port = port
        if port == 80:
            listen_port = sim_port
            print(f"[SIM] Redirect port 80 -> {listen_port}")
        return await original_start_server(client_connected_cb, host, listen_port, *args, **kwargs)

    setattr(asyncio, "start_server", start_server_compat)

    # MicroPython stream writer accepts str directly; CPython requires bytes.
    # The firmware writes both, so normalize here for compatibility.
    original_write = asyncio.StreamWriter.write

    def write_compat(self, data):
        if isinstance(data, str):
            data = data.encode("utf-8")
        return original_write(self, data)

    asyncio.StreamWriter.write = write_compat


class _Pin:
    IN = 0
    OUT = 1

    def __init__(self, pin_id, mode=None):
        self.pin_id = pin_id
        self.mode = mode
        self._value = 0

    def value(self, new_value=None):
        if new_value is None:
            return self._value
        self._value = 1 if new_value else 0
        return self._value


class _UART:
    def __init__(self, *_args, **_kwargs):
        self._buffer = bytearray()

    def any(self):
        return len(self._buffer)

    def read(self, nbytes=None):
        if not self._buffer:
            return b""
        if nbytes is None or nbytes >= len(self._buffer):
            data = bytes(self._buffer)
            self._buffer.clear()
            return data
        data = bytes(self._buffer[:nbytes])
        del self._buffer[:nbytes]
        return data


class _WDT:
    def __init__(self, timeout=8000):
        self.timeout = timeout

    def feed(self):
        return None


def _machine_reset():
    print("[SIM] machine.reset() called (ignored in simulator).")


def _install_machine_module():
    machine_mod = types.ModuleType("machine")
    machine_mod.Pin = _Pin
    machine_mod.UART = _UART
    machine_mod.WDT = _WDT
    machine_mod.reset = _machine_reset
    sys.modules["machine"] = machine_mod


class _WLAN:
    def __init__(self, interface):
        self.interface = interface

    def active(self, enabled=None):
        if enabled is None:
            return _SimState.ap_active
        _SimState.ap_active = bool(enabled)
        return _SimState.ap_active

    def config(self, *args, **kwargs):
        if args and isinstance(args[0], str):
            return _SimState.ap_config.get(args[0])
        for key, value in kwargs.items():
            _SimState.ap_config[key] = value
        return None

    def ifconfig(self, cfg=None):
        if cfg is None:
            return _SimState.ap_ifconfig
        _SimState.ap_ifconfig = tuple(cfg)
        return _SimState.ap_ifconfig


def _install_network_module():
    network_mod = types.ModuleType("network")
    network_mod.AP_IF = 1
    network_mod.WLAN = _WLAN
    sys.modules["network"] = network_mod


def install(sim_port=8080):
    _install_time_compat()
    _install_gc_compat()
    _install_open_compat()
    _install_asyncio_compat(sim_port=sim_port)
    _install_machine_module()
    _install_network_module()
    print("[SIM] MicroPython compatibility layer installed.")
