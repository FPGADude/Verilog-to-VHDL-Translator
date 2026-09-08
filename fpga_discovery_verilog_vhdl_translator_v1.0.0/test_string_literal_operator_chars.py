from translator_core import VerilogToVHDLTranslator


def test_character_string_literals_that_look_like_operators_are_not_split():
    src = r'''
module char_literal_demo(
    input wire sel,
    output reg [7:0] ch
);
    always @(*) begin
        if (sel)
            ch = "-";
        else
            ch = "+";
    end
endmodule
'''
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    v = r.vhdl
    assert 'to_unsigned(45, 8)' in v
    assert 'to_unsigned(43, 8)' in v
    assert 'resize(unsigned(")' not in v
