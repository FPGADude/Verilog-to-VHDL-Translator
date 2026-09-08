
from translator_core import VerilogToVHDLTranslator

def test_32bit_hex_mask_above_vhdl_integer_range_becomes_bit_vector():
    src = r"""
module wide_mask(
    input  wire [31:0] a,
    output wire [31:0] y
);
    assign y = a & 32'hFFFF_FFFE;
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    v = result.vhdl
    assert "4294967294" not in v
    assert '"11111111111111111111111111111110"' in v
