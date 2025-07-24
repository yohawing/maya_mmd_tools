import socket

code = """
from tests.gui import run_gui_tests
run_gui_tests.run()
"""


# code = 'print("hello from maya")\n'
with socket.create_connection(("localhost", 7001)) as s:
    s.sendall(code.encode("utf-8"))
    s.shutdown(socket.SHUT_WR)
    response = s.recv(4096)
    print(response.decode("utf-8"))
