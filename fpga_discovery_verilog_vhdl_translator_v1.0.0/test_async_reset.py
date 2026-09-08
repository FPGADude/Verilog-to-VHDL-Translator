
from translator_core import VerilogToVHDLTranslator

def test_async_active_high_or_reset():
    src = """
module demo(input clk, input reset, input d, output reg q);
always @(posedge clk or posedge reset) begin
    if (reset)
        q <= 1'b0;
    else
        q <= d;
end
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.message for d in result.diagnostics]
    assert "process(clk, reset)" in result.vhdl
    assert "if reset = '1' then" in result.vhdl
    assert "elsif rising_edge(clk) then" in result.vhdl

def test_async_active_high_comma_reset():
    src = """
module demo(input clk, input reset, output reg [1:0] q);
always @(posedge clk, posedge reset)
    if (reset)
        q <= 2'b00;
    else
        q <= q + 2'b01;
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.message for d in result.diagnostics]
    assert "process(clk, reset)" in result.vhdl
    assert "elsif rising_edge(clk) then" in result.vhdl

def test_async_reset_with_else_if_clock_branch():
    src = """
module demo(input clk, input reset, input en, output reg q);
always @(posedge clk or posedge reset) begin
    if (reset)
        q <= 1'b0;
    else if (en)
        q <= 1'b1;
    else
        q <= 1'b0;
end
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.message for d in result.diagnostics]
    assert "elsif rising_edge(clk) then" in result.vhdl
    assert "if en = '1' then" in result.vhdl
