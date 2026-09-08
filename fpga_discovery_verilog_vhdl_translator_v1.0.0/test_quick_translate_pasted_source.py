
from pathlib import Path
import tempfile
from project_core import ProjectTranslator

def test_pasted_standalone_module_translates_through_project_pipeline():
    source = r"""
module nes_controller(
    input  wire clk,
    input  wire data,
    output reg  latch,
    output reg  nes_clk,
    output reg [7:0] buttons
);
    reg [7:0] shift_reg;
    always @(posedge clk) begin
        latch <= 1'b0;
        nes_clk <= ~nes_clk;
        shift_reg <= {shift_reg[6:0], data};
        if (nes_clk)
            buttons <= shift_reg;
    end
endmodule
"""
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "pasted_verilog.v"
        p.write_text(source, encoding="utf-8")
        result = ProjectTranslator().translate_files([p])

    assert result.ok, result.diagnostics
    assert result.translated_count == 1
    assert len(result.roots) == 1
    assert result.roots[0].module_name == "nes_controller"
    assert "nes_controller" in result.modules
    vhdl = result.modules["nes_controller"].result.vhdl
    assert "entity nes_controller is" in vhdl
    assert "architecture rtl of nes_controller is" in vhdl
