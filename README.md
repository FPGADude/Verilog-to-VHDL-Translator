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

The final mixed-feature pre-release qualification reached:

- 2 translated modules
- 0 Vivado errors
- 0 Vivado warnings

See `SUPPORTED_VERILOG.md` for the qualified RTL subset and scope limits.

## Requirements

- Python 3
- Tkinter
- AMD Vivado for integrated verification

Run:

```bash
python app.py
```

## Release Candidate policy

v1.0.0 is the first public release. From this point, changes should be limited to:

- release-blocking bugs
- packaging/documentation corrections
- problems found during the final smoke test

No example or qualification projects are bundled in the release-candidate
distribution.

## License

This project is distributed under the **FPGA Discovery Non-Commercial
License v1.0**. Personal, educational, academic, research, and other
non-commercial use is permitted under the license terms. Commercial
redistribution, resale, paid bundling, commercial hosted services, and other
commercial exploitation of the translator require prior written permission.

The license does **not** claim ownership of HDL generated from a user's own
source material. Generated VHDL may be used in commercial FPGA projects,
subject to rights and licenses applicable to the user's input material.

## RC3 release-blocking fixes

A final RV32I CPU smoke test exposed two release-blocking translation defects:

- `$signed(a) < $signed(b)` could be swallowed by an overly greedy `$signed(...)`
  recognizer, producing malformed VHDL.
- Wide constants such as `32'hFFFF_FFFE` were routed through VHDL INTEGER,
  exceeding Vivado's supported integer range.

RC3 fixes both and adds focused regressions for them.

## RC4 release-blocking fix

The RV32I immediate generator exposed scalar replication inside nested
concatenation, for example:

`{{20{instr[31]}}, instr[31:20]}`

RC3 correctly recognized the replication but called the vector-only
`repeat_slv` helper with a scalar `std_logic` bit. Vivado rejected the call.
RC4 adds an overloaded scalar form of `repeat_slv`, preserving baseline-VHDL
compatibility for sign-extension patterns used heavily in CPU RTL.

## RC5 release-blocking fix

The RV32I JALR datapath exposed an operator-precedence interaction:

`(rs1_data + imm_i) & 32'hFFFF_FFFE`

RC4 correctly preserved the wide mask, but the bitwise-AND fallback did not
recursively translate the parenthesized addition. That left raw `+` arithmetic
between `std_logic_vector` operands and Vivado could not resolve either `+` or
the resulting `and`.

RC5 now parses top-level bitwise OR/XOR/AND explicitly at the correct precedence
level and recursively types each operand before applying the VHDL bitwise
operator. A focused regression protects the exact JALR expression.

## RC6 release-blocking fix

The Flash Part 6 smoke test exposed packed-vector module parameters such as:

`parameter [23:0] PAGE_BASE = 24'h3FF000`

Earlier releases modeled all module parameters as VHDL INTEGER generics, so the
parser rejected this declaration. RC6 preserves packed parameter width, emits a
`std_logic_vector` generic, keeps the parameter in expression-width analysis,
and supports slices such as `PAGE_BASE[23:16]`.

A focused regression protects this exact construct.

## RC7 release-blocking fixes

Flash Part 6 verification exposed clocked Verilog integer registers and task
side effects on module-level registers. RC7 emits sequential integer registers
as VHDL integer signals while preserving combinational integer temporaries as
process variables. Tasks that modify outer registers now receive explicit VHDL
signal parameters at each procedure call.

## Final v1.0 release fixes

Flash Part 6 verification reached the project top and exposed two additional
baseline-VHDL integration cases:

- Chained Verilog ternary muxes in continuous assignments are now flattened
  into one VHDL conditional signal assignment, e.g.
  `a when c0 else b when c1 else c`. This avoids malformed/nested VHDL
  conditional expressions and fixes scalar muxes such as `engine_start`,
  `qspi_cs_n`, and the warm-up routing signals.
- Empty named output-port connections on ordinary child modules now translate
  to VHDL `open`, not a blank association. This fixes constructs such as
  `.busy()` on the SPI byte engine.

Focused regressions protect both failures.

## RC9 regression correction

A late re-run of the previously hardware-validated Pmod ACL + VGA project found
a regression in character-valued Verilog string literals such as `"+"` and
`"-"`. A generic expression splitter was incorrectly interpreting operator
characters inside quoted literals as arithmetic operators.

RC9 makes top-level operator splitting string-literal aware and adds a focused
regression for operator-like character literals.

Final release qualification re-ran the Pmod ACL/VGA, RV32I, and Flash Part 6
real-project translation matrix in addition to the self-contained automated tests.
The Pmod ACL/VGA output was also rebuilt in a normal Vivado project, programmed
to a Basys 3, and confirmed to behave the same as the original Verilog design.
