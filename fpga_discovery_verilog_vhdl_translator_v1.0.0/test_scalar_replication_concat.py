
from translator_core import VerilogToVHDLTranslator

def test_scalar_bit_replication_in_nested_concat_uses_scalar_repeat_overload():
    src = r"""
module immgen_style(
    input  wire [31:0] instr,
    output wire [31:0] imm_i
);
    assign imm_i = {{20{instr[31]}}, instr[31:20]};
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    v = result.vhdl

    assert "function repeat_slv(value : std_logic; count : natural)" in v
    assert "repeat_slv(instr(31), 20)" in v
    assert "{20{" not in v
