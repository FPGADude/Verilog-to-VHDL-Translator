# FPGA Discovery Verilog → VHDL Translator — v1.0.0

## First public release

Version 1.0.0 is the first public release of the FPGA Discovery Verilog → VHDL Translator.

The application translates a documented subset of synthesizable Verilog into baseline-compatible VHDL and can invoke AMD Vivado to verify the generated RTL. It is designed around deterministic translation: unsupported or unsafe-to-translate constructs should produce diagnostics instead of silently generating questionable HDL.

## Highlights

- Multi-file Verilog project translation and hierarchy discovery
- Quick Translate: paste a standalone Verilog module directly into the editor; no source file required
- Verilog and generated-VHDL hierarchy views
- Baseline VHDL output using `.vhdl` files
- Parameters/generics and named parameter overrides
- Combinational and sequential RTL, FSMs, `case`, nested control flow
- Signed arithmetic and width-aware conversions
- Functions and synthesizable tasks
- Counted `for` loops and dynamic indexing
- Unpacked memories and `$readmemh` ROM embedding
- Preprocessor support for the documented subset
- Project-aware child-port width coercion
- Integrated Vivado RTL verification
- Persistent Vivado executable and FPGA part configuration
- Expandable/minimizable translation and verification report
- Copy and export generated VHDL

## Validation

The release-candidate cycle included targeted regression designs plus real FPGA projects. The automated Python regression suite contains 51 passing tests at release.

Real-project smoke tests include:
- multi-module Pmod ACL + VGA design — translated, built in Vivado, programmed, and hardware validated on Basys 3;
- five-module RV32I RISC-V core — generated VHDL accepted by Vivado with non-fatal synthesis warnings;
- seven-module Basys 3 QSPI Flash Part 6 design — generated VHDL accepted by Vivado with non-fatal constant/no-load warnings.

## Scope

This tool does not claim complete Verilog-language compatibility. See `SUPPORTED_VERILOG.md` for the intended translation subset and known boundaries. Vivado verification is strongly recommended before using generated RTL in hardware.

## License

See `LICENSE` for the project's source-available, non-commercial license terms.

## RC9 correction

- Fixed `"+"`, `"-"`, and other operator-like characters inside Verilog string
  literals being misread by expression precedence logic.
- Added regression coverage for character-valued string literals.
- Re-ran translation of the real Pmod ACL/VGA, RV32I, and Flash Part 6 projects:
  all modules translated with zero translator diagnostics.

## Final release qualification

RC9 was promoted without translator-engine changes after the generated seven-module Pmod ACL + VGA VHDL was built in a normal Vivado project, programmed to a Basys 3, and hardware-validated to behave the same as the original Verilog design.
