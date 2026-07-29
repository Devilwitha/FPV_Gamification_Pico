import argparse
import ctypes
import importlib
import json
import os
import shutil
import sys
import time

from pico_runtime import install


DEFAULT_SIM_PROFILES = {
    "pico_w": {
        "mem_free_kb": 100,
        "mem_alloc_kb": 80,
        "cpu_freq_mhz": 125,
        "cpu_scale": 1.0,
        "net_latency_ms": 2,
        "real_ram_limit_mb": None,
    },
    "pico2": {
        "mem_free_kb": 180,
        "mem_alloc_kb": 96,
        "cpu_freq_mhz": 150,
        "cpu_scale": 1.0,
        "net_latency_ms": 1,
        "real_ram_limit_mb": None,
    },
}

PROFILE_KEYS = (
    "mem_free_kb",
    "mem_alloc_kb",
    "cpu_freq_mhz",
    "cpu_scale",
    "net_latency_ms",
    "real_ram_limit_mb",
)

_JOB_HANDLE = None


def _normalize_profile(raw_profile, fallback_profile):
    normalized = dict(fallback_profile)
    for key in PROFILE_KEYS:
        if key in raw_profile:
            normalized[key] = raw_profile[key]

    normalized["mem_free_kb"] = int(normalized["mem_free_kb"])
    normalized["mem_alloc_kb"] = int(normalized["mem_alloc_kb"])
    normalized["cpu_freq_mhz"] = int(normalized["cpu_freq_mhz"])
    normalized["cpu_scale"] = float(normalized["cpu_scale"])
    normalized["net_latency_ms"] = int(normalized["net_latency_ms"])
    if normalized.get("real_ram_limit_mb") in (None, ""):
        normalized["real_ram_limit_mb"] = None
    else:
        normalized["real_ram_limit_mb"] = int(normalized["real_ram_limit_mb"])
    return normalized


def _apply_windows_process_memory_limit(limit_bytes):
    global _JOB_HANDLE

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", ctypes.c_uint32),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", ctypes.c_uint32),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", ctypes.c_uint32),
            ("SchedulingClass", ctypes.c_uint32),
        ]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_job_object = kernel32.CreateJobObjectW
    set_information_job_object = kernel32.SetInformationJobObject
    assign_process_to_job_object = kernel32.AssignProcessToJobObject
    get_current_process = kernel32.GetCurrentProcess

    create_job_object.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    create_job_object.restype = ctypes.c_void_p
    set_information_job_object.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
    set_information_job_object.restype = ctypes.c_int
    assign_process_to_job_object.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    assign_process_to_job_object.restype = ctypes.c_int
    get_current_process.argtypes = []
    get_current_process.restype = ctypes.c_void_p

    JOB_OBJECT_LIMIT_PROCESS_MEMORY = 0x00000100
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9

    h_job = create_job_object(None, None)
    if not h_job:
        raise OSError("CreateJobObjectW failed")

    info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
    info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_PROCESS_MEMORY | JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    info.ProcessMemoryLimit = int(limit_bytes)

    ok = set_information_job_object(
        h_job,
        JobObjectExtendedLimitInformation,
        ctypes.byref(info),
        ctypes.sizeof(info),
    )
    if not ok:
        raise OSError("SetInformationJobObject failed")

    ok = assign_process_to_job_object(h_job, get_current_process())
    if not ok:
        raise OSError("AssignProcessToJobObject failed")

    _JOB_HANDLE = h_job


def _apply_posix_process_memory_limit(limit_bytes):
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (int(limit_bytes), int(limit_bytes)))


def apply_real_memory_limit(limit_mb):
    limit_mb = int(limit_mb)
    if limit_mb <= 0:
        raise ValueError("real_ram_limit_mb must be > 0")

    limit_bytes = limit_mb * 1024 * 1024
    if sys.platform.startswith("win"):
        _apply_windows_process_memory_limit(limit_bytes)
        return
    _apply_posix_process_memory_limit(limit_bytes)


