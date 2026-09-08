
from translator_core import VerilogToVHDLTranslator

def test_concat_times_sized_literal_uses_safe_integer_multiply():
    src = """
module demo(input [7:0] hi, input [7:0] lo, output reg [31:0] scaled);
always @(*) begin
    scaled = {hi, lo} * 32'd3331;
end
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    v = r.vhdl
    assert "to_integer(unsigned(std_logic_vector'(hi & lo))) * 3331" in v
    assert "std_logic_vector(to_unsigned(" in v
