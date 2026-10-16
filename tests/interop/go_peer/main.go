// Interop peer for tests/test_interop_go.py: Go's own crypto/tls (no OpenSSL code)
// speaking hpqv v2's TLS profile, so our pyOpenSSL/OpenSSL side is checked against an
// independent implementation of RFC 8446 + RFC 10024 (X25519MLKEM768) + ML-DSA-65
// (draft-ietf-tls-mldsa) instead of against itself.
//
// usage: go_peer -role server|client -addr HOST:PORT -cert C.pem -key K.pem -peer P.pem
//        [-groups pq|classical]
// Prints one JSON line with what Go negotiated and the hpqv exporter values.
package main

import (
	"bytes"
	"crypto/tls"
	"crypto/x509"
	"encoding/hex"
	"encoding/json"
	"encoding/pem"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"time"
)

func must(err error) {
	if err != nil {
		json.NewEncoder(os.Stdout).Encode(map[string]string{"error": err.Error()})
		os.Exit(1)
	}
}

func main() {
	role := flag.String("role", "server", "")
	addr := flag.String("addr", "127.0.0.1:0", "")
	certPath := flag.String("cert", "", "")
	keyPath := flag.String("key", "", "")
	peerPath := flag.String("peer", "", "")
	groups := flag.String("groups", "pq", "")
	flag.Parse()

	pair, err := tls.LoadX509KeyPair(*certPath, *keyPath)
	must(err)
	peerPEM, err := os.ReadFile(*peerPath)
	must(err)
	block, _ := pem.Decode(peerPEM)
	pinned, err := x509.ParseCertificate(block.Bytes)
	must(err)

	verify := func(raw [][]byte, _ [][]*x509.Certificate) error {
		if len(raw) == 0 {
			return errors.New("no peer certificate")
		}
		got, err := x509.ParseCertificate(raw[0])
		if err != nil {
			return err
		}
		if !bytes.Equal(got.RawSubjectPublicKeyInfo, pinned.RawSubjectPublicKeyInfo) {
			return errors.New("peer key is not the pinned key")
		}
		return nil
	}
	curves := []tls.CurveID{tls.X25519MLKEM768}
	if *groups == "classical" {
		curves = []tls.CurveID{tls.X25519}
	}
	cfg := &tls.Config{
		MinVersion: tls.VersionTLS13, MaxVersion: tls.VersionTLS13,
		Certificates: []tls.Certificate{pair}, CurvePreferences: curves,
		NextProtos: []string{"hpqv/2"}, InsecureSkipVerify: true, // pinning below replaces chains
		VerifyPeerCertificate: verify, ClientAuth: tls.RequireAnyClientCert,
		SessionTicketsDisabled: true,
	}

	var conn *tls.Conn
	if *role == "server" {
		ln, err := net.Listen("tcp", *addr)
		must(err)
		fmt.Println(ln.Addr().String()) // first line: where to connect
		raw, err := ln.Accept()
		must(err)
		conn = tls.Server(raw, cfg)
	} else {
		raw, err := net.Dial("tcp", *addr)
		must(err)
		conn = tls.Client(raw, cfg)
	}
	conn.SetDeadline(time.Now().Add(10 * time.Second))
	must(conn.Handshake())
	if *role == "server" {
		_, err = conn.Write([]byte{4, 0, 0}) // hpqv MSG_READY frame
		must(err)
	} else {
		ready := make([]byte, 3)
		_, err = io.ReadFull(conn, ready)
		must(err)
		if !bytes.Equal(ready, []byte{4, 0, 0}) {
			must(errors.New("expected READY"))
		}
	}
	st := conn.ConnectionState()
	exp := func(label string, n int) string {
		b, err := st.ExportKeyingMaterial(label, nil, n)
		must(err)
		return hex.EncodeToString(b)
	}
	peer := st.PeerCertificates[0]
	json.NewEncoder(os.Stdout).Encode(map[string]any{
		"version":        fmt.Sprintf("0x%04x", st.Version),
		"curve_id":       fmt.Sprintf("0x%04x", uint16(st.CurveID)),
		"cipher":         tls.CipherSuiteName(st.CipherSuite),
		"alpn":           st.NegotiatedProtocol,
		"peer_key_alg":   peer.PublicKeyAlgorithm.String(),
		"peer_sig_alg":   peer.SignatureAlgorithm.String(),
		"did_resume":     st.DidResume,
		"exp_c2s":        exp("EXPORTER-hpqv2 media c2s", 32),
		"exp_s2c":        exp("EXPORTER-hpqv2 media s2c", 32),
		"exp_session_id": exp("EXPORTER-hpqv2 session id", 4),
		"exp_sas":        exp("EXPORTER-hpqv2 sas", 4),
	})
	conn.Close()
}
