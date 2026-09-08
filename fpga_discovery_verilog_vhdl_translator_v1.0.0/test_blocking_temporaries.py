
from translator_core import VerilogToVHDLTranslator

def test_blocking_scratch_reg_in_clocked_block_becomes_process_variable():
    src = """
module demo(
    input clk,
    input reset,
    input [7:0] a,
    output reg [7:0] q
);
reg [7:0] tmp;
always @(posedge clk or posedge reset) begin
    if (reset) begin
        q <= 8'd0;
    end else begin
        tmp = a;
        tmp = tmp + 8'd1;
        q <= tmp;
    end
end
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    assert not any("Blocking assignment inside clocked always block" in d.message for d in result.diagnostics)
    assert "variable tmp_var : std_logic_vector(7 downto 0);" in result.vhdl
    assert "tmp_var := a;" in result.vhdl
    assert "q_reg <= tmp_var;" in result.vhdl
