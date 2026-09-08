# Supported Verilog RTL — v1.0.0 RC1

The FPGA Discovery Verilog → VHDL Translator focuses on a practical,
synthesizable Verilog RTL subset.

## Qualified areas

- Verilog-2001 style modules
- ANSI and supported non-ANSI port declarations
- `wire`, `reg`, signed declarations
- packed vectors, bit selects, part selects
- parameters and localparams
- named parameter overrides
- continuous assignments
- clocked `always` blocks
- asynchronous-reset sequential logic
- combinational `always @(*)`
- supported explicit sensitivity lists
- `if` / `else if` / `else`
- `case` / `default`
- counted synthesizable procedural `for` loops
- supported generate-for / generate-if structures
- multi-module hierarchy with named port connections
- synthesizable functions
- synthesizable tasks
- unpacked memories
- inferred writable RAM
- supported `$readmemh` ROM initialization
- arithmetic and comparisons
- logical and bitwise operators
- reduction AND / OR / XOR
- signed arithmetic
- signed multiplication
- arithmetic right shift
- concatenation and replication
- dynamic packed-vector indexing
- ternary expressions in supported contexts
- `.vh` headers
- `` `include ``
- object-like `` `define `` macros
- supported conditional preprocessing
- `$clog2` in supported parameterized-width expressions
- selected Vivado-recognized vendor primitives/resources

## Intentional scope limits

v1.0 is not intended to support:

- SystemVerilog
- classes/interfaces/packages/UVM
- arbitrary testbench-only behavioral code
- unrestricted delays/timing behavior
- force/release
- assertions/coverage
- unrestricted procedural file I/O
- every macro/preprocessor feature
- every vendor-specific extension
- unsupported constructs without diagnostics

## Translation philosophy

The translator is deterministic. Unsupported or ambiguous constructs should
produce diagnostics rather than silently generating questionable VHDL.

Vivado verification is part of the intended workflow and should be used before
treating translated output as release-ready FPGA RTL.
