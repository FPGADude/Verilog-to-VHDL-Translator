
from translator_core import VerilogToVHDLTranslator

def test_verilog_case_sensitive_name_collision_is_renamed():
    src = """
module demo(input [9:0] snake_y, output y);
localparam SNAKE_Y = 200;
assign y = (snake_y == SNAKE_Y);
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    assert "constant SNAKE_Y_const : integer := 200;" in result.vhdl
    assert "SNAKE_Y_const" in result.vhdl

def test_function_boolean_result_becomes_std_logic():
    src = """
module demo(input a, input b, output y);
function both;
    input a;
    input b;
    begin
        both = a && b;
    end
endfunction
assign y = both(a,b);
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    assert "both_result := '1';" in result.vhdl
    assert "both_result := '0';" in result.vhdl

def test_sum_of_bit_selects_context_sizes_each_bit():
    src = """
module demo(input [4:0] bits, output [2:0] count);
assign count = bits[0] + bits[1] + bits[2] + bits[3] + bits[4];
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    assert "unsigned(bits(0))" not in result.vhdl
    assert "std_logic_vector\'(\"00\" & bits(0))" in result.vhdl

def test_comb_process_reads_output_reg_proxy_not_out_port():
    src = """
module demo(input a, output reg q, output reg y);
always @(*) begin
    y = q & a;
end
always @(posedge a) begin
    q <= ~q;
end
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    assert ("process(q_reg, a)" in result.vhdl or "process(a, q_reg)" in result.vhdl)
    assert "process(a, q)" not in result.vhdl
