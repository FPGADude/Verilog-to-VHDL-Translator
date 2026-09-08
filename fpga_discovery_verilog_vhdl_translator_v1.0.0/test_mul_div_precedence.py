
from translator_core import VerilogToVHDLTranslator

def test_vector_divided_by_integer_constant():
    src = """
module demo(input clk, output reg hit);
localparam [24:0] DUR = 25'd7500000;
reg [24:0] timer;
always @(posedge clk) begin
    if (timer == (DUR / 3))
        hit <= 1'b1;
end
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    assert "unsigned(DUR)" in result.vhdl
    assert " / 3" in result.vhdl
    assert "DUR / 3" not in result.vhdl

def test_multiply_then_divide_preserves_verilog_left_associativity():
    src = """
module demo(input clk, output reg hit);
localparam [24:0] DUR = 25'd7500000;
reg [24:0] timer;
always @(posedge clk) begin
    if (timer == (DUR * 2 / 3))
        hit <= 1'b1;
end
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    v = result.vhdl
    # The generated form must divide the result of DUR*2, not compute integer 2/3 first.
    assert "unsigned(std_logic_vector(unsigned(DUR) * 2)) / 3" in v
    assert "(2 / 3)" not in v
