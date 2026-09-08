from pathlib import Path
from vivado_verify import build_verify_tcl, _extract_messages


def test_tcl_generation():
    text = build_verify_tcl([Path("a.vhdl"), Path("folder with spaces/b.vhdl")], "top", "xc7a35tcpg236-1")
    assert "read_vhdl -vhdl2008" not in text
    assert "read_vhdl " in text
    assert "synth_design -rtl -top top -part xc7a35tcpg236-1" in text
    assert "FPGA_DISCOVERY_VERIFY_PASS" in text


def test_message_extraction():
    log = """WARNING: [Synth 8-1] benign warning\nERROR: [Synth 8-2] bad VHDL\nWARNING: [Synth 8-1] benign warning\nCRITICAL WARNING: [Synth 8-3] another warning\n"""
    errors, warnings = _extract_messages(log)
    assert errors == ["ERROR: [Synth 8-2] bad VHDL"]
    assert len(warnings) == 2


if __name__ == "__main__":
    test_tcl_generation()
    test_message_extraction()
    print("PASS: Vivado verification helper tests")
