
from translator_core import VerilogToVHDLTranslator

def test_memory_element_lhs_uses_element_width_not_one_bit():
    src = """
module demo(input clk, input reset);
reg [7:0] calibration [0:15];
integer i;
always @(posedge clk) begin
    if (reset) begin
        for (i = 0; i < 16; i = i + 1)
            calibration[i] <= 0;
    end
end
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    v = r.vhdl
    assert "calibration(i) <= std_logic_vector(to_unsigned(0, 8));" in v
    assert "calibration(i) <= '0';" not in v
