
from translator_core import VerilogToVHDLTranslator

def test_verilog_case_without_default_gets_vhdl_others_null():
    src = """
module demo(input [3:0] state, output reg y);
localparam [3:0] A = 4'd0,
                 B = 4'd1,
                 C = 4'd2;
always @(*) begin
    y = 1'b0;
    case (state)
        A: y = 1'b1;
        B: y = 1'b0;
        C: y = 1'b1;
    endcase
end
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    v = result.vhdl
    assert "case state is" in v
    assert "when others =>" in v
    assert "null;" in v
