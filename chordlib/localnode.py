""" Defines a local Chord node.

This is the object you want to use to begin structuring a Chord ring on your
machine. At this point, you can either join an existing ring (by using the
`LocalNode.join_ring` method), or simply by waiting for a node to join this
ring.
"""

import threading
import socket
import select
import random
import time

from chordlib import utils as chutils
from chordlib import fingertable

from chordlib import commlib
from chordlib import chordnode
from chordlib import remotenode

import packetlib
from   packetlib import chord as chordpkt
from   packetlib import utils as pktutils

class LocalNode(chordnode.ChordNode):
    """ Represents the current local node in the Chord hash ring.
    """

    def __init__(self, data, bind_addr=('localhost', 2017)):
        """ Creates a node on a specific address with specific data.

        Typically, the data that you pass is simply a string representation of
        the binding address. This is because in Cicada we _use_ the binding
        address hash as the identifier of the node to route packets.

        :data       the data to be hashed by this Chord node. It creates a
                    unique identifier for the node.
        :bind_addr  specifies the address to use for the listener socket.
        """

        # This socket is responsible for inbound connections (from new potential
        # Peer nodes). It is always in an "accept" state in a separate thread.
        self.listener = packetlib.debug.LoggedSocket()
        self.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.listener.bind(bind_addr)
        self.listener.listen(5)

        # This is the listener thread. When it receives a new socket, it will
        # create a Peer object from it.
        print "Starting listener thread for", self.listener.getsockname()
        self.listen_thread = commlib.ListenerThread(self.listener,
                                                    self.on_new_peer)
        self.listen_thread.start()

        # This is a thread that processes all of the known peers for messages
        # and calls the appropriate message handler.
        self.processor = commlib.SocketProcessor()
        self.processor.start()

        # These are all of the known direct neighbors in the Chord ring.
        self.peers = []

        super(LocalNode, self).__init__(data, bind_addr)

    def join_ring(self, remote_address, timeout=10):
        """ Joins a Chord ring through a node at the specified address.

        We use the stabilization technique outlined in the Chord paper, which
        means that a JOIN only involves asking our entry point for our immediate
        successor.

        :remote_address     the address referring to a node in a Chord ring.
        :timeout[=30]       the number of seconds to wait for a response.
        """
        remote_address = (socket.gethostbyname(remote_address[0]),
                          remote_address[1])
        self.pr("join_ring::self", str(self))
        self.pr("join_ring::addr", str(remote_address))

        assert self.fingers.real_length <= 1, "join_ring: existing nodes!"
        assert self.predecessor is None,      "join_ring: predecessor set!"

        #
        # Create a Peer object that represents the external node that is letting
        # us into the ring. We send a JOIN request to the address, expecting a
        # response indicating our would-be successor.
        #
        # We validate this by establishing a connection to the successor and
        # adding it to our peer list.
        #

        joiner_peer = self.add_peer(remote_address, remote_address)
        join_msg = chordpkt.JoinRequest.make_packet(self.local_addr)
        if not self.processor.request(joiner_peer.peer_sock, join_msg,
                                      self.on_join_response, timeout):
            raise ValueError("JOIN request didn't get a response in time.")

        return join_msg

    def add_peer(self, peer_address, peer_listener_addr, peer_socket=None):
        """ From an arbitrary remote address, add a Peer to the ring.
        """
        p = remotenode.RemoteNode(peer_address, peer_listener_addr,
                                  existing_socket=peer_socket)
        self.peers.append(p)
        self.processor.add_socket(p.peer_sock,
                                  lambda s, msg: self.process(s, msg))
        return p

    def add_node(self, node):
        """ Adds a node to the internal finger table.

        This means proper ordering in the finger table with respect to the
        node's hash value.
        """
        self.pr("add_node::self", str(self))
        self.pr("add_node::node", str(node))
        assert isinstance(node, chordnode.ChordNode), \
               "add_node: not a ChordNode object!"

        self.fingers.insert(node)

    def remove_node(self, node):
        """ Indicates a node has disconnected / failed in the Chord ring.
        """
        self.pr("remove_node::self", str(self))
        self.pr("remote_addr::node", str(node))
        assert isinstance(node, chordnode.ChordNode), \
               "remove_node: not a ChordNode object!"

        self.fingers.remove(node)

        #
        # If this node used to be our successor, replace it with the next
        # available node. Likewise with the predecessor, except the previous
        # available node.
        #
        # If this was the last node in the ring (excluding us) this should
        # properly trigger the "first node" code path in `on_node_joined`.
        #

        if self.successor is node:
            self.successor = self.fingers.find_successor(node.hash + 1)

        if self.predecessor is node:
            self.predecessor = self.fingers.find_predecessor(node.hash - 1)

    def stabilize(self):
        """ Runs the stabilization algorithm.

        Stabilizing means asking our successor, n, "for the successor's
        predecessor p, and decid[ing] whether p should be [our] successor
        instead." Then, we tell n's successor about n. That way, the successor
        can learn about n if it didn't already know in the first place.
        """
        if self.successor is None:  # nothing to stabilize, yet
            return

        # import pdb; pdb.set_trace()
        x = self.successor.get_predecessor()

        # If our successor doesn't *have* a predecessor, tell them about us, at
        # least! That way, the ring is linked.
        if x is None:
            assert isinstance(self.successor, Peer)
            self.successor.notify(self)
            return

        # We HAVE to use an open-ended range check, because if our successor's
        # predecessor is us (as it would be in the normal case), we'd be setting
        # us as our own successor!
        if self.fingers.local.within_open(x.hash):
            self.fingers.set_successor(x)
        self.successor.notify(self)

    def fix_fingers(self):
        """ This ensures that the finger table is current.
        """
        index = random.randint(1, len(self.fingers) - 1)
        thumb = self.finger(index)
        if thumb.node is None:
            return

        # self.pr("fix_fingers: fixing finger(%d)" % index)
        # self.pr("fix_fingers: fingers are\n%s" % self.fingers)
        thumb.node = self.fingers.find_successor(thumb.start)
        # self.pr("fix_fingers: fingers are now\n%s" % self.fingers)

    def process(self, peer_socket, msg):
        if peer_socket is None:     # socket closed?
            print "RemoteNode shut down!"
            return

        print "LocalNode received message %s on %s:%d" % (repr(msg),
            peer_socket.getsockname()[0], peer_socket.getsockname()[1])

        handlers = {
            packetlib.MessageType.MSG_CH_JOIN:  self.on_join_request,
            packetlib.MessageType.MSG_CH_INFO:  self.on_info_request,
            packetlib.MessageType.MSG_CH_INFO:  self.on_notify_request,
        }

        for msg_type, func in handlers.iteritems():
            if msg.type == msg_type:
                func(peer_socket, msg)
                break
        else:
            raise ValueError("Invalid message received! No handler.")

    def on_new_peer(self, client_address, client_socket):
        """ Creates a new Peer from a socket that just joined this ring.
        """
        self.processor.add_socket(client_socket, self.process)

    def on_info_request(self, sock, msg):
        if self.predecessor is None:
            pred_addr = self.listener.getsockname()
        else:
            pred_addr = self.predecessor.remote_addr

        infor_msg = chordpkt.JoinResponse.make_packet(
            self.successor.hash, self.successor.remote_addr,
            original=msg)

        self.pr("Responding to INFO with %s" % repr(infor_msg))
        self.processor.response(message, sock, infor_msg)
        return True

    def on_join_response(self, sock, msg):
        """ Executed when an JOIN _response_ is received.
        """
        self.pr("join_response::self", self)
        self.pr("join_response::sock", sock)
        self.pr("join_response::msg", msg)

        joinr_msg = chordpkt.JoinResponse.unpack(msg.data)

        print "Our successor:", joinr_msg.listener
        successor_peer = self.add_peer(sock.getsockname(), joinr_msg.listener)
        return True

        # TODO NEXT TIME:
        # We need to send the proper successor address that we can properly
        # join. Currently, it seems like it's just the temporary peer socket
        # address.

        # self.add_node(successor_peer)

        # assert self.successor == successor_peer, "Invalid successor!"

    def on_join_request(self, sock, msg):
        """ Receives a JOIN request from a node previously outside the ring.

        Chord specifies that this occurs when a new node joins the ring and
        chooses us as its successor. Thus, we use this node to (potentially)
        establish our predecessor.

        If it's our first node, though, it also gets added to the finger table!

        The response is an RJOIN containing the would-be successor information
        of the incoming Peer. If we're the only ones in the ring, we return
        "NONE" and let them use our remote address.
        """

        self.pr("node_joined::self", str(self))
        self.pr("node_joined::sock", str(sock))
        self.pr("node_joined::msg", repr(msg))

        # import pdb; pdb.set_trace()

        # Always add node, because it could be better than some existing ones.
        req = chordpkt.JoinRequest.unpack(msg.data)
        p = self.add_peer(sock.getpeername(), req.listener, sock)
        self.add_node(p)

        # Is this node closer to us than our existing predecessor?
        if self.predecessor is None or \
           hashring.Interval(self.predecessor.hash, self.hash).within(p.hash):
            self.predecessor = p

        response_object = self if (
            not self.successor or \
            self.successor.local_addr == req.listener
        ) else self.successor
        response = chordpkt.JoinResponse.make_packet(
            response_object.hash, response_object.local_addr, original=msg)

        self.pr("node_joined:", "response --", repr(response))
        self.processor.response(sock, response)
        return True

    def on_notify_request(self, sock, msg):
        """ Determine whether or not a node should be our predecessor.
        """
        self.pr("notify::sock", sock)
        self.pr("notify::msg", msg)

        node = [ x for x in self.peers if x.peer_sock is sock ][0]
        if self.predecessor is None or \
           hashring.Interval(self.predecessor.hash, self.hash).within(node):
            self.predecessor = node

        assert self.predecessor != self, "notify: set self as predecessor!"

        notify_msg = chordpkt.NotifyResponse.make_packet(
            self.hash, self.successor.remote_addr,
            self.predecessor.remote_addr)

        self.processor.respond_to(msg, sock, notify_msg)
        return True

