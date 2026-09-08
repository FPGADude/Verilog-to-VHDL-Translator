
from translator_core import VerilogToVHDLTranslator

def test_memory_index_plus_one_bit_literal_becomes_integer_plus_one():
    src = """
module demo(input clk, input [3:0] idx, output reg [7:0] q);
reg [7:0] mem [0:15];
always @(posedge clk) begin
    q <= mem[idx + 1'b1];
end
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    v = r.vhdl
    assert "mem(to_integer(unsigned(idx)) + 1)" in v
    assert "+ '1'" not in v
