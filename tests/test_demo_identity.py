"""Identity creation must produce working peer pins without destroying existing keys."""
import pytest
from hpqv import demo, handshake


def test_created_identity_authenticates_both_roles(tmp_path):
    path = tmp_path / "demo identity.json"
    assert hasattr(demo, "create_identity"), "GUI and CLI need shared identity creation"
    demo.create_identity(path)
    a_pub, a_secret, a_peer = demo._load_identity(path, "caller")
    b_pub, b_secret, b_peer = demo._load_identity(path, "listener")
    assert a_peer == b_pub and b_peer == a_pub
    hello, kem, kem_pub = handshake.build_hello(a_pub, a_secret)
    accept, listener = handshake.accept_hello(hello, b_peer, b_secret)
    caller = handshake.finish(accept, kem, kem_pub, a_peer)
    assert caller[0] == listener[0]
    assert caller[1] == listener[2]


def test_create_identity_preserves_existing_file(tmp_path):
    path = tmp_path / "identity.json"
    path.write_text("existing private identity")
    assert hasattr(demo, "create_identity"), "Identity creation must refuse overwrite"
    with pytest.raises(FileExistsError):
        demo.create_identity(path)
    assert path.read_text() == "existing private identity"

