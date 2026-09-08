
from translator_core import VerilogToVHDLTranslator

def test_open_drain_inout_z_literal():
    src = """
module od(
    input wire pull_low,
    inout wire sda
);
assign sda = pull_low ? 1'b0 : 1'bz;
wire sda_in = sda;
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    assert "sda : inout std_logic" in r.vhdl
    assert "'Z'" in r.vhdl
    assert "sda_in" in r.vhdl

def test_vector_binary_meta_literal():
    src = """
module od4(input wire en, output wire [3:0] bus);
assign bus = en ? 4'b1010 : 4'bzzzz;
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    assert '"ZZZZ"' in r.vhdl
