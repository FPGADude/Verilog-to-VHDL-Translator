
from translator_core import VerilogToVHDLTranslator

def test_localparam_clog2_and_symbolic_zero_replication():
    src = """
module demo #(parameter integer CLK=100_000_000, parameter integer FREQ=100_000)
(input clk);
localparam integer DIV = CLK / (FREQ * 4);
localparam integer COUNT_WIDTH = $clog2(DIV);
reg [COUNT_WIDTH-1:0] count;
always @(posedge clk)
    count <= {COUNT_WIDTH{1'b0}};
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    v = r.vhdl
    assert "function clog2" in v
    assert "constant COUNT_WIDTH : integer := clog2(DIV);" in v
    assert "signal count : std_logic_vector(COUNT_WIDTH-1 downto 0);" in v
    assert "count <= (others => '0');" in v
    assert "$clog2" not in v
    assert "COUNT_WIDTH{'0'}" not in v

def test_symbolic_replication_of_z_in_assignment():
    src = """
module demo #(parameter integer W=8)(output wire [W-1:0] data_bus);
assign data_bus = {W{1'bz}};
endmodule
"""
    r = VerilogToVHDLTranslator().translate_text(src)
    assert r.ok, [d.format() for d in r.diagnostics]
    assert "data_bus <= (others => 'Z');" in r.vhdl
