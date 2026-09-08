
from translator_core import VerilogToVHDLTranslator

def test_equality_operand_is_in_comb_sensitivity():
    src = """
module demo(input [9:0] x, input [9:0] y, output reg hit);
always @(*) begin
    hit = 1'b0;
    if ((y == 10'd256) && (x[2:0] != 3'b111))
        hit = 1'b1;
end
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    assert "process(y, x)" in r.vhdl or "process(x, y)" in r.vhdl

def test_relational_and_equality_reads_are_not_masked_as_assignments():
    src = """
module demo(input [1:0] state, input [7:0] x, output reg y);
always @(*) begin
    y = 1'b0;
    if ((state == 2'd1) && (x <= 8'd20))
        y = 1'b1;
end
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    assert ("process(state, x)" in r.vhdl or "process(x, state)" in r.vhdl)
