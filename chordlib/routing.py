""" Defines hashing functions and the Chord finger table.
"""

import hashlib

from chordlib import search
from chordlib import utils, L


HASHFN = hashlib.md5#hashlib.sha256
def chord_hash(data):
    import random, string
    return ''.join([ random.choice(string.letters) for _ in xrange(2) ])
    return HASHFN(data).digest()


HASHLEN = len(chord_hash("0"))
BITCOUNT = HASHLEN * 8
HASHMOD  = 2 ** BITCOUNT


def pack_string(data):
    """ Turns a string into its unique numeric representation.

    Packs a string into a binary value by summing each individual character,
    appropriately shifted. For example,

        "HI" is converted to 'H' + ('I' << 8), where each character is the ASCII
        digit equivalent.

    This assumes an ASCII charset because of the 8-bit-per-character factor.

    Of course, long strings become incredibly large numbers. Python does support
    arbitrarily large numbers, but I don't recommend using this function for
    very long strings.
    """
    if not isinstance(data, str):
        raise TypeError("Expected str, got %s" % str(type(data)))

    total = 0
    for i, c in enumerate(data):
        total += ord(c) << (8 * (len(data) - 1 - i))

    L.debug("Hash for %s -- %d", repr(data), total)
    return total

def unpack_string(val):
    """ Turns a numeric value into a string by treating every byte as a char.
    """
    string = ""
    while val > 0:
        string += chr(val & 0xFF)
        val >>= 8
    return string[::-1]

def khash(k, m):
    return (2 ** k) % m

def moddist(a, b, m):
    """ Finds the distance FROM a TO b in a modulo ring of size m. """
    if b >= a: return b - a
    return (m - a) + b


class Hash(object):
    """ Represents a hashed object with proper conversions between types.
    """
    def __init__(self, value="", hashed=""):
        """ Initializes the hash in one of two ways.

        Either you know the initial value, and the hash is computed, or you know
        the hashed value (and the initial value is by definition not
        determinable) and only that is stored.
        """
        if value and hashed:
            raise ValueError("Either pass a value or its hash.")

        self._value = value
        if self._value:
            self._hash_str = chord_hash(self._value)
            self._hash_int = pack_string(self._hash_str)

        elif isinstance(hashed, int):
            self._hash_str = unpack_string(hashed)
            self._hash_int = hashed

        elif isinstance(hashed, str):
            self._hash_str = hashed
            self._hash_int = pack_string(hashed)

        elif isinstance(hashed, Hash):  # copy
            self._hash_str = str(hashed)
            self._hash_int = int(hashed)
            self._value = hashed.value

        else:
            raise TypeError("Expected value or (int, str, Hash), got: "
                "value='%s',hashed='%s'" % (value, hashed))

        assert str(self) == unpack_string(int(self)), \
            "Unpacked hash must match direct hash!"

        assert len(str(self)) == (BITCOUNT / 8), \
            "Invalid hash size: %s" % str(self)

    @property
    def value(self):
        return self._value

    def __eq__(self, other):
        if isinstance(other, int):
            return int(self) == other
        elif isinstance(other, str):
            return str(self) == other
        elif isinstance(other, Hash):
            return int(self) == int(other)
        raise TypeError("Hash.__eq__ called with invalid parameter.")

    def __ne__(self, other):
        return not (self == other)

    def __str__(self):  return self._hash_str
    def __int__(self):  return self._hash_int % HASHMOD
    def __repr__(self): return str(int(self))


class Interval(object):
    """ Represents an interval [a, b) in a modulus ring.
    """
    def __init__(self, start, end, mod=HASHMOD):
        self.modulus = mod
        self.interval = (start, end)

    def within(self, x):
        """ Is `x` within [start, end)? """
        x = int(x)  # convert from `Hash` object
        assert x < self.modulus, "checking un-%% value: %s/%d" % (x, HASHMOD)
        if self.end < self.start:   # interval wraps around mod boundary
            return utils.in_range(x, self.start, self.modulus) or \
                   utils.in_range(x, 0, self.end)

        return utils.in_range(x, *self.interval)

    def within_open(self, x):
        """ Is `x` within (start, end)? """
        x = int(x)  # convert from `Hash` object
        assert x < self.modulus, "checking un-%% value: %s/%d" % (x, HASHMOD)
        if self.end < self.start:   # interval wraps around mod boundary
            return utils.in_range(x, self.start + 1, self.modulus) or \
                   utils.in_range(x, 0, self.end)

        return utils.in_range(x, self.start + 1, self.end)

    def within_closed(self, x):
        """ Is `x` within [start, end]? """
        x = int(x)  # convert from `Hash` object
        assert x < self.modulus, "checking un-%% value: %s/%d" % (x, HASHMOD)
        if self.end < self.start:   # interval wraps around mod boundary
            return utils.in_range(x, self.start, self.modulus) or \
                   utils.in_range(x, 0, self.end + 1)

        return utils.in_range(x, self.start, self.end + 1)

    @property
    def start(self): return self.interval[0]

    @property
    def end(self): return self.interval[1]

    def __repr__(self): return str(self)
    def __str__(self):
        return "[%d, %s)" % (self.start, self.end)


