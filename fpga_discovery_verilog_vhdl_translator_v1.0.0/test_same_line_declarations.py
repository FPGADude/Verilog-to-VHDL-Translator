
from translator_core import VerilogToVHDLTranslator

def test_two_wire_declarations_on_same_source_line():
    src = """
module demo(output y);
wire signed [10:0] car0_x; wire [9:0] car0_y;
wire signed [10:0] car1_x; wire [9:0] car1_y;
assign y = car0_y[0] ^ car1_y[0];
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    v = result.vhdl
    assert "signal car0_x : std_logic_vector(10 downto 0);" in v
    assert "signal car0_y : std_logic_vector(9 downto 0);" in v
    assert "signal car1_x : std_logic_vector(10 downto 0);" in v
    assert "signal car1_y : std_logic_vector(9 downto 0);" in v
