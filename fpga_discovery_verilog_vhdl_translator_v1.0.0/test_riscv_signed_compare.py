
from translator_core import VerilogToVHDLTranslator

def test_signed_cast_comparison_does_not_get_swallowed_by_cast_regex():
    src = r"""
module signed_cmp(
    input  wire [31:0] a,
    input  wire [31:0] b,
    output reg  [31:0] y
);
    always @* begin
        y = ($signed(a) < $signed(b)) ? 32'd1 : 32'd0;
    end
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    v = result.vhdl
    assert "$signed" not in v
    assert "signed(a)" in v
    assert "signed(b)" in v
    assert "if signed(a) < signed(b) then" in v