def load_sim_profiles(profile_file_path):
    profiles = {name: dict(values) for name, values in DEFAULT_SIM_PROFILES.items()}
    if not os.path.isfile(profile_file_path):
        save_sim_profiles(profile_file_path, profiles)
        return profiles

    try:
        with open(profile_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return profiles

    if not isinstance(data, dict):
        return profiles

    merged = dict(profiles)
    for name, raw_profile in data.items():
        if not isinstance(name, str) or not name.strip():
            continue
        if not isinstance(raw_profile, dict):
            continue
        fallback = merged.get(name, merged["pico_w"])
        try:
            merged[name] = _normalize_profile(raw_profile, fallback)
        except Exception:
            continue
    return merged


def save_sim_profiles(profile_file_path, profiles):
    out_profiles = {}
    for name, profile in profiles.items():
        if not isinstance(name, str) or not name.strip():
            continue
        try:
            out_profiles[name.strip()] = _normalize_profile(profile, DEFAULT_SIM_PROFILES["pico_w"])
        except Exception:
            continue

    os.makedirs(os.path.dirname(profile_file_path), exist_ok=True)
    with open(profile_file_path, "w", encoding="utf-8") as f:
        json.dump(out_profiles, f, indent=2)
        f.write("\n")


MODULE_DIR = os.path.dirname(os.path.abspath(__file__))
SIM_PROFILES_PATH = os.path.join(MODULE_DIR, "sim_profiles.json")
SIM_PROFILES = load_sim_profiles(SIM_PROFILES_PATH)


def _rmtree_with_retry(path, attempts=5, delay_s=0.3):
    """shutil.rmtree() direkt nach Prozessende eines vorherigen Simulator-
    Laufs schlaegt unter Windows gelegentlich mit PermissionError fehl, weil
    das Betriebssystem (oder ein Virenscanner) eine Datei im Ordner noch
    kurz offen haelt, obwohl der Python-Prozess selbst schon beendet ist -
    ein kurzer Retry mit Verzoegerung reicht dafuer normalerweise aus."""
    last_error = None
    for attempt in range(attempts):
        try:
            shutil.rmtree(path)
            return
        except PermissionError as e:
            last_error = e
            if attempt < attempts - 1:
                time.sleep(delay_s)
    raise last_error


def clone_source_to_data(source_dir, data_dir, refresh=False):
    if refresh and os.path.isdir(data_dir):
        _rmtree_with_retry(data_dir)

    if not os.path.isdir(data_dir):
        shutil.copytree(source_dir, data_dir)
        print("[SIM] Created data clone from source.")


def main():
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    profile_file_path = os.path.join(project_root, "pico_simulator", "sim_profiles.json")
    sim_profiles = load_sim_profiles(profile_file_path)

    parser = argparse.ArgumentParser(description="Run FPV Pico firmware with a MicroPython simulator layer.")
    parser.add_argument(
        "--entry",
        choices=["main", "boot", "recovery"],
        default="main",
        help="Firmware entry module from source (default: main)",
    )
    parser.add_argument(
        "--source-dir",
        default=os.path.join(project_root, "source"),
        help="Path to source directory that contains firmware files.",
    )
    parser.add_argument(
        "--data-dir",
        default=os.path.join(project_root, "data"),
        help="Path to writable cloned firmware workspace used for simulation.",
    )
    parser.add_argument(
        "--refresh-data",
        action="store_true",
        help="Delete data directory and clone a fresh copy from source before starting.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Desktop port used when firmware tries to listen on port 80.",
    )
    parser.add_argument(
        "--sim-profile",
        choices=sorted(sim_profiles.keys()),
        default="pico_w",
        help="Simulation profile preset for RAM/CPU/network realism.",
    )
    parser.add_argument("--mem-free-kb", type=int, help="Override simulated gc.mem_free() in KB.")
    parser.add_argument("--mem-alloc-kb", type=int, help="Override simulated gc.mem_alloc() in KB.")
    parser.add_argument("--cpu-freq-mhz", type=int, help="Override simulated machine.freq() in MHz.")
    parser.add_argument("--cpu-scale", type=float, help="Scale sleep timing (>1 slower, <1 faster).")
    parser.add_argument("--net-latency-ms", type=int, help="Artificial latency added per stream drain.")
    parser.add_argument(
        "--real-ram-limit-mb",
        type=int,
        help="Hard process RAM limit in MB (OS-level limit for real OOM-style testing).",
    )
    args = parser.parse_args()

    source_dir = os.path.abspath(args.source_dir)
    data_dir = os.path.abspath(args.data_dir)
    if not os.path.isdir(source_dir):
        raise SystemExit(f"Source directory not found: {source_dir}")

    clone_source_to_data(source_dir, data_dir, refresh=args.refresh_data)

    profile = dict(sim_profiles[args.sim_profile])
    if args.mem_free_kb is not None:
        profile["mem_free_kb"] = args.mem_free_kb
    if args.mem_alloc_kb is not None:
        profile["mem_alloc_kb"] = args.mem_alloc_kb
    if args.cpu_freq_mhz is not None:
        profile["cpu_freq_mhz"] = args.cpu_freq_mhz
    if args.cpu_scale is not None:
        profile["cpu_scale"] = args.cpu_scale
    if args.net_latency_ms is not None:
        profile["net_latency_ms"] = args.net_latency_ms
    if args.real_ram_limit_mb is not None:
        profile["real_ram_limit_mb"] = args.real_ram_limit_mb

    if profile.get("real_ram_limit_mb") is not None:
        apply_real_memory_limit(profile["real_ram_limit_mb"])

    os.chdir(data_dir)
    if data_dir not in sys.path:
        sys.path.insert(0, data_dir)

    install(
        sim_port=args.port,
        mem_free_bytes=profile["mem_free_kb"] * 1024,
        mem_alloc_bytes=profile["mem_alloc_kb"] * 1024,
        cpu_freq_hz=profile["cpu_freq_mhz"] * 1_000_000,
        cpu_scale=profile["cpu_scale"],
        net_latency_ms=profile["net_latency_ms"],
    )

    print(f"[SIM] Source directory: {source_dir}")
    print(f"[SIM] Working directory: {data_dir}")
    print(
        "[SIM] Profile: "
        f"{args.sim_profile} "
        f"(mem_free={profile['mem_free_kb']}KB, mem_alloc={profile['mem_alloc_kb']}KB, "
        f"cpu={profile['cpu_freq_mhz']}MHz, cpu_scale={profile['cpu_scale']}, "
        f"latency={profile['net_latency_ms']}ms, real_ram_limit={profile['real_ram_limit_mb']}MB)"
    )
    print(f"[SIM] Importing firmware entry: {args.entry}.py")
    print(f"[SIM] Open UI at: http://127.0.0.1:{args.port}/")

    importlib.import_module(args.entry)


if __name__ == "__main__":
    main()
