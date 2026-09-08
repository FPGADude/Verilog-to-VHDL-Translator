# FPGA Discovery Verilog → VHDL Translator

**Version:** 1.0.0

A deterministic desktop translator for practical synthesizable Verilog RTL,
developed around real FPGA workflows and verified through AMD Vivado.

This project is intentionally **not** presented as a universal Verilog compiler.
Its goal is to translate a documented, practical RTL subset and produce clear
diagnostics when a construct falls outside that subset.

## Main workflow

1. **Import Verilog Files**
2. **Translate Files**
3. Inspect source, generated VHDL, hierarchy, and diagnostics
4. **Verify VHDL** with Vivado
5. **Export VHDL Files**

## Application features

- Multi-file Verilog project import
- `.v` and `.vh` support
- Verilog module hierarchy
- VHDL entity hierarchy
- Line-numbered Verilog and VHDL editors
- Maximize Verilog / Maximize VHDL / Restore Split View
- Deterministic translation diagnostics
- Integrated Vivado verification
- Expandable/minimizable Project Translation Report
- `.vhdl` export
- Copy VHDL
- Persistent Vivado configuration
- Digilent board presets for:
  - Basys 3
  - Nexys A7-50T
  - Nexys A7-100T
  - Cmod A7-15T
  - Cmod A7-35T

## Why Vivado verification is integrated

Generating text that looks like VHDL is not enough. Verilog and VHDL differ
significantly in typing, sizing, signed arithmetic, port semantics, and
elaboration behavior.

The **Verify VHDL** action asks Vivado to read and elaborate/synthesize the
generated VHDL for the selected FPGA part. This has repeatedly caught real
translation bugs during development.

## Qualification

Development included both purpose-built language qualification modules and real
FPGA projects. Generated VHDL has been used in Vivado in place of original
Verilog and tested successfully on FPGA hardware.

See `SUPPORTED_VERILOG.md` for the qualified RTL subset and scope limits.

## Requirements

- Python 3
- Tkinter (included with Python installation)
- AMD Vivado for integrated verification

Run:

```bash
python app.py
```

## License

This project is distributed under the **FPGA Discovery Non-Commercial
License v1.0**. Personal, educational, academic, research, and other
non-commercial use is permitted under the license terms. Commercial
redistribution, resale, paid bundling, commercial hosted services, and other
commercial exploitation of the translator require prior written permission.

The license does **not** claim ownership of HDL generated from a user's own
source material. Generated VHDL may be used in commercial FPGA projects,
subject to rights and licenses applicable to the user's input material.