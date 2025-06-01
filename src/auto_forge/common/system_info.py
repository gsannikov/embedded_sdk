"""
Script:         system_info.py
Author:         AutoForge Team

Description:
    Provides the SystemInfo class and supporting enums for collecting detailed system
    information such as OS type, distribution, architecture, user environment, package
    manager, virtualization/container status, and system uptime.
"""

import getpass
import platform
import re
import shutil
import socket
import subprocess
from contextlib import suppress
from typing import Optional, Tuple

# Third-party
import psutil

# AutoForge imports
from auto_forge import SysInfoPackageManagerType, SysInfoLinuxDistroType

AUTO_FORGE_MODULE_NAME = "SystemInfo"
AUTO_FORGE_MODULE_DESCRIPTION = "System information collector"


class SystemInfo:
    def __init__(self) -> None:
        """
        Initialize CoreSysInfo and collect system metadata. This includes platform type,
        virtualization/container status, user and host info, package manager, memory,
        and (on Linux) distribution details.
        """
        self._system_type: str = platform.system().lower()
        self._is_wsl: bool = "wsl" in platform.release().lower()
        self._is_docker: bool = self._detect_docker()
        self._architecture: str = platform.machine()
        self._python_version: str = platform.python_version()
        self._hostname: str = socket.gethostname()
        self._uptime: Optional[float] = self._get_uptime()
        self._username: str = getpass.getuser()
        self._package_manager: Optional[SysInfoPackageManagerType] = self._detect_package_manager()
        self._linux_distro: Optional[SysInfoLinuxDistroType] = None
        self._linux_version: Optional[str] = None
        self._virtualization: Optional[str] = self._detect_virtualization()

        if self._system_type == "linux":
            self._linux_distro, self._linux_version = self._get_linux_distro()

        self._total_memory_mb: Optional[int] = self._get_total_memory()

    @staticmethod
    def _get_linux_distro() -> Tuple[SysInfoLinuxDistroType, str]:
        """
        Retrieve the Linux distribution as a standardized enum and version string.
        Returns:
            Tuple[SysInfoLinuxDistroType, str]: (Distro enum, version ID), or (UNKNOWN, "") if not found.
        """
        with suppress(Exception):
            with open("/etc/os-release") as f:
                data = {}
                for line in f:
                    if "=" in line:
                        k, v = line.strip().split("=", 1)
                        data[k] = v.strip('"')
                distro_id = data.get("ID", "").lower()
                version_id = data.get("VERSION_ID", "")
                return SysInfoLinuxDistroType.from_id(distro_id), version_id

        return SysInfoLinuxDistroType.UNKNOWN, ""

    @staticmethod
    def _detect_package_manager() -> Optional[SysInfoPackageManagerType]:
        """
        Detect the available package manager on the system.
        Returns:
            Optional[SysInfoPackageManagerType]: Detected package manager enum value, or None if none found.
        """
        with suppress(Exception):
            for pm in SysInfoPackageManagerType:
                if shutil.which(pm.value):
                    return pm
        return None

    def _get_total_memory(self) -> Optional[int]:
        """
        Retrieve the total system memory in megabytes.
        Returns:
            int: Total memory in MB if successfully determined, else None.
        """
        with suppress(Exception):
            if self._system_type == "linux":
                with open("/proc/meminfo") as f:
                    for line in f:
                        if line.startswith("MemTotal:"):
                            return int(line.split()[1]) // 1024  # in MB

            elif self._system_type == "darwin":
                # noinspection SpellCheckingInspection
                output = subprocess.check_output(["sysctl", "-n", "hw.memsize"]).decode().strip()
                return int(output) // (1024 * 1024)

            elif self._system_type == "windows":
                return int(psutil.virtual_memory().total) // (1024 * 1024)

        return None

    @staticmethod
    def _get_uptime() -> Optional[float]:
        """
        Retrieve the system uptime in seconds.
        Returns:
            float: Uptime in seconds, or None if it could not be determined.
        """
        with suppress(Exception):
            if platform.system().lower() == "linux":
                with open("/proc/uptime") as f:
                    return float(f.readline().split()[0])

            elif platform.system().lower() == "darwin":
                # noinspection SpellCheckingInspection
                output = subprocess.check_output(["sysctl", "-n", "kern.boottime"]).decode()
                match = re.search(r"sec = (\d+)", output)
                if match:
                    import time
                    boot_time = int(match.group(1))
                    return time.time() - boot_time
        return None

    # noinspection SpellCheckingInspection
    @staticmethod
    def _detect_virtualization() -> Optional[str]:
        """
        Detect if the system is running in a virtualized environment.
        Returns:
            str: Virtualization type (e.g., 'kvm', 'vmware') if detected, else None.
        """
        with suppress(Exception):
            if shutil.which("systemd-detect-virt"):
                output = subprocess.check_output(["systemd-detect-virt", "--quiet", "--vm"]).decode().strip()
                return output if output else None
        return None

    @staticmethod
    def _detect_docker() -> bool:
        """
        Detect whether the system is running inside a Docker container.
        Returns:
            bool: True if Docker is detected, False otherwise.
        """
        with suppress(Exception):
            with open("/proc/1/cgroup", "rt") as f:
                return any("docker" in line for line in f)
        return False

    def as_dict(self) -> dict:
        """
        Return a dictionary of collected system information.
        Returns:
            dict: A dictionary containing all detected system attributes, suitable for serialization or logging.
        """
        return {"system_type": self._system_type, "is_wsl": self._is_wsl, "is_docker": self._is_docker,
            "architecture": self._architecture, "python_version": self._python_version, "hostname": self._hostname,
            "username": self._username, "package_manager": self._package_manager if self._package_manager else None,
            "linux_distro": self._linux_distro if self._linux_distro else None, "linux_version": self._linux_version,
            "total_memory_mb": self._total_memory_mb, "virtualization": self._virtualization,
            "uptime_sec": self._uptime, }

    def __str__(self) -> str:
        """
        Return a human-readable, formatted string of system information.
        Returns:
            str: A multi-line string representing key system details.
        """
        return "\n".join(f"{k:20}: {v if v is not None else 'N/A'}" for k, v in self.as_dict().items())
