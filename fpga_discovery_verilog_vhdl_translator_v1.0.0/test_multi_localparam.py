
from translator_core import VerilogToVHDLTranslator

def test_multiple_localparams_share_vector_width():
    src = """
module demo(input [1:0] state, output reg y);
localparam [1:0] IDLE = 2'd0,
                 RUN  = 2'd1,
                 DONE = 2'd2;
always @(*) begin
    y = 1'b0;
    case (state)
        IDLE: y = 1'b0;
        RUN:  y = 1'b1;
        DONE: y = 1'b0;
    endcase
end
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    v = result.vhdl
    assert "constant IDLE : std_logic_vector(1 downto 0)" in v
    assert "constant RUN : std_logic_vector(1 downto 0)" in v
    assert "constant DONE : std_logic_vector(1 downto 0)" in v
    assert "constant IDLE" in v and ", RUN =" not in v
