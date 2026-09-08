
from pathlib import Path
import tempfile
from project_core import ProjectTranslator

def test_variable_left_shift_one_hot_generation():
    src = r"""
module led_blinker(
    input wire clk,
    input wire [3:0] key_code,
    output reg [15:0] led
);
reg [3:0] active_led;
always @(posedge clk) begin
    led <= (16'b1 << key_code);
    active_led <= key_code;
    if (active_led == key_code)
        led <= (16'b1 << active_led);
end
endmodule
"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "led_blinker.v"
        p.write_text(src, encoding="utf-8")
        r = ProjectTranslator().translate_files([p])
        assert r.ok, r.diagnostics + [d.format() for m in r.modules.values() for d in m.result.diagnostics]
        v = r.modules["led_blinker"].result.vhdl
        assert "shift_left" in v
        assert "to_integer(unsigned(key_code))" in v
        assert "to_integer(unsigned(active_led))" in v
