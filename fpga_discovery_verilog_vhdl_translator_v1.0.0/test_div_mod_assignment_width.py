
from translator_core import VerilogToVHDLTranslator

def test_div_mod_result_is_resized_to_narrow_destination():
    src = """
module demo(input [16:0] score, output reg [3:0] digit);
always @(*) begin
    digit = (score / 10000) % 10;
end
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    v = result.vhdl
    assert "resize(" in v
    assert ", 4)" in v
    assert "unsigned(score)" in v
    assert " rem 10" in v
