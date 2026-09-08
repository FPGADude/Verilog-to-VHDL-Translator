
from pathlib import Path
import tempfile
from project_core import ProjectTranslator

def test_complex_input_port_actual_uses_helper_signal():
    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td/"child.v").write_text("""
module child(input send, output busy);
assign busy = send;
endmodule
""")
        (td/"top.v").write_text("""
module top(input key_valid, input uart_busy, output busy);
child u_child(
    .send(key_valid && !uart_busy),
    .busy(busy)
);
endmodule
""")
        p = ProjectTranslator().translate_files([td/"child.v", td/"top.v"])
        assert p.ok, p.diagnostics
        v = p.modules["top"].result.vhdl
        assert "signal u_child_send_actual : std_logic;" in v
        assert "u_child_send_actual <=" in v
        assert "send => u_child_send_actual" in v
        assert "send => '1' when" not in v
