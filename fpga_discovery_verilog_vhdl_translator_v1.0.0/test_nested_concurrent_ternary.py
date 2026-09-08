
from translator_core import VerilogToVHDLTranslator

def test_nested_mux_chain_is_baseline_vhdl_conditional_signal_assignment():
    src = r"""
module mux_chain(
    input wire [1:0] sel,
    input wire a,
    input wire b,
    input wire c,
    output wire y
);
    assign y = (sel == 2'd0) ? a :
               (sel == 2'd1) ? b :
                               c;
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    v = r.vhdl

    assert "y <= a when" in v
    assert "else b when" in v
    assert "else c;" in v
    assert "'1' when a when" not in v
