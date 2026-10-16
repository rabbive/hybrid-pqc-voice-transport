"""Test helpers: v2 sessions without a second process (real TLS over 127.0.0.1)."""
from hpqv import packet, secure
from hpqv.keyring import Keyring


def channels():
    """(client, server) SecureChannels from secure.local_pair()."""
    return secure.local_pair()


def rings():
    """(client_ring, server_ring): what one side seals, the other opens."""
    client, server = secure.local_pair()
    pair = Keyring(client.media_keys()), Keyring(server.media_keys())
    client.close()
    server.close()
    return pair


def sealer_and_ring():
    """(seal, ring): seal(seq, frame, flags=0) makes a datagram for `ring` to open.

    The sealer takes any seq, in any order, so tests can reorder and replay; a
    Keyring refuses to seal a seq that does not increase.
    """
    client, server = secure.local_pair()
    keys = client.media_keys()
    key, iv = packet.direction_keys(keys.send_secret)

    def seal(seq, frame, flags=0):
        return packet.seal(key, iv, keys.session_id, seq, frame, flags=flags)

    ring = Keyring(server.media_keys())
    client.close()
    server.close()
    return seal, ring
