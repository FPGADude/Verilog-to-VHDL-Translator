
from translator_core import VerilogToVHDLTranslator

def test_one_bit_function_call_in_if_becomes_boolean_comparison():
    src = """
module demo(input a, output reg y);
function hit;
    input a;
    begin
        hit = a;
    end
endfunction
always @(*) begin
    y = 1'b0;
    if (hit(a))
        y = 1'b1;
end
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    assert "if hit(a) = '1' then" in r.vhdl

def test_or_of_function_calls_is_not_greedily_parsed_as_one_call():
    src = """
module demo(input a, input b, output y);
function hit;
    input x;
    begin
        hit = x;
    end
endfunction
assign y = hit(a) || hit(b);
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    assert "(hit(a) = '1') or (hit(b) = '1')" in r.vhdl

def test_signed_function_locals_use_signed_math():
    src = """
module demo(input signed [10:0] px, input signed [10:0] x0, output y);
function hit;
    input signed [10:0] px;
    input signed [10:0] x0;
    reg signed [11:0] x;
    begin
        x = px - x0;
        hit = (x >= 0);
    end
endfunction
assign y = hit(px,x0);
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    assert "signed(x)" in r.vhdl
    assert "to_signed(0, 12)" in r.vhdl
