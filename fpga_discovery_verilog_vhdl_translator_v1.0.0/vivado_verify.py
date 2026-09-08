from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


DEFAULT_PART = "xc7a35tcpg236-1"


@dataclass
class VivadoVerificationResult:
    ok: bool
    top: str
    part: str
    returncode: int
    command: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    log: str = ""
    workdir: str = ""

    @property
    def error_count(self) -> int:
        return len(self.errors)

    @property
    def warning_count(self) -> int:
        return len(self.warnings)


def _version_key(path: Path):
    # Sort Vivado version directories such as 2025.2 numerically when possible.
    nums = re.findall(r"\d+", str(path))
    return tuple(int(n) for n in nums[-3:]) if nums else (0,)


def discover_vivado(configured: str | None = None) -> str | None:
    """Find a usable Vivado launcher on Windows/Linux without requiring setup."""
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())

    for exe in ("vivado.bat", "vivado"):
        found = shutil.which(exe)
        if found:
            candidates.append(Path(found))

    # Common AMD/Xilinx install locations.  glob() on a non-Windows host is
    # harmless; these paths are primarily for packaged Windows use.
    if os.name == "nt":
        roots = [Path("C:/AMD/Vivado"), Path("C:/Xilinx/Vivado")]
        for root in roots:
            if root.exists():
                versions = sorted((p for p in root.iterdir() if p.is_dir()), key=_version_key, reverse=True)
                for version in versions:
                    candidates.extend([version / "bin" / "vivado.bat", version / "bin" / "vivado.exe"])

    seen: set[str] = set()
    for c in candidates:
        try:
            key = str(c.resolve()).lower()
        except OSError:
            key = str(c).lower()
        if key in seen:
            continue
        seen.add(key)
        if c.exists() and c.is_file():
            return str(c)
    return None


def _tcl_quote(path: Path) -> str:
    # Braced Tcl strings safely handle spaces and Windows path separators.
    return "{" + str(path.resolve()).replace("}", "\\}") + "}"


def build_verify_tcl(vhdl_files: list[Path], top: str, part: str) -> str:
    lines = [
        "set_msg_config -severity INFO -suppress",
        "puts {FPGA_DISCOVERY_VERIFY_BEGIN}",
    ]
    for path in vhdl_files:
        lines.append(f"read_vhdl {_tcl_quote(path)}")
    lines += [
        f"synth_design -rtl -top {top} -part {part}",
        "puts {FPGA_DISCOVERY_VERIFY_PASS}",
        "puts [format {TOP=%s PART=%s} [get_property TOP [current_fileset]] [get_property PART [current_project]]]",
        "exit 0",
    ]
    return "\n".join(lines) + "\n"


def _extract_messages(text: str) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    warnings: list[str] = []
    seen_e: set[str] = set()
    seen_w: set[str] = set()

    # Vivado commonly emits "ERROR: [Synth ...]" / "WARNING: [Synth ...]".
    # Include CRITICAL WARNING as warning-level information for verification.
    for raw in text.splitlines():
        line = raw.strip()
        upper = line.upper()
        if upper.startswith("ERROR:"):
            if line not in seen_e:
                seen_e.add(line)
                errors.append(line)
        elif upper.startswith("CRITICAL WARNING:") or upper.startswith("WARNING:"):
            if line not in seen_w:
                seen_w.add(line)
                warnings.append(line)

    return errors, warnings


def verify_vhdl_files(
    vivado_path: str,
    vhdl_files: list[Path],
    top: str,
    part: str = DEFAULT_PART,
    timeout: int = 180,
    resource_files: list[Path] | None = None,
) -> VivadoVerificationResult:
    """Run a headless Vivado RTL synthesis/elaboration check over generated VHDL."""
    launcher = Path(vivado_path)
    if not launcher.exists():
        return VivadoVerificationResult(
            False, top, part, -1, errors=[f"Vivado launcher not found: {vivado_path}"]
        )
    if not vhdl_files:
        return VivadoVerificationResult(False, top, part, -1, errors=["No VHDL files were supplied for verification."])

    workdir = Path(tempfile.mkdtemp(prefix="fpga_discovery_vhdl_verify_"))

    # Memory-init resources referenced by generated XPMs are resolved by filename
    # during synthesis. Place a private copy beside the verification run so the
    # user's original project files are never modified.
    for resource in resource_files or []:
        resource = Path(resource)
        if resource.exists() and resource.is_file():
            shutil.copy2(resource, workdir / resource.name)

    tcl = workdir / "verify_vhdl.tcl"
    tcl.write_text(build_verify_tcl(vhdl_files, top, part), encoding="utf-8")

    # .bat files need cmd.exe when shell=False on Windows.
    if os.name == "nt" and launcher.suffix.lower() in {".bat", ".cmd"}:
        command = ["cmd.exe", "/d", "/s", "/c", str(launcher), "-mode", "batch", "-nolog", "-nojournal", "-source", str(tcl)]
    else:
        command = [str(launcher), "-mode", "batch", "-nolog", "-nojournal", "-source", str(tcl)]

    try:
        proc = subprocess.run(
            command,
            cwd=workdir,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" and hasattr(subprocess, "CREATE_NO_WINDOW") else 0),
        )
        log = (proc.stdout or "") + ("\n" if proc.stdout and proc.stderr else "") + (proc.stderr or "")
        errors, warnings = _extract_messages(log)
        passed_marker = "FPGA_DISCOVERY_VERIFY_PASS" in log
        ok = proc.returncode == 0 and passed_marker and not errors
        if not ok and not errors:
            errors.append(f"Vivado verification failed with exit code {proc.returncode}.")
        return VivadoVerificationResult(ok, top, part, proc.returncode, command, errors, warnings, log, str(workdir))
    except subprocess.TimeoutExpired as exc:
        log = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + ((exc.stderr or "") if isinstance(exc.stderr, str) else "")
        errors, warnings = _extract_messages(log)
        errors.append(f"Vivado verification timed out after {timeout} seconds.")
        return VivadoVerificationResult(False, top, part, -2, command, errors, warnings, log, str(workdir))
    except OSError as exc:
        return VivadoVerificationResult(False, top, part, -3, command, [f"Could not launch Vivado: {exc}"], [], "", str(workdir))
