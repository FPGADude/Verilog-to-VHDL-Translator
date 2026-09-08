
from project_core import ProjectTranslator
from pathlib import Path
import tempfile

def test_empty_named_child_output_maps_to_open():
    child = r"""
module child(
    input wire clk,
    output wire busy,
    output wire done
);
    assign busy = clk;
    assign done = clk;
endmodule
"""
    top = r"""
module top(
    input wire clk,
    output wire done
);
    child u_child(
        .clk(clk),
        .busy(),
        .done(done)
    );
endmodule
"""
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "child.v").write_text(child)
        (td / "top.v").write_text(top)
        p = ProjectTranslator().translate_files([td / "child.v", td / "top.v"])

    assert p.ok, p.diagnostics
    v = p.modules["top"].result.vhdl
    assert "busy => open" in v
    assert "busy => ," not in v
