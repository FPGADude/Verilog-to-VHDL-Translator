
from translator_core import VerilogToVHDLTranslator

def test_rv32i_jalr_add_then_mask_is_fully_typed():
    src = r"""
module jalr_mask(
    input  wire [31:0] rs1_data,
    input  wire [31:0] imm_i,
    output wire [31:0] next_pc
);
    assign next_pc = (rs1_data + imm_i) & 32'hFFFF_FFFE;
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    v = result.vhdl

    # The addition must be translated numerically before the vector AND.
    assert "std_logic_vector(" in v
    assert "unsigned(rs1_data)" in v
    assert "unsigned(imm_i)" in v
    assert 'and ("11111111111111111111111111111110")' in v
    assert "(rs1_data + imm_i) and" not in v
