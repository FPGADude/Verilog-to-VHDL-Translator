
from app import TranslatorApp

def test_xpm_warnings_classified_as_vendor():
    w = "[Synth 8-7129] Port dina[7] in module xpm_memory_base is either unconnected or has no load"
    assert TranslatorApp._classify_vivado_warning(w) == "Vendor XPM internals"

def test_project_warning_stays_actionable():
    w = "[Synth 8-3917] design top has port amp1_shutdown_n driven by constant 1"
    assert TranslatorApp._classify_vivado_warning(w) == "Project / generated VHDL"

def test_repetitive_xpm_port_warnings_are_collapsed():
    warnings = [
        "[Synth 8-7129] Port dina[7] in module xpm_memory_base is either unconnected or has no load",
        "[Synth 8-7129] Port dina[6] in module xpm_memory_base is either unconnected or has no load",
        "[Synth 8-7129] Port dina[5] in module xpm_memory_base is either unconnected or has no load",
    ]
    lines = TranslatorApp._summarize_vendor_warnings(warnings)
    assert len(lines) == 1
    assert "3 vendor XPM port/no-load warnings" in lines[0]
