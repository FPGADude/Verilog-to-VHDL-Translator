from translator_core import VerilogToVHDLTranslator

def test_packed_vector_parameter_becomes_vector_generic_and_supports_selects():
    src = r"""
module vector_param #(
    parameter integer CLK_HZ = 100_000_000,
    parameter [23:0] PAGE_BASE = 24'h3FF000
)(
    output wire [7:0] high_byte
);
    assign high_byte = PAGE_BASE[23:16];
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    v = result.vhdl
    assert "CLK_HZ : integer := 100_000_000" in v
    assert "PAGE_BASE : std_logic_vector(23 downto 0)" in v
    assert "std_logic_vector(to_unsigned(4190208, 24))" in v
    assert "PAGE_BASE(23 downto 16)" in v
