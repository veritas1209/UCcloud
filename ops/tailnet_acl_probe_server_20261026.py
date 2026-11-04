"""Short-lived fixed-response listener. No commands, credentials or data access."""
import selectors
import socket
import sys
import time

selector = selectors.DefaultSelector()
listeners = []
for address in sys.argv[1:]:
    family = socket.AF_INET6 if ':' in address else socket.AF_INET
    listener = socket.create_server((address, 49091), family=family)
    listener.setblocking(False)
    selector.register(listener, selectors.EVENT_READ)
    listeners.append(listener)
end = time.monotonic() + 600
try:
    while time.monotonic() < end:
        for item, _ in selector.select(timeout=1):
            connection, _ = item.fileobj.accept()
            with connection:
                connection.settimeout(2)
                connection.sendall(b'UCCLOUD_ACL_PROBE\n')
finally:
    for listener in listeners:
        listener.close()
