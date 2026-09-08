
from translator_core import VerilogToVHDLTranslator

def test_combinational_integer_temporary_becomes_process_variable():
    src = """
module demo(
    input wire [13:0] distance_cm,
    input wire valid,
    output reg [15:0] led_bar
);
integer level;
always @(*) begin
    led_bar = 16'h0000;
    level = 0;
    if (valid) begin
        if (distance_cm <= 14'd30)
            level = 16;
        else if (distance_cm >= 14'd300)
            level = 0;
        else
            level = ((300 - distance_cm) * 16) / 270;
        case (level)
            0: led_bar = 16'h0000;
            1: led_bar = 16'h0001;
            default: led_bar = 16'hFFFF;
        endcase
    end
end
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    v = r.vhdl
    assert "variable level : integer;" in v
    assert "signal level" not in v
    assert "level := 0;" in v
    assert "level := 16;" in v
    assert "to_integer(unsigned(distance_cm))" in v
    assert "case level is" in v
