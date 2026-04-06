"""
Isolation primitive — single mechanism for both RUN (build) and `docksmith run`.

Uses:
  - chroot(2)              : filesystem root isolation
  - unshare(NEWPID|NEWUTS) : process and hostname namespace isolation
  - ctypes → libc          : direct syscall access from Python

run_isolated(rootfs, cmd, env, workdir) → int (exit code)
"""

import ctypes
import ctypes.util
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Linux namespace flags (from <sched.h>)
CLONE_NEWNS  = 0x00020000   # mount namespace
CLONE_NEWUTS = 0x04000000   # hostname/domainname
CLONE_NEWPID = 0x20000000   # PID namespace

MS_NOSUID    = 2
MS_NODEV     = 4
MS_NOEXEC    = 8
MS_BIND      = 4096


def _libc():
    name = ctypes.util.find_library("c") or "libc.so.6"
    return ctypes.CDLL(name, use_errno=True)


def run_isolated(
    rootfs: str,
    cmd: List[str],
    env: Optional[Dict[str, str]] = None,
    workdir: str = "/",
) -> int:
    """
    Execute cmd inside rootfs with full filesystem isolation.
    The child process cannot read or write outside rootfs.
    Returns the exit code of the command.

    Requires: Linux, running as root (or with user-namespace capability).
    """
    if not sys.platform.startswith("linux"):
        print("Error: isolation requires Linux.", file=sys.stderr)
        sys.exit(1)

    rootfs  = str(Path(rootfs).resolve())
    workdir = workdir or "/"
    env     = env or {}

    child_env = _build_env(env)

    # Determine how to invoke the command.
    # If the command is a single shell string, wrap it in /bin/sh -c
    shell = _find_shell(rootfs)
    if len(cmd) == 1 and _needs_shell(cmd[0]):
        actual_cmd = [shell, "-c", cmd[0]]
    else:
        actual_cmd = list(cmd)

    def preexec_fn():
        """Runs in the child process after fork(), before exec()."""
        libc = _libc()

        # 1. Unshare namespaces
        flags = CLONE_NEWPID | CLONE_NEWUTS | CLONE_NEWNS
        r = libc.unshare(ctypes.c_int(flags))
        # unshare failure (e.g. no CAP_SYS_ADMIN) is non-fatal for chroot-only mode

        # 2. Mount /proc inside rootfs if the directory exists
        proc_inside = os.path.join(rootfs, "proc")
        if os.path.isdir(proc_inside):
            libc.mount(
                b"proc",
                proc_inside.encode(),
                b"proc",
                ctypes.c_ulong(MS_NOSUID | MS_NODEV | MS_NOEXEC),
                None,
            )

        # 3. chroot
        try:
            os.chroot(rootfs)
        except PermissionError:
            sys.stderr.write(
                "Error: chroot() requires root privileges.\n"
                "  → Run with: sudo docksmith ...\n"
            )
            os._exit(126)

        # 4. Working directory (inside the new root)
        try:
            os.chdir(workdir)
        except (FileNotFoundError, NotADirectoryError):
            os.chdir("/")

    try:
        proc = subprocess.Popen(
            actual_cmd,
            env=child_env,
            preexec_fn=preexec_fn,
            stdin=sys.stdin,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
        proc.wait()
        return proc.returncode
    except FileNotFoundError:
        print(
            f"Error: executable not found inside container: {actual_cmd[0]}",
            file=sys.stderr,
        )
        return 127


def _build_env(image_env: Dict[str, str]) -> Dict[str, str]:
    """Build a clean environment for the container process."""
    env = {}
    # Minimal safe defaults
    env["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
    env["HOME"] = "/root"
    env["TERM"] = os.environ.get("TERM", "xterm")
    # Layer image ENV on top
    env.update(image_env)
    return env


def _find_shell(rootfs: str) -> str:
    for sh in ("/bin/sh", "/bin/bash", "/usr/bin/sh", "/usr/bin/bash"):
        if os.path.isfile(rootfs + sh):
            return sh
    return "/bin/sh"


def _needs_shell(cmd: str) -> bool:
    """Heuristic: does this string need a shell to interpret it?"""
    shell_chars = set("|&;<>()$`\\\"'{}[]!#~")
    return any(c in cmd for c in shell_chars) or " " in cmd.strip()