#!/usr/bin/env python

import socket

class Utility:

    # Return the primary IP address of the interface that has the default route.
    # Credit: fatal_error at https://stackoverflow.com/a/28950776
    def myIpAddress():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('10.255.255.255', 1))                        # Doesn't even have to be reachable
            IP = s.getsockname()[0]
        except Exception:
            IP = '127.0.0.1'
        finally:
            s.close()
        return IP
