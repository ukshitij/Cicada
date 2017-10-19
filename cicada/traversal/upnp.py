#! /usr/bin/env python2
import miniupnpc as upnp
from . import portmapper


class UPnP(portmapper.PortMapper):
    """ Manages and caches port mappings for listeners using UPnP.

    References:
      - https://github.com/miniupnp/miniupnp/blob/master/miniupnpc/miniupnpcmodule.c
      - https://github.com/arvidn/libtorrent/blob/master/src/upnp.cpp
    """
    def __init__(self, local_address=None):
        super(UPnP, self).__init__(local_address)
        self._upnp = upnp.UPnP()

    def create(self):
        """ Discovers the UPnP server.
        """
        self._upnp.discover()
        self._upnp_addr = self._upnp.selectigd()
        self.external_ip = self._upnp.externalipaddress()
        super(UPnP, self).create()

    def add_port_mapping(self, local_port, external_port, protocol="tcp"):
        """ Adds a new port mapping to the gateway.
        """
        rv = super(UPnP, self).add_port_mapping(local_port, external_port,
                                                protocol)
        if not rv: return False

        try:
            self._upnp.addportmapping(external_port, protocol.upper(),
                                  self.local_address, local_port,
                                  "Cicada server, %d -> %d" % (
                                    local_port, external_port), None)
        except:
            self._remove_from_cache(local_port)

    def cleanup(self):
        """ Deletes all port mappings and clears the cache.
        """
        for local, pair in self.mappings.iteritems():
            external, protocol = pair
            self._upnp.deleteportmapping(external, protocol)

        super(UPnP, self).cleanup()
