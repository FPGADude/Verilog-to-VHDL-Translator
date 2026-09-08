
from translator_core import VerilogToVHDLTranslator

def test_sequential_integer_and_task_side_effects():
    src = r"""
module demo(
    input wire clk,
    input wire [7:0] value,
    output reg start,
    output reg [7:0] data
);
    integer count;

    task launch;
        input [7:0] v;
        begin
            data <= v;
            start <= 1'b1;
        end
    endtask

    always @(posedge clk) begin
        start <= 1'b0;
        if (count >= 10)
            count <= 0;
        else
            count <= count + 1;
        launch(value);
    end
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    v = r.vhdl
    assert "signal count : integer;" in v
    assert "count <= 0;" in v
    assert "count <= (count + 1);" in v
    assert "signal outer_data : out std_logic_vector(7 downto 0)" in v
    assert "signal outer_start : out std_logic" in v
    assert "outer_data <= v;" in v
    assert "outer_start <= '1';" in v
    assert "launch(value, data_reg, start_reg);" in v

def test_combinational_integer_remains_process_variable_only():
    src = r"""
module comb_demo(
    input wire [7:0] x,
    output reg [7:0] y
);
    integer level;
    always @(*) begin
        level = x;
        y = level;
    end
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    assert "variable level : integer;" in r.vhdl
    assert "signal level : integer;" not in r.vhdl
