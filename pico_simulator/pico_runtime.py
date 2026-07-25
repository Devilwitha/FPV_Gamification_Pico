import asyncio
import builtins
import gc
import sys
import time
import types


class _SimState:
    start_monotonic = time.monotonic()
    cpu_scale = 1.0
    cpu_freq_hz = 133_000_000
    mem_free_bytes = 180 * 1024
    mem_alloc_bytes = 70 * 1024
    net_latency_ms = 0
    ap_active = False
    sta_active = False
    sta_connected = False
    ap_config = {
        "essid": "FPV_Gamification_Pico_SIM",
        "password": "",
        "pm": 0,
    }
    ap_ifconfig = ("192.168.4.1", "255.255.255.0", "192.168.4.1", "192.168.4.1")


_original_open = builtins.open
_original_time_sleep = time.sleep


def _install_time_compat(cpu_scale):
    _SimState.cpu_scale = max(0.1, float(cpu_scale))

    def ticks_ms():
        return int((time.monotonic() - _SimState.start_monotonic) * 1000)

    def ticks_diff(a, b):
        return int(a - b)

    def sleep_ms(ms):
        _original_time_sleep((max(0, ms) / 1000.0) * _SimState.cpu_scale)

    if not hasattr(time, "ticks_ms"):
        setattr(time, "ticks_ms", ticks_ms)
    if not hasattr(time, "ticks_diff"):
        setattr(time, "ticks_diff", ticks_diff)
    if not hasattr(time, "sleep_ms"):
        setattr(time, "sleep_ms", sleep_ms)


def _install_gc_compat(mem_free_bytes, mem_alloc_bytes):
    _SimState.mem_free_bytes = int(mem_free_bytes)
    _SimState.mem_alloc_bytes = int(mem_alloc_bytes)
    setattr(gc, "mem_free", lambda: _SimState.mem_free_bytes)
    setattr(gc, "mem_alloc", lambda: _SimState.mem_alloc_bytes)


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


def _install_asyncio_compat(sim_port, cpu_scale, net_latency_ms):
    _SimState.net_latency_ms = max(0, int(net_latency_ms))

    if not hasattr(asyncio, "sleep_ms"):

        async def sleep_ms(ms):
            await asyncio.sleep((max(0, ms) / 1000.0) * max(0.1, float(cpu_scale)))

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

    original_drain = asyncio.StreamWriter.drain

    async def drain_compat(self):
        if _SimState.net_latency_ms > 0:
            await asyncio.sleep(_SimState.net_latency_ms / 1000.0)
        return await original_drain(self)

    asyncio.StreamWriter.drain = drain_compat


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


def _machine_freq(new_freq_hz=None):
    if new_freq_hz is None:
        return _SimState.cpu_freq_hz
    _SimState.cpu_freq_hz = int(new_freq_hz)
    return _SimState.cpu_freq_hz


def _install_machine_module():
    machine_mod = types.ModuleType("machine")
    machine_mod.Pin = _Pin
    machine_mod.UART = _UART
    machine_mod.WDT = _WDT
    machine_mod.reset = _machine_reset
    machine_mod.freq = _machine_freq
    sys.modules["machine"] = machine_mod


class _WLAN:
    def __init__(self, interface):
        self.interface = interface

    def active(self, enabled=None):
        state_name = "ap_active" if self.interface == 1 else "sta_active"
        if enabled is None:
            return getattr(_SimState, state_name)
        setattr(_SimState, state_name, bool(enabled))
        if self.interface != 1 and not enabled:
            _SimState.sta_connected = False
        return getattr(_SimState, state_name)

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

    def connect(self, ssid, password=None):
        if self.interface != 0:
            return None
        _SimState.sta_active = True
        _SimState.sta_connected = bool(ssid)
        return None

    def disconnect(self):
        if self.interface == 0:
            _SimState.sta_connected = False
        return None

    def isconnected(self):
        return self.interface == 0 and _SimState.sta_connected

    def scan(self):
        return []


def _install_network_module():
    network_mod = types.ModuleType("network")
    network_mod.STA_IF = 0
    network_mod.AP_IF = 1
    network_mod.WLAN = _WLAN
    sys.modules["network"] = network_mod


def install(
    sim_port=8080,
    mem_free_bytes=180 * 1024,
    mem_alloc_bytes=70 * 1024,
    cpu_freq_hz=133_000_000,
    cpu_scale=1.0,
    net_latency_ms=0,
):
    _SimState.cpu_freq_hz = int(cpu_freq_hz)
    _install_time_compat(cpu_scale=cpu_scale)
    _install_gc_compat(mem_free_bytes=mem_free_bytes, mem_alloc_bytes=mem_alloc_bytes)
    _install_open_compat()
    _install_asyncio_compat(sim_port=sim_port, cpu_scale=cpu_scale, net_latency_ms=net_latency_ms)
    _install_machine_module()
    _install_network_module()
    print(
        "[SIM] MicroPython compatibility layer installed "
        f"(mem_free={_SimState.mem_free_bytes}B, mem_alloc={_SimState.mem_alloc_bytes}B, "
        f"cpu={_SimState.cpu_freq_hz}Hz, cpu_scale={_SimState.cpu_scale:.2f}, latency={_SimState.net_latency_ms}ms)."
    )