class Route(Interval):
    """ Represents a single entry in a finger table.
    """
    NODE_COUNT = 5      # fallback nodes (incl. current node)

    def __init__(self, start, end, node=None, mod=HASHMOD):
        super(Route, self).__init__(start, end, mod)
        self.nodes = []
        if node is not None:    # set initial node, if any
            self.nodes.append(node)

    @property
    def node(self):
        return self.nodes[-1] if self.nodes else None

    @node.setter
    def node(self, value):
        """ Sets a new latest node value, maintaining backup list length.
        """
        assert value, "Can't add non-nodes! %s" % value
        while len(self.nodes) >= Route.NODE_COUNT:
            self.nodes.pop(0)   # at least one slot for the new value
        self.nodes.append(value)

    def remove(self):
        """ Removes the latest node from the entry; there are no length checks.
        """
        self.nodes.pop(-1)

    def __repr__(self): return str(self)
    def __str__(self):
        return "%s | %s" % (Interval.__str__(self), self.node)


class RoutingTable(object):
    """ Establishes a routing table for a particular node.

    The routing table is a list that is rotated around the root node (that is,
    the node this table is established for). On initialization, it's established
    to match the size of the bit-length of the hash.

    TODO: Optimize this.
    """

    def __init__(self, node, bitcount=BITCOUNT):
        """ Initializes the routing table with 2^i intervals around the root.
        """
        self.modulus = 2 ** bitcount
        self.seen_nodes = set()

        node_hash = int(node.hash)
        self.entries = [
            Route((node_hash + 2 ** i) % self.modulus,
                  (node_hash + 2 ** (i + 1)) % self.modulus,
                  None, self.modulus) \
            for i in xrange(bitcount)
        ]

        self.root = node
        self.local = Route(self.entries[-1].end, int(self.root.hash), self.root)

    def insert(self, node):
        """ Adds a node to the table if it's better than any successors.

        TODO: Improve O(n) insertion.
        """
        self.seen_nodes.add(node)

        for i, f in enumerate(self.entries):
            # If this interval doesn't have a node associated with it
            #   OR
            # This node is closer to the start of the interval than the existing
            # node.
            if f.node is None or (
               moddist(f.start, int(node.hash), self.modulus) < \
               moddist(f.start, int(f.node.hash), self.modulus)):
                f.node = node

    def remove(self, node):
        """ Removes an existing node from the table.

        If possible, the route will then point to the next available node.

        For example, removing <3> from the table: [
            [0, 3)  -> <3>,
            [3, 7)  -> <4>,
            [8, 15) -> <4>,
        ] should result in <4> for all intervals.

        There are a few scenarios to consider:
            - Removal of entries UP TO the end of the table.
            - Removal of entries PAST the end of the table (i.e. a segment
              within the ring, wrapping around the modulus).
            - Removal of entries WITHIN the table without wrapping (this is
              the "normal" case).

        Each of these mean the same action: the first entry that follows the
        removed ones is the new successor node.
        """
        L.info("Discarding node from lookup table with hash=%d", node.hash)
        L.debug("Removing node from routing table:")
        L.debug("\t%s", node)

        self.seen_nodes.discard(node)

        removed = {}    # dict -> { index: cleaned node }
        L.debug("During removal, removed:")
        for i, f in enumerate(self.entries):
            if f.node is node:
                L.debug("\t%s", f)
                removed[i] = f
                f.remove()

        L.debug("Node routing table is now:")
        for i in xrange(0, len(self) - 1, 2):
            L.debug("\t%s ; %s", self.finger(i), self.finger(i + 1))

        # TODO: Optimize this, because they should all be the same?
        for index, entry in removed.iteritems():
            repl = self.find_successor(entry.start)
            if repl:
                self.entries[index].node = repl

    def find_successor(self, value):
        """ Finds the successor node for a particular value.
        """
        return self.find_predecessor(value).successor

    def find_predecessor(self, value):
        """ Finds the predecessor node for a particular value.
        """
        start = self.root
        if start.successor is None:     # no routes yet
            return start

        tmp_entry = Interval(int(start.hash), int(start.successor.hash),
                             self.modulus)

        value = int(value)  # ensure we don't deal with `Hash`
        while not tmp_entry.within_closed(value):
            start = start.fingers.lookup_preceding(value)

            # If this evaluates to `None`, this is most likely a `RemoteNode`,
            # so we don't have any routing information for it. Thus, we need to
            # ask the node to continue the lookup for us (from a higher level).
            if start.fingers.finger(0).node is None:
                return start

            tmp_entry = Interval(int(start.hash), int(start.successor.hash),
                                 self.modulus)

        return start

    def lookup_preceding(self, value):
        """ Finds the route that comes before the given value.
        """
        for i in xrange(len(self) - 1, -1, -1):
            n = self.finger(i).node
            if Interval(
                int(self.root.hash), value, self.modulus
            ).within_open(int(n.hash)):
                return n
        return self.root

    def finger(self, i):
        return self.entries[i]

    @property
    def successor(self):
        return self.finger(0)

    @successor.setter
    def successor(self, node):
        self.successor.node = node

    @property
    def real_length(self):
        """ Returns the number of unique nodes in the finger table. """
        return len(set([self.finger(i).node for i in xrange(len(self))]))

    def __len__(self):  return len(self.entries)
    def __repr__(self): return str(self)
    def __str__(self):
        return "[ %s ]" % ",\n  ".join([
            str(self.finger(i)) for i in xrange(len(self))
        ])