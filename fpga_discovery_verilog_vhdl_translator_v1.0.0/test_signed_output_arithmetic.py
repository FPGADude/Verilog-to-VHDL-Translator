
from translator_core import VerilogToVHDLTranslator

def test_signed_output_reg_proxy_preserves_signed_arithmetic():
    src = """
module demo(input clk, input reset, output reg signed [10:0] x);
localparam W = 48;
always @(posedge clk or posedge reset) begin
    if (reset)
        x <= 0;
    else if (x <= -W)
        x <= 640;
    else
        x <= x - 3;
end
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    v = result.vhdl
    assert "signed(x_reg)" in v
    assert "to_signed(-(W), 11)" in v
    assert "std_logic_vector(to_signed(640, 11))" in v
    assert "std_logic_vector(signed(x_reg) - to_signed(3, 11))" in v
