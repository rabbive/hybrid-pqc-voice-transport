-- Wireshark dissector for the hpqv v2 voice datagram (docs/wire-format.md, v2).
--
--   ver_type(1) = 0x21 | flags(1) | session_id(4) | seq(uint48 BE) | ciphertext + tag
--   flags: bits 0-1 key generation mod 4, bit 2 FEC hint, bits 3-7 reserved (0)
--
-- Heuristic: the listener's port is chosen by the user and the caller's is
-- ephemeral, so this claims any UDP payload that looks like a v2 datagram.
-- Load with:  wireshark -X lua_script:tools/wireshark/hpqv.lua
-- or copy into ~/.local/lib/wireshark/plugins/.

local hpqv = Proto("hpqv", "hpqv v2 voice datagram")

local f = hpqv.fields
f.ver_type   = ProtoField.uint8("hpqv.ver_type", "Version/type", base.HEX)
f.flags      = ProtoField.uint8("hpqv.flags", "Flags", base.HEX)
f.gen        = ProtoField.uint8("hpqv.gen", "Key generation (mod 4)", base.DEC, nil, 0x03)
f.fec        = ProtoField.bool("hpqv.fec", "Sender FEC on", 8, nil, 0x04)
f.session_id = ProtoField.bytes("hpqv.session_id", "Session id")
f.seq        = ProtoField.uint64("hpqv.seq", "Sequence", base.DEC)
f.ciphertext = ProtoField.bytes("hpqv.ciphertext",
                                "Encrypted Opus frame + Poly1305 tag (ChaCha20-Poly1305)")

local HEADER, TAG, MAX = 12, 16, 1200

local function looks_like_v2(tvb)
    local len = tvb:len()
    if len < HEADER + TAG + 1 or len > MAX then return false end
    if tvb(0, 1):uint() ~= 0x21 then return false end
    return bit.band(tvb(1, 1):uint(), 0xF8) == 0
end

function hpqv.dissector(tvb, pinfo, tree)
    if not looks_like_v2(tvb) then return 0 end
    local seq = tvb(6, 6):uint64()
    local gen = bit.band(tvb(1, 1):uint(), 0x03)
    pinfo.cols.protocol = "HPQV"
    pinfo.cols.info = string.format("hpqv v2 voice seq=%s gen=%d len=%d",
                                    tostring(seq), gen, tvb:len())
    local t = tree:add(hpqv, tvb(), "hpqv v2 voice datagram")
    t:add(f.ver_type, tvb(0, 1)):append_text(" (version 2, voice)")
    local fl = t:add(f.flags, tvb(1, 1))
    fl:add(f.gen, tvb(1, 1))
    fl:add(f.fec, tvb(1, 1))
    t:add(f.session_id, tvb(2, 4))
    t:add(f.seq, tvb(6, 6))
    t:add(f.ciphertext, tvb(HEADER))
    return tvb:len()
end

hpqv:register_heuristic("udp", function(tvb, pinfo, tree)
    if not looks_like_v2(tvb) then return false end
    hpqv.dissector(tvb, pinfo, tree)
    return true
end)
