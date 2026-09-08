from hpqv.audio import OpusCodec, FRAME_BYTES

def test_opus_roundtrip_frame_length():
    codec_e, codec_d = OpusCodec(), OpusCodec()
    pcm = b"\x00\x01" * 960                 # one 20ms mono frame
    encoded = codec_e.encode(pcm)
    assert 0 < len(encoded) < FRAME_BYTES   # compressed
    decoded = codec_d.decode(encoded)
    assert len(decoded) == FRAME_BYTES      # 960 samples * 2 bytes
