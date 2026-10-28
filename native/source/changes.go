package main

import (
	"compress/gzip"
	"encoding/json"
	"errors"
	"io"
	"net/http"
	"strconv"
	"strings"
	"sync"
	"time"
)

type changeSession struct {
	sent             uint64
	acknowledgements chan uint64
}
type changeHub struct {
	sync.Mutex
	sessions map[string]*changeSession
}

type deletedVersion struct {
	kind, id string
	sequence uint64
}

// State replication tracks the latest version of each entity, not a queue of
// every intervening tick. A slow reader catches up to the current state.
func (b *Book) markChanged(kind, id string, version uint64) {
	if b.versions == nil {
		b.versions = make(map[string]map[string]uint64)
	}
	if b.versions[kind] == nil {
		b.versions[kind] = make(map[string]uint64)
	}
	b.versions[kind][id] = version
	if _, exists := b.Records[kind][id]; !exists {
		b.deleted = append(b.deleted, deletedVersion{kind, id, version})
	}
	if len(b.deleted) > 65536 {
		for _, old := range b.deleted[:32768] {
			if _, live := b.Records[old.kind][old.id]; !live && b.versions[old.kind][old.id] == old.sequence {
				delete(b.versions[old.kind], old.id)
				if old.sequence > b.changeFloor {
					b.changeFloor = old.sequence
				}
			}
		}
		b.deleted = b.deleted[32768:]
	}
}

type StateChanges struct {
	Stream       string                                           `json:"stream_id,omitempty"`
	Op           string                                           `json:"op"`
	Epoch        string                                           `json:"epoch"`
	Base         uint64                                           `json:"base"`
	Sequence     uint64                                           `json:"sequence"`
	Connected    bool                                             `json:"connected"`
	Complete     bool                                             `json:"complete"`
	AccountMS    int64                                            `json:"account_ms"`
	PositionMS   int64                                            `json:"position_ms"`
	ServerTimeMS int64                                            `json:"server_time_msc"`
	Updates      map[string]map[string]json.RawMessage            `json:"updates"`
	Patches      map[string]map[string]map[string]json.RawMessage `json:"patches,omitempty"`
}

func (b *Book) stateChanges(epoch string, after uint64) (StateChanges, error) {
	b.RLock()
	defer b.RUnlock()
	if epoch != b.Epoch || after > b.Sequence || after < b.changeFloor || !b.Connected || !b.Complete {
		return StateChanges{}, errors.New("source snapshot required")
	}
	result := StateChanges{Op: "changes", Epoch: b.Epoch, Base: after, Sequence: b.Sequence,
		Connected: b.Connected, Complete: b.Complete, AccountMS: b.AccountMS, PositionMS: b.PositionMS,
		ServerTimeMS: b.ServerTimeMS, Updates: make(map[string]map[string]json.RawMessage)}
	for kind, versions := range b.versions {
		for id, version := range versions {
			if version <= after {
				continue
			}
			if result.Updates[kind] == nil {
				result.Updates[kind] = make(map[string]json.RawMessage)
			}
			// A missing row is an explicit tombstone; JSON encodes it as null.
			result.Updates[kind][id] = b.Records[kind][id]
		}
	}
	return result, nil
}

func (b *Book) changes(w http.ResponseWriter, r *http.Request) {
	after, err := strconv.ParseUint(r.URL.Query().Get("after"), 10, 64)
	if err != nil {
		http.Error(w, "Invalid sequence", 400)
		return
	}
	epoch := r.URL.Query().Get("epoch")
	next, err := b.stateChanges(epoch, after)
	if err != nil {
		http.Error(w, "Snapshot required", 409)
		return
	}
	b.changeHub.Lock()
	if b.changeHub.sessions == nil {
		b.changeHub.sessions = make(map[string]*changeSession)
	}
	if len(b.changeHub.sessions) >= 64 {
		b.changeHub.Unlock()
		http.Error(w, "Too many state readers", 503)
		return
	}
	id := token()
	session := &changeSession{acknowledgements: make(chan uint64, 1)}
	b.changeHub.sessions[id] = session
	b.changeHub.Unlock()
	defer func() { b.changeHub.Lock(); delete(b.changeHub.sessions, id); b.changeHub.Unlock() }()
	w.Header().Set("Content-Type", "application/x-ndjson")
	w.Header().Set("Cache-Control", "no-store")
	controller := http.NewResponseController(w)
	var output io.Writer = w
	var compressed *gzip.Writer
	if strings.Contains(r.Header.Get("Accept-Encoding"), "gzip") {
		w.Header().Set("Content-Encoding", "gzip")
		w.Header().Set("Vary", "Accept-Encoding")
		compressed, _ = gzip.NewWriterLevel(w, gzip.BestSpeed)
		defer compressed.Close()
		output = compressed
	}
	encoder := json.NewEncoder(output)
	flush := func() error {
		if compressed != nil {
			if err := compressed.Flush(); err != nil {
				return err
			}
		}
		return controller.Flush()
	}
	ticker := time.NewTicker(100 * time.Millisecond)
	defer ticker.Stop()
	positions := newPositionBaselines()
	for {
		if r.Header.Get("X-Native-Field-Patches") == "1" {
			positions.compact(&next)
		}
		next.Stream = id
		b.changeHub.Lock()
		session.sent = next.Sequence
		b.changeHub.Unlock()
		_ = controller.SetWriteDeadline(time.Now().Add(15 * time.Second))
		if encoder.Encode(next) != nil || flush() != nil {
			return
		}
		after = next.Sequence
		// Permit only one frame in flight, including the socket buffers.
		ackDeadline := time.NewTimer(15 * time.Second)
		select {
		case <-r.Context().Done():
			ackDeadline.Stop()
			return
		case <-ackDeadline.C:
			return
		case <-session.acknowledgements:
			ackDeadline.Stop()
		}
		select {
		case <-r.Context().Done():
			return
		case <-ticker.C:
		}
		next, err = b.stateChanges(epoch, after)
		if err != nil {
			_ = encoder.Encode(map[string]string{"op": "resync"})
			_ = flush()
			return
		}
	}
}

func (b *Book) acknowledgeChanges(w http.ResponseWriter, r *http.Request) {
	if r.Method != http.MethodPost {
		http.Error(w, "POST required", 405)
		return
	}
	var ack struct {
		Stream   string `json:"stream_id"`
		Sequence uint64 `json:"sequence"`
	}
	decoder := json.NewDecoder(http.MaxBytesReader(w, r.Body, 512))
	if decoder.Decode(&ack) != nil {
		http.Error(w, "Invalid acknowledgement", 400)
		return
	}
	b.changeHub.Lock()
	session := b.changeHub.sessions[ack.Stream]
	if session == nil || session.sent != ack.Sequence {
		b.changeHub.Unlock()
		http.Error(w, "State version changed", 409)
		return
	}
	select {
	case session.acknowledgements <- ack.Sequence:
	default:
	}
	b.changeHub.Unlock()
	w.WriteHeader(http.StatusNoContent)
}
