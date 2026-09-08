
from translator_core import VerilogToVHDLTranslator

def test_clocked_for_loop_memory_clear():
    src = """
module demo(input clk, input reset);
reg [13:0] samples [0:7];
integer i;
always @(posedge clk) begin
    if (reset) begin
        for (i = 0; i < 8; i = i + 1)
            samples[i] <= 14'd0;
    end
end
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    v = r.vhdl
    assert "for i in 0 to 7 loop" in v
    assert "samples(i) <=" in v
    assert "for (i <=" not in v
    assert not any("Blocking assignment inside clocked always block" in d.message for d in r.diagnostics)
