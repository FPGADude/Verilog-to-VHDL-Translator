
from app import BOARD_PRESETS

def test_fpga_discovery_board_presets():
    assert BOARD_PRESETS == {
        "Basys 3": "xc7a35tcpg236-1",
        "Nexys A7-50T": "xc7a50ticsg324-1L",
        "Nexys A7-100T": "xc7a100tcsg324-1",
        "Cmod A7-15T": "xc7a15tcpg236-1",
        "Cmod A7-35T": "xc7a35tcpg236-1",
    }
