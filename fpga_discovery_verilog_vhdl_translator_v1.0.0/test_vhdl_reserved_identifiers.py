
from translator_core import VerilogToVHDLTranslator

def test_reserved_output_port_select_uses_extended_identifier():
    src = """
module child(
    input clk,
    output reg select
);
always @(posedge clk)
    select <= ~select;
endmodule
"""
    result = VerilogToVHDLTranslator().translate_text(src)
    assert result.ok, [d.format() for d in result.diagnostics]
    assert r"\select\ : out std_logic" in result.vhdl
    assert r"\select\ <= select_reg;" in result.vhdl

def test_reserved_child_formal_select_is_escaped_in_port_map():
    src = """
module child(input a, output select);
assign select = a;
endmodule

module top(input a, output y);
child u_child(.a(a), .select(y));
endmodule
"""
    # This focused test uses the project layer because the formal name belongs
    # to the child-module interface.
    from pathlib import Path
    import tempfile
    from project_core import ProjectTranslator

    with tempfile.TemporaryDirectory() as td:
        td = Path(td)
        (td / "child.v").write_text(
            "module child(input a, output select); assign select = a; endmodule\n"
        )
        (td / "top.v").write_text(
            "module top(input a, output y); child u_child(.a(a), .select(y)); endmodule\n"
        )
        project = ProjectTranslator().translate_files([td/"child.v", td/"top.v"])
        assert project.modules["child"].result.ok
        assert r"\select\ <= a;" in project.modules["child"].result.vhdl
        assert project.modules["top"].result.ok
        assert r"\select\ => y" in project.modules["top"].result.vhdl
