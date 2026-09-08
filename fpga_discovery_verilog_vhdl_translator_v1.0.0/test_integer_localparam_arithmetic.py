
from translator_core import VerilogToVHDLTranslator

def test_untyped_localparam_with_underscored_decimal_stays_integer():
    src = """
module demo(input clk, output reg tick);
localparam SCAN_MAX = 100_000 - 1;
reg [16:0] count;
always @(posedge clk) begin
    if (count == SCAN_MAX) begin
        count <= 0;
        tick <= 1'b1;
    end else begin
        count <= count + 1'b1;
        tick <= 1'b0;
    end
end
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    assert "constant SCAN_MAX : integer := (100000 - 1);" in r.vhdl
    assert "std_logic_vector(unsigned(100_000)" not in r.vhdl
