
from translator_core import VerilogToVHDLTranslator

def test_mixed_concat_is_explicitly_qualified():
    src = """
module demo(input [3:0] idx, output reg [15:0] status);
always @(*) begin
    status = {8'h10, 4'h0, idx};
end
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    v = r.vhdl
    assert "std_logic_vector'(" in v
    assert "& idx" in v

def test_concat_inside_multiply_is_explicitly_qualified():
    src = """
module demo(input [7:0] hi, input [7:0] lo, output reg [31:0] scaled);
always @(*) begin
    scaled = {hi, lo} * 32'd3331;
end
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    assert "unsigned(std_logic_vector'(hi & lo))" in r.vhdl
