package main

import (
	"bytes"
	"encoding/json"
)

// Baselines belong to one acknowledged stream. A new stream always sends full
// rows first; it never assumes the contents of a caller's earlier snapshot.
type positionBaselines struct {
	rows  map[string]map[string]json.RawMessage
	bytes int
}

func newPositionBaselines() *positionBaselines {
	return &positionBaselines{rows: make(map[string]map[string]json.RawMessage)}
}

func positionRowBytes(row map[string]json.RawMessage) int {
	n := 512
	for key, value := range row {
		n += len(key) + len(value) + 64
	}
	return n
}

func (b *positionBaselines) compact(frame *StateChanges) {
	for id, raw := range frame.Updates["positions"] {
		previous := b.rows[id]
		if raw == nil {
			if previous != nil {
				b.bytes -= positionRowBytes(previous)
			}
			delete(b.rows, id)
			continue
		}
		var current map[string]json.RawMessage
		if json.Unmarshal(raw, &current) != nil || current == nil {
			continue
		}
		if previous != nil && len(previous) == len(current) {
			patch := make(map[string]json.RawMessage)
			sameShape := true
			for key, value := range current {
				if _, exists := previous[key]; !exists {
					sameShape = false
				}
				if !bytes.Equal(value, previous[key]) {
					patch[key] = value
				}
			}
			// A shape change is a replacement. Otherwise, send only changed fields.
			if sameShape && len(patch) > 0 && len(patch) < len(current) {
				if frame.Patches == nil {
					frame.Patches = make(map[string]map[string]map[string]json.RawMessage)
				}
				if frame.Patches["positions"] == nil {
					frame.Patches["positions"] = make(map[string]map[string]json.RawMessage)
				}
				frame.Patches["positions"][id] = patch
				delete(frame.Updates["positions"], id)
			}
		}
		if previous != nil {
			b.bytes -= positionRowBytes(previous)
		}
		b.rows[id] = current
		b.bytes += positionRowBytes(current)
		if len(b.rows) > 32768 || b.bytes > 32*1024*1024 {
			// Eviction only increases the next frame to full rows; it cannot lose data.
			b.rows = make(map[string]map[string]json.RawMessage)
			b.bytes = 0
		}
	}
}
