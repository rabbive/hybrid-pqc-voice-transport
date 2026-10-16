from hpqv.audio import OpusCodec, FRAME_BYTES, _encoder_get


def test_encoder_fec_and_packet_loss_settings():
    for fec, want_fec, want_loss in ((True, 1, 10), (False, 0, 0)):
        codec = OpusCodec(fec=fec)
        assert _encoder_get(codec.enc, 4013) == want_fec
        assert _encoder_get(codec.enc, 4015) == want_loss


def test_opus_roundtrip_frame_length():
    codec_e, codec_d = OpusCodec(), OpusCodec()
    pcm = b"\x00\x01" * 960                 # one 20ms mono frame
    encoded = codec_e.encode(pcm)
    assert 0 < len(encoded) < FRAME_BYTES   # compressed
    decoded = codec_d.decode(encoded)
    assert len(decoded) == FRAME_BYTES      # 960 samples * 2 bytes
